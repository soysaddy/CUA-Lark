from perception.ax_inspector import AXInspector
from utils.window_manager import WindowManager


class RuleChecker:
    def __init__(self) -> None:
        self.ax = AXInspector()

    def check(self, condition: dict) -> tuple[bool, str]:
        ctype = condition["type"]
        if ctype == "always_true":
            return True, "always true"
        if ctype == "frontmost_app_is":
            actual = self.ax.get_frontmost_app_name()
            return actual in ("Lark", "飞书", "Feishu"), f"frontmost={actual}"
        if ctype == "window_size_matches":
            bounds = WindowManager.get_window_bounds()
            return bool(bounds), f"bounds={bounds}"
        if ctype == "ax_element_exists":
            elements = self.ax.find_elements(
                role=condition.get("ax_role"),
                title_contains=condition.get("ax_title_contains"),
                description_contains=condition.get("ax_description_contains"),
            )
            if condition.get("focused"):
                elements = [element for element in elements if element.focused]
            return bool(elements), f"found={len(elements)}"
        if ctype == "ax_element_text_contains":
            elements = self.ax.find_elements(title_contains=condition.get("text"))
            return bool(elements), f"found={len(elements)}"
        if ctype == "ax_element_value_contains":
            elements = self.ax.find_elements(role=condition.get("ax_role"), value_contains=condition.get("text"))
            return bool(elements), f"found={len(elements)}"
        if ctype == "ax_element_value_is_empty":
            elements = self.ax.find_elements(role=condition.get("ax_role"))
            return any((not element.value or not element.value.strip()) for element in elements), "empty check"
        if ctype == "window_title_contains":
            return True, "not implemented strictly"
        return False, f"unknown rule type: {ctype}"
