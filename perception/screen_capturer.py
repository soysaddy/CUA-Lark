import base64
import io
import os
import subprocess
import tempfile
import time
from typing import Optional

from PIL import Image
from loguru import logger

from config import config
from utils.privacy import sanitize_image
from utils.window_manager import WindowManager


class ScreenCapturer:
    @staticmethod
    def _postprocess(
        image: Image.Image,
        bounds: Optional[dict],
        started_at: float,
    ) -> dict:
        raw_size = image.size

        # ── Retina 修正 ──
        # screencapture -l 在 Retina 屏上输出 2x 像素图
        # 必须缩回逻辑尺寸，否则 AI 返回的坐标会偏 2 倍
        if bounds and bounds.get("width") and bounds.get("height"):
            logical_w = bounds["width"]
            logical_h = bounds["height"]
            # 存在 >1.2x 倍率差才缩放（容忍小误差）
            if image.width > logical_w * 1.2:
                image = image.resize((logical_w, logical_h), Image.LANCZOS)
                logger.debug(
                    f"Retina 缩放: {raw_size} → {image.size} "
                    f"(逻辑窗口 {logical_w}×{logical_h})"
                )

        if config.sanitize_screenshots:
            image = sanitize_image(image)

        if image.width > config.screenshot_max_width:
            ratio = config.screenshot_max_width / image.width
            image = image.resize(
                (config.screenshot_max_width, int(image.height * ratio)),
                Image.LANCZOS,
            )

        buffer = io.BytesIO()
        image.save(buffer, format="PNG", quality=config.screenshot_quality)
        return {
            "image": image,
            "base64": base64.b64encode(buffer.getvalue()).decode("utf-8"),
            "raw_size": raw_size,
            "resized_size": image.size,
            "bounds": bounds,
            "capture_duration_ms": (time.time() - started_at) * 1000,
        }

    @staticmethod
    def _capture_by_window_id(window_id: int) -> Optional[Image.Image]:
        fd, temp_path = tempfile.mkstemp(prefix="cua_lark_capture_", suffix=".png")
        os.close(fd)
        try:
            cmd = ["screencapture", "-x", "-o", "-l", str(window_id), temp_path]
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=10, check=False
            )
            if result.returncode != 0:
                logger.warning(
                    f"screencapture -l 失败: stdout={result.stdout.strip()} "
                    f"stderr={result.stderr.strip()}"
                )
                return None
            if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                logger.warning("screencapture -l 输出为空")
                return None
            return Image.open(temp_path).convert("RGB")
        except Exception as exc:
            logger.warning(f"screencapture -l 异常: {exc}")
            return None
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass

    def capture_lark_window(self, bounds: Optional[dict] = None) -> Optional[dict]:
        """截取飞书窗口内容，不需要飞书在前台。"""
        window_info = WindowManager.get_window_info()
        if not window_info or not window_info.get("window_id"):
            logger.warning("无法截图：找不到飞书窗口")
            return None

        window_id = window_info["window_id"]
        capture_bounds = bounds or window_info.get("bounds")

        started_at = time.time()
        image = self._capture_by_window_id(window_id)
        if image is None:
            logger.warning(f"无法截图：screencapture -l {window_id} 失败")
            return None

        logger.debug(
            f"原始截图 {image.size}, 逻辑窗口 "
            f"{capture_bounds['width']}×{capture_bounds['height']}"
        )
        return self._postprocess(image, capture_bounds, started_at)

    def capture_full_screen(self) -> Optional[dict]:
        fd, temp_path = tempfile.mkstemp(prefix="cua_lark_capture_", suffix=".png")
        os.close(fd)
        started_at = time.time()
        try:
            result = subprocess.run(
                ["screencapture", "-x", temp_path],
                capture_output=True, text=True, timeout=10, check=False,
            )
            if result.returncode != 0:
                logger.warning(f"全屏截图失败: {result.stderr.strip()}")
                return None
            image = Image.open(temp_path).convert("RGB")
            return self._postprocess(image, None, started_at)
        except Exception as exc:
            logger.warning(f"全屏截图异常: {exc}")
            return None
        finally:
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
