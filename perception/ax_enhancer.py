import re
from typing import Optional

from loguru import logger

from agent.perception_fusion import FusedPerception
from perception.ax_inspector import AXElement, AXInspector
from perception.vision_client import VisionClient


class AXEnhancer:
    def __init__(self) -> None:
        self.ax = AXInspector()
        self.vision = VisionClient()

    def enhance(
        self,
        vision_action: dict,
        target_description: str,
        perception: FusedPerception,
        visual_target: Optional[dict] = None,
    ) -> dict:
        enhanced = dict(vision_action)
        if enhanced.get("type") not in {"click", "double_click", "right_click"}:
            return enhanced

        if self._is_valid_model_coordinate(enhanced.get("coordinate"), perception):
            enhanced["coordinate_source"] = "vision_original"
            return enhanced

        if not target_description:
            enhanced["coordinate_source"] = "missing_target"
            return enhanced

        matched = self._find_matching_element(
            target_description=target_description,
            ax_elements=perception.ax_elements,
        )
        if matched and self._is_reasonable_element(matched, perception):
            logger.info(
                f"AX增强命中: {target_description} -> "
                f"{matched.role} {matched.title or matched.description or matched.value}"
            )
            enhanced["coordinate_source"] = "ax_enhanced"
            enhanced["ax_coordinate"] = list(matched.center)
            enhanced["ax_ref"] = matched
            return enhanced

        som_instruction = self._som_instruction(
            target_description=target_description,
            visual_target=visual_target or {},
        )
        if som_instruction and perception.annotated_b64 and perception.som_description and perception.som_marks:
            located = self.vision.locate_element_by_som(
                screenshot_b64=perception.annotated_b64,
                som_description=perception.som_description,
                instruction=som_instruction,
            )
            som_id = int(located.get("som_id", 0) or 0)
            mark = next(
                (item for item in perception.som_marks if item.mark_id == som_id),
                None,
            )
            if mark:
                enhanced["coordinate"] = list(mark.center)
                enhanced["coordinate_source"] = "som_relocated"
                enhanced["relocated_som_id"] = som_id
                enhanced["relocation_reason"] = located.get("reason", "")
                return enhanced

        enhanced["coordinate_source"] = "unresolved_target"
        return enhanced

    def _find_matching_element(
        self,
        target_description: str,
        ax_elements: list[AXElement],
    ) -> Optional[AXElement]:
        if not ax_elements:
            return None
        keywords = self._extract_keywords(target_description)
        best_match = None
        best_score = 0.0
        for elem in ax_elements:
            score = 0.0
            text = f"{elem.title} {elem.description} {elem.role} {elem.value}".lower()
            for keyword in keywords:
                if keyword.lower() in text:
                    score += 2.0
            role_map = {
                "搜索": ["AXTextField", "AXSearchField"],
                "输入": ["AXTextField", "AXTextArea"],
                "按钮": ["AXButton"],
                "发送": ["AXButton"],
                "链接": ["AXLink"],
            }
            for hint, roles in role_map.items():
                if hint in target_description and elem.role in roles:
                    score += 1.5
            if elem.role in {
                "AXButton",
                "AXTextField",
                "AXTextArea",
                "AXLink",
                "AXMenuItem",
            }:
                score += 0.5
            if elem.size[0] < 5 or elem.size[1] < 5:
                score = 0.0
            if score > best_score:
                best_score = score
                best_match = elem
        return best_match if best_score >= 2.0 else None

    @staticmethod
    def _is_reasonable_element(element: AXElement, perception: FusedPerception) -> bool:
        width, height = perception.screenshot.size
        x, y = element.position
        w, h = element.size
        cx, cy = element.center
        if w < 8 or h < 8:
            return False
        if w > width * 0.95 or h > height * 0.95:
            return False
        if not (0 <= cx <= width * 2 and 0 <= cy <= height * 2):
            return False
        if x < -width * 0.2 or y < -height * 0.2:
            return False
        return True

    @staticmethod
    def _is_valid_model_coordinate(
        coordinate: object,
        perception: FusedPerception,
    ) -> bool:
        if not isinstance(coordinate, (list, tuple)) or len(coordinate) != 2:
            return False
        try:
            x, y = int(coordinate[0]), int(coordinate[1])
        except Exception:
            return False
        width, height = perception.screenshot.size
        return 0 <= x < width and 0 <= y < height

    @staticmethod
    def _som_instruction(target_description: str, visual_target: dict) -> str:
        parts = []
        if target_description:
            parts.append(target_description)
        kind = str(visual_target.get("kind", "") or "")
        anchor = str(visual_target.get("anchor", "") or "")
        confidence = str(visual_target.get("confidence", "") or "")
        if kind and kind != "unknown":
            parts.append(f"目标类型: {kind}")
        if anchor:
            parts.append(f"位置提示: {anchor}")
        if confidence and confidence != "low":
            parts.append(f"置信度: {confidence}")
        return "；".join(parts).strip()

    @staticmethod
    def _extract_keywords(description: str) -> list[str]:
        stop_words = {
            "的",
            "了",
            "在",
            "中",
            "个",
            "是",
            "和",
            "或",
            "点击",
            "操作",
            "选择",
            "那个",
            "这个",
            "一个",
        }
        words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+", description)
        results = [word for word in words if word not in stop_words and len(word) >= 2]
        results.append(description)
        return results
