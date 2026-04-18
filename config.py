import os
from dataclasses import dataclass, field


@dataclass
class LarkWindowConfig:
    mode: str = "centered_ratio"  # 窗口模式：按比例居中
    x: int = 50  # 窗口左上角 x 坐标
    y: int = 50  # 窗口左上角 y 坐标
    width: int = 1280  # 固定窗口宽度
    height: int = 800  # 固定窗口高度
    width_ratio: float = 0.8  # 窗口宽度占屏幕 80%
    height_ratio: float = 0.8  # 窗口高度占屏幕 80%
    min_margin_x: int = 24  # 左右最小边距
    min_margin_y: int = 40  # 上下最小边距
    display_index: int = 1  # 使用第 1 个显示器
    retina_scale: int = 2  # Retina 缩放倍数


@dataclass
class Config:
    openai_api_key: str = field(default_factory=lambda: os.getenv("OPENAI_API_KEY", "sk-W7YTOovYFrZ4kGEfx4n659lZ0t4DOrtkv8"))  # API Key，优先读环境变量
    openai_base_url: str = field(default_factory=lambda: os.getenv("OPENAI_BASE_URL", "https://yunwu.ai/v1").rstrip("/"))  # API 地址，去掉末尾 /
    openai_model: str = field(default_factory=lambda: os.getenv("OPENAI_MODEL", "gpt-5.4-mini"))  # 主模型
    planner_model: str = field(default_factory=lambda: os.getenv("OPENAI_PLANNER_MODEL", "gpt-5.4-mini"))  # 规划模型
    screenshot_max_width: int = 1280  # 截图最大宽度
    screenshot_quality: int = 85  # 截图质量
    action_interval: float = 0.5  # 每次操作间隔秒数
    page_load_wait: float = 1.5  # 页面加载等待秒数
    max_total_steps: int = 10  # 最大执行步数
    vision_history_length: int = 8  # 保留视觉历史数量
    lark_window: LarkWindowConfig = field(default_factory=LarkWindowConfig)  # 飞书窗口配置


config = Config()  # 创建全局配置对象