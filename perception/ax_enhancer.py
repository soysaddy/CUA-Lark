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

    def enhance(self, vision_action: dict, target_description: str, perception: FusedPerception) -> dict:
        enhanced = dict(vision_action)
        if enhanced.get("type") not in {"click", "double_click", "right_click"}:
            return enhanced

        matched = self._find_matching_element(
            target_description=target_description,
            ax_elements=perception.ax_elements,
            vision_coord=enhanced.get("coordinate"),
            coord_system=perception.coord_system,
        )
        if matched:
            logger.info(
                f"AX增强命中: {target_description} -> {matched.role} {matched.title or matched.description or matched.value}"
            )
            enhanced["coordinate_source"] = "ax_enhanced"
            enhanced["ax_coordinate"] = list(matched.center)
            enhanced["ax_ref"] = matched
            if "coordinate" in enhanced:
                enhanced["vision_coordinate"] = enhanced["coordinate"]
            return enhanced

        if enhanced.get("coordinate"):
            enhanced["coordinate_source"] = "vision_original"
            return enhanced

        if not target_description:
            enhanced["coordinate_source"] = "missing_target"
            return enhanced

        if perception.annotated_b64 and perception.som_description and perception.som_marks:
            located = self.vision.locate_element_by_som(
                screenshot_b64=perception.annotated_b64,
                som_description=perception.som_description,
                instruction=target_description,
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
        vision_coord: Optional[list],
        coord_system,
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
            if vision_coord and coord_system and elem.position != (0, 0):
                vx, vy = coord_system.som_to_pyautogui(vision_coord[0], vision_coord[1])
                ex, ey = elem.center
                distance = ((vx - ex) ** 2 + (vy - ey) ** 2) ** 0.5
                if distance < 30:
                    score += 3.0
                elif distance < 80:
                    score += 1.5
                elif distance < 150:
                    score += 0.5
            if elem.role in {"AXButton", "AXTextField", "AXTextArea", "AXLink", "AXMenuItem"}:
                score += 0.5
            if elem.size[0] < 5 or elem.size[1] < 5:
                score = 0.0
            if score > best_score:
                best_score = score
                best_match = elem
        return best_match if best_score >= 2.0 else None

    @staticmethod
    def _extract_keywords(description: str) -> list[str]:
        stop_words = {"的", "了", "在", "中", "个", "是", "和", "或", "点击", "操作", "选择", "那个", "这个", "一个"}
        words = re.findall(r"[\u4e00-\u9fff]+|[a-zA-Z]+", description)
        results = [word for word in words if word not in stop_words and len(word) >= 2]
        results.append(description)
        return results
