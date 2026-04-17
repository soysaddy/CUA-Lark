import colorsys
from dataclasses import dataclass
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont


@dataclass
class MarkedElement:
    mark_id: int
    label: str
    bbox: tuple[int, int, int, int]
    center: tuple[int, int]
    source: str


class SoMAnnotator:
    def __init__(self) -> None:
        self.colors = self._generate_colors(50)
        try:
            self.font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
        except Exception:
            self.font = ImageFont.load_default()

    @staticmethod
    def _generate_colors(n: int) -> list[tuple[int, int, int]]:
        colors = []
        for idx in range(n):
            r, g, b = colorsys.hsv_to_rgb(idx / max(n, 1), 0.9, 0.9)
            colors.append((int(r * 255), int(g * 255), int(b * 255)))
        return colors

    def annotate(
        self,
        screenshot: Image.Image,
        ax_elements: Iterable,
        window_offset: tuple[int, int] = (0, 0),
        retina_scale: int = 2,
        raw_screenshot_size: tuple[int, int] | None = None,
    ):
        annotated = screenshot.copy()
        draw = ImageDraw.Draw(annotated)
        marks: list[MarkedElement] = []
        raw_width = raw_screenshot_size[0] if raw_screenshot_size else screenshot.width
        raw_height = raw_screenshot_size[1] if raw_screenshot_size else screenshot.height
        ratio_x = screenshot.width / raw_width if raw_width else 1.0
        ratio_y = screenshot.height / raw_height if raw_height else 1.0
        mark_id = 1

        for elem in ax_elements:
            if getattr(elem, "role", "") not in {
                "AXButton",
                "AXTextField",
                "AXTextArea",
                "AXMenuItem",
                "AXLink",
                "AXCheckBox",
                "AXRadioButton",
                "AXPopUpButton",
                "AXStaticText",
                "AXImage",
            }:
                continue
            if elem.size[0] < 5 or elem.size[1] < 5:
                continue
            x1 = int((elem.position[0] - window_offset[0]) * retina_scale * ratio_x)
            y1 = int((elem.position[1] - window_offset[1]) * retina_scale * ratio_y)
            x2 = int(x1 + elem.size[0] * retina_scale * ratio_x)
            y2 = int(y1 + elem.size[1] * retina_scale * ratio_y)
            if x1 < 0 or y1 < 0 or x2 > screenshot.width or y2 > screenshot.height:
                continue
            color = self.colors[mark_id % len(self.colors)]
            draw.rectangle([x1, y1, x2, y2], outline=color, width=2)
            draw.rectangle([x1, max(y1 - 20, 0), x1 + 28, y1], fill=color)
            draw.text((x1 + 3, max(y1 - 18, 0)), str(mark_id), fill="white", font=self.font)
            marks.append(
                MarkedElement(
                    mark_id=mark_id,
                    label=f"{elem.role}: {elem.title or elem.description or elem.value[:20]}",
                    bbox=(x1, y1, x2, y2),
                    center=((x1 + x2) // 2, (y1 + y2) // 2),
                    source="ax_api",
                )
            )
            mark_id += 1
        return annotated, marks

    @staticmethod
    def format_marks_for_llm(marks: list[MarkedElement]) -> str:
        lines = ["截图中标注了以下可交互元素:"]
        lines.extend(f"[{mark.mark_id}] {mark.label}" for mark in marks)
        lines.append("请输出要操作的元素编号，不要输出坐标。")
        return "\n".join(lines)
