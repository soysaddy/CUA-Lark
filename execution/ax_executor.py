from typing import Optional

from perception.ax_inspector import AXElement, AXInspector


class AXExecutor:
    def __init__(self) -> None:
        self.ax = AXInspector()

    def click_first(
        self,
        role: Optional[str] = None,
        title_contains: Optional[str] = None,
        description_contains: Optional[str] = None,
    ) -> Optional[AXElement]:
        elements = self.ax.find_elements(role=role, title_contains=title_contains, description_contains=description_contains)
        if not elements:
            return None
        target = elements[0]
        if self.ax.perform_action(target, "AXPress"):
            return target
        return None
