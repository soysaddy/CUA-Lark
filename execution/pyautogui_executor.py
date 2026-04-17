try:
    import pyautogui
except Exception:
    pyautogui = None


class PyAutoGUIExecutor:
    @staticmethod
    def click(x: int, y: int) -> bool:
        if not pyautogui:
            return False
        pyautogui.click(x, y)
        return True

    @staticmethod
    def hotkey(*keys: str) -> bool:
        if not pyautogui:
            return False
        pyautogui.hotkey(*keys)
        return True

    @staticmethod
    def press(key: str) -> bool:
        if not pyautogui:
            return False
        pyautogui.press(key)
        return True
