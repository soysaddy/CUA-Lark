import json
from typing import Optional

from loguru import logger

from agent.perception_fusion import FusedPerception
from agent.state_schema import normalize_page_id
from config import config
from knowledge.lark_capabilities import (
    LARK_CAPABILITIES,
    LARK_COMMON_PATTERNS,
    LARK_PAGE_SIGNATURES,
)
from utils.cost_tracker import CostTracker
from utils.openai_client import create_openai_client


DECISION_PROMPT = f"""你是 CUA-Lark 的视觉决策核心。
你以当前截图作为第一事实来源，planner 只提供高层目标和候选路径，不能替代你对当前界面的判断。
你必须返回合法的 json，不要输出额外文字。

## 工作方式
你围绕 observe -> think -> act 工作：
1. observe: 看清当前飞书界面与状态
2. think: 判断当前是否已经满足任务目标；若没有，再决定下一步最合理动作
3. act: 只输出一个下一步动作

## 页面知识
{LARK_PAGE_SIGNATURES}

## 能力边界
{LARK_CAPABILITIES}

## 常见候选路径
{LARK_COMMON_PATTERNS}

## 规则
1. 截图是主判断源，planner 只给 goal / preferred_path / fallback_path / expected_transition
2. 如果当前截图已经清楚满足任务目标，直接输出 done
3. 不要盲目重复上一动作；只有确实没有完成证据时才重试
4. 如果入口不可见或当前路径不稳，可以主动选择 planner 提供的 fallback_path
5. 遇到登录、验证码、系统权限、人工确认等必须人工处理的步骤，输出 pause_for_user
6. 输出 current_page 时只用: im_main|im_chat|calendar|docs|search|unknown
7. coordinate 是相对当前截图左上角的像素坐标

## 可用动作
- click
- double_click
- right_click
- type
- hotkey
- scroll
- wait
- done
- fail
- pause_for_user

## 输出 json
{{
  "observation": "你对当前界面的客观观察",
  "current_page": "im_main|im_chat|calendar|docs|search|unknown",
  "thinking": "为什么认为当前状态尚未完成，或者为什么选择该动作",
  "action": {{
    "type": "click|double_click|right_click|type|hotkey|scroll|wait|done|fail|pause_for_user",
    "coordinate": [x, y],
    "text": "",
    "keys": [],
    "direction": "up|down",
    "amount": 3,
    "seconds": 1.0,
    "reason": "动作理由"
  }},
  "target_description": "目标元素描述",
  "confidence": "high|medium|low",
  "progress_percent": 0
}}
"""


class VisionDecisionEngine:
    def __init__(self) -> None:
        self.client = create_openai_client()
        self.cost = CostTracker()
        self.history: list[dict] = []

    def reset(self) -> None:
        self.history = []

    def decide(
        self,
        task_goal: str,
        plan: dict,
        perception: FusedPerception,
        step_num: int,
        last_verification: Optional[dict] = None,
    ) -> dict:
        if not self.client:
            return self._fallback_decision(task_goal)

        try:
            width = perception.screenshot.width
            height = perception.screenshot.height
            plan_context = {
                "goal": plan.get("goal", task_goal),
                "preferred_path": plan.get("preferred_path", ""),
                "fallback_path": plan.get("fallback_path", ""),
                "expected_transition": plan.get("expected_transition", {}),
            }
            context_text = [
                f"任务: {task_goal}",
                f"当前 step: {step_num}",
                f"截图尺寸: {width}x{height}",
                "规划上下文:",
                json.dumps(plan_context, ensure_ascii=False),
            ]
            if last_verification:
                context_text.extend(
                    [
                        "上一步验证结果:",
                        json.dumps(last_verification, ensure_ascii=False),
                    ]
                )
            if perception.ax_summary:
                context_text.extend(["AX 辅助结构信息:", perception.ax_summary])
            if perception.som_description:
                context_text.extend(["可交互元素标注:", perception.som_description])

            messages = [{"role": "system", "content": DECISION_PROMPT}]
            if self.history:
                messages.extend(self.history[-6:])
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "\n".join(context_text)},
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
                        {"type": "text", "text": "请基于截图直接返回 json。"},
                    ],
                }
            )

            response = self.client.chat.completions.create(
                model=config.openai_model,
                messages=messages,
                max_tokens=900,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            payload = json.loads(response.choices[0].message.content)
            usage = getattr(response, "usage", None)
            self.cost.add_usage(getattr(usage, "total_tokens", 0) if usage else 0)
            payload["_tokens"] = getattr(usage, "total_tokens", 0) if usage else 0
            payload["current_page"] = normalize_page_id(payload.get("current_page", "unknown"))
            payload.setdefault("observation", "")
            payload.setdefault("thinking", "")
            payload.setdefault("target_description", "")
            payload.setdefault("confidence", "medium")
            payload.setdefault("progress_percent", 0)
            payload.setdefault("action", {"type": "wait", "seconds": 0.8, "reason": "缺少动作"})

            self.history.extend(
                [
                    {
                        "role": "user",
                        "content": json.dumps(
                            {
                                "task": task_goal,
                                "step": step_num,
                                "plan": plan_context,
                            },
                            ensure_ascii=False,
                        ),
                    },
                    {
                        "role": "assistant",
                        "content": json.dumps(
                            {
                                "observation": payload.get("observation", ""),
                                "current_page": payload.get("current_page", "unknown"),
                                "action": payload.get("action", {}),
                                "confidence": payload.get("confidence", "medium"),
                            },
                            ensure_ascii=False,
                        ),
                    },
                ]
            )
            return payload
        except Exception as exc:
            logger.warning(f"视觉决策失败，降级启发式: {exc}")
            return self._fallback_decision(task_goal)

    @property
    def stats(self) -> dict:
        return {
            "calls": self.cost.total_calls,
            "total_tokens": self.cost.total_tokens,
            "estimated_cost_usd": self.cost.estimated_cost_usd,
        }

    @staticmethod
    def _fallback_decision(task_goal: str) -> dict:
        return {
            "observation": "缺少可用视觉决策，无法继续可靠判断当前界面。",
            "current_page": "unknown",
            "thinking": f"当前无法可靠完成任务：{task_goal}",
            "action": {
                "type": "fail",
                "reason": "当前环境缺少可用的视觉决策能力",
            },
            "target_description": "",
            "confidence": "low",
            "progress_percent": 0,
            "_tokens": 0,
        }
