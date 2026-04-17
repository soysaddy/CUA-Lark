import json
import re
from typing import Any, Optional

from loguru import logger

from config import config
from utils.openai_client import create_openai_client


SUPPORTED_TASKS = {
    "send_message": {
        "description": "发送飞书消息",
        "parameters": ["contact", "message"],
        "examples": [
            "给张三发消息：明天开会",
            "在飞书上告诉李四下午3点有评审",
        ],
    },
    "create_event": {
        "description": "创建日历事件",
        "parameters": ["title", "date", "time_start", "time_end", "attendees"],
        "examples": [
            "创建明天下午3点到4点的产品评审会",
            "建一个会议，周五上午10点，邀请张三李四",
        ],
    },
    "create_doc": {
        "description": "新建飞书文档",
        "parameters": ["title", "content"],
        "examples": [
            "新建一篇文档叫会议纪要",
            "创建文档：产品需求说明书",
        ],
    },
}


class TaskRouter:
    def __init__(self) -> None:
        self.client = create_openai_client()

    def route(self, user_input: str) -> dict[str, Any]:
        if self.client:
            result = self._route_with_openai(user_input)
            if result:
                return result
        return self._route_with_rules(user_input)

    def _route_with_openai(self, user_input: str) -> Optional[dict[str, Any]]:
        try:
            tasks_desc = json.dumps(SUPPORTED_TASKS, ensure_ascii=False, indent=2)
            response = self.client.chat.completions.create(
                model=config.router_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是任务路由器。把用户输入匹配到预定义任务，并提取参数。"
                            "无法匹配时返回 matched=false。请直接返回 json。\n"
                            f"支持的任务:\n{tasks_desc}"
                        ),
                    },
                    {"role": "user", "content": f"{user_input}\n请直接返回 json。"},
                ],
                max_tokens=300,
                temperature=0,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            logger.info(f"任务路由结果: {result}")
            return result
        except Exception as exc:
            logger.warning(f"OpenAI 路由失败，降级到规则匹配: {exc}")
            return None

    def _route_with_rules(self, user_input: str) -> dict[str, Any]:
        text = user_input.strip()

        send_match = re.search(r"(给|发.*给|告诉)(?P<contact>[^：:，, ]+).*[：:](?P<message>.+)$", text)
        if send_match:
            return {
                "matched": True,
                "task_name": "send_message",
                "params": {
                    "contact": send_match.group("contact"),
                    "message": send_match.group("message").strip(),
                },
                "confidence": "medium",
            }

        if "文档" in text or "会议纪要" in text:
            title = self._extract_after_keyword(text, ["叫", "标题", "：", ":"]) or "新建文档"
            return {
                "matched": True,
                "task_name": "create_doc",
                "params": {"title": title, "content": text},
                "confidence": "low",
            }

        if "会议" in text or "日历" in text or "评审会" in text:
            return {
                "matched": True,
                "task_name": "create_event",
                "params": {
                    "title": text,
                    "date": "待确认",
                    "time_start": "待确认",
                    "time_end": "待确认",
                    "attendees": [],
                },
                "confidence": "low",
            }

        return {
            "matched": False,
            "task_name": "",
            "params": {},
            "confidence": "low",
            "supported_tasks": list(SUPPORTED_TASKS.keys()),
        }

    @staticmethod
    def _extract_after_keyword(text: str, keywords: list[str]) -> str:
        for keyword in keywords:
            if keyword in text:
                parts = text.split(keyword, 1)
                if len(parts) == 2 and parts[1].strip():
                    return parts[1].strip()
        return ""
