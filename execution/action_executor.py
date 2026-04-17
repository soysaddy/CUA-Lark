import time
from typing import Optional

from loguru import logger

from perception.ax_inspector import AXInspector
from utils.coord_transform import CoordSystem
from utils.window_manager import WindowManager

try:
    import pyautogui
    import pyperclip
except Exception:
    pyautogui = None
    pyperclip = None

if pyautogui:
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.15


class ActionExecutor:
    def __init__(self) -> None:
        self.ax = AXInspector()

    def execute(self, action: dict, coord_system: Optional[CoordSystem] = None) -> bool:
        if not pyautogui:
            logger.error("pyautogui 不可用")
            return False
        action_type = action.get("type", "")

        # ── 关键：需要前台交互的动作，执行前先激活飞书 ──
        # 截图不需要前台（-l 模式），但鼠标/键盘事件必须发到前台窗口
        if action_type in {
            "click", "double_click", "right_click",
            "type", "hotkey", "scroll", "key_press",
        }:
            WindowManager.activate_lark()
            time.sleep(0.3)

        try:
            if action_type in {"click", "double_click", "right_click"}:
                return self._execute_click(action, coord_system)
            if action_type == "type":
                return self._execute_type(action)
            if action_type == "hotkey":
                keys = action.get("keys", [])
                pyautogui.hotkey(*keys)
                time.sleep(0.3)
                return True
            if action_type == "scroll":
                return self._execute_scroll(action, coord_system)
            if action_type == "wait":
                time.sleep(action.get("seconds", 1.0))
                return True
            if action_type == "key_press":
                pyautogui.press(action.get("key", "return"))
                return True
            logger.warning(f"未知动作类型: {action_type}")
            return False
        except Exception as exc:
            logger.error(f"执行动作失败[{action_type}]: {exc}")
            return False

    def _execute_click(self, action: dict, coord_system: Optional[CoordSystem]) -> bool:
        click_func = {
            "click": pyautogui.click,
            "double_click": pyautogui.doubleClick,
            "right_click": pyautogui.rightClick,
        }.get(action["type"], pyautogui.click)

        # 优先 AX 引用
        ax_ref = action.get("ax_ref")
        if ax_ref and getattr(ax_ref, "_raw_ref", None):
            if self.ax.perform_action(ax_ref, "AXPress"):
                logger.debug("AX Press 成功")
                return True

        # AX 坐标（已是屏幕绝对坐标）
        if "ax_coordinate" in action:
            x, y = action["ax_coordinate"]
            logger.debug(f"AX 坐标点击: ({x}, {y})")
            click_func(x, y)
            return True

        # 正规路径：通过 coord_system 转换
        if "coordinate" in action and coord_system:
            x, y = coord_system.som_to_pyautogui(*action["coordinate"])
            logger.debug(f"som_to_pyautogui: {action['coordinate']} → ({x}, {y})")
            click_func(x, y)
            return True

        # ── 兜底：图片坐标 + 窗口偏移 ──
        if "coordinate" in action:
            img_x, img_y = action["coordinate"]
            window_info = WindowManager.get_window_info()
            if window_info and window_info.get("bounds"):
                b = window_info["bounds"]
                screen_x = b["x"] + img_x
                screen_y = b["y"] + img_y
                logger.debug(
                    f"兜底坐标: 图片({img_x},{img_y}) + "
                    f"窗口({b['x']},{b['y']}) = 屏幕({screen_x},{screen_y})"
                )
                click_func(screen_x, screen_y)
            else:
                logger.warning(f"无窗口位置，直接使用原始坐标: ({img_x},{img_y})")
                click_func(img_x, img_y)
            return True

        logger.warning("点击动作缺少坐标信息")
        return False

    def _execute_type(self, action: dict) -> bool:
        text = action.get("text", "")
        if not text or not pyperclip:
            return False
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("command", "v")
        time.sleep(0.3)
        return True

    @staticmethod
    def _execute_scroll(action: dict, coord_system: Optional[CoordSystem]) -> bool:
        direction = action.get("direction", "down")
        amount = action.get("amount", 3)

        if "coordinate" in action:
            if coord_system:
                x, y = coord_system.som_to_pyautogui(*action["coordinate"])
            else:
                img_x, img_y = action["coordinate"]
                window_info = WindowManager.get_window_info()
                if window_info and window_info.get("bounds"):
                    b = window_info["bounds"]
                    x, y = b["x"] + img_x, b["y"] + img_y
                else:
                    x, y = img_x, img_y
            pyautogui.moveTo(x, y)

        pyautogui.scroll(amount if direction == "up" else -amount)
        time.sleep(0.3)
        return True