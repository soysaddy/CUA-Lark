from loguru import logger

from config import config

try:
    from openai import OpenAI
except Exception:
    OpenAI = None


def create_openai_client():
    if not OpenAI or not config.openai_api_key:
        return None

    kwargs = {"api_key": config.openai_api_key}
    if config.openai_base_url:
        kwargs["base_url"] = config.openai_base_url
    try:
        return OpenAI(**kwargs)
    except TypeError as exc:
        logger.error(f"OpenAI 客户端初始化失败: {exc}")
        logger.error("请检查 openai/httpx 版本兼容性，当前项目建议使用 httpx<0.28。")
        return None
