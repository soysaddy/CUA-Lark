from dataclasses import dataclass
from enum import Enum, auto
from typing import List

from loguru import logger


class GuardrailSignal(Enum):
    CONTINUE = auto()
    RECOVER = auto()
    REPLAN = auto()
    ABORT = auto()


@dataclass
class GuardrailResult:
    signal: GuardrailSignal
    detail: str = ""


class Guardrail:
    RECOVER_STATES = {
        "window_unfocused",
        "window_abnormal",
        "dialog_blocking",
        "covered",
        "blank",
    }

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.consecutive_low_confidence = 0
        self.consecutive_no_progress = 0
        self.pages_visited: list[str] = []
        self.action_history: list[str] = []

    def check(self, step_num: int, decision: dict, verify_result: dict, history: List) -> GuardrailResult:
        if verify_result.get("task_completed", False):
            return GuardrailResult(GuardrailSignal.CONTINUE, "")

        confidence = decision.get("confidence", "medium")
        action = decision.get("action", {})
        current_page = decision.get("current_page", "unknown")
        post_action_state = verify_result.get("post_action_state", "unknown")

        if confidence == "low":
            self.consecutive_low_confidence += 1
        else:
            self.consecutive_low_confidence = 0
        if self.consecutive_low_confidence >= 3:
            return GuardrailResult(GuardrailSignal.REPLAN, "连续多步低置信度")

        if step_num > 30:
            return GuardrailResult(GuardrailSignal.ABORT, "总步数超出上限")
        if step_num > 20:
            return GuardrailResult(GuardrailSignal.REPLAN, "总步数偏高，需要重规划")

        if self._should_count_no_progress(verify_result):
            self.consecutive_no_progress += 1
        else:
            self.consecutive_no_progress = 0
        if self.consecutive_no_progress >= 3:
            logger.warning("最近 3 步均未推进")
            return GuardrailResult(GuardrailSignal.REPLAN, "连续 3 步未推进计划")

        action_sig = self._action_signature(
            action,
            decision.get("target_description", ""),
        )
        self.action_history.append(action_sig)
        self.action_history = self.action_history[-10:]
        if len(self.action_history) >= 5:
            from collections import Counter

            count = Counter(self.action_history[-5:]).most_common(1)[0][1]
            if count >= 3:
                return GuardrailResult(GuardrailSignal.REPLAN, "最近5步内重复动作过多")

        self.pages_visited.append(str(current_page or "unknown"))
        self.pages_visited = self.pages_visited[-5:]
        if len(self.pages_visited) >= 3 and all(
            page in {"other", "unknown", ""} for page in self.pages_visited[-3:]
        ):
            return GuardrailResult(GuardrailSignal.RECOVER, "连续停留在未知页面")

        if post_action_state in self.RECOVER_STATES:
            return GuardrailResult(GuardrailSignal.RECOVER, f"检测到异常状态: {post_action_state}")

        if history and verify_result and not verify_result.get("step_completed", False):
            recent_failed = history[-3:]
            if len(recent_failed) == 3 and self._is_retry_loop(recent_failed):
                logger.warning("最近 3 步形成 retry 死循环")
                return GuardrailResult(GuardrailSignal.REPLAN, "连续 retry 未推进，疑似陷入死循环")

        return GuardrailResult(GuardrailSignal.CONTINUE, "")

    @staticmethod
    def _should_count_no_progress(verify_result: dict) -> bool:
        return (
            not verify_result.get("step_completed", False)
            and not verify_result.get("progress_made", False)
            and verify_result.get("next_step_hint", "") not in {"wait", "reobserve"}
        )

    @staticmethod
    def _action_signature(action: dict, target_description: str = "") -> str:
        return "|".join(
            [
                str(action.get("type", "")),
                str(action.get("coordinate")),
                str(action.get("text", "")),
                str(action.get("keys", [])),
                str(action.get("direction", "")),
                str(action.get("amount", "")),
                str(target_description or ""),
            ]
        )

    def _is_retry_loop(self, recent_steps: List) -> bool:
        retry_steps = [
            step for step in recent_steps
            if step.verification.get("next_step_hint", "") == "retry"
        ]
        if len(retry_steps) < 3:
            return False
        if any(
            step.verification.get("step_completed", False)
            or step.verification.get("progress_made", False)
            for step in retry_steps
        ):
            return False

        states = [
            step.verification.get("post_action_state", "unknown")
            for step in retry_steps
        ]
        action_sigs = [
            self._action_signature(
                step.action_executed,
                step.verification.get("target_name", ""),
            )
            for step in retry_steps
        ]
        return len(set(states)) <= 1 and len(set(action_sigs)) <= 1
