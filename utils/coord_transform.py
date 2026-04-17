from dataclasses import dataclass

from config import config


@dataclass
class CoordSystem:
    window_x: int
    window_y: int
    window_width: int
    window_height: int
    retina_scale: int
    raw_screenshot_width: int
    raw_screenshot_height: int
    resized_screenshot_width: int
    resized_screenshot_height: int

    @property
    def resize_ratio(self) -> float:
        if self.raw_screenshot_width == 0:
            return 1.0
        return self.resized_screenshot_width / self.raw_screenshot_width

    def som_to_pyautogui(self, som_x: int, som_y: int) -> tuple[int, int]:
        raw_x = som_x / self.resize_ratio
        raw_y = som_y / self.resize_ratio
        logical_x = raw_x / self.retina_scale
        logical_y = raw_y / self.retina_scale
        return int(logical_x + self.window_x), int(logical_y + self.window_y)

    @staticmethod
    def ax_to_pyautogui(ax_x: int, ax_y: int) -> tuple[int, int]:
        return int(ax_x), int(ax_y)

    @staticmethod
    def ax_center_to_pyautogui(ax_pos: tuple[int, int], ax_size: tuple[int, int]) -> tuple[int, int]:
        return int(ax_pos[0] + ax_size[0] // 2), int(ax_pos[1] + ax_size[1] // 2)


def create_coord_system(window_bounds: dict, raw_size: tuple[int, int], resized_size: tuple[int, int]) -> CoordSystem:
    return CoordSystem(
        window_x=window_bounds["x"],
        window_y=window_bounds["y"],
        window_width=window_bounds["width"],
        window_height=window_bounds["height"],
        retina_scale=config.lark_window.retina_scale,
        raw_screenshot_width=raw_size[0],
        raw_screenshot_height=raw_size[1],
        resized_screenshot_width=resized_size[0],
        resized_screenshot_height=resized_size[1],
    )
