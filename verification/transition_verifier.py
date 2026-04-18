from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from agent.perception_fusion import FusedPerception
from agent.state_schema import (
    normalize_expected_transition,
    normalize_page_id,
    page_satisfies_target,
    target_page_from_transition,
)


@dataclass
class TransitionContext:
    task_goal: str
    plan: dict[str, Any]
    action: dict[str, Any]
    decision: dict[str, Any]
    before_perception: FusedPerception
    after_perception: Optional[FusedPerception]
    exec_success: bool


@dataclass
class TransitionVerification:
    state: str = "unknown"
    transition: str = "unknown"
    step_completed: bool = False
    task_completed: bool = False
    confidence: str = "low"
    evidence: list[str] = field(default_factory=list)
    next_step_hint: str = "reobserve"
    source: str = "visual_primary"

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "status": self.confidence_to_status(),
                "details": list(self.evidence),
                "post_action_state": self.state,
                "progress_made": self.transition in {"partial", "completed"},
                "template": "visual_primary",
                "verification_level": "visual",
            }
        )
        return payload

    def confidence_to_status(self) -> str:
        if self.task_completed or self.step_completed:
            return "confirmed" if self.confidence == "high" else "inferred"
        if self.confidence in {"high", "medium"}:
            return "inferred"
        return "unknown"


class TransitionVerifier:
    def __init__(self, vision: Any) -> None:
        self.vision = vision

    @property
    def stats(self) -> dict[str, Any]:
        return self.vision.stats

    def verify(self, context: TransitionContext) -> dict[str, Any]:
        expected_transition = normalize_expected_transition(
            context.plan.get("expected_transition"),
            fallback_text=context.task_goal,
        )
        target_page = target_page_from_transition(expected_transition)
        target_name = (
            expected_transition.get("target_name")
            or self._extract_target_name(context.task_goal)
        )
        before_state = normalize_page_id(
            context.decision.get("current_page", "unknown")
        )
        after_b64 = (
            context.after_perception.screenshot_b64
            if context.after_perception and context.after_perception.screenshot_b64
            else context.before_perception.screenshot_b64
        )
        auxiliary_evidence = self._build_auxiliary_evidence(context, expected_transition)
        visual = self.vision.verify_transition(
            task_goal=context.task_goal,
            expected_transition=expected_transition,
            action=context.action,
            before_b64=context.before_perception.screenshot_b64,
            after_b64=after_b64,
            auxiliary_evidence=auxiliary_evidence,
        )
        verification = self._from_visual_result(
            visual=visual,
            context=context,
            before_state=before_state,
            target_page=target_page,
            target_name=target_name,
        )
        return verification.as_dict()

    def _from_visual_result(
        self,
        visual: dict[str, Any],
        context: TransitionContext,
        before_state: str,
        target_page: str,
        target_name: str,
    ) -> TransitionVerification:
        state = normalize_page_id(visual.get("state", "unknown"))
        transition = str(visual.get("transition", "unknown") or "unknown")
        confidence = str(visual.get("confidence", "low") or "low")
        evidence = self._normalize_evidence(visual.get("evidence"))
        step_completed = bool(visual.get("step_completed", False))
        task_completed = bool(visual.get("task_completed", False))
        next_step_hint = str(visual.get("next_step_hint", "reobserve") or "reobserve")

        if state == "unknown" and context.after_perception:
            page_info = self.vision.identify_page(context.after_perception.screenshot_b64)
            guessed_state = normalize_page_id(page_info.get("page", "unknown"))
            if guessed_state != "unknown" and confidence == "low":
                state = guessed_state
                evidence.append(
                    f"页面识别辅助判断为 {guessed_state}: {page_info.get('details', '')}".strip()
                )

        if not context.exec_success:
            evidence.append("动作未成功执行")
            if next_step_hint == "none":
                next_step_hint = "retry"
            transition = "none" if transition == "unknown" else transition

        if state != "unknown" and target_page != "unknown" and page_satisfies_target(state, target_page):
            step_completed = True
            if self._is_page_open_goal(context.task_goal, target_page):
                task_completed = True
            if transition == "unknown":
                transition = "completed"
            if confidence == "low":
                confidence = "medium"

        if context.action.get("type") == "done":
            task_completed = True
            step_completed = True
            transition = "completed"
            if state == "unknown":
                state = before_state
            if confidence == "low":
                confidence = "medium"
            next_step_hint = "none"

        if task_completed:
            next_step_hint = "none"
        elif step_completed and next_step_hint == "none":
            next_step_hint = "reobserve"
        elif next_step_hint == "none":
            next_step_hint = self._default_hint(transition, context.exec_success, state, before_state)

        if target_name and not any(target_name in item for item in evidence):
            evidence.append(f"目标对象: {target_name}")

        return TransitionVerification(
            state=state,
            transition=transition,
            step_completed=step_completed,
            task_completed=task_completed,
            confidence=confidence,
            evidence=evidence or ["视觉验证未给出明确证据"],
            next_step_hint=next_step_hint,
        )

    @staticmethod
    def _normalize_evidence(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value:
            return [str(value).strip()]
        return []

    @staticmethod
    def _default_hint(
        transition: str,
        exec_success: bool,
        state: str,
        before_state: str,
    ) -> str:
        if not exec_success:
            return "retry"
        if transition == "partial":
            return "wait"
        if transition == "completed":
            return "reobserve"
        if state != "unknown" and state != before_state:
            return "reobserve"
        return "retry"

    @staticmethod
    def _build_auxiliary_evidence(
        context: TransitionContext,
        expected_transition: dict[str, str],
    ) -> str:
        parts = []
        if context.after_perception and context.after_perception.ax_summary:
            parts.append(f"after_ax={context.after_perception.ax_summary}")
        elif context.before_perception.ax_summary:
            parts.append(f"before_ax={context.before_perception.ax_summary}")
        if expected_transition:
            parts.append(f"expected={expected_transition}")
        return "\n".join(parts)

    @staticmethod
    def _is_page_open_goal(task_goal: str, target_page: str) -> bool:
        text = str(task_goal or "")
        if target_page == "unknown":
            return False
        if any(keyword in text for keyword in ("发送", "输入", "填写", "回复")):
            return False
        return any(keyword in text for keyword in ("打开", "查看", "进入", "切到", "切换到"))

    @staticmethod
    def _extract_target_name(text: str) -> str:
        source = str(text or "").strip()
        if not source:
            return ""
        for quote in ("「", "“", "\""):
            if quote in source:
                break
        import re

        quote_match = re.search(r"[「“\"]([^」”\"]{1,40})[」”\"]", source)
        if quote_match:
            return quote_match.group(1).strip()
        return ""
