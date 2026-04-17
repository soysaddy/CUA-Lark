import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger

from agent.guardrail import Guardrail, GuardrailResult, GuardrailSignal
from agent.perception_fusion import FusedPerception, PerceptionFusion
from agent.planner import VisionPlanner
from config import config
from execution.action_executor import ActionExecutor
from execution.recovery import RecoveryManager, RecoveryStatus
from knowledge.lark_capabilities import (
    LARK_CAPABILITIES,
    LARK_COMMON_PATTERNS,
    LARK_PAGE_SIGNATURES,
)
from perception.ax_enhancer import AXEnhancer
from perception.vision_client import VisionClient
from utils.openai_client import create_openai_client
from verification.transition_verifier import TransitionContext, TransitionVerifier


@dataclass
class StepRecord:
    step_num: int
    plan_step_description: str
    observation: str
    thinking: str
    action_decided: dict
    action_executed: dict
    verification: dict
    screenshot_path: str = ""
    duration: float = 0.0


@dataclass
class TaskResult:
    task: str
    goal: str
    success: bool
    steps: list[StepRecord] = field(default_factory=list)
    total_duration: float = 0.0
    plan: dict = field(default_factory=dict)
    error: str = ""
    vision_calls: int = 0
    total_tokens: int = 0
    handoff_required: bool = False
    handoff_reason: str = ""


VISION_DECISION_PROMPT = f"""你是 CUA-Lark Agent，一个通过视觉理解来操控飞书桌面端的 AI 智能体。
你必须返回合法的 json 对象，不要输出额外文字。

## 你的角色
- 你通过屏幕截图看飞书界面
- 你理解页面内容、布局、当前状态
- 你决定下一步操作来完成任务

## 飞书知识
{LARK_PAGE_SIGNATURES}

## 飞书能力边界
{LARK_CAPABILITIES}

## 常见模式
{LARK_COMMON_PATTERNS}

## 你能执行的操作
1. click - 点击坐标
2. double_click - 双击坐标
3. right_click - 右键点击
4. type - 输入文字
5. hotkey - 快捷键
6. scroll - 滚动
7. wait - 等待
8. done - 任务完成
9. fail - 任务无法完成
10. pause_for_user - 暂停并请求用户接管当前步骤

## 坐标规则（极其重要）
- coordinate 是相对于截图图片左上角 (0,0) 的像素坐标
- 截图的宽高会在每次请求中告知你，坐标不得超出该范围
- 请精确点击目标元素的中心位置
- 左侧导航栏通常在 x=0~70 范围内

## 输出格式
请严格输出 json：
{{
  "observation": "当前界面观察（描述你看到了什么）",
  "current_page": "im_main|im_chat|calendar|docs|search|other",
  "thinking": "分析与下一步理由（说明为什么选择这个操作）",
    "action": {{
    "type": "click|double_click|right_click|type|hotkey|scroll|wait|done|fail|pause_for_user",
    "coordinate": [x, y],
    "text": "",
    "keys": [],
    "direction": "up|down",
    "amount": 3,
    "seconds": 1.0,
    "reason": "操作理由"
  }},
  "target_description": "目标元素描述",
  "confidence": "high|medium|low",
  "progress_percent": 0
}}

## 规则
1. 每次只输出一个操作
2. 仔细观察截图再决策，不要盲目重复上一步
3. 如果上一步操作后界面没变化，说明操作无效，换一种方式
4. 页面加载中优先 wait
5. 有弹窗/遮挡先关闭遮挡
6. 如果目标已达成，输出 done
7. 如果遇到登录、验证码、系统权限弹窗、人工确认等必须由用户处理的步骤，输出 pause_for_user
8. 如果上一步验证显示“列表项已选中但聊天页未确认”或“消息列表状态仍在收敛”，优先 wait 或继续观察，不要立即升级为 double_click/right_click
"""


class VisionDecisionLoop:
    def __init__(self, save_dir: str = "./runs") -> None:
        self.client = create_openai_client()
        self.planner = VisionPlanner()
        self.perception = PerceptionFusion()
        self.ax_enhancer = AXEnhancer()
        self.executor = ActionExecutor()
        self.guardrail = Guardrail()
        self.vision = VisionClient()
        self.save_dir = save_dir
        self.conversation_history: list[dict] = []
        self.max_history = config.vision_history_length
        self.last_vision_error = ""
        self.last_verify_result: dict = {}
        self.last_verification_feedback: str = ""
        self.last_decided_action: dict = {}
        self.last_target_description: str = ""
        self.consecutive_wait_hints = 0
        self.retry_hint_count = 0
        self.next_capture_cache: Optional[tuple[dict, dict]] = None
        self.transition_verifier = TransitionVerifier(
            perception=self.perception,
            vision=self.vision,
        )

    def run(self, user_input: str) -> TaskResult:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(self.save_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)

        result = TaskResult(task=user_input, goal="", success=False)
        started_at = time.time()
        self.conversation_history = []
        self.guardrail.reset()
        self.last_vision_error = ""
        self.last_verify_result = {}
        self.last_verification_feedback = ""
        self.last_decided_action = {}
        self.last_target_description = ""
        self.consecutive_wait_hints = 0
        self.retry_hint_count = 0
        self.next_capture_cache = None
        perception_issue_counts: dict[int, int] = {}
        fallback_used_steps: set[int] = set()

        # ── 规划 ──
        initial_perception = self.perception.perceive(with_som=False, with_ax=False)
        plan = self.planner.plan(
            user_input=user_input,
            current_screenshot_b64=initial_perception.screenshot_b64,
        )
        result.plan = plan
        result.goal = plan.get("goal", user_input)
        if not plan.get("feasible", False):
            result.error = f"任务不可行: {plan.get('reasoning', '')}"
            result.total_duration = time.time() - started_at
            return result

        logger.info(
            f"计划: {result.goal} | "
            f"{len(plan.get('steps', []))} 步 | "
            f"信心: {plan.get('confidence', '?')}"
        )

        current_plan_step = 0
        plan_steps = plan.get("steps", [])

        for step_num in range(1, config.max_total_steps + 1):
            plan_hint = ""
            plan_step = None
            plan_context = self._build_plan_context_text(plan, None)
            if current_plan_step < len(plan_steps):
                plan_step = plan_steps[current_plan_step]
                plan_hint = (
                    f"当前计划步骤 [{plan_step.get('id', current_plan_step + 1)}"
                    f"/{len(plan_steps)}]: {plan_step.get('description', '')}"
                )
                plan_context = self._build_plan_context_text(plan, plan_step)

            # ── 感知 ──
            if self.next_capture_cache:
                screen_data, bounds = self.next_capture_cache
                perception = self.perception.perceive_from_capture(
                    screen_data=screen_data,
                    bounds=bounds,
                    with_som=True,
                    with_ax=True,
                )
                self.next_capture_cache = None
            else:
                perception = self.perception.perceive(with_som=True, with_ax=True)
            perception_diag = self._build_perception_diag(perception)
            screenshot_path = os.path.join(run_dir, f"step_{step_num:02d}.png")
            perception.screenshot.save(screenshot_path)
            if perception.annotated_screenshot:
                perception.annotated_screenshot.save(
                    os.path.join(run_dir, f"step_{step_num:02d}_som.png")
                )

            # ── 决策 ──
            decision = self._vision_decide(
                perception=perception,
                task_goal=result.goal,
                plan_hint=plan_hint,
                plan_context=plan_context,
                step_num=step_num,
            )
            decision = self._stabilize_aggressive_retry(decision)
            result.vision_calls += 1
            result.total_tokens += decision.pop("_tokens", 0)

            if not decision:
                result.error = "Vision 决策为空"
                break

            action = decision.get("action", {})
            action_type = action.get("type", "")

            logger.info(
                f"Step {step_num}: {action_type} | "
                f"{decision.get('observation', '')[:60]} | "
                f"confidence={decision.get('confidence', '?')}"
            )

            # ── done ──
            if action_type == "done":
                final_check = self._final_verify(result.goal)
                result.success = final_check.get("success", True)
                result.steps.append(
                    StepRecord(
                        step_num=step_num,
                        plan_step_description=plan_hint,
                        observation=decision.get("observation", ""),
                        thinking=decision.get("thinking", ""),
                        action_decided=action,
                        action_executed={"type": "done"},
                        verification=final_check,
                        screenshot_path=screenshot_path,
                        duration=0.0,
                    )
                )
                break

            # ── handoff / pause_for_user ──
            if action_type in {"pause_for_user", "handoff"}:
                step_key = current_plan_step if current_plan_step < len(plan_steps) else -1
                handoff_resolution = self._resolve_internal_handoff(
                    decision=decision,
                    plan=plan,
                    plan_steps=plan_steps,
                    current_plan_step=current_plan_step,
                    plan_hint=plan_hint,
                    perception_diag=perception_diag,
                    issue_counts=perception_issue_counts,
                    fallback_used_steps=fallback_used_steps,
                )
                if handoff_resolution["mode"] == "retry_capture":
                    retry_verify = {
                        "step_completed": False,
                        "status": "unknown",
                        "transition": None,
                        "next_step_hint": "reobserve",
                        "post_action_state": "perception_retry",
                        "progress_made": False,
                        "template": "internal_perception_retry",
                        "details": handoff_resolution["details"],
                        "perception_diag": perception_diag,
                        "exec_success": False,
                    }
                    self._apply_hint_flags(retry_verify)
                    self._update_transition_counters(retry_verify)
                    result.steps.append(
                        StepRecord(
                            step_num=step_num,
                            plan_step_description=plan_hint,
                            observation=decision.get("observation", ""),
                            thinking=decision.get("thinking", ""),
                            action_decided=action,
                            action_executed={"type": "internal_reobserve"},
                            verification=retry_verify,
                            screenshot_path=screenshot_path,
                            duration=0.0,
                        )
                    )
                    self.last_verify_result = retry_verify
                    self.last_verification_feedback = self._format_verification_feedback(
                        retry_verify
                    )
                    self.next_capture_cache = None
                    continue
                if handoff_resolution["mode"] == "fallback":
                    decision = handoff_resolution["decision"]
                    action = decision.get("action", {})
                    action_type = action.get("type", "")
                    if handoff_resolution.get("current_plan_step") is not None:
                        current_plan_step = handoff_resolution["current_plan_step"]
                        step_key = current_plan_step
                        if current_plan_step < len(plan_steps):
                            plan_step = plan_steps[current_plan_step]
                            plan_hint = (
                                f"当前计划步骤 [{plan_step.get('id', current_plan_step + 1)}"
                                f"/{len(plan_steps)}]: {plan_step.get('description', '')}"
                            )
                            plan_context = self._build_plan_context_text(plan, plan_step)
                else:
                    handoff_reason = handoff_resolution.get(
                        "reason",
                        action.get("reason", "需要用户接管当前步骤"),
                    )
                    perception_issue_counts.pop(step_key, None)
                    result.handoff_required = True
                    result.handoff_reason = handoff_reason
                    result.error = handoff_reason
                    result.steps.append(
                        StepRecord(
                            step_num=step_num,
                            plan_step_description=plan_hint,
                            observation=decision.get("observation", ""),
                            thinking=decision.get("thinking", ""),
                            action_decided=action,
                            action_executed={"type": "pause_for_user"},
                            verification={
                                "step_completed": False,
                                "handoff_required": True,
                                "reason": handoff_reason,
                                "perception_diag": perception_diag,
                            },
                            screenshot_path=screenshot_path,
                            duration=0.0,
                        )
                    )
                    break

            # ── fail ──
            if action_type == "fail":
                result.error = action.get("reason", "Agent 主动报告失败")
                if self.last_vision_error:
                    result.error = f"{result.error}: {self.last_vision_error}"
                break

            # ── 执行 ──
            enhanced_action = self.ax_enhancer.enhance(
                vision_action=action,
                target_description=decision.get("target_description", ""),
                perception=perception,
            )
            fallback_step_key = enhanced_action.get("_planner_fallback_step_key")
            if fallback_step_key is not None:
                fallback_used_steps.add(fallback_step_key)

            step_started = time.time()
            preflight_verify = self._build_click_preflight_result(
                enhanced_action,
                decision.get("target_description", ""),
                original_action=action,
            )
            exec_success = False
            post_action_perception = None
            if preflight_verify is None:
                exec_success = self.executor.execute(
                    enhanced_action, perception.coord_system
                )
                time.sleep(config.action_interval)

            # ── 轻量验证（不重新截图，用 AX 快速检查）──
            if preflight_verify is not None:
                verify_result = preflight_verify
            else:
                verify_result = self._quick_verify_lite(
                    enhanced_action,
                    plan_step,
                    result.goal,
                    decision.get("current_page", ""),
                    decision.get("target_description", ""),
                    update_counters=False,
                )
                if self._needs_heavy_post_action_confirmation(verify_result):
                    screen_data, bounds = self.perception.capture_screen()
                    if screen_data and bounds:
                        post_action_perception = self.perception.perceive_from_capture(
                            screen_data=screen_data,
                            bounds=bounds,
                            with_som=False,
                            with_ax=False,
                        )
                        self.next_capture_cache = (screen_data, bounds)
                        verify_result = self._quick_verify_lite(
                            enhanced_action,
                            plan_step,
                            result.goal,
                            decision.get("current_page", ""),
                            decision.get("target_description", ""),
                            post_action_perception=post_action_perception,
                            update_counters=False,
                        )
                self._update_transition_counters(verify_result)
            verify_result["exec_success"] = exec_success
            verify_result["perception_diag"] = perception_diag
            if enhanced_action.get("_planner_fallback_consumed"):
                verify_result["planner_fallback_consumed"] = True
                verify_result["fallback_source"] = "execution"
            elif (
                str(plan.get("fallback_path", "") or "").find("搜索") >= 0
                and plan_step
                and plan_step.get("type") in {"search_open", "search_select"}
            ):
                verify_result["planner_fallback_consumed"] = True
                verify_result["fallback_source"] = "model_or_plan"
            else:
                verify_result["planner_fallback_consumed"] = False
                verify_result["fallback_source"] = "none"

            record = StepRecord(
                step_num=step_num,
                plan_step_description=plan_hint,
                observation=decision.get("observation", ""),
                thinking=decision.get("thinking", ""),
                action_decided=action,
                action_executed=enhanced_action,
                verification=verify_result,
                screenshot_path=screenshot_path,
                duration=time.time() - step_started,
            )
            result.steps.append(record)
            self.last_decided_action = dict(action)
            self.last_target_description = decision.get("target_description", "")
            self.last_verify_result = verify_result
            self.last_verification_feedback = self._format_verification_feedback(
                verify_result
            )

            if (
                verify_result.get("step_completed", False)
                and current_plan_step < len(plan_steps)
            ):
                current_plan_step += 1
                if current_plan_step >= len(plan_steps):
                    final_check = self._final_verify(result.goal)
                    if final_check.get("success", False):
                        result.success = True
                        break

            # ── 护栏 ──
            guardrail_result = self.guardrail.check(
                step_num=step_num,
                decision=decision,
                verify_result=verify_result,
                history=result.steps,
            )
            if guardrail_result.signal == GuardrailSignal.ABORT:
                result.error = f"护栏中止: {guardrail_result.detail}"
                break
            if guardrail_result.signal == GuardrailSignal.RECOVER:
                recovery_result = RecoveryManager.attempt_recovery(
                    self.executor,
                    current_state=guardrail_result.detail,
                    current_page=decision.get("current_page", ""),
                    max_attempts=3,
                )
                if result.steps:
                    result.steps[-1].verification["recovery"] = {
                        "status": recovery_result.status.value,
                        "reason": recovery_result.reason,
                        "page": recovery_result.snapshot.page,
                        "page_confidence": recovery_result.snapshot.page_confidence,
                        "frontmost": recovery_result.snapshot.frontmost,
                        "has_dialog": recovery_result.snapshot.has_dialog,
                        "actions": recovery_result.actions,
                    }
                if recovery_result.status == RecoveryStatus.HANDOFF:
                    result.handoff_required = True
                    result.handoff_reason = recovery_result.reason
                    result.error = recovery_result.reason
                    break
                if recovery_result.status == RecoveryStatus.NEED_REPLAN:
                    guardrail_result = GuardrailResult(
                        GuardrailSignal.REPLAN, recovery_result.reason
                    )
                if recovery_result.status == RecoveryStatus.RETRYABLE:
                    logger.info(f"恢复结果: retryable | {recovery_result.reason}")
                    continue
            if guardrail_result.signal == GuardrailSignal.REPLAN:
                if self.next_capture_cache:
                    screen_data, bounds = self.next_capture_cache
                    replan_perception = self.perception.perceive_from_capture(
                        screen_data=screen_data,
                        bounds=bounds,
                        with_som=False,
                        with_ax=False,
                    )
                else:
                    replan_perception = self.perception.perceive(
                        with_som=False, with_ax=False
                    )
                new_plan = self.planner.replan(
                    original_plan=plan,
                    current_step=current_plan_step,
                    current_screenshot_b64=replan_perception.screenshot_b64,
                    issue=guardrail_result.detail or "执行偏离预期",
                )
                if new_plan.get("feasible"):
                    plan = new_plan
                    plan_steps = new_plan.get("steps", [])
                    current_plan_step = 0
                    logger.info(f"重规划成功: {len(plan_steps)} 步")
                else:
                    result.error = "重规划失败"
                    break
        else:
            result.error = f"超过最大步数 {config.max_total_steps}"

        result.total_duration = time.time() - started_at
        self._save_trace(result, run_dir)
        status = "✅" if result.success else "❌"
        logger.info(
            f"{status} 任务结束: {len(result.steps)}步 "
            f"{result.total_duration:.1f}s {result.error}"
        )
        return result

    def _vision_decide(
        self,
        perception: FusedPerception,
        task_goal: str,
        plan_hint: str,
        plan_context: str,
        step_num: int,
    ) -> dict:
        if not self.client:
            return self._heuristic_decide(task_goal, plan_hint, step_num)

        try:
            self.last_vision_error = ""
            messages = [{"role": "system", "content": VISION_DECISION_PROMPT}]

            # ── 完整对话历史（含上一步的观察和结果）──
            messages.extend(self.conversation_history[-self.max_history:])

            # ── 告诉 AI 图片尺寸，让坐标有据可依 ──
            img_w = perception.screenshot.width
            img_h = perception.screenshot.height

            context_parts = [
                f"## 当前任务\n目标: {task_goal}\n{plan_hint}\n当前第 {step_num} 步",
                f"\n## 规划上下文\n{plan_context}",
                f"\n## 截图尺寸\n宽={img_w}px 高={img_h}px\n"
                f"coordinate 的 x 范围 [0, {img_w}], y 范围 [0, {img_h}]",
            ]

            # 上一步结果反馈，帮助 AI 避免重复
            if self.conversation_history:
                last_assistant = None
                for msg in reversed(self.conversation_history):
                    if msg["role"] == "assistant":
                        last_assistant = msg["content"]
                        break
                if last_assistant:
                    context_parts.append(
                        f"\n## 上一步结果\n{last_assistant}\n"
                        f"如果上一步操作后界面没有变化，请换一种操作方式。"
                    )

            if self.last_verification_feedback:
                context_parts.append(
                    f"\n## 上一步动作后验证\n{self.last_verification_feedback}"
                )

            if perception.ax_summary:
                context_parts.append(
                    f"\n## 页面结构信息\n{perception.ax_summary}"
                )
            if perception.som_description:
                context_parts.append(
                    f"\n## 可交互元素标注\n{perception.som_description}"
                )

            context_text = "\n".join(context_parts)

            user_content = [
                {"type": "text", "text": context_text},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": (
                            f"data:image/png;base64,"
                            f"{perception.annotated_b64 or perception.screenshot_b64}"
                        ),
                        "detail": "high",
                    },
                },
                {
                    "type": "text",
                    "text": "请仔细分析截图并决定下一步操作，严格返回 json。",
                },
            ]
            messages.append({"role": "user", "content": user_content})

            response = self.client.chat.completions.create(
                model=config.openai_model,
                messages=messages,
                max_tokens=1200,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            raw_content = response.choices[0].message.content
            result = json.loads(raw_content)
            usage = getattr(response, "usage", None)
            result["_tokens"] = (
                getattr(usage, "total_tokens", 0) if usage else 0
            )

            # ── 保存完整决策到历史（不含图片，但含完整观察和动作）──
            action_summary = result.get("action", {})
            self.conversation_history.extend([
                {
                    "role": "user",
                    "content": (
                        f"[Step {step_num}] 目标: {task_goal}\n"
                        f"{plan_hint}\n"
                        f"截图尺寸: {img_w}x{img_h}"
                    ),
                },
                {
                    "role": "assistant",
                    "content": json.dumps(
                        {
                            "observation": result.get("observation", ""),
                            "thinking": result.get("thinking", ""),
                            "action": action_summary,
                            "current_page": result.get("current_page", ""),
                            "progress": result.get("progress_percent", 0),
                        },
                        ensure_ascii=False,
                    ),
                },
            ])
            return result
        except Exception as exc:
            self.last_vision_error = str(exc)
            logger.warning(f"Vision 决策失败，降级启发式: {exc}")
            return self._heuristic_decide(task_goal, plan_hint, step_num)

    @staticmethod
    def _heuristic_decide(
        task_goal: str, plan_hint: str, step_num: int
    ) -> dict:
        if "打开飞书全局搜索" in plan_hint:
            return {
                "observation": "启发式：执行搜索快捷键",
                "current_page": "other",
                "thinking": "快捷键打开搜索最稳定",
                "action": {
                    "type": "hotkey",
                    "keys": ["command", "k"],
                    "reason": "快捷键打开全局搜索",
                },
                "target_description": "全局搜索",
                "confidence": "medium",
                "progress_percent": min(step_num * 10, 90),
                "_tokens": 0,
            }
        if "输入" in plan_hint:
            text = (
                task_goal.split("：", 1)[-1]
                if "：" in task_goal
                else task_goal
            )
            return {
                "observation": "启发式：输入文本",
                "current_page": "other",
                "thinking": "计划要求输入",
                "action": {
                    "type": "type",
                    "text": text,
                    "reason": "根据计划输入",
                },
                "target_description": "输入框",
                "confidence": "low",
                "progress_percent": min(step_num * 10, 90),
                "_tokens": 0,
            }
        return {
            "observation": "启发式能力不足",
            "current_page": "other",
            "thinking": "缺少视觉模型",
            "action": {
                "type": "fail",
                "reason": "缺少视觉决策能力",
            },
            "target_description": "",
            "confidence": "low",
            "progress_percent": 0,
            "_tokens": 0,
        }

    @staticmethod
    def _build_perception_diag(perception: FusedPerception) -> dict:
        coord = perception.coord_system
        screenshot_size = [
            perception.screenshot.width,
            perception.screenshot.height,
        ]
        raw_size = (
            [coord.raw_screenshot_width, coord.raw_screenshot_height]
            if coord else screenshot_size
        )
        resized_size = (
            [coord.resized_screenshot_width, coord.resized_screenshot_height]
            if coord else screenshot_size
        )
        window_bounds = (
            {
                "x": coord.window_x,
                "y": coord.window_y,
                "width": coord.window_width,
                "height": coord.window_height,
            }
            if coord else None
        )
        truly_too_small = screenshot_size[0] < 900 or screenshot_size[1] < 600
        return {
            "screenshot_size": screenshot_size,
            "raw_size": raw_size,
            "resized_size": resized_size,
            "window_bounds": window_bounds,
            "capture_source": getattr(perception, "capture_source", "unknown"),
            "size_assessment": (
                "too_small" if truly_too_small else "size_ok_threshold_maybe_strict"
            ),
        }

    @staticmethod
    def _build_plan_context_text(plan: dict, plan_step: Optional[dict]) -> str:
        parts = []
        if plan.get("preferred_path"):
            parts.append(f"preferred_path={plan.get('preferred_path')}")
        if plan.get("fallback_path"):
            parts.append(f"fallback_path={plan.get('fallback_path')}")
        if plan.get("expected_transition"):
            expected = plan.get("expected_transition")
            if isinstance(expected, dict):
                expected = json.dumps(expected, ensure_ascii=False)
            parts.append(f"expected_transition={expected}")
        if plan_step:
            if plan_step.get("type"):
                parts.append(f"current_step_type={plan_step.get('type')}")
            if plan_step.get("success_signal"):
                signal = plan_step.get("success_signal")
                if isinstance(signal, dict):
                    signal = json.dumps(signal, ensure_ascii=False)
                parts.append(f"current_step_target={signal}")
        return "\n".join(parts) or "无额外规划上下文"

    def _resolve_internal_handoff(
        self,
        decision: dict,
        plan: dict,
        plan_steps: list[dict],
        current_plan_step: int,
        plan_hint: str,
        perception_diag: dict,
        issue_counts: dict[int, int],
        fallback_used_steps: set[int],
    ) -> dict:
        if not self._is_internal_perception_handoff(decision):
            return {"mode": "handoff"}

        step_key = current_plan_step if current_plan_step < len(plan_steps) else -1
        attempts = issue_counts.get(step_key, 0)
        issue_counts[step_key] = attempts + 1
        fallback_text = str(plan.get("fallback_path", "") or "")
        fallback_available = "搜索" in fallback_text or any(
            step.get("type") == "search_open"
            for step in plan_steps[current_plan_step:]
        )
        fallback_attempted = step_key in fallback_used_steps

        if attempts == 0:
            return {
                "mode": "retry_capture",
                "details": [
                    "检测到系统内部感知不稳定，先重拍并重感知一次，不立即 handoff",
                    f"截图诊断: {json.dumps(perception_diag, ensure_ascii=False)}",
                ],
            }

        if fallback_available and not fallback_attempted:
            fallback_resolution = self._build_search_fallback_decision(
                plan=plan,
                plan_steps=plan_steps,
                current_plan_step=current_plan_step,
                decision=decision,
                plan_hint=plan_hint,
                fallback_used_steps=fallback_used_steps,
            )
            if fallback_resolution:
                return {
                    "mode": "fallback",
                    **fallback_resolution,
                }

        if perception_diag.get("size_assessment") == "too_small":
            if fallback_available:
                reason = "当前窗口尺寸异常，已重试且已尝试可用 fallback 仍无法确认目标"
            else:
                reason = "当前窗口尺寸异常，已重试但无可用 fallback，仍无法确认目标"
        else:
            if fallback_available:
                reason = "已重试且已尝试可用 fallback 仍无法确认目标，当前更适合由用户接管"
            else:
                reason = "已重试但无可用 fallback，仍无法确认目标，当前更适合由用户接管"
        return {"mode": "handoff", "reason": reason}

    @staticmethod
    def _is_internal_perception_handoff(decision: dict) -> bool:
        text = " ".join(
            part
            for part in (
                decision.get("observation", ""),
                decision.get("thinking", ""),
                str(decision.get("action", {}).get("reason", "") or ""),
            )
            if part
        )
        keywords = (
            "截图", "重新截图", "图像", "画面", "过小", "太小", "模糊",
            "看不清", "看不见", "无法识别", "无法确认", "低置信度",
            "窗口尺寸", "界面过小",
        )
        return any(keyword in text for keyword in keywords)

    def _build_search_fallback_decision(
        self,
        plan: dict,
        plan_steps: list[dict],
        current_plan_step: int,
        decision: dict,
        plan_hint: str,
        fallback_used_steps: set[int],
    ) -> Optional[dict]:
        step_key = current_plan_step if current_plan_step < len(plan_steps) else -1
        if step_key in fallback_used_steps:
            return None

        fallback_text = str(plan.get("fallback_path", "") or "")
        search_index = None
        for index in range(current_plan_step, len(plan_steps)):
            if plan_steps[index].get("type") == "search_open":
                search_index = index
                break

        if search_index is None and "搜索" not in fallback_text:
            return None

        guarded = dict(decision)
        guarded["thinking"] = (
            f"{decision.get('thinking', '')} 当前感知不稳定，先消费 planner 的搜索 fallback。"
        ).strip()
        guarded["action"] = {
            "type": "hotkey",
            "keys": ["command", "k"],
            "reason": "入口识别不清，优先转搜索链路而不是直接失败",
            "_planner_fallback_consumed": True,
            "_planner_fallback_step_key": step_key,
            "_fallback_source": "execution",
        }
        guarded["target_description"] = "全局搜索"
        guarded["confidence"] = "medium"

        resolved = {
            "decision": guarded,
            "plan_hint": plan_hint,
            "current_plan_step": None,
        }
        if search_index is not None:
            resolved["current_plan_step"] = search_index
        return resolved

    def _quick_verify_lite(
        self,
        action: dict,
        plan_step: Optional[dict],
        task_goal: str,
        current_page: str,
        target_description: str,
        post_action_perception: Optional[FusedPerception] = None,
        update_counters: bool = True,
    ) -> dict:
        """动作后快速验证。
        这里做的是轻量状态迁移检查，不做阻塞式重型闭环。
        """
        result: dict = {
            "step_completed": False,
            "details": [],
            "status": "unknown",
            "transition": None,
            "next_step_hint": "",
        }

        try:
            success_signal = ""
            if plan_step:
                success_signal = plan_step.get("success_signal", "") or plan_step.get(
                    "description", ""
                )
            success_signal = success_signal or task_goal

            transition_verify = self.transition_verifier.verify(
                TransitionContext(
                    current_page=current_page,
                    action=action,
                    expected_signal=success_signal,
                    task_goal=task_goal,
                    target_description=target_description,
                    plan_step_type=plan_step.get("type", "") if plan_step else "",
                    previous_hint=self.last_verify_result.get("next_step_hint", ""),
                    previous_template=self.last_verify_result.get("template", ""),
                    consecutive_waits=self.consecutive_wait_hints,
                    retry_count=self.retry_hint_count,
                ),
                after_perception=post_action_perception,
            )
            result.update(
                {
                    "status": transition_verify.get("status", "unknown"),
                    "transition": transition_verify.get("transition"),
                    "next_step_hint": transition_verify.get("next_step_hint", ""),
                    "post_action_state": transition_verify.get(
                        "after_state", "unknown"
                    ),
                    "progress_made": transition_verify.get("progress_made", False),
                    "template": transition_verify.get("template", "generic"),
                    "target_name": transition_verify.get("target_name", ""),
                    "verification_level": transition_verify.get("verification_level", "light"),
                    "needs_heavy_observation": transition_verify.get("needs_heavy_observation", False),
                }
            )
            result["step_completed"] = transition_verify.get("step_completed", False)
            self._apply_hint_flags(result)
            if transition_verify.get("details"):
                result["details"].extend(transition_verify["details"])
        except Exception as exc:
            result["details"].append(f"AX 验证异常: {exc}")

        if update_counters:
            self._update_transition_counters(result)
        return result

    def _build_click_preflight_result(
        self,
        action: dict,
        target_description: str,
        original_action: Optional[dict] = None,
    ) -> Optional[dict]:
        if action.get("type") not in {"click", "double_click", "right_click"}:
            return None
        if any((action.get("ax_ref"), action.get("ax_coordinate"), action.get("coordinate"))):
            return None

        failure_reason = "click 缺少可执行坐标，已跳过无效点击"
        failure_source = "unknown"
        coordinate_source = str(action.get("coordinate_source", "") or "")
        original_coordinate = bool((original_action or {}).get("coordinate"))

        if action.get("_retry_coordinate_reset"):
            failure_source = "retry_coordinate_cleared"
            failure_reason = "retry 时已主动移除旧坐标，但本轮未重新定位成功"
        elif not original_coordinate:
            if coordinate_source == "missing_target":
                failure_source = "model_missing_coordinate_and_target"
                failure_reason = "模型未提供坐标，且缺少可定位的目标描述"
            elif coordinate_source == "unresolved_target":
                failure_source = "model_missing_coordinate_enhancer_unresolved"
                failure_reason = "模型未提供坐标，enhancer 也未能补出坐标"
            else:
                failure_source = "model_missing_coordinate"
                failure_reason = "模型未提供坐标，当前点击无法执行"
        else:
            failure_source = coordinate_source or "enhancer_unresolved"
            failure_reason = "模型原始坐标不可复用，enhancer 也未能补出新坐标"

        result = {
            "step_completed": False,
            "status": "failed",
            "transition": False,
            "next_step_hint": "retry" if target_description else "reobserve",
            "post_action_state": "click_not_executable",
            "progress_made": False,
            "template": "click_preflight",
            "target_name": target_description,
            "details": [
                failure_reason
            ],
            "failure_source": failure_source,
            "coordinate_source": coordinate_source,
            "exec_skipped": True,
        }
        self._apply_hint_flags(result)
        self._update_transition_counters(result)
        return result

    @staticmethod
    def _needs_heavy_post_action_confirmation(verify_result: dict) -> bool:
        return bool(verify_result.get("needs_heavy_observation", False))

    def _stabilize_aggressive_retry(self, decision: dict) -> dict:
        action = decision.get("action", {})
        next_step_hint = self.last_verify_result.get("next_step_hint", "")
        if not next_step_hint:
            return decision

        if next_step_hint == "wait" and action.get("type") in {
            "click", "double_click", "right_click", "hotkey", "scroll"
        }:
            guarded = dict(decision)
            guarded["thinking"] = (
                f"{decision.get('thinking', '')} 上一步验证要求 wait，先短暂等待，不直接升级动作。"
            ).strip()
            guarded["action"] = {
                "type": "wait",
                "seconds": 0.8,
                "reason": "上一步验证要求先 wait",
            }
            return guarded

        if (
            next_step_hint == "retry"
            and self.last_decided_action
            and not self._actions_equivalent(action, self.last_decided_action)
        ):
            guarded = dict(decision)
            guarded["thinking"] = (
                f"{decision.get('thinking', '')} 当前应先 retry same action，不直接升级为激进重试。"
            ).strip()
            guarded["action"] = self._build_retry_action()
            if self.last_target_description:
                guarded["target_description"] = self.last_target_description
            return guarded

        return decision

    def _build_retry_action(self) -> dict:
        retry_action = dict(self.last_decided_action)
        if (
            retry_action.get("type") in {"click", "double_click", "right_click"}
            and self.last_target_description
        ):
            retry_action.pop("coordinate", None)
            retry_action["_retry_coordinate_reset"] = True
        retry_action["reason"] = "上一轮验证建议 retry same action"
        return retry_action

    @staticmethod
    def _actions_equivalent(current_action: dict, previous_action: dict) -> bool:
        comparable_fields = ("type", "coordinate", "text", "keys", "direction", "amount")
        for field in comparable_fields:
            if current_action.get(field) != previous_action.get(field):
                return False
        return True

    def _update_transition_counters(self, verify_result: dict) -> None:
        hint = verify_result.get("next_step_hint", "")
        template = verify_result.get("template", "")
        previous_template = self.last_verify_result.get("template", "")

        if hint == "wait" and template and template == previous_template:
            self.consecutive_wait_hints += 1
        elif hint == "wait":
            self.consecutive_wait_hints = 1
        else:
            self.consecutive_wait_hints = 0

        if hint == "retry" and template and template == previous_template:
            self.retry_hint_count += 1
        elif hint == "retry":
            self.retry_hint_count = 1
        else:
            self.retry_hint_count = 0

    @staticmethod
    def _apply_hint_flags(verify_result: dict) -> None:
        hint = verify_result.get("next_step_hint", "")
        verify_result["pending_confirmation"] = hint in {"wait", "reobserve"}
        verify_result["block_aggressive_retry"] = hint in {"wait", "reobserve"}

    @staticmethod
    def _format_verification_feedback(verify_result: dict) -> str:
        parts = []
        if verify_result.get("template"):
            parts.append(f"模板={verify_result['template']}")
        if verify_result.get("status"):
            parts.append(f"验证状态={verify_result['status']}")
        if verify_result.get("transition") is not None:
            parts.append(f"发生预期迁移={verify_result['transition']}")
        if verify_result.get("next_step_hint"):
            parts.append(f"建议下一步={verify_result['next_step_hint']}")
        if verify_result.get("post_action_state"):
            parts.append(f"状态={verify_result['post_action_state']}")
        if verify_result.get("target_name"):
            parts.append(f"目标={verify_result['target_name']}")
        if verify_result.get("step_completed"):
            parts.append("步骤已完成")
        elif verify_result.get("pending_confirmation"):
            parts.append("仍在等待单击结果确认")
        if verify_result.get("block_aggressive_retry"):
            parts.append("暂不允许升级为 double_click/right_click")
        details = verify_result.get("details", [])
        if details:
            parts.append("；".join(details[:3]))
        return "\n".join(parts)

    def _final_verify(self, goal: str) -> dict:
        time.sleep(0.5)
        final_perception = self.perception.perceive(
            with_som=False, with_ax=True
        )
        vision_check = self.vision.verify_visual(
            final_perception.screenshot_b64,
            f"请确认以下任务目标是否已经完成：{goal}",
        )
        ax_signals = []
        for element in self.perception.ax.find_elements(role="AXTextArea"):
            if element.focused and (
                not element.value or not element.value.strip()
            ):
                ax_signals.append("焦点消息输入框为空")
        return {
            "success": vision_check.get("answer", False),
            "vision_evidence": vision_check.get("evidence", ""),
            "ax_signals": ax_signals,
            "method": "vision+ax",
        }

    @staticmethod
    def _save_trace(result: TaskResult, run_dir: str) -> None:
        payload = {
            "task": result.task,
            "goal": result.goal,
            "success": result.success,
            "total_duration": result.total_duration,
            "vision_calls": result.vision_calls,
            "total_tokens": result.total_tokens,
            "handoff_required": result.handoff_required,
            "handoff_reason": result.handoff_reason,
            "plan": result.plan,
            "error": result.error,
            "steps": [
                {
                    "step_num": s.step_num,
                    "plan_hint": s.plan_step_description,
                    "observation": s.observation,
                    "thinking": s.thinking,
                    "action_decided": s.action_decided,
                    "action_executed": {
                        k: v
                        for k, v in s.action_executed.items()
                        if k != "ax_ref"  # ax_ref 不可序列化
                    },
                    "verification": s.verification,
                    "screenshot": os.path.basename(s.screenshot_path),
                    "duration": s.duration,
                }
                for s in result.steps
            ],
        }
        with open(
            os.path.join(run_dir, "trace.json"), "w", encoding="utf-8"
        ) as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
