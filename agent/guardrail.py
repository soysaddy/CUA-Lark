import re
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
    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self.consecutive_same_observation = 0
        self.last_observation = ""
        self.consecutive_low_confidence = 0
        self.pages_visited: list[str] = []
        self.action_history: list[str] = []

    def check(self, step_num: int, decision: dict, verify_result: dict, history: List) -> GuardrailResult:
        observation = decision.get("observation", "")
        confidence = decision.get("confidence", "medium")
        action = decision.get("action", {})
        current_page = decision.get("current_page", "unknown")
        next_step_hint = verify_result.get("next_step_hint", "")
        observation_key = self._normalize_observation(observation)

        if self._is_similar(observation_key, self.last_observation):
            self.consecutive_same_observation += 1
        else:
            self.consecutive_same_observation = 1 if observation_key else 0
        self.last_observation = observation_key
        if self.consecutive_same_observation >= 4:
            return GuardrailResult(GuardrailSignal.REPLAN, "连续多步观察几乎未变化，疑似陷入循环")

        if confidence == "low":
            self.consecutive_low_confidence += 1
        else:
            self.consecutive_low_confidence = 0
        if self.consecutive_low_confidence >= 3:
            return GuardrailResult(GuardrailSignal.REPLAN, "连续多步低置信度")

        if not self._hint_suspends(next_step_hint, "repeat"):
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
                    return GuardrailResult(GuardrailSignal.RECOVER, "最近5步内重复动作过多")

        if step_num > 30:
            return GuardrailResult(GuardrailSignal.ABORT, "总步数超出上限")
        if step_num > 20:
            return GuardrailResult(GuardrailSignal.REPLAN, "总步数偏高，需要重规划")

        self.pages_visited.append(current_page)
        if len(self.pages_visited) >= 3 and all(page in {"other", "unknown", ""} for page in self.pages_visited[-3:]):
            return GuardrailResult(GuardrailSignal.RECOVER, "连续停留在未知页面")

        if history and verify_result and not verify_result.get("step_completed", False):
            recent_failed = history[-3:]
            if len(recent_failed) == 3 and self._is_retry_loop(recent_failed):
                logger.warning("最近 3 步形成 retry 死循环")
                return GuardrailResult(GuardrailSignal.REPLAN, "连续 retry 未推进，疑似陷入死循环")

            if len(recent_failed) == 3 and all(
                not step.verification.get("step_completed", False)
                and not self._hint_suspends(
                    step.verification.get("next_step_hint", ""),
                    "no_progress",
                )
                and not step.verification.get("progress_made", False)
                for step in recent_failed
            ):
                logger.warning("最近 3 步均未推进")
                return GuardrailResult(GuardrailSignal.REPLAN, "连续 3 步未推进计划")

        return GuardrailResult(GuardrailSignal.CONTINUE, "")

    @staticmethod
    def _is_similar(obs1: str, obs2: str) -> bool:
        if not obs1 or not obs2:
            return False
        set1, set2 = set(obs1.split()), set(obs2.split())
        if not set1 or not set2:
            return False
        if set1 == set2:
            return True
        common = set1 & set2
        overlap = len(common) / max(len(set1), len(set2))
        return overlap >= 0.6 and len(common) >= 4

    @staticmethod
    def _normalize_observation(observation: str) -> str:
        if not observation:
            return ""
        text = re.sub(r"[^\w\u4e00-\u9fff]+", " ", observation.lower())
        raw_tokens = re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9_]+", text)
        stopwords = {
            "当前", "界面", "页面", "飞书", "显示", "打开", "可见", "一个",
            "处于", "仍在", "右侧", "左侧", "顶部", "底部", "区域",
            "当前在", "右侧是", "左侧是",
        }
        normalized = []
        seen = set()
        for token in raw_tokens:
            expanded = [token]
            if re.fullmatch(r"[\u4e00-\u9fff]{4,}", token):
                expanded = [token[i:i + 2] for i in range(len(token) - 1)]
            for item in expanded:
                if item in stopwords:
                    continue
                if item not in seen:
                    seen.add(item)
                    normalized.append(item)
        return " ".join(normalized)

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

    @staticmethod
    def _hint_suspends(hint: str, kind: str) -> bool:
        if kind == "repeat":
            return hint in {"wait", "reobserve"}
        if kind == "no_progress":
            return hint in {"wait", "reobserve"}
        return False
