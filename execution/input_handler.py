import time

try:
    import pyautogui
    import pyperclip
except Exception:
    pyautogui = None
    pyperclip = None


class InputHandler:
    @staticmethod
    def paste_text(text: str) -> bool:
        if not pyautogui or not pyperclip:
            return False
        pyperclip.copy(text)
        time.sleep(0.1)
        pyautogui.hotkey("command", "v")
        return True
