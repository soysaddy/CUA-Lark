import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from loguru import logger

from agent.decision_engine import VisionDecisionEngine
from agent.guardrail import Guardrail, GuardrailResult, GuardrailSignal
from agent.perception_fusion import FusedPerception, PerceptionFusion
from agent.planner import VisionPlanner
from config import config
from execution.action_executor import ActionExecutor
from execution.recovery import RecoveryManager, RecoveryStatus
from perception.ax_enhancer import AXEnhancer
from perception.vision_client import VisionClient
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


class VisionDecisionLoop:
    def __init__(self, save_dir: str = "./runs") -> None:
        self.planner = VisionPlanner()
        self.perception = PerceptionFusion()
        self.decision_engine = VisionDecisionEngine()
        self.vision = VisionClient()
        self.verifier = TransitionVerifier(vision=self.vision)
        self.ax_enhancer = AXEnhancer()
        self.executor = ActionExecutor()
        self.guardrail = Guardrail()
        self.save_dir = save_dir
        self.next_capture_cache: Optional[tuple[dict, dict]] = None
        self.last_verification: dict = {}

    def run(self, user_input: str) -> TaskResult:
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        run_dir = os.path.join(self.save_dir, run_id)
        os.makedirs(run_dir, exist_ok=True)

        result = TaskResult(task=user_input, goal=user_input, success=False)
        started_at = time.time()

        self.guardrail.reset()
        self.decision_engine.reset()
        self.next_capture_cache = None
        self.last_verification = {}

        decision_calls_before = self.decision_engine.stats["calls"]
        decision_tokens_before = self.decision_engine.stats["total_tokens"]
        verify_calls_before = self.verifier.stats["calls"]
        verify_tokens_before = self.verifier.stats["total_tokens"]

        initial_perception = self.perception.observe_light()
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
            f"计划: {result.goal} | preferred={plan.get('preferred_path', '') or '-'} | "
            f"fallback={plan.get('fallback_path', '') or '-'} | 信心: {plan.get('confidence', '?')}"
        )

        for step_num in range(1, config.max_total_steps + 1):
            before_perception = self._observe_for_decision()
            screenshot_path = os.path.join(run_dir, f"step_{step_num:02d}.png")
            before_perception.screenshot.save(screenshot_path)
            if before_perception.annotated_screenshot:
                before_perception.annotated_screenshot.save(
                    os.path.join(run_dir, f"step_{step_num:02d}_som.png")
                )

            decision = self.decision_engine.decide(
                task_goal=result.goal,
                plan=plan,
                perception=before_perception,
                step_num=step_num,
                last_verification=self.last_verification,
            )
            action = decision.get("action", {}) or {}
            action_type = action.get("type", "")

            logger.info(
                f"Step {step_num}: {action_type} | "
                f"{decision.get('observation', '')[:80]} | "
                f"confidence={decision.get('confidence', '?')}"
            )

            if action_type == "done":
                verify_result = {
                    "state": decision.get("current_page", "unknown"),
                    "transition": "completed",
                    "step_completed": True,
                    "task_completed": True,
                    "confidence": decision.get("confidence", "high"),
                    "evidence": [decision.get("observation", "视觉判断目标已达成")],
                    "next_step_hint": "none",
                    "status": "confirmed",
                    "details": [decision.get("observation", "视觉判断目标已达成")],
                    "post_action_state": decision.get("current_page", "unknown"),
                    "progress_made": True,
                    "template": "visual_done",
                    "verification_level": "visual",
                    "perception_diag": self._build_perception_diag(before_perception),
                    "exec_success": False,
                }
                result.steps.append(
                    StepRecord(
                        step_num=step_num,
                        plan_step_description=self._plan_summary(plan),
                        observation=decision.get("observation", ""),
                        thinking=decision.get("thinking", ""),
                        action_decided=action,
                        action_executed={"type": "done", "reason": action.get("reason", "")},
                        verification=verify_result,
                        screenshot_path=screenshot_path,
                        duration=0.0,
                    )
                )
                self.last_verification = verify_result
                result.success = True
                break

            if action_type in {"pause_for_user", "handoff"}:
                reason = action.get("reason", "需要用户接管当前步骤")
                result.handoff_required = True
                result.handoff_reason = reason
                result.error = reason
                result.steps.append(
                    StepRecord(
                        step_num=step_num,
                        plan_step_description=self._plan_summary(plan),
                        observation=decision.get("observation", ""),
                        thinking=decision.get("thinking", ""),
                        action_decided=action,
                        action_executed={"type": "pause_for_user"},
                        verification={
                            "task_completed": False,
                            "step_completed": False,
                            "status": "failed",
                            "details": [reason],
                            "perception_diag": self._build_perception_diag(before_perception),
                        },
                        screenshot_path=screenshot_path,
                        duration=0.0,
                    )
                )
                break

            if action_type == "fail":
                result.error = action.get("reason", "Agent 主动报告失败")
                result.steps.append(
                    StepRecord(
                        step_num=step_num,
                        plan_step_description=self._plan_summary(plan),
                        observation=decision.get("observation", ""),
                        thinking=decision.get("thinking", ""),
                        action_decided=action,
                        action_executed={"type": "fail"},
                        verification={
                            "task_completed": False,
                            "step_completed": False,
                            "status": "failed",
                            "details": [result.error],
                            "perception_diag": self._build_perception_diag(before_perception),
                        },
                        screenshot_path=screenshot_path,
                        duration=0.0,
                    )
                )
                break

            enhanced_action = self.ax_enhancer.enhance(
                vision_action=action,
                target_description=decision.get("target_description", ""),
                perception=before_perception,
            )
            preflight = self._preflight_action(
                original_action=action,
                enhanced_action=enhanced_action,
                target_description=decision.get("target_description", ""),
                perception=before_perception,
            )

            step_started = time.time()
            exec_success = False
            after_perception: Optional[FusedPerception] = None

            if preflight is None:
                exec_success = self.executor.execute(
                    enhanced_action,
                    before_perception.coord_system,
                )
                time.sleep(config.action_interval)
                after_perception = self._observe_after_action()
                verify_result = self.verifier.verify(
                    TransitionContext(
                        task_goal=result.goal,
                        plan=plan,
                        action=enhanced_action,
                        decision=decision,
                        before_perception=before_perception,
                        after_perception=after_perception,
                        exec_success=exec_success,
                    )
                )
            else:
                verify_result = preflight

            verify_result["perception_diag"] = self._build_perception_diag(
                after_perception or before_perception
            )
            verify_result["exec_success"] = exec_success

            record = StepRecord(
                step_num=step_num,
                plan_step_description=self._plan_summary(plan),
                observation=decision.get("observation", ""),
                thinking=decision.get("thinking", ""),
                action_decided=action,
                action_executed=enhanced_action,
                verification=verify_result,
                screenshot_path=screenshot_path,
                duration=time.time() - step_started,
            )
            result.steps.append(record)
            self.last_verification = verify_result

            if verify_result.get("task_completed", False):
                result.success = True
                break

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
                        GuardrailSignal.REPLAN,
                        recovery_result.reason,
                    )
                else:
                    continue

            if guardrail_result.signal == GuardrailSignal.REPLAN:
                current_capture = after_perception or before_perception
                new_plan = self.planner.replan(
                    original_plan=plan,
                    current_step=step_num,
                    current_screenshot_b64=current_capture.screenshot_b64,
                    issue=guardrail_result.detail or "执行偏离预期",
                )
                if new_plan.get("feasible", False):
                    plan = new_plan
                    result.plan = new_plan
                    logger.info("重规划成功: 已更新高层 preferred/fallback/transition")
                    continue
                result.error = "重规划失败"
                break
        else:
            result.error = f"超过最大步数 {config.max_total_steps}"

        result.total_duration = time.time() - started_at
        result.vision_calls = (
            self.decision_engine.stats["calls"] - decision_calls_before
            + self.verifier.stats["calls"] - verify_calls_before
        )
        result.total_tokens = (
            self.decision_engine.stats["total_tokens"] - decision_tokens_before
            + self.verifier.stats["total_tokens"] - verify_tokens_before
        )
        self._save_trace(result, run_dir)
        status = "✅" if result.success else "❌"
        logger.info(
            f"{status} 任务结束: {len(result.steps)}步 "
            f"{result.total_duration:.1f}s {result.error}"
        )
        return result

    def _observe_for_decision(self) -> FusedPerception:
        if self.next_capture_cache:
            screen_data, bounds = self.next_capture_cache
            self.next_capture_cache = None
            return self.perception.perceive_from_capture(
                screen_data=screen_data,
                bounds=bounds,
            with_som=True,
            with_ax=True,
        )
        return self.perception.observe_annotated()

    def _observe_after_action(self) -> Optional[FusedPerception]:
        screen_data, bounds = self.perception.capture_screen()
        if not screen_data or not bounds:
            return None
        self.next_capture_cache = (screen_data, bounds)
        return self.perception.perceive_from_capture(
            screen_data=screen_data,
            bounds=bounds,
            with_som=False,
            with_ax=True,
        )

    @staticmethod
    def _plan_summary(plan: dict) -> str:
        payload = {
            "goal": plan.get("goal", ""),
            "preferred_path": plan.get("preferred_path", ""),
            "fallback_path": plan.get("fallback_path", ""),
            "expected_transition": plan.get("expected_transition", {}),
        }
        return json.dumps(payload, ensure_ascii=False)

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
        too_small = screenshot_size[0] < 900 or screenshot_size[1] < 600
        return {
            "screenshot_size": screenshot_size,
            "raw_size": raw_size,
            "resized_size": resized_size,
            "window_bounds": window_bounds,
            "capture_source": getattr(perception, "capture_source", "unknown"),
            "capture_duration_ms": getattr(perception, "capture_duration_ms", 0.0),
            "perception_duration_ms": getattr(perception, "perception_duration_ms", 0.0),
            "size_assessment": "too_small" if too_small else "size_ok",
            "ax_enabled": getattr(perception, "ax_enabled", False),
            "som_enabled": getattr(perception, "som_enabled", False),
        }

    @staticmethod
    def _preflight_action(
        original_action: dict,
        enhanced_action: dict,
        target_description: str,
        perception: FusedPerception,
    ) -> Optional[dict]:
        if enhanced_action.get("type") not in {"click", "double_click", "right_click"}:
            return None
        if any(
            key in enhanced_action for key in ("coordinate", "ax_coordinate", "ax_ref")
        ):
            return None

        coordinate_source = enhanced_action.get("coordinate_source", "unknown")
        if original_action.get("_retry_coordinate_reset"):
            failure_source = "retry_coordinate_cleared"
            details = ["retry 时已主动移除旧坐标，但本轮未重新定位成功"]
        elif not original_action.get("coordinate") and not target_description:
            failure_source = "model_missing_coordinate_and_target"
            details = ["模型未提供坐标，也没有可用于重新定位的目标描述"]
        elif not original_action.get("coordinate"):
            failure_source = "model_missing_coordinate_enhancer_unresolved"
            details = ["模型未提供坐标，增强定位也未能找到可执行目标"]
        else:
            failure_source = "unresolved_click_coordinate"
            details = ["当前 click 动作缺少可执行坐标"]

        return {
            "state": "unknown",
            "transition": "none",
            "step_completed": False,
            "task_completed": False,
            "confidence": "low",
            "evidence": details,
            "next_step_hint": "retry",
            "status": "failed",
            "details": details,
            "post_action_state": "click_not_executable",
            "progress_made": False,
            "template": "click_preflight",
            "verification_level": "rule_guard",
            "failure_source": failure_source,
            "coordinate_source": coordinate_source,
            "target_name": target_description,
            "exec_skipped": True,
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
                        key: value
                        for key, value in s.action_executed.items()
                        if key != "ax_ref"
                    },
                    "verification": s.verification,
                    "screenshot": os.path.basename(s.screenshot_path),
                    "duration": s.duration,
                }
                for s in result.steps
            ],
        }
        with open(os.path.join(run_dir, "trace.json"), "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
