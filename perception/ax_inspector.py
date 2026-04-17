from dataclasses import dataclass
from typing import Any, Optional

from loguru import logger

try:
    import AppKit
    from ApplicationServices import (
        AXUIElementCopyAttributeValue,
        AXUIElementCreateApplication,
        AXUIElementCreateSystemWide,
        AXUIElementPerformAction,
        AXUIElementSetAttributeValue,
        kAXErrorSuccess,
    )
except Exception:
    AppKit = None
    AXUIElementCopyAttributeValue = None
    AXUIElementCreateApplication = None
    AXUIElementCreateSystemWide = None
    AXUIElementPerformAction = None
    AXUIElementSetAttributeValue = None
    kAXErrorSuccess = None


@dataclass
class AXElement:
    role: str = ""
    title: str = ""
    description: str = ""
    value: str = ""
    identifier: str = ""
    position: tuple[int, int] = (0, 0)
    size: tuple[int, int] = (0, 0)
    focused: bool = False
    enabled: bool = True
    _raw_ref: Any = None

    @property
    def center(self) -> tuple[int, int]:
        return (
            int(self.position[0] + self.size[0] / 2),
            int(self.position[1] + self.size[1] / 2),
        )


class AXInspector:
    def __init__(self) -> None:
        self._system_wide = AXUIElementCreateSystemWide() if AXUIElementCreateSystemWide else None

    def available(self) -> bool:
        return bool(AppKit and AXUIElementCreateApplication)

    def get_lark_app_ref(self) -> Optional[Any]:
        if not self.available():
            return None
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        for app in workspace.runningApplications():
            bundle_id = app.bundleIdentifier() or ""
            if "lark" in bundle_id.lower() or app.localizedName() in ("Lark", "飞书", "Feishu"):
                return AXUIElementCreateApplication(app.processIdentifier())
        return None

    def _get_attr(self, element: Any, attr_name: str) -> Optional[Any]:
        if not AXUIElementCopyAttributeValue:
            return None
        err, value = AXUIElementCopyAttributeValue(element, attr_name, None)
        if err == kAXErrorSuccess:
            return value
        return None

    def _parse_element(self, ax_ref: Any) -> AXElement:
        position = self._get_attr(ax_ref, "AXPosition")
        size = self._get_attr(ax_ref, "AXSize")
        pos = (
            int(getattr(position, "x", 0)) if position is not None else 0,
            int(getattr(position, "y", 0)) if position is not None else 0,
        )
        siz = (
            int(getattr(size, "width", 0)) if size is not None else 0,
            int(getattr(size, "height", 0)) if size is not None else 0,
        )
        return AXElement(
            role=str(self._get_attr(ax_ref, "AXRole") or ""),
            title=str(self._get_attr(ax_ref, "AXTitle") or ""),
            description=str(self._get_attr(ax_ref, "AXDescription") or ""),
            value=str(self._get_attr(ax_ref, "AXValue") or ""),
            identifier=str(self._get_attr(ax_ref, "AXIdentifier") or ""),
            position=pos,
            size=siz,
            focused=bool(self._get_attr(ax_ref, "AXFocused")),
            enabled=bool(self._get_attr(ax_ref, "AXEnabled") is not False),
            _raw_ref=ax_ref,
        )

    def find_elements(
        self,
        role: Optional[str] = None,
        title_contains: Optional[str] = None,
        description_contains: Optional[str] = None,
        value_contains: Optional[str] = None,
        max_depth: int = 10,
    ) -> list[AXElement]:
        app_ref = self.get_lark_app_ref()
        if not app_ref or not AXUIElementCopyAttributeValue:
            return []
        results: list[AXElement] = []
        self._walk_tree(app_ref, role, title_contains, description_contains, value_contains, results, 0, max_depth)
        return results

    def _walk_tree(
        self,
        element: Any,
        role: Optional[str],
        title_contains: Optional[str],
        description_contains: Optional[str],
        value_contains: Optional[str],
        results: list[AXElement],
        depth: int,
        max_depth: int,
    ) -> None:
        if depth > max_depth:
            return
        parsed = self._parse_element(element)
        matched = True
        if role and parsed.role != role:
            matched = False
        if title_contains and title_contains not in parsed.title:
            matched = False
        if description_contains and description_contains not in parsed.description:
            matched = False
        if value_contains and value_contains not in parsed.value:
            matched = False
        if matched and any((role, title_contains, description_contains, value_contains)):
            results.append(parsed)
        children = self._get_attr(element, "AXChildren") or []
        for child in children:
            self._walk_tree(child, role, title_contains, description_contains, value_contains, results, depth + 1, max_depth)

    def perform_action(self, element: AXElement, action: str = "AXPress") -> bool:
        if not element._raw_ref or not AXUIElementPerformAction:
            return False
        return AXUIElementPerformAction(element._raw_ref, action) == kAXErrorSuccess

    def set_value(self, element: AXElement, value: str) -> bool:
        if not element._raw_ref or not AXUIElementSetAttributeValue:
            return False
        return AXUIElementSetAttributeValue(element._raw_ref, "AXValue", value) == kAXErrorSuccess

    def get_frontmost_app_name(self) -> str:
        if not AppKit:
            return ""
        active = AppKit.NSWorkspace.sharedWorkspace().frontmostApplication()
        return active.localizedName() if active else ""

    def is_lark_frontmost(self) -> bool:
        return self.get_frontmost_app_name() in ("Lark", "飞书", "Feishu")

    def get_focused_element(self) -> Optional[AXElement]:
        app_ref = self.get_lark_app_ref()
        if not app_ref:
            return None
        focused = self._get_attr(app_ref, "AXFocusedUIElement")
        if not focused:
            return None
        return self._parse_element(focused)

    def dump_tree(self, max_depth: int = 4) -> str:
        app_ref = self.get_lark_app_ref()
        if not app_ref:
            return "飞书未运行或 AX 不可用"
        lines: list[str] = []
        self._dump_node(app_ref, lines, 0, max_depth)
        return "\n".join(lines)

    def _dump_node(self, element: Any, lines: list[str], depth: int, max_depth: int) -> None:
        if depth > max_depth:
            return
        parsed = self._parse_element(element)
        lines.append(
            f"{'  ' * depth}[{parsed.role}] title='{parsed.title}' desc='{parsed.description}' value='{parsed.value[:30]}'"
        )
        for child in self._get_attr(element, "AXChildren") or []:
            self._dump_node(child, lines, depth + 1, max_depth)
