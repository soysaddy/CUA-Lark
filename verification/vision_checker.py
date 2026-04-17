from perception.screen_capturer import ScreenCapturer
from perception.vision_client import VisionClient


class VisionChecker:
    def __init__(self) -> None:
        self.capturer = ScreenCapturer()
        self.vision = VisionClient()

    def check(self, question: str, expected: bool = True) -> tuple[bool, str]:
        screen = self.capturer.capture_lark_window()
        if not screen:
            return False, "no screenshot"
        result = self.vision.verify_visual(screen["base64"], question)
        actual = result.get("answer", False)
        return actual == expected, result.get("evidence", "")
