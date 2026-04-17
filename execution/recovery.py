import time
from dataclasses import dataclass, field
from enum import Enum

from loguru import logger

from perception.screen_capturer import ScreenCapturer
from perception.vision_client import VisionClient
from utils.window_manager import WindowManager

try:
    import pyautogui
except Exception:
    pyautogui = None


class RecoveryStatus(str, Enum):
    RECOVERED = "recovered"
    RETRYABLE = "retryable"
    NEED_REPLAN = "need_replan"
    HANDOFF = "handoff"


@dataclass
class RecoverySnapshot:
    frontmost: bool = False
    page: str = "unknown"
    page_confidence: str = "unknown"
    has_dialog: bool = False
    has_search_overlay: bool = False
    focused_role: str = ""
    dialog_text: str = ""
    details: str = ""


@dataclass
class RecoveryResult:
    status: RecoveryStatus
    reason: str = ""
    snapshot: RecoverySnapshot = field(default_factory=RecoverySnapshot)
    actions: list[str] = field(default_factory=list)


class RecoveryManager:
    SAFE_PAGES = {"im_main", "im_chat", "calendar", "docs", "search"}
    SENSITIVE_KEYWORDS = (
        "登录", "验证", "验证码", "密码", "授权", "权限",
        "允许", "确认身份", "扫码", "手机", "邮箱", "管理员",
    )

    @classmethod
    def _ensure_lark_frontmost(cls, ax) -> bool:
        if ax.is_lark_frontmost():
            return True
        if not WindowManager.activate_lark():
            logger.warning("恢复前激活飞书失败")
            return False
        time.sleep(0.2)
        if ax.is_lark_frontmost():
            return True
        logger.warning("恢复前飞书未成功切到前台")
        return False

    @classmethod
    def _inspect_state(cls, ax, current_page: str = "") -> RecoverySnapshot:
        snapshot = RecoverySnapshot(frontmost=ax.is_lark_frontmost())

        focused = ax.get_focused_element()
        if focused:
            snapshot.focused_role = focused.role

        dialogs = ax.find_elements(role="AXSheet")
        dialogs += ax.find_elements(role="AXDialog")
        snapshot.has_dialog = bool(dialogs)
        dialog_texts = []
        for dialog in dialogs:
            for part in (dialog.title, dialog.description, dialog.value):
                if part and part not in dialog_texts:
                    dialog_texts.append(part)
        snapshot.dialog_text = " | ".join(dialog_texts[:4])

        search_fields = ax.find_elements(role="AXTextField", description_contains="搜索")
        search_fields += ax.find_elements(role="AXTextField", title_contains="搜索")
        snapshot.has_search_overlay = bool(search_fields)

        page_hint = current_page if current_page in cls.SAFE_PAGES else "unknown"
        try:
            screen_data = ScreenCapturer().capture_lark_window()
            if screen_data and screen_data.get("base64"):
                page_info = VisionClient().identify_page(screen_data["base64"])
                observed_page = page_info.get("page") or ""
                if observed_page in cls.SAFE_PAGES:
                    snapshot.page = observed_page
                    snapshot.page_confidence = "confirmed"
                else:
                    snapshot.page = "unknown"
                    snapshot.page_confidence = "unknown"
                snapshot.details = page_info.get("details", "")
            else:
                snapshot.page = "unknown"
                snapshot.page_confidence = "unknown"
        except Exception as exc:
            snapshot.page = "unknown"
            snapshot.page_confidence = "unknown"
            snapshot.details = f"state inspect failed: {exc}"

        if snapshot.has_search_overlay and snapshot.page in {"unknown", "other", ""}:
            snapshot.page = "search"
            snapshot.page_confidence = "confirmed"

        if (
            snapshot.page == "unknown"
            and page_hint in cls.SAFE_PAGES
        ):
            snapshot.page = page_hint
            snapshot.page_confidence = "inferred"

        return snapshot

    @classmethod
    def _is_sensitive_state(cls, snapshot: RecoverySnapshot) -> bool:
        if not snapshot.dialog_text:
            return False
        return any(keyword in snapshot.dialog_text for keyword in cls.SENSITIVE_KEYWORDS)

    @classmethod
    def _is_safe_state(cls, snapshot: RecoverySnapshot) -> bool:
        if not snapshot.frontmost or snapshot.has_dialog:
            return False
        if snapshot.page_confidence != "confirmed":
            return False
        if snapshot.page == "search":
            return snapshot.has_search_overlay
        return snapshot.page in cls.SAFE_PAGES

    @classmethod
    def _result_from_snapshot(
        cls,
        snapshot: RecoverySnapshot,
        current_page: str,
        reason: str,
        actions: list[str],
        exhausted: bool = False,
    ) -> RecoveryResult:
        if cls._is_sensitive_state(snapshot):
            return RecoveryResult(
                status=RecoveryStatus.HANDOFF,
                reason=snapshot.dialog_text or "检测到需要人工处理的弹窗",
                snapshot=snapshot,
                actions=actions,
            )

        if not snapshot.frontmost:
            return RecoveryResult(
                status=RecoveryStatus.HANDOFF if exhausted else RecoveryStatus.RETRYABLE,
                reason="飞书未处于前台",
                snapshot=snapshot,
                actions=actions,
            )

        if cls._is_safe_state(snapshot):
            if current_page in cls.SAFE_PAGES and snapshot.page != current_page:
                return RecoveryResult(
                    status=RecoveryStatus.NEED_REPLAN,
                    reason=f"已恢复到安全状态 {snapshot.page}，但偏离原页面 {current_page}",
                    snapshot=snapshot,
                    actions=actions,
                )
            return RecoveryResult(
                status=RecoveryStatus.RECOVERED,
                reason=reason or f"已回到安全状态 {snapshot.page}",
                snapshot=snapshot,
                actions=actions,
            )

        if snapshot.page_confidence in {"inferred", "unknown"}:
            return RecoveryResult(
                status=RecoveryStatus.NEED_REPLAN if exhausted else RecoveryStatus.RETRYABLE,
                reason=(
                    f"当前页面仅{snapshot.page_confidence}判定为 {snapshot.page}"
                    if snapshot.page != "unknown"
                    else "当前页面仍未确认"
                ),
                snapshot=snapshot,
                actions=actions,
            )

        if exhausted:
            if snapshot.has_dialog:
                return RecoveryResult(
                    status=RecoveryStatus.HANDOFF,
                    reason=snapshot.dialog_text or "弹窗未能自动关闭",
                    snapshot=snapshot,
                    actions=actions,
                )
            return RecoveryResult(
                status=RecoveryStatus.NEED_REPLAN,
                reason="恢复后仍未回到已知安全状态",
                snapshot=snapshot,
                actions=actions,
            )

        return RecoveryResult(
            status=RecoveryStatus.RETRYABLE,
            reason=reason or "状态仍可继续恢复",
            snapshot=snapshot,
            actions=actions,
        )

    @classmethod
    def _choose_action(cls, snapshot: RecoverySnapshot, current_page: str = "") -> dict | None:
        if cls._is_sensitive_state(snapshot):
            return None

        if snapshot.has_dialog:
            return {
                "name": "close_dialog",
                "steps": [("hotkey", ["escape"])],
                "reason": "存在弹窗，优先关闭遮挡",
            }

        if snapshot.page == "search" and current_page not in {"search", ""}:
            return {
                "name": "exit_search_overlay",
                "steps": [("hotkey", ["escape"])],
                "reason": "当前在搜索浮层，先回到主页面",
            }

        if snapshot.page in {"unknown", "other", ""}:
            return cls._messages_home_action("当前页面未知，回到消息主页面作为安全状态")

        if snapshot.page not in cls.SAFE_PAGES:
            return cls._messages_home_action("当前页面不在已知安全状态，回到消息主页面")

        return None

    @staticmethod
    def _messages_home_action(reason: str) -> dict:
        return {
            "name": "go_messages_home",
            "steps": [("hotkey", ["command", "1"]), ("wait", 0.8)],
            "reason": reason,
        }

    @classmethod
    def _run_step(cls, step_type: str, param) -> bool:
        if step_type == "hotkey":
            pyautogui.hotkey(*param)
            return True
        if step_type == "wait":
            time.sleep(param)
            return True
        return False

    @classmethod
    def attempt_recovery(
        cls,
        executor=None,
        current_state: str = "",
        current_page: str = "",
        max_attempts: int = 3,
    ) -> RecoveryResult:
        logger.warning(f"开始恢复，当前状态: {current_state}")
        if not pyautogui:
            return RecoveryResult(
                status=RecoveryStatus.HANDOFF,
                reason="pyautogui 不可用，无法恢复",
            )

        from perception.ax_inspector import AXInspector

        ax = AXInspector()
        actions_taken: list[str] = []
        if not cls._ensure_lark_frontmost(ax):
            snapshot = cls._inspect_state(ax, current_page)
            return RecoveryResult(
                status=RecoveryStatus.HANDOFF,
                reason="无法将飞书切到前台",
                snapshot=snapshot,
                actions=actions_taken,
            )

        snapshot = cls._inspect_state(ax, current_page)
        initial_result = cls._result_from_snapshot(
            snapshot=snapshot,
            current_page=current_page,
            reason="恢复前状态检查",
            actions=actions_taken,
        )
        if initial_result.status in {
            RecoveryStatus.RECOVERED,
            RecoveryStatus.NEED_REPLAN,
            RecoveryStatus.HANDOFF,
        }:
            return initial_result

        for _ in range(max_attempts):
            action = cls._choose_action(snapshot, current_page)
            if not action:
                return cls._result_from_snapshot(
                    snapshot=snapshot,
                    current_page=current_page,
                    reason="没有合适的恢复动作，交回主循环继续观察",
                    actions=actions_taken,
                    exhausted=False,
                )

            logger.info(f"  恢复策略: {action['name']} - {action['reason']}")
            for step_type, param in action["steps"]:
                if step_type == "hotkey" and not cls._ensure_lark_frontmost(ax):
                    snapshot = cls._inspect_state(ax, current_page)
                    return RecoveryResult(
                        status=RecoveryStatus.HANDOFF,
                        reason="恢复过程中飞书失去前台焦点",
                        snapshot=snapshot,
                        actions=actions_taken,
                    )

                cls._run_step(step_type, param)
                if step_type == "hotkey":
                    actions_taken.append("+".join(param))
                else:
                    actions_taken.append(f"{step_type}:{param}")

                snapshot = cls._inspect_state(ax, current_page)
                step_result = cls._result_from_snapshot(
                    snapshot=snapshot,
                    current_page=current_page,
                    reason=f"{action['name']} 后状态检查",
                    actions=actions_taken,
                )
                if step_result.status in {
                    RecoveryStatus.RECOVERED,
                    RecoveryStatus.RETRYABLE,
                    RecoveryStatus.NEED_REPLAN,
                    RecoveryStatus.HANDOFF,
                }:
                    return step_result

        snapshot = cls._inspect_state(ax, current_page)
        return cls._result_from_snapshot(
            snapshot=snapshot,
            current_page=current_page,
            reason="恢复次数已耗尽",
            actions=actions_taken,
            exhausted=True,
        )
