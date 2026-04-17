import base64
import io
import time
from dataclasses import dataclass, field, replace
from typing import Optional

from PIL import Image
from loguru import logger

from perception.ax_inspector import AXElement, AXInspector
from perception.screen_capturer import ScreenCapturer
from perception.som_annotator import MarkedElement, SoMAnnotator
from utils.coord_transform import CoordSystem, create_coord_system
from utils.window_manager import WindowManager


@dataclass
class FusedPerception:
    screenshot: Image.Image
    screenshot_b64: str
    annotated_screenshot: Optional[Image.Image] = None
    _annotated_b64: Optional[str] = field(default=None, repr=False)
    ax_summary: str = ""
    ax_elements: list[AXElement] = field(default_factory=list)
    som_marks: list[MarkedElement] = field(default_factory=list)
    som_description: str = ""
    coord_system: Optional[CoordSystem] = None
    timestamp: float = 0.0
    capture_duration_ms: float = 0.0
    capture_source: str = "fresh"
    ax_enabled: bool = False
    som_enabled: bool = False

    @property
    def annotated_b64(self) -> Optional[str]:
        if self._annotated_b64 is None and self.annotated_screenshot is not None:
            buf = io.BytesIO()
            self.annotated_screenshot.save(buf, format="PNG")
            self._annotated_b64 = base64.b64encode(buf.getvalue()).decode("utf-8")
        return self._annotated_b64

    @annotated_b64.setter
    def annotated_b64(self, value: Optional[str]) -> None:
        self._annotated_b64 = value


class PerceptionFusion:
    def __init__(self) -> None:
        self.capturer = ScreenCapturer()
        self.ax = AXInspector()
        self.som = SoMAnnotator()
        self._capture_usage: dict[int, int] = {}
        self._perception_cache: dict[tuple[int, bool, bool], FusedPerception] = {}

    @staticmethod
    def _get_bounds() -> Optional[dict]:
        """获取窗口逻辑边界，AppleScript 优先，Quartz 兜底。"""
        bounds = WindowManager.get_window_bounds(silent=True)
        if bounds:
            return bounds
        # 飞书不在前台时 AppleScript 可能失败，用 Quartz 兜底
        window_info = WindowManager.get_window_info()
        if window_info and window_info.get("bounds"):
            return window_info["bounds"]
        return None

    def perceive(self, with_som: bool = True, with_ax: bool = True) -> FusedPerception:
        screen_data, bounds = self.capture_screen()
        if not screen_data or not bounds:
            logger.error("截图失败")
            return FusedPerception(
                screenshot=Image.new("RGB", (1, 1)),
                screenshot_b64="",
                timestamp=time.time(),
            )
        return self.perceive_from_capture(
            screen_data=screen_data,
            bounds=bounds,
            with_som=with_som,
            with_ax=with_ax,
        )

    def capture_screen(self, bounds: Optional[dict] = None) -> tuple[Optional[dict], Optional[dict]]:
        capture_bounds = bounds or self._get_bounds()
        if not capture_bounds:
            logger.error("无法获取飞书窗口")
            return None, None
        screen_data = self.capturer.capture_lark_window(capture_bounds)
        if screen_data:
            screen_data["capture_source"] = "fresh"
            capture_key = self._capture_key(screen_data)
            self._capture_usage[capture_key] = 0
            self._prune_caches()
        return screen_data, capture_bounds

    def perceive_from_capture(
        self,
        screen_data: dict,
        bounds: dict,
        with_som: bool = True,
        with_ax: bool = True,
    ) -> FusedPerception:
        started_at = time.time()
        capture_key = self._capture_key(screen_data)
        cache_key = (capture_key, with_ax, with_som)
        cached = self._perception_cache.get(cache_key)
        if cached:
            return replace(
                cached,
                capture_source="reused",
                capture_duration_ms=screen_data.get("capture_duration_ms", cached.capture_duration_ms),
                timestamp=time.time(),
            )

        usage_count = self._capture_usage.get(capture_key, 0)
        self._capture_usage[capture_key] = usage_count + 1
        result = FusedPerception(
            screenshot=Image.new("RGB", (1, 1)),
            screenshot_b64="",
            timestamp=time.time(),
            capture_source="fresh" if usage_count == 0 else "reused",
            ax_enabled=with_ax,
            som_enabled=False,
        )

        result.screenshot = screen_data["image"]
        result.screenshot_b64 = screen_data["base64"]

        if with_ax:
            try:
                result.ax_elements = self.ax.find_elements(max_depth=6)
                result.ax_summary = self._build_ax_summary(result.ax_elements)
            except Exception as exc:
                logger.warning(f"AX 获取失败，降级纯视觉: {exc}")
                result.ax_summary = "(AX信息不可用)"

        if with_som and result.ax_elements:
            try:
                annotated, marks = self.som.annotate(
                    screenshot=result.screenshot,
                    ax_elements=result.ax_elements,
                    window_offset=(bounds["x"], bounds["y"]),
                    raw_screenshot_size=screen_data.get("raw_size"),
                )
                result.annotated_screenshot = annotated
                result.som_marks = marks
                result.som_description = self.som.format_marks_for_llm(marks)
                result.som_enabled = True
            except Exception as exc:
                logger.warning(f"SoM 标注失败，跳过: {exc}")

        result.coord_system = create_coord_system(
            window_bounds=bounds,
            raw_size=screen_data["raw_size"],
            resized_size=screen_data.get("resized_size", result.screenshot.size),
        )
        result.capture_duration_ms = (time.time() - started_at) * 1000
        self._perception_cache[cache_key] = result
        self._prune_caches()
        return result

    @staticmethod
    def _capture_key(screen_data: dict) -> int:
        return id(screen_data)

    def _prune_caches(self) -> None:
        while len(self._capture_usage) > 8:
            oldest = next(iter(self._capture_usage))
            self._capture_usage.pop(oldest, None)
            stale_keys = [key for key in self._perception_cache if key[0] == oldest]
            for stale_key in stale_keys:
                self._perception_cache.pop(stale_key, None)
        while len(self._perception_cache) > 12:
            self._perception_cache.pop(next(iter(self._perception_cache)), None)

    @staticmethod
    def _build_ax_summary(elements: list[AXElement]) -> str:
        interactable_roles = {
            "AXButton", "AXTextField", "AXTextArea", "AXMenuItem",
            "AXLink", "AXCheckBox", "AXRadioButton", "AXPopUpButton",
            "AXTab", "AXToolbar",
        }
        lines = ["[页面结构信息 - 来自Accessibility API]"]
        for elem in elements:
            if elem.role not in interactable_roles:
                continue
            if elem.size[0] < 5 or elem.size[1] < 5:
                continue
            parts = [elem.role.replace("AX", "")]
            if elem.title:
                parts.append(f'"{elem.title}"')
            elif elem.description:
                parts.append(f"({elem.description})")
            if elem.value:
                preview = elem.value[:30] + ("..." if len(elem.value) > 30 else "")
                parts.append(f'value="{preview}"')
            if elem.focused:
                parts.append("[focused]")
            parts.append(f"at({elem.position[0]},{elem.position[1]})")
            parts.append(f"size({elem.size[0]}x{elem.size[1]})")
            lines.append("  - " + " ".join(parts))
        if len(lines) == 1:
            lines.append("  (未检测到可交互元素)")
        return "\n".join(lines)
