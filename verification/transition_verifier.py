import re
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class VerificationStatus(str, Enum):
    CONFIRMED = "confirmed"
    INFERRED = "inferred"
    UNKNOWN = "unknown"
    FAILED = "failed"


class NextStepHint(str, Enum):
    RETRY = "retry"
    WAIT = "wait"
    REOBSERVE = "reobserve"
    REPLAN = "replan"
    HANDOFF = "handoff"


@dataclass
class TransitionContext:
    current_page: str
    action: dict
    expected_signal: Any
    task_goal: str = ""
    target_description: str = ""
    plan_step_type: str = ""
    previous_hint: str = ""
    previous_template: str = ""
    consecutive_waits: int = 0
    retry_count: int = 0


@dataclass
class TransitionVerification:
    template: str = "generic"
    status: str = VerificationStatus.UNKNOWN.value
    transition: Optional[bool] = None
    next_step_hint: str = NextStepHint.REOBSERVE.value
    after_state: str = "unknown"
    details: list[str] = field(default_factory=list)
    progress_made: bool = False
    step_completed: bool = False
    target_name: str = ""
    needs_heavy_observation: bool = False
    verification_level: str = "light"

    def as_dict(self) -> dict:
        return asdict(self)


class TransitionVerifier:
    MAX_CONSECUTIVE_WAITS = 1

    def __init__(self, perception: Any, vision: Any) -> None:
        self.perception = perception
        self.vision = vision
        self.ax = perception.ax

    def verify(self, context: TransitionContext, after_perception: Optional[Any] = None) -> dict:
        template = self._select_template(context)
        verification: TransitionVerification
        if template == "list_item_to_detail":
            verification = self._verify_list_item_to_detail(context, after_perception)
        elif template == "search_result_open":
            verification = self._verify_search_result_open(context, after_perception)
        elif template == "navigation_to_page":
            verification = self._verify_navigation_to_page(context, after_perception)
        elif template == "dialog_visibility":
            verification = self._verify_dialog_visibility(context)
        elif template == "search_overlay":
            verification = self._verify_search_overlay(context)
        elif template == "input_edit":
            verification = self._verify_input_edit(context)
        else:
            verification = TransitionVerification(
                template="generic",
                status=VerificationStatus.UNKNOWN.value,
                transition=None,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state=context.current_page or "unknown",
                details=[
                    "未匹配到专用 verifier 模板",
                    f"action={context.action.get('type', '') or 'unknown'}",
                    f"plan_step_type={context.plan_step_type or 'unknown'}",
                    f"expected={context.expected_signal or context.task_goal or 'unknown'}",
                    f"current_page={context.current_page or 'unknown'}",
                    f"target={context.target_description or 'unknown'}",
                ],
            )
        return self._finalize_wait_decision(context, verification).as_dict()

    def _select_template(self, context: TransitionContext) -> str:
        action_type = context.action.get("type", "")
        expected_page = self._infer_expected_page(context.expected_signal or context.task_goal)
        keys = context.action.get("keys", [])
        signal = self._signal_text(context.expected_signal or context.task_goal)
        target = context.target_description or ""
        plan_step_type = context.plan_step_type or ""

        if action_type == "wait":
            if (
                context.previous_template in {
                    "list_item_to_detail",
                    "search_result_open",
                    "navigation_to_page",
                    "dialog_visibility",
                    "search_overlay",
                }
                and context.previous_hint in {"wait", "reobserve"}
                and context.consecutive_waits <= self.MAX_CONSECUTIVE_WAITS
            ):
                return context.previous_template
            return "generic"

        if plan_step_type == "module_navigation":
            return "navigation_to_page"
        if plan_step_type == "list_item_open":
            return "list_item_to_detail"
        if plan_step_type == "search_open":
            return "search_overlay"
        if plan_step_type == "search_select":
            return "search_result_open"
        if plan_step_type == "input":
            return "input_edit"

        if action_type == "hotkey" and keys == ["command", "k"]:
            return "search_overlay"
        if expected_page == "search":
            return "search_overlay"
        if action_type == "type":
            return "input_edit"
        if self._is_module_navigation_intent(context, signal):
            return "navigation_to_page"
        if self._is_search_select_intent(context, signal):
            return "search_result_open"
        if expected_page == "im_chat" and action_type == "click":
            return "list_item_to_detail"
        if expected_page in {"im_main", "calendar", "docs"} and action_type in {"click", "hotkey"}:
            return "navigation_to_page"
        if any(keyword in signal for keyword in ("弹窗", "对话框", "关闭")) or any(
            keyword in target for keyword in ("关闭", "弹窗", "对话框")
        ):
            return "dialog_visibility"
        return "generic"

    def _verify_list_item_to_detail(
        self,
        context: TransitionContext,
        after_perception: Optional[Any] = None,
    ) -> TransitionVerification:
        return self._verify_chat_open_transition(
            context,
            after_perception,
            template="list_item_to_detail",
            pending_source="消息列表",
        )

    def _verify_search_result_open(
        self,
        context: TransitionContext,
        after_perception: Optional[Any] = None,
    ) -> TransitionVerification:
        return self._verify_chat_open_transition(
            context,
            after_perception,
            template="search_result_open",
            pending_source="搜索结果",
        )

    def _verify_chat_open_transition(
        self,
        context: TransitionContext,
        after_perception: Optional[Any],
        template: str,
        pending_source: str,
    ) -> TransitionVerification:
        target_name = self._extract_target_name(
            context.expected_signal,
            context.target_description,
            context.task_goal,
        )
        light_result = self._lightweight_im_transition(context, target_name)
        if light_result:
            light_result.template = template
            if not after_perception or light_result.step_completed:
                return light_result

        if not after_perception:
            return TransitionVerification(
                template=template,
                status=VerificationStatus.UNKNOWN.value,
                transition=None,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="lightweight_uncertain",
                details=[f"轻量验证无法确认{pending_source}打开会话结果，需要重型确认"],
                target_name=target_name,
                needs_heavy_observation=True,
                verification_level="light",
            )

        after = after_perception
        if not after.screenshot_b64:
            return TransitionVerification(
                template=template,
                status=VerificationStatus.UNKNOWN.value,
                next_step_hint=NextStepHint.REOBSERVE.value,
                details=[f"{pending_source}点击后截图失败"],
                target_name=target_name,
                verification_level="heavy",
            )

        if template == "search_result_open":
            after_page = self.vision.identify_page(after.screenshot_b64).get("page", "unknown")
            if after_page == "search":
                return TransitionVerification(
                    template=template,
                    status=VerificationStatus.FAILED.value,
                    transition=False,
                    next_step_hint=self._retry_or_replan(context),
                    after_state="search",
                    details=["点击搜索结果后仍停留在搜索页，未进入目标聊天"],
                    target_name=target_name,
                    verification_level="heavy",
                )

        state_info = self.vision.classify_im_transition(
            after.screenshot_b64,
            target_name,
        )
        state = state_info.get("state", "unknown")
        evidence = state_info.get("evidence", "")
        target_visible = bool(state_info.get("target_visible", False))
        target_selected = bool(state_info.get("target_selected", False))

        if state == "chat_opened":
            return TransitionVerification(
                template=template,
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="chat_opened",
                details=[f"聊天页已打开: {evidence}".strip()],
                progress_made=True,
                step_completed=True,
                target_name=target_name,
                verification_level="heavy",
            )

        if state == "conversation_selected":
            hint = self._retry_or_replan(context) if context.consecutive_waits >= self.MAX_CONSECUTIVE_WAITS else NextStepHint.WAIT.value
            details = [f"{pending_source}目标已选中，等待聊天页稳定: {evidence}".strip()]
            if hint in {NextStepHint.RETRY.value, NextStepHint.REPLAN.value}:
                details.append(f"已连续等待仍未进入聊天页，升级为 {hint}")
            return TransitionVerification(
                template=template,
                status=VerificationStatus.INFERRED.value,
                transition=False,
                next_step_hint=hint,
                after_state="conversation_selected",
                details=details,
                progress_made=True,
                target_name=target_name,
                verification_level="heavy",
            )

        if state == "im_list":
            details = [f"仍未进入聊天页: {evidence}".strip()]
            if target_visible:
                details.append(f"{pending_source}中目标仍可见但未进入聊天页，优先 retry same action")
                hint = self._retry_or_replan(context)
            else:
                hint = NextStepHint.REOBSERVE.value
            return TransitionVerification(
                template=template,
                status=VerificationStatus.UNKNOWN.value,
                transition=False,
                next_step_hint=hint,
                after_state="im_list",
                details=details,
                progress_made=target_selected,
                target_name=target_name,
                verification_level="heavy",
            )

        return TransitionVerification(
            template=template,
            status=VerificationStatus.UNKNOWN.value,
            transition=None,
            next_step_hint=NextStepHint.REOBSERVE.value,
            after_state="unknown",
            details=[f"无法确认{pending_source}打开会话结果: {evidence}".strip()],
            target_name=target_name,
            verification_level="heavy",
        )

    def _verify_navigation_to_page(
        self,
        context: TransitionContext,
        after_perception: Optional[Any] = None,
    ) -> TransitionVerification:
        expected_page = self._infer_expected_page(context.expected_signal or context.task_goal)
        light_result = self._lightweight_page_transition(context, expected_page)
        if light_result:
            return light_result

        if not after_perception:
            return TransitionVerification(
                template="navigation_to_page",
                status=VerificationStatus.UNKNOWN.value,
                transition=None,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="lightweight_uncertain",
                details=["轻量验证无法确认导航结果，需要重型确认"],
                needs_heavy_observation=True,
                verification_level="light",
            )

        after = after_perception
        if not after.screenshot_b64:
            return TransitionVerification(
                template="navigation_to_page",
                status=VerificationStatus.UNKNOWN.value,
                next_step_hint=NextStepHint.REOBSERVE.value,
                details=["导航后截图失败"],
                verification_level="heavy",
            )

        page_info = self.vision.identify_page(after.screenshot_b64)
        after_page = page_info.get("page", "unknown")
        evidence = page_info.get("details", "")

        if expected_page and after_page == expected_page:
            return TransitionVerification(
                template="navigation_to_page",
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state=after_page,
                details=[f"已进入目标页面 {after_page}: {evidence}".strip()],
                progress_made=True,
                step_completed=True,
                verification_level="heavy",
            )

        if after_page == context.current_page and after_page not in {"unknown", "other", ""}:
            loading = self._has_loading_signal(evidence)
            return TransitionVerification(
                template="navigation_to_page",
                status=VerificationStatus.UNKNOWN.value if loading else VerificationStatus.FAILED.value,
                transition=False,
                next_step_hint=NextStepHint.WAIT.value if loading else self._retry_or_replan(context),
                after_state=after_page,
                details=[
                    (
                        f"仍停留在原页面 {after_page}，存在加载迹象: {evidence}".strip()
                        if loading
                        else f"仍停留在原页面 {after_page}，未见明确加载迹象: {evidence}".strip()
                    )
                ],
                verification_level="heavy",
            )

        if after_page not in {"unknown", "other", ""}:
            return TransitionVerification(
                template="navigation_to_page",
                status=VerificationStatus.FAILED.value,
                transition=False,
                next_step_hint=NextStepHint.RETRY.value,
                after_state=after_page,
                details=[f"进入了非目标页面 {after_page}: {evidence}".strip()],
                verification_level="heavy",
            )

        return TransitionVerification(
            template="navigation_to_page",
            status=VerificationStatus.UNKNOWN.value,
            transition=None,
            next_step_hint=NextStepHint.REOBSERVE.value,
            after_state="unknown",
            details=[f"无法确认导航结果: {evidence}".strip()],
            verification_level="heavy",
        )

    def _verify_dialog_visibility(self, context: TransitionContext) -> TransitionVerification:
        dialogs = self.ax.find_elements(role="AXSheet")
        dialogs += self.ax.find_elements(role="AXDialog")
        signal = self._signal_text(context.expected_signal or context.target_description)
        expect_closed = any(keyword in signal for keyword in ("关闭", "消失"))
        expect_open = any(keyword in signal for keyword in ("出现", "打开"))

        if expect_closed and not dialogs:
            return TransitionVerification(
                template="dialog_visibility",
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="dialog_closed",
                details=["弹窗已关闭"],
                progress_made=True,
                step_completed=True,
                verification_level="light",
            )

        if expect_open and dialogs:
            return TransitionVerification(
                template="dialog_visibility",
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="dialog_open",
                details=["弹窗已出现"],
                progress_made=True,
                step_completed=True,
                verification_level="light",
            )

        if dialogs:
            return TransitionVerification(
                template="dialog_visibility",
                status=VerificationStatus.UNKNOWN.value,
                transition=False,
                next_step_hint=NextStepHint.WAIT.value if expect_open else NextStepHint.RETRY.value,
                after_state="dialog_open",
                details=["弹窗状态未达到预期"],
                verification_level="light",
            )

        return TransitionVerification(
            template="dialog_visibility",
            status=VerificationStatus.UNKNOWN.value,
            transition=None,
            next_step_hint=NextStepHint.REOBSERVE.value,
            after_state="dialog_unknown",
            details=["当前未检测到弹窗"],
            verification_level="light",
        )

    def _verify_search_overlay(self, context: TransitionContext) -> TransitionVerification:
        fields = self.ax.find_elements(role="AXTextField", description_contains="搜索")
        fields += self.ax.find_elements(role="AXTextField", title_contains="搜索")
        has_search = bool(fields)
        signal = self._signal_text(context.expected_signal or "")
        action = context.action
        esc_close_intent = (
            action.get("type") == "hotkey"
            and action.get("keys") == ["escape"]
            and (
                context.current_page == "search"
                or context.previous_template == "search_overlay"
                or any(keyword in signal for keyword in ("搜索浮层已关闭", "关闭搜索"))
            )
        )
        expect_open = (
            action.get("type") == "hotkey" and action.get("keys") == ["command", "k"]
        ) or any(keyword in signal for keyword in ("搜索框已打开", "搜索浮层已打开", "打开搜索"))
        expect_close = any(keyword in signal for keyword in ("搜索浮层已关闭", "关闭搜索")) or esc_close_intent

        if expect_open and has_search:
            return TransitionVerification(
                template="search_overlay",
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="search",
                details=["搜索浮层已打开"],
                progress_made=True,
                step_completed=True,
                verification_level="light",
            )

        if expect_close and not has_search:
            return TransitionVerification(
                template="search_overlay",
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="search_closed",
                details=["搜索浮层已关闭"],
                progress_made=True,
                step_completed=True,
                verification_level="light",
            )

        return TransitionVerification(
            template="search_overlay",
            status=VerificationStatus.UNKNOWN.value,
            transition=False,
            next_step_hint=NextStepHint.WAIT.value if expect_open else NextStepHint.RETRY.value,
            after_state="search" if has_search else "search_closed",
            details=["搜索浮层状态未达到预期"],
            verification_level="light",
        )

    def _verify_input_edit(self, context: TransitionContext) -> TransitionVerification:
        focused = self.ax.get_focused_element()
        expected_text = context.action.get("text", "")
        if focused and expected_text and expected_text in (focused.value or ""):
            return TransitionVerification(
                template="input_edit",
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="input_updated",
                details=["AX: 焦点元素已包含输入内容"],
                progress_made=True,
                step_completed=True,
                verification_level="light",
            )
        return TransitionVerification(
            template="input_edit",
            status=VerificationStatus.UNKNOWN.value,
            transition=False,
            next_step_hint=NextStepHint.REOBSERVE.value,
            after_state="input_unknown",
            details=["未能确认输入结果"],
            verification_level="light",
        )

    def _lightweight_im_transition(
        self,
        context: TransitionContext,
        target_name: str,
    ) -> Optional[TransitionVerification]:
        focused_inputs = self.ax.find_elements(role="AXTextArea")
        if any(elem.focused for elem in focused_inputs):
            return TransitionVerification(
                template="list_item_to_detail",
                status=VerificationStatus.CONFIRMED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state="chat_opened",
                details=["AX: 聊天输入框已获得焦点"],
                progress_made=True,
                step_completed=True,
                target_name=target_name,
                verification_level="light",
            )

        focused = self.ax.get_focused_element()
        focused_text = " ".join(
            part for part in (
                getattr(focused, "title", ""),
                getattr(focused, "description", ""),
                getattr(focused, "value", ""),
            ) if part
        )
        if target_name and focused_text and target_name in focused_text:
            hint = (
                self._retry_or_replan(context)
                if context.consecutive_waits >= self.MAX_CONSECUTIVE_WAITS
                else NextStepHint.WAIT.value
            )
            return TransitionVerification(
                template="list_item_to_detail",
                status=VerificationStatus.INFERRED.value,
                transition=False,
                next_step_hint=hint,
                after_state="conversation_selected",
                details=["AX: 目标会话元素已获得焦点"],
                progress_made=True,
                target_name=target_name,
                verification_level="light",
            )
        return None

    def _lightweight_page_transition(
        self,
        context: TransitionContext,
        expected_page: str,
    ) -> Optional[TransitionVerification]:
        if not expected_page:
            return None

        detected = self._ax_page_guess()
        if detected == expected_page:
            return TransitionVerification(
                template="navigation_to_page",
                status=VerificationStatus.INFERRED.value,
                transition=True,
                next_step_hint=NextStepHint.REOBSERVE.value,
                after_state=detected,
                details=[f"AX: 命中目标页面关键词 {detected}，作为弱证据"],
                progress_made=True,
                step_completed=False,
                needs_heavy_observation=True,
                verification_level="light",
            )

        if detected == context.current_page and self._has_loading_signal(""):
            return TransitionVerification(
                template="navigation_to_page",
                status=VerificationStatus.UNKNOWN.value,
                transition=False,
                next_step_hint=NextStepHint.WAIT.value,
                after_state=detected,
                details=[f"AX: 仍停留在原页面 {detected}，存在加载迹象"],
                verification_level="light",
            )
        return None

    def _ax_page_guess(self) -> str:
        if self.ax.find_elements(role="AXTextField", description_contains="搜索") or self.ax.find_elements(
            role="AXTextField", title_contains="搜索"
        ):
            return "search"

        focused_inputs = self.ax.find_elements(role="AXTextArea")
        if any(elem.focused for elem in focused_inputs):
            return "im_chat"

        if self.ax.find_elements(title_contains="云文档") or self.ax.find_elements(description_contains="云文档"):
            return "docs"

        if self.ax.find_elements(title_contains="日历") or self.ax.find_elements(description_contains="日历"):
            return "calendar"

        return ""

    @staticmethod
    def _infer_expected_page(signal: Any) -> str:
        if isinstance(signal, dict):
            page = str(signal.get("target_page", "") or "").strip()
            if page:
                return page
            page = str(signal.get("page", "") or "").strip()
            if page:
                return page
            state = str(signal.get("state", "") or "")
            if state == "search_open":
                return "search"
            if state == "chat_opened":
                return "im_chat"
            if state == "message_list_visible":
                return "im_main"
            transition_data = signal.get("expected_transition", "")
            if isinstance(transition_data, dict):
                to_page = str(
                    transition_data.get("target_page", "")
                    or transition_data.get("to", "")
                    or ""
                ).strip()
                if to_page:
                    return to_page
            transition = str(transition_data or "")
            if "->" in transition:
                to_part = transition.split("->", 1)[1].strip()
                if to_part.startswith("im_chat"):
                    return "im_chat"
                if to_part.startswith("im_main"):
                    return "im_main"
                if to_part.startswith("search"):
                    return "search"
                if to_part.startswith("docs"):
                    return "docs"
                if to_part.startswith("calendar"):
                    return "calendar"
            signal = signal.get("text", "")

        signal = str(signal or "")
        if any(keyword in signal for keyword in ("搜索框", "搜索页", "搜索浮层", "全局搜索")):
            return "search"
        if any(keyword in signal for keyword in ("云文档", "文档页", "文档主页", "文档编辑")):
            return "docs"
        if any(keyword in signal for keyword in ("日历", "日程")):
            return "calendar"
        if any(keyword in signal for keyword in ("聊天", "对话", "会话")):
            return "im_chat"
        if any(keyword in signal for keyword in ("消息主页", "消息列表", "会话列表")):
            return "im_main"
        return ""

    def _should_stop_waiting(self, context: TransitionContext, verification: TransitionVerification) -> bool:
        return (
            context.action.get("type") == "wait"
            and verification.next_step_hint == NextStepHint.WAIT.value
            and context.previous_hint in {"wait", "reobserve"}
            and context.consecutive_waits > self.MAX_CONSECUTIVE_WAITS
        )

    def _finalize_wait_decision(
        self,
        context: TransitionContext,
        verification: TransitionVerification,
    ) -> TransitionVerification:
        if not self._should_stop_waiting(context, verification):
            return verification

        verification.status = VerificationStatus.FAILED.value
        verification.transition = False
        verification.next_step_hint = self._retry_or_replan(context)
        verification.details.append("本轮检查后仍未收敛，停止继续 wait，升级下一步动作")
        return verification

    def _has_loading_signal(self, evidence: str) -> bool:
        loading_keywords = (
            "加载中",
            "正在加载",
            "请稍候",
            "稍候",
            "同步中",
            "刷新中",
            "打开中",
            "进入中",
            "跳转中",
        )
        if any(keyword in evidence for keyword in loading_keywords):
            return True

        if self.ax.find_elements(role="AXProgressIndicator"):
            return True

        for keyword in ("加载", "稍候", "同步", "刷新", "打开中", "进入中"):
            if self.ax.find_elements(title_contains=keyword):
                return True
            if self.ax.find_elements(description_contains=keyword):
                return True
            if self.ax.find_elements(value_contains=keyword):
                return True
        return False

    @staticmethod
    def _retry_or_replan(context: TransitionContext) -> str:
        return (
            NextStepHint.REPLAN.value
            if context.retry_count >= 2
            else NextStepHint.RETRY.value
        )

    @staticmethod
    def _extract_target_name(*texts: Any) -> str:
        for text in texts:
            if not text:
                continue
            if isinstance(text, dict):
                candidate = str(text.get("target_name", "") or "").strip()
                if TransitionVerifier._is_valid_target_candidate(candidate):
                    return candidate
                candidate = str(text.get("target", "") or "").strip()
                if TransitionVerifier._is_valid_target_candidate(candidate):
                    return candidate
                text = text.get("text", "")
            text = str(text)
            for pattern in (
                r"[「“\"]([^」”\"]{1,20})[」”\"]",
                r"名为[「“\"]?([^」”\"，。:：\s]{1,20})",
                r"与([^\s，。、“”\"'「」『』]{1,20})的聊天",
                r"点击[^\w\u4e00-\u9fff]*[“\"「『]?([^”\"」』]{1,20})[”\"」』]?",
                r"查看消息[-:： ]+([^\s，。、“”\"'「」『』]{1,20})",
            ):
                match = re.search(pattern, text)
                if match:
                    candidate = match.group(1).strip()
                    if TransitionVerifier._is_valid_target_candidate(candidate):
                        return candidate

            direct = text.strip().strip("“”\"'「」『』[]()（）")
            if TransitionVerifier._is_valid_target_candidate(direct):
                return direct
        return ""

    @staticmethod
    def _is_valid_target_candidate(candidate: str) -> bool:
        if not candidate or len(candidate) > 20:
            return False
        invalid_parts = (
            "消息界面",
            "界面并进入名为",
            "聊天页",
            "群聊",
            "会话",
            "搜索",
            "列表",
            "结果",
            "点击",
            "进入",
            "查看",
        )
        return not any(part in candidate for part in invalid_parts)

    @staticmethod
    def _signal_text(signal: Any) -> str:
        if isinstance(signal, dict):
            parts = [
                str(signal.get("text", "") or ""),
                str(signal.get("state", "") or ""),
                str(signal.get("page", "") or ""),
                str(signal.get("target_page", "") or ""),
                str(signal.get("target", "") or ""),
                str(signal.get("target_name", "") or ""),
            ]
            transition = signal.get("expected_transition", "")
            if isinstance(transition, dict):
                parts.extend(
                    [
                        str(transition.get("from", "") or ""),
                        str(transition.get("to", "") or ""),
                        str(transition.get("target_page", "") or ""),
                        str(transition.get("target_name", "") or ""),
                    ]
                )
            else:
                parts.append(str(transition or ""))
            return " ".join(part for part in parts if part)
        return str(signal or "")

    @staticmethod
    def _is_module_navigation_intent(context: TransitionContext, signal_text: str) -> bool:
        text = " ".join(
            part for part in (
                context.target_description,
                str(context.action.get("reason", "") or ""),
                signal_text,
            ) if part
        )
        return any(
            keyword in text
            for keyword in ("左侧导航", "导航栏", "模块", "消息入口", "云文档入口", "日历入口", "切换到消息", "切到消息")
        )

    @staticmethod
    def _is_search_select_intent(context: TransitionContext, signal_text: str) -> bool:
        if context.action.get("type") != "click":
            return False
        text = " ".join(
            part for part in (
                context.target_description,
                str(context.action.get("reason", "") or ""),
                signal_text,
            ) if part
        )
        return context.current_page == "search" or "搜索结果" in text
