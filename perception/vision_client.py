import json

from loguru import logger

from config import config
from utils.cost_tracker import CostTracker
from utils.openai_client import create_openai_client


class VisionClient:
    def __init__(self) -> None:
        self.client = create_openai_client()
        self.model = config.openai_model
        self.cost = CostTracker()

    def identify_page(self, screenshot_b64: str) -> dict:
        return self._call(
            system="你是飞书页面识别器。根据截图输出页面类型。",
            user_text="识别当前飞书页面。",
            image_b64=screenshot_b64,
            schema='{"page":"im_main|im_chat|calendar|docs|search|unknown","details":"描述"}',
        )

    def locate_element_by_som(self, screenshot_b64: str, som_description: str, instruction: str) -> dict:
        return self._call(
            system="你是 UI 元素选择器。根据指令，在已编号元素中选择最匹配的元素编号。",
            user_text=f"指令: {instruction}\n\n{som_description}",
            image_b64=screenshot_b64,
            schema='{"som_id":0,"confidence":"high|medium|low","reason":"原因"}',
        )

    def verify_visual(self, screenshot_b64: str, question: str) -> dict:
        return self._call(
            system="你是 UI 验证器。根据截图回答是或否，并给出依据。",
            user_text=question,
            image_b64=screenshot_b64,
            schema='{"answer":true,"evidence":"依据"}',
        )

    def classify_im_transition(self, screenshot_b64: str, target_name: str) -> dict:
        return self._call(
            system=(
                "你是飞书消息列表状态验证器。"
                "判断当前界面处于以下哪种状态："
                "im_list（仍在消息列表页）、"
                "conversation_selected（列表项已选中但聊天页未稳定打开）、"
                "chat_opened（聊天页已打开）、"
                "unknown（无法判断）。"
            ),
            user_text=(
                f"目标会话名: {target_name or '未知'}。\n"
                "请根据当前飞书截图判断："
                "1. 是否仍停留在消息列表页；"
                "2. 目标会话是否已被选中；"
                "3. 是否已经打开聊天页。"
            ),
            image_b64=screenshot_b64,
            schema=(
                '{"state":"im_list|conversation_selected|chat_opened|unknown",'
                '"target_visible":true,'
                '"target_selected":false,'
                '"confidence":"high|medium|low",'
                '"evidence":"依据"}'
            ),
        )

    def _call(self, system: str, user_text: str, image_b64: str, schema: str) -> dict:
        if not self.client:
            return {}
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": f"{system}\n请输出严格 json: {schema}"},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": f"{user_text}\n请直接返回 json。"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{image_b64}", "detail": "high"}},
                        ],
                    },
                ],
                max_tokens=400,
                temperature=0.1,
                response_format={"type": "json_object"},
            )
            usage = getattr(response, "usage", None)
            self.cost.add_usage(getattr(usage, "total_tokens", 0) if usage else 0)
            return json.loads(response.choices[0].message.content)
        except Exception as exc:
            logger.warning(f"视觉调用失败: {exc}")
            return {}

    @property
    def stats(self) -> dict:
        return {
            "calls": self.cost.total_calls,
            "total_tokens": self.cost.total_tokens,
            "estimated_cost_usd": self.cost.estimated_cost_usd,
        }
