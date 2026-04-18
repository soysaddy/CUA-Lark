import json
import re
from typing import Any, Optional

from loguru import logger

from agent.state_schema import (
    build_expected_transition,
    normalize_expected_transition,
)
from config import config
from knowledge.lark_capabilities import LARK_CAPABILITIES, LARK_COMMON_PATTERNS
from utils.openai_client import create_openai_client


class VisionPlanner:
    def __init__(self) -> None:
        self.client = create_openai_client()

    def plan(
        self,
        user_input: str,
        current_screenshot_b64: Optional[str] = None,
    ) -> dict[str, Any]:
        task_text = user_input.strip()
        if self.client:
            result = self._plan_with_openai(task_text, current_screenshot_b64)
            if result:
                return self._normalize_plan(task_text, result)
        return self._normalize_plan(task_text, self._fallback_plan(task_text))

    def replan(
        self,
        original_plan: dict,
        current_step: int,
        current_screenshot_b64: str,
        issue: str,
    ) -> dict[str, Any]:
        task_text = str(original_plan.get("goal") or issue or "").strip()
        if self.client:
            result = self._replan_with_openai(
                original_plan=original_plan,
                current_screenshot_b64=current_screenshot_b64,
                issue=issue,
            )
            if result:
                return self._normalize_plan(task_text, result)
        return self._normalize_plan(task_text, self._fallback_plan(task_text))

    def _plan_with_openai(
        self,
        user_input: str,
        current_screenshot_b64: Optional[str],
    ) -> Optional[dict[str, Any]]:
        try:
            user_parts: list[dict[str, Any]] = [
                {
                    "type": "text",
                    "text": (
                        f"用户任务: {user_input}\n\n"
                        "请只输出高层计划，不要写死执行步骤，不要输出具体点击细节。\n"
                        "返回严格 json。"
                    ),
                }
            ]
            if current_screenshot_b64:
                user_parts.append(
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{current_screenshot_b64}",
                            "detail": "low",
                        },
                    }
                )

            response = self.client.chat.completions.create(
                model=config.planner_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是飞书桌面端高层任务规划器。\n"
                            "你只负责给出目标、首选路径、备选路径、期望状态迁移，不负责写死执行步骤。\n\n"
                            f"{LARK_CAPABILITIES}\n\n{LARK_COMMON_PATTERNS}\n\n"
                            "请输出 json: "
                            "{feasible, confidence, goal, preferred_path, fallback_path, expected_transition, reasoning, risk_notes}\n"
                            "要求:\n"
                            "1. preferred_path / fallback_path 是高层候选路径，不是固定脚本\n"
                            "2. expected_transition 用结构化 json，"
                            "其中 to / target_page 应使用系统当前一致的 canonical page/state id；"
                            "例如可写成 "
                            '{"from":"current","to":"im_chat","target_page":"im_chat","target_name":"大群","text":"进入目标会话"}，'
                            "但这只是示例，不是写死模板\n"
                            "3. 不要输出 steps，不要替执行层决定具体点哪里\n"
                            "4. 可以参考当前截图，但不要过度相信单次截图\n"
                            "5. 请直接返回 json"
                        ),
                    },
                    {"role": "user", "content": user_parts},
                ],
                max_tokens=900,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as exc:
            logger.warning(f"Planner 调用失败，降级规则规划: {exc}")
            return None

    def _replan_with_openai(
        self,
        original_plan: dict,
        current_screenshot_b64: str,
        issue: str,
    ) -> Optional[dict[str, Any]]:
        try:
            response = self.client.chat.completions.create(
                model=config.planner_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "你是飞书桌面端高层任务规划器。\n"
                            "当前执行遇阻，请在不写死执行细节的前提下，更新高层 preferred_path / fallback_path / expected_transition。\n"
                            f"原计划: {json.dumps(original_plan, ensure_ascii=False)}\n"
                            f"问题: {issue}\n"
                            "expected_transition 的 to / target_page 请继续使用系统当前一致的 canonical page/state id。\n"
                            "请只返回 json。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "当前截图供你更新高层策略，请直接返回 json。",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{current_screenshot_b64}",
                                    "detail": "low",
                                },
                            },
                        ],
                    },
                ],
                max_tokens=900,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as exc:
            logger.warning(f"重规划失败，降级高层策略规划: {exc}")
            return None

    @staticmethod
    def _normalize_plan(task_text: str, raw_plan: dict[str, Any]) -> dict[str, Any]:
        goal = str(raw_plan.get("goal") or task_text).strip()
        preferred_path = str(raw_plan.get("preferred_path") or "").strip()
        fallback_path = str(raw_plan.get("fallback_path") or "").strip()
        expected_transition = normalize_expected_transition(
            raw_plan.get("expected_transition"),
            fallback_text=goal,
        )
        normalized = {
            "feasible": bool(raw_plan.get("feasible", True)),
            "confidence": raw_plan.get("confidence", "medium"),
            "goal": goal,
            "preferred_path": preferred_path,
            "fallback_path": fallback_path,
            "expected_transition": expected_transition,
            "reasoning": str(raw_plan.get("reasoning") or "").strip(),
            "risk_notes": raw_plan.get("risk_notes") or "",
        }
        return VisionPlanner._apply_task_strategies(task_text, normalized)

    @staticmethod
    def _apply_task_strategies(task_text: str, plan: dict[str, Any]) -> dict[str, Any]:
        target_name = VisionPlanner._extract_target_name(task_text)
        target_page = VisionPlanner._infer_target_page(task_text)
        updated = dict(plan)

        if VisionPlanner._looks_like_message_open_task(task_text):
            if not updated.get("goal"):
                updated["goal"] = (
                f"打开目标会话「{target_name}」" if target_name else task_text
                )
            if not updated.get("preferred_path"):
                updated["preferred_path"] = (
                "优先利用当前可见的消息列表、已打开聊天或消息模块中的直接入口到达目标会话"
                )
            if not updated.get("fallback_path"):
                updated["fallback_path"] = (
                "如果列表中不可见或当前不在消息模块，则先进入消息模块，再通过搜索定位目标会话"
                )
            if updated["expected_transition"].get("target_page") == "unknown":
                updated["expected_transition"] = build_expected_transition(
                    to_page="im_chat",
                    from_page="current",
                    target_name=target_name,
                    text="打开目标会话",
                )
            elif target_name and not updated["expected_transition"].get("target_name"):
                updated["expected_transition"]["target_name"] = target_name
            return updated

        if target_page != "unknown":
            if not updated.get("preferred_path"):
                updated["preferred_path"] = VisionPlanner._default_preferred_path(target_page)
            if not updated.get("fallback_path"):
                updated["fallback_path"] = VisionPlanner._default_fallback_path(target_page)
            if updated["expected_transition"].get("target_page") == "unknown":
                updated["expected_transition"] = build_expected_transition(
                    to_page=target_page,
                    from_page="current",
                    target_name=target_name,
                    text=task_text,
                )
            elif target_name and not updated["expected_transition"].get("target_name"):
                updated["expected_transition"]["target_name"] = target_name
            return updated

        return plan

    @staticmethod
    def _fallback_plan(task_text: str) -> dict[str, Any]:
        target_name = VisionPlanner._extract_target_name(task_text)
        target_page = VisionPlanner._infer_target_page(task_text)

        if VisionPlanner._looks_like_message_open_task(task_text):
            return {
                "feasible": True,
                "confidence": "medium",
                "goal": f"打开目标会话「{target_name}」" if target_name else task_text,
                "preferred_path": "优先在当前可见的消息列表、已打开聊天或消息模块中直达目标会话",
                "fallback_path": "如果列表中不可见或当前不在消息模块，则先进入消息模块，再通过搜索定位目标会话",
                "expected_transition": build_expected_transition(
                    to_page="im_chat",
                    from_page="current",
                    target_name=target_name,
                    text="打开目标会话",
                ),
                "reasoning": "消息打开类任务适合由执行层结合当前视觉状态，优先走列表直达，必要时转搜索。",
                "risk_notes": "目标会话可能当前不可见，执行时需结合列表可见性决定是否转搜索。",
            }

        if "发送" in task_text and target_name:
            return {
                "feasible": True,
                "confidence": "medium",
                "goal": task_text,
                "preferred_path": "若当前已在目标会话，则直接输入并发送；否则先定位目标会话",
                "fallback_path": "通过搜索定位目标会话后输入并发送消息",
                "expected_transition": build_expected_transition(
                    to_page="im_chat",
                    from_page="current",
                    target_name=target_name,
                    text="进入目标会话并发送消息",
                ),
                "reasoning": "发送消息前需要先确保已定位到目标会话。",
                "risk_notes": "搜索结果可能同名，需要执行层结合当前截图选择正确会话。",
            }

        if target_page != "unknown":
            return {
                "feasible": True,
                "confidence": "medium",
                "goal": task_text,
                "preferred_path": VisionPlanner._default_preferred_path(target_page),
                "fallback_path": VisionPlanner._default_fallback_path(target_page),
                "expected_transition": build_expected_transition(
                    to_page=target_page,
                    from_page="current",
                    target_name=target_name,
                    text=task_text,
                ),
                "reasoning": "该任务以页面/模块切换为主，执行层应结合当前截图自主决定具体入口。",
                "risk_notes": "界面版本差异可能导致入口位置变化，执行时应以当前可见 UI 为准。",
            }

        return {
            "feasible": False,
            "confidence": "low",
            "goal": task_text,
            "preferred_path": "",
            "fallback_path": "",
            "expected_transition": build_expected_transition(
                to_page="unknown",
                from_page="current",
                text=task_text,
            ),
            "reasoning": "无法从任务文本中稳定抽出可执行的高层目标。",
            "risk_notes": "请将任务描述限制在飞书客户端内的消息、日历、云文档等场景。",
        }

    @staticmethod
    def _looks_like_message_open_task(text: str) -> bool:
        has_open_verb = any(
            keyword in text for keyword in ("查看", "进入", "打开", "定位", "切到", "切换到", "找", "找到")
        )
        has_chat_object = any(
            keyword in text for keyword in ("消息", "群", "群聊", "会话", "聊天", "私聊", "对话")
        )
        has_target = bool(VisionPlanner._extract_target_name(text))
        has_non_message_module = any(
            keyword in text for keyword in ("云文档", "文档", "日历", "会议", "邮箱")
        )
        return has_open_verb and (has_chat_object or has_target) and not has_non_message_module

    @staticmethod
    def _infer_target_page(text: str) -> str:
        source = str(text or "")
        if any(keyword in source for keyword in ("云文档", "文档")):
            return "docs"
        if any(keyword in source for keyword in ("日历", "日程", "会议")):
            return "calendar"
        if any(keyword in source for keyword in ("搜索", "查找")):
            return "search"
        if any(keyword in source for keyword in ("聊天", "群聊", "会话", "私聊", "对话")):
            return "im_chat"
        if any(keyword in source for keyword in ("消息", "消息页", "消息界面", "消息模块", "会话列表")):
            return "im_main"
        return "unknown"

    @staticmethod
    def _extract_target_name(text: str) -> str:
        source = str(text or "").strip()
        if not source:
            return ""
        quote_match = re.search(r"[「“\"]([^」”\"]{1,40})[」”\"]", source)
        if quote_match:
            return quote_match.group(1).strip()

        patterns = [
            r"(?:打开|进入|切到|切换到|找到|找|查看)\s*([^，。,:：]{1,24})(?:群聊|聊天|会话|对话)",
            r"(?:给|向)\s*([^，。,:：]{1,24})\s*(?:发消息|发送消息)",
            r"(?:给|向)\s*([^，。,:：]{1,24})\s*[：:]",
            r"名为\s*([^，。,:：]{1,24})",
        ]
        invalid = {"消息", "消息界面", "消息页", "云文档", "文档", "日历", "搜索"}
        for pattern in patterns:
            match = re.search(pattern, source)
            if not match:
                continue
            candidate = match.group(1).strip("「」“”\"' ")
            if candidate and candidate not in invalid:
                return candidate
        return ""

    @staticmethod
    def _default_preferred_path(target_page: str) -> str:
        if target_page == "calendar":
            return "优先使用当前可见的左侧导航或稳定入口切换到日历模块"
        if target_page == "docs":
            return "优先使用当前可见的左侧导航或稳定入口切换到云文档模块"
        if target_page == "search":
            return "优先聚焦飞书搜索入口并打开搜索层"
        if target_page == "im_main":
            return "优先切换到消息模块并确认会话列表可见"
        return "优先利用当前界面上最直接、最稳定的入口到达目标状态"

    @staticmethod
    def _default_fallback_path(target_page: str) -> str:
        if target_page in {"calendar", "docs", "im_main"}:
            return "若直接入口不可见或未响应，可改用搜索或稳定快捷方式进入目标模块"
        if target_page == "search":
            return "若当前搜索入口不可见，可先回到主界面后再尝试打开搜索"
        return "若首选路径不可行，可回到安全状态后再通过搜索或稳定入口重试"
