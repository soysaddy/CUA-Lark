import subprocess
import time
from typing import Optional

from loguru import logger

from config import config

try:
    import AppKit
except Exception:
    AppKit = None

try:
    import Quartz
except Exception:
    Quartz = None


class WindowManager:
    APP_CANDIDATES = ("Lark", "飞书", "Feishu")
    APP_LABELS = tuple(name.lower() for name in APP_CANDIDATES)
    TOLERANCE = 10
    SETTLE_RETRIES = 5
    SETTLE_INTERVAL = 0.2

    # ── 内部辅助 ──────────────────────────────────────────────

    @classmethod
    def _normalize_window_label(cls, value: str) -> str:
        text = str(value or "").strip().lower()
        return text.strip("-_:/[](){}| ")

    @classmethod
    def _matches_window_keyword(cls, value: str) -> bool:
        return cls._normalize_window_label(value) in cls.APP_LABELS

    @classmethod
    def _is_lark_window_candidate(cls, window: dict) -> bool:
        if int(window.get("kCGWindowLayer", 0) or 0) != 0:
            return False
        if float(window.get("kCGWindowAlpha", 1) or 1) == 0:
            return False

        owner = str(window.get("kCGWindowOwnerName", "") or "")
        name = str(window.get("kCGWindowName", "") or "")
        if cls._matches_window_keyword(owner):
            return True
        return cls._matches_window_keyword(name)

    @classmethod
    def _list_lark_windows(cls) -> list[dict]:
        if not Quartz:
            return []
        try:
            windows = Quartz.CGWindowListCopyWindowInfo(
                Quartz.kCGWindowListOptionAll, Quartz.kCGNullWindowID
            )
        except Exception as exc:
            logger.warning(f"读取窗口列表失败: {exc}")
            return []

        matches = []
        for window in windows or []:
            if not cls._is_lark_window_candidate(window):
                continue

            owner = str(window.get("kCGWindowOwnerName", "") or "")
            name = str(window.get("kCGWindowName", "") or "")
            owner_matched = cls._matches_window_keyword(owner)
            name_matched = cls._matches_window_keyword(name)
            bounds = window.get("kCGWindowBounds", {}) or {}
            width = int(bounds.get("Width", 0) or 0)
            height = int(bounds.get("Height", 0) or 0)
            x = int(bounds.get("X", 0) or 0)
            y = int(bounds.get("Y", 0) or 0)
            if width < 100 or height < 100:
                continue
            matches.append({
                "window_id": int(window.get("kCGWindowNumber", 0) or 0),
                "owner": owner,
                "owner_pid": int(window.get("kCGWindowOwnerPID", 0) or 0),
                "name": name,
                "owner_matched": owner_matched,
                "name_matched": name_matched,
                "bounds": {"x": x, "y": y, "width": width, "height": height},
            })
        return matches

    @classmethod
    def _app_info(cls) -> Optional[dict]:
        if not AppKit:
            return None
        workspace = AppKit.NSWorkspace.sharedWorkspace()
        for app in workspace.runningApplications():
            name = app.localizedName() or ""
            bundle_id = (app.bundleIdentifier() or "").lower()
            if name in cls.APP_CANDIDATES or "lark" in bundle_id or "feishu" in bundle_id:
                return {"name": name or "Lark", "pid": int(app.processIdentifier())}
        return None

    @classmethod
    def _app_name(cls) -> str:
        app_info = cls._app_info()
        return app_info["name"] if app_info else "Lark"

    @staticmethod
    def _run_osascript(script: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["osascript", "-e", script],
            capture_output=True, text=True, timeout=5, check=False,
        )

    @classmethod
    def _window_script(cls, body: str) -> str:
        return f'''
        tell application "System Events"
            if not (exists process "{cls._app_name()}") then error "process-not-found"
            tell process "{cls._app_name()}"
                {body}
            end tell
        end tell
        '''

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        if AppKit:
            try:
                frame = AppKit.NSScreen.mainScreen().frame()
                return int(frame.size.width), int(frame.size.height)
            except Exception:
                pass
        return config.lark_window.width, config.lark_window.height

    @classmethod
    def _target_bounds(cls) -> dict:
        cfg = config.lark_window
        if cfg.mode != "centered_ratio":
            return {"x": cfg.x, "y": cfg.y, "width": cfg.width, "height": cfg.height}

        screen_width, screen_height = cls._screen_size()
        width = min(max(320, int(screen_width * cfg.width_ratio)), screen_width - cfg.min_margin_x * 2)
        height = min(max(240, int(screen_height * cfg.height_ratio)), screen_height - cfg.min_margin_y * 2)
        x = max(cfg.min_margin_x, int((screen_width - width) / 2))
        y = max(cfg.min_margin_y, int((screen_height - height) / 2))
        return {"x": x, "y": y, "width": width, "height": height}

    # ── 窗口操作 ──────────────────────────────────────────────

    @classmethod
    def activate_lark(cls) -> bool:
        try:
            result = cls._run_osascript(f'tell application "{cls._app_name()}" to activate')
            time.sleep(0.5)
            if result.returncode == 0:
                return True
            logger.error(f"激活飞书失败: stdout={result.stdout.strip()} stderr={result.stderr.strip()}")
            return False
        except Exception as exc:
            logger.error(f"激活飞书失败: {exc}")
            return False

    @classmethod
    def _prepare_window(cls) -> None:
        script = cls._window_script("""
        if (count of windows) = 0 then return
        set frontmost to true
        set win to front window
        try
            set value of attribute "AXMinimized" of win to false
        end try
        try
            set value of attribute "AXFullScreen" of win to false
        end try
        try
            perform action "AXRaise" of win
        end try
        """)
        cls._run_osascript(script)
        time.sleep(cls.SETTLE_INTERVAL)

    @classmethod
    def get_window_bounds(cls, silent: bool = False) -> Optional[dict]:
        window_info = cls.get_window_info()
        if window_info and window_info.get("bounds"):
            return window_info["bounds"]

        script = cls._window_script("""
        if (count of windows) = 0 then return ""
        set win to front window
        set winPos to position of win
        set winSize to size of win
        return (item 1 of winPos as text) & "," & (item 2 of winPos as text) & "," & (item 1 of winSize as text) & "," & (item 2 of winSize as text)
        """)
        try:
            result = cls._run_osascript(script)
            if result.returncode != 0:
                if not silent:
                    logger.warning(f"读取飞书窗口失败: stdout={result.stdout.strip()} stderr={result.stderr.strip()}")
                return None
            text = result.stdout.strip()
            if not text:
                return None
            x, y, width, height = [int(part) for part in text.split(",")]
            return {"x": x, "y": y, "width": width, "height": height}
        except Exception as exc:
            if not silent:
                logger.error(f"获取飞书窗口失败: {exc}")
            return None

    @classmethod
    def get_window_info(cls) -> Optional[dict]:
        """
        通过 Quartz CGWindowList 获取飞书主窗口信息（含 window_id）。
        不依赖飞书是否在前台。
        """
        matches = cls._list_lark_windows()
        if not matches:
            return None

        # owner 精确命中优先于 name 精确命中；同类中再选面积最大的主窗口
        return max(
            matches,
            key=lambda m: (
                1 if m.get("owner_matched") else 0,
                1 if m.get("name_matched") else 0,
                m["bounds"]["width"] * m["bounds"]["height"],
            ),
        )

    @classmethod
    def set_window_bounds(cls, x: int, y: int, width: int, height: int) -> bool:
        script = cls._window_script(f"""
        if (count of windows) = 0 then return "no-window"
        set win to front window
        set position of win to {{{x}, {y}}}
        set size of win to {{{width}, {height}}}
        return "ok"
        """)
        try:
            result = cls._run_osascript(script)
            time.sleep(0.3)
            if result.returncode == 0:
                return True
            logger.warning(f"设置飞书窗口失败: stdout={result.stdout.strip()} stderr={result.stderr.strip()}")
            return False
        except Exception as exc:
            logger.error(f"设置飞书窗口失败: {exc}")
            return False

    @classmethod
    def _matches_target(cls, bounds: Optional[dict]) -> bool:
        if not bounds:
            return False
        target = cls._target_bounds()
        return (
            abs(bounds["x"] - target["x"]) <= cls.TOLERANCE
            and abs(bounds["y"] - target["y"]) <= cls.TOLERANCE
            and abs(bounds["width"] - target["width"]) <= cls.TOLERANCE
            and abs(bounds["height"] - target["height"]) <= cls.TOLERANCE
        )

    @classmethod
    def ensure_standard_window(cls) -> bool:
        if not cls.activate_lark():
            return False

        cls._prepare_window()
        current = cls.get_window_bounds()
        if cls._matches_target(current):
            logger.info(f"飞书窗口已就绪: {current}")
            return True

        target = cls._target_bounds()
        logger.info(f"调整飞书窗口: current={current}, target={target}")
        if not cls.set_window_bounds(target["x"], target["y"], target["width"], target["height"]):
            return False

        for _ in range(cls.SETTLE_RETRIES):
            time.sleep(cls.SETTLE_INTERVAL)
            current = cls.get_window_bounds()
            if cls._matches_target(current):
                logger.info(f"飞书窗口已就绪: {current}")
                return True

        logger.warning(f"飞书窗口标准化失败: current={current}, target={target}")
        return False

    @classmethod
    def diagnose(cls) -> dict:
        current = cls.get_window_bounds()
        return {
            "app_name": cls._app_name(),
            "current": current,
            "target": cls._target_bounds(),
            "matches_target": cls._matches_target(current),
            "mode": config.lark_window.mode,
            "window_info": cls.get_window_info(),
        }
