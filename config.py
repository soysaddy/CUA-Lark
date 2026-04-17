import os
from dataclasses import dataclass, field


@dataclass
class LarkWindowConfig:
    mode: str = "centered_ratio"
    x: int = 50
    y: int = 50
    width: int = 1400
    height: int = 875
    width_ratio: float = 0.9
    height_ratio: float = 0.88
    min_margin_x: int = 24
    min_margin_y: int = 40
    display_index: int = 1
    retina_scale: int = 2


@dataclass
class Config:
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", ""))
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "").rstrip("/"))
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))
    planner_model: str = field(default_factory=lambda: os.getenv("OPENAI_PLANNER_MODEL", "gpt-5.4-mini"))
    router_model: str = field(default_factory=lambda: os.getenv("OPENAI_ROUTER_MODEL", "gpt-5.4-mini"))
    screenshot_max_width: int = 1280
    screenshot_quality: int = 85
    action_interval: float = 0.5
    page_load_wait: float = 1.0
    max_total_steps: int = 25
    vision_history_length: int = 8
    max_retries_per_state: int = 3
    state_timeout: float = 15.0
    use_test_account: bool = True
    sanitize_screenshots: bool = True
    lark_window: LarkWindowConfig = field(default_factory=LarkWindowConfig)


config = Config()
