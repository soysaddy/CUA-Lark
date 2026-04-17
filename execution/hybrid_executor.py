import subprocess
import time
from typing import Optional

from loguru import logger

from execution.input_handler import InputHandler
from execution.pyautogui_executor import PyAutoGUIExecutor
from perception.ax_inspector import AXInspector
from utils.coord_transform import CoordSystem


class HybridExecutor:
    def __init__(self) -> None:
        self.ax = AXInspector()
        self.coord_sys: Optional[CoordSystem] = None
        self.som_marks: list = []

    def update_context(self, coord_sys: CoordSystem, som_marks: list) -> None:
        self.coord_sys = coord_sys
        self.som_marks = som_marks

    def execute(self, action: dict) -> bool:
        action_type = action["type"]
        try:
            if action_type == "activate_app":
                return self._activate_app(action["app_name"])
            if action_type == "ensure_window_size":
                from utils.window_manager import WindowManager

                return WindowManager.ensure_standard_window()
            if action_type == "hotkey":
                return PyAutoGUIExecutor.hotkey(*action["keys"])
            if action_type == "key_press":
                return PyAutoGUIExecutor.press(action["key"])
            if action_type == "type_text":
                return InputHandler.paste_text(action["text"])
            if action_type == "ax_click_element":
                return self._ax_click(action)
            if action_type == "vision_click":
                return self._vision_click(action)
            if action_type == "wait":
                time.sleep(action.get("seconds", 1.0))
                return True
            if action_type == "shell":
                subprocess.run(action["cmd"], shell=True, timeout=10, check=False)
                return True
            logger.warning(f"未知动作类型: {action_type}")
            return False
        except Exception as exc:
            logger.error(f"动作执行失败[{action_type}]: {exc}")
            return False

    def _activate_app(self, app_name: str) -> bool:
        subprocess.run(["osascript", "-e", f'tell application "{app_name}" to activate'], capture_output=True, timeout=5, check=False)
        time.sleep(0.5)
        return self.ax.is_lark_frontmost()

    def _ax_click(self, action: dict) -> bool:
        elements = self.ax.find_elements(
            role=action.get("ax_role"),
            title_contains=action.get("text_contains"),
            description_contains=action.get("ax_description_contains"),
        )
        if elements:
            target = elements[0]
            if self.ax.perform_action(target, "AXPress"):
                return True
            if self.coord_sys:
                x, y = self.coord_sys.ax_center_to_pyautogui(target.position, target.size)
                return PyAutoGUIExecutor.click(x, y)
        return self._vision_click(action) if action.get("fallback") else False

    def _vision_click(self, action: dict) -> bool:
        som_id = action.get("som_id")
        if som_id and self.som_marks and self.coord_sys:
            for mark in self.som_marks:
                if mark.mark_id == som_id:
                    x, y = self.coord_sys.som_to_pyautogui(*mark.center)
                    return PyAutoGUIExecutor.click(x, y)
        logger.warning(f"vision_click 缺少可用的 som_id，上下文指令: {action.get('instruction', '')}")
        return False
