import json
from typing import Any, Optional

from loguru import logger

from agent.state_schema import normalize_expected_transition, normalize_page_id
from config import config
from utils.cost_tracker import CostTracker
from utils.openai_client import create_openai_client


class VisionClient:
    def __init__(self) -> None:
        self.client = create_openai_client()
        self.model = config.openai_model
        self.cost = CostTracker()

    def identify_page(self, screenshot_b64: str) -> dict[str, Any]:
        payload = self._call(
            system="你是飞书页面识别器。根据截图识别当前页面。",
            user_parts=[
                {"type": "text", "text": "识别当前飞书页面，请直接返回 json。"},
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{screenshot_b64}",
                        "detail": "high",
                    },
                },
            ],
            schema='{"page":"im_main|im_chat|calendar|docs|search|unknown","details":"依据","confidence":"high|medium|low"}',
        )
        payload["page"] = normalize_page_id(payload.get("page", "unknown"))
        return payload

    def locate_element_by_som(
        self,
        screenshot_b64: str,
        som_description: str,
        instruction: str,
    ) -> dict[str, Any]:
        return self._call(
            system="你是 UI 元素选择器。根据指令，在已编号元素中选择最匹配的元素编号。",
            user_parts=[
                {
                    "type": "text",
                    "text": f"指令: {instruction}\n\n{som_description}\n\n请直接返回 json。",
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:image/png;base64,{screenshot_b64}",
                        "detail": "high",
                    },
                },
            ],
            schema='{"som_id":0,"confidence":"high|medium|low","reason":"原因"}',
        )

    def verify_transition(
        self,
        task_goal: str,
        expected_transition: Any,
        action: dict[str, Any],
        before_b64: str,
        after_b64: Optional[str],
        auxiliary_evidence: str = "",
    ) -> dict[str, Any]:
        transition = normalize_expected_transition(expected_transition, fallback_text=task_goal)
        user_parts: list[dict[str, Any]] = [
            {
                "type": "text",
                "text": (
                    "请比较动作前后截图，判断动作后是否发生了预期状态迁移，以及任务是否已完成。\n"
                    f"task_goal={task_goal}\n"
                    f"expected_transition={json.dumps(transition, ensure_ascii=False)}\n"
                    f"action={json.dumps(action, ensure_ascii=False)}\n"
                    f"auxiliary_evidence={auxiliary_evidence or 'none'}\n"
                    "请直接返回 json。"
                ),
            },
            {
                "type": "text",
                "text": "这是动作前截图：",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{before_b64}",
                    "detail": "high",
                },
            },
        ]
        if after_b64:
            user_parts.extend(
                [
                    {"type": "text", "text": "这是动作后截图："},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{after_b64}",
                            "detail": "high",
                        },
                    },
                ]
            )

        payload = self._call(
            system=(
                "你是飞书任务视觉验证器。"
                "截图是主事实来源，辅助结构信息只能作为补充证据，弱辅助证据不能推翻高置信视觉事实。"
            ),
            user_parts=user_parts,
            schema=(
                '{"state":"im_main|im_chat|calendar|docs|search|unknown",'
                '"transition":"completed|partial|none|unknown",'
                '"step_completed":true,'
                '"task_completed":false,'
                '"confidence":"high|medium|low",'
                '"next_step_hint":"retry|wait|reobserve|replan|handoff|none",'
                '"evidence":["证据1","证据2"]}'
            ),
        )
        payload["state"] = normalize_page_id(payload.get("state", "unknown"))
        return payload

    def _call(self, system: str, user_parts: list[dict[str, Any]], schema: str) -> dict[str, Any]:
        if not self.client:
            return {}
        try:
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {
                        "role": "system",
                        "content": f"{system}\n请输出严格 json: {schema}",
                    },
                    {"role": "user", "content": user_parts},
                ],
                max_tokens=500,
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
