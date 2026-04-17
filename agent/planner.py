import json
import re
from typing import Any, Optional

from loguru import logger

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
                return self._apply_task_strategies(
                    task_text,
                    self._normalize_plan(result),
                )
        return self._apply_task_strategies(
            task_text,
            self._normalize_plan(self._fallback_plan(task_text)),
        )

    def replan(
        self,
        original_plan: dict,
        current_step: int,
        current_screenshot_b64: str,
        issue: str,
    ) -> dict[str, Any]:
        task_text = original_plan.get("goal", "") or issue
        if self.client:
            result = self._replan_with_openai(
                original_plan, current_step, current_screenshot_b64, issue
            )
            if result:
                return self._apply_task_strategies(
                    task_text,
                    self._normalize_plan(result),
                )
        steps = (
            original_plan.get("steps", [])[current_step:]
            or original_plan.get("steps", [])
        )
        return self._apply_task_strategies(
            task_text,
            self._normalize_plan(
            {
                "feasible": bool(steps),
                "confidence": "low",
                "goal": task_text,
                "steps": steps,
                "reasoning": "基于原计划降级重规划",
                "risk_notes": issue,
            }
            ),
        )

    def _plan_with_openai(
        self,
        user_input: str,
        current_screenshot_b64: Optional[str] = None,
    ) -> Optional[dict[str, Any]]:
        try:
            user_parts: list[dict] = [
                {
                    "type": "text",
                    "text": (
                        f"用户指令: {user_input}\n\n"
                        "请根据当前飞书界面截图，规划具体可执行的步骤。\n"
                        "每个步骤应该是一个具体的 GUI 操作（点击某个按钮、输入文字等）。\n"
                        "请直接返回 json。"
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
                            "你是飞书桌面端任务规划器。\n\n"
                            f"{LARK_CAPABILITIES}\n\n{LARK_COMMON_PATTERNS}\n\n"
                            "## 规划原则\n"
                            "1. 观察当前截图，但不要过度相信单次截图；如果状态不确定，优先产出稳健 fallback 链路\n"
                            "2. 规划从当前状态到目标的最短路径，但不要过早替执行层做决定\n"
                            "3. 每步应描述目标状态迁移和候选策略，不必把所有 fallback 写成固定点击路径\n"
                            "4. 飞书左侧导航栏有：消息、视频会议、日历、云文档、邮箱 等入口\n"
                            "5. 如果目标页面已经显示在当前截图中，步骤可以很少；消息场景可表达 preferred_path 和 fallback_path\n"
                            "6. 优先使用以下 step type: module_navigation, list_item_open, search_open, search_select, input\n"
                            "7. success_signal 只需描述结构化目标，例如 {target_page,target_name,expected_transition}\n"
                            "8. 消息任务可在 plan 顶层补充 preferred_path / fallback_path / expected_transition，而不是把整条固定路径写死\n\n"
                            "请输出 json: {feasible, confidence, goal, preferred_path?, fallback_path?, expected_transition?, steps[], reasoning, risk_notes}\n"
                            "steps 中每个元素: {id, description, type, success_signal}"
                        ),
                    },
                    {"role": "user", "content": user_parts},
                ],
                max_tokens=1200,
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
        current_step: int,
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
                            "你是飞书桌面端任务规划器。原计划执行遇阻，"
                            "请根据当前界面重新规划剩余步骤。\n"
                            f"原计划: {json.dumps(original_plan, ensure_ascii=False)}\n"
                            f"已完成到步骤索引: {current_step}\n"
                            f"遇到的问题: {issue}\n"
                            "请直接返回 json。"
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": "当前飞书界面截图，请重新规划。",
                            },
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{current_screenshot_b64}",
                                    "detail": "high",
                                },
                            },
                        ],
                    },
                ],
                max_tokens=1000,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            return json.loads(response.choices[0].message.content)
        except Exception as exc:
            logger.warning(f"重规划失败: {exc}")
            return None

    def _fallback_plan(self, user_input: str) -> dict[str, Any]:
        text = user_input.strip()

        message_target = self._extract_message_target(text)
        if self._looks_like_message_open_task(text) and message_target:
            return self._build_message_open_plan(text, message_target)

        # ── 发消息 ──
        send_match = re.search(
            r"(给|发.*给|告诉)(?P<contact>[^：:，, ]+).*[：:](?P<message>.+)$",
            text,
        )
        if send_match:
            contact = send_match.group("contact")
            message = send_match.group("message").strip()
            return {
                "feasible": True,
                "confidence": "medium",
                "goal": f"向{contact}发送消息：{message}",
                "steps": [
                    {
                        "id": 1,
                        "description": "打开飞书全局搜索",
                        "type": "search_open",
                        "success_signal": "搜索框已打开",
                    },
                    {
                        "id": 2,
                        "description": f"搜索联系人{contact}",
                        "type": "input",
                        "success_signal": f"搜索结果中出现{contact}",
                    },
                    {
                        "id": 3,
                        "description": f"点击联系人{contact}",
                        "type": "search_select",
                        "success_signal": f"进入与{contact}的聊天",
                    },
                    {
                        "id": 4,
                        "description": f"输入消息{message}",
                        "type": "input",
                        "success_signal": "消息已输入到输入框",
                    },
                    {
                        "id": 5,
                        "description": "按回车发送消息",
                        "type": "input",
                        "success_signal": f"消息{message}已出现在聊天中",
                    },
                ],
                "reasoning": "标准发消息流程",
                "risk_notes": "联系人搜索结果可能不唯一",
            }

        # ── 查看/浏览文档 ──
        if any(kw in text for kw in ("查看", "浏览", "打开")) and "文档" in text:
            return {
                "feasible": True,
                "confidence": "medium",
                "goal": text,
                "steps": [
                    {
                        "id": 1,
                        "description": "点击左侧导航栏的「云文档」图标",
                        "type": "module_navigation",
                        "success_signal": "云文档主页已显示",
                    },
                ],
                "reasoning": "查看文档只需切换到文档模块",
                "risk_notes": "导航栏图标位置可能因版本不同而变化",
            }

        # ── 创建文档 ──
        if any(kw in text for kw in ("新建", "创建", "写")) and "文档" in text:
            return {
                "feasible": True,
                "confidence": "low",
                "goal": text,
                "steps": [
                    {
                        "id": 1,
                        "description": "点击左侧导航栏的「云文档」图标",
                        "type": "module_navigation",
                        "success_signal": "已进入文档页",
                    },
                    {
                        "id": 2,
                        "description": "打开新建文档入口",
                        "type": "list_item_open",
                        "success_signal": "已打开文档编辑页",
                    },
                    {
                        "id": 3,
                        "description": "输入标题和内容",
                        "type": "input",
                        "success_signal": "文档内容显示正确",
                    },
                ],
                "reasoning": "创建文档流程",
                "risk_notes": "新建按钮可能因版本不同而变化",
            }

        # ── 日历/会议 ──
        if any(kw in text for kw in ("会议", "日历", "评审会", "日程")):
            return {
                "feasible": True,
                "confidence": "low",
                "goal": text,
                "steps": [
                    {
                        "id": 1,
                        "description": "点击左侧导航栏的「日历」图标",
                        "type": "module_navigation",
                        "success_signal": "已进入日历页",
                    },
                    {
                        "id": 2,
                        "description": "打开新建日程",
                        "type": "list_item_open",
                        "success_signal": "新建日程表单已出现",
                    },
                    {
                        "id": 3,
                        "description": "填写会议信息并保存",
                        "type": "input",
                        "success_signal": "日历中出现新事件",
                    },
                ],
                "reasoning": "日历建会流程",
                "risk_notes": "时间和参与人需要精细视觉判断",
            }

        return {
            "feasible": False,
            "confidence": "low",
            "goal": text,
            "steps": [],
            "reasoning": "超出当前能力范围或无法稳定拆解",
            "risk_notes": "请将任务限制在消息、日历、文档场景内",
        }

    @staticmethod
    def _normalize_plan(plan: dict[str, Any]) -> dict[str, Any]:
        steps = []
        for index, raw_step in enumerate(plan.get("steps", []), start=1):
            if not isinstance(raw_step, dict):
                raw_step = {"description": str(raw_step)}

            description = (
                raw_step.get("description")
                or raw_step.get("step")
                or raw_step.get("action")
                or raw_step.get("name")
                or f"步骤 {index}"
            )
            step_type = (
                raw_step.get("type")
                or VisionPlanner._infer_step_type(description)
            )
            success_signal = VisionPlanner._normalize_success_signal(
                raw_step.get("success_signal")
                or raw_step.get("expected")
                or description,
                step_type,
                description,
            )
            step = {
                key: value
                for key, value in dict(raw_step).items()
                if key not in {"when", "fallback"}
            }
            step.update(
                {
                    "id": raw_step.get("id", index),
                    "description": description,
                    "type": step_type,
                    "success_signal": success_signal,
                }
            )
            steps.append(step)

        normalized = dict(plan)
        normalized["steps"] = steps
        return normalized

    @staticmethod
    def _infer_step_type(description: str) -> str:
        if "搜索结果" in description:
            return "search_select"
        if any(
            kw in description
            for kw in (
                "左侧导航", "导航栏", "侧边栏", "模块", "消息入口",
                "云文档入口", "日历入口", "切到消息", "切到云文档",
                "切到日历", "消息页", "云文档页", "日历页",
            )
        ):
            return "module_navigation"
        if any(kw in description for kw in ("打开搜索", "全局搜索", "搜索框", "搜索浮层")):
            return "search_open"
        if any(kw in description for kw in ("输入", "填写", "粘贴")):
            return "input"
        if any(kw in description for kw in ("会话", "群聊", "聊天", "联系人")) and any(
            kw in description for kw in ("点击", "打开", "进入")
        ):
            return "list_item_open"
        if any(kw in description for kw in ("点击", "打开", "进入", "切换")):
            return "module_navigation"
        if any(kw in description for kw in ("确认", "验证", "检查")):
            return "verify"
        return "module_navigation"

    @staticmethod
    def _normalize_success_signal(
        signal: Any,
        step_type: str,
        description: str,
    ) -> Any:
        if isinstance(signal, dict):
            return signal

        text = str(signal or description)
        target = VisionPlanner._extract_message_target(description) or VisionPlanner._extract_message_target(text)
        page = VisionPlanner._infer_page_from_text(text) or VisionPlanner._infer_page_from_text(description)

        if step_type == "module_navigation":
            return {
                "target_page": page or "unknown",
                "expected_transition": VisionPlanner._make_expected_transition(
                    to_page=page or "unknown",
                ),
                "text": text,
            }
        if step_type == "list_item_open":
            return {
                "target_page": "im_chat",
                "target_name": target,
                "expected_transition": VisionPlanner._make_expected_transition(
                    from_page="im_main",
                    to_page="im_chat",
                    target_name=target,
                ),
                "text": text,
            }
        if step_type == "search_open":
            return {
                "target_page": "search",
                "expected_transition": VisionPlanner._make_expected_transition(
                    to_page="search",
                ),
                "text": text,
            }
        if step_type == "search_select":
            return {
                "target_page": "im_chat",
                "target_name": target,
                "expected_transition": VisionPlanner._make_expected_transition(
                    from_page="search",
                    to_page="im_chat",
                    target_name=target,
                ),
                "text": text,
            }
        if step_type == "input":
            return {
                "target_name": target,
                "expected_transition": VisionPlanner._make_expected_transition(
                    from_page="input",
                    to_page="input_updated",
                    target_name=target,
                ),
                "text": text,
            }
        return text

    @staticmethod
    def _make_expected_transition(
        to_page: str,
        from_page: str = "current",
        target_name: str = "",
    ) -> dict[str, str]:
        return {
            "from": from_page,
            "to": to_page,
            "target_page": to_page,
            "target_name": target_name,
        }

    @staticmethod
    def _apply_task_strategies(task_text: str, plan: dict[str, Any]) -> dict[str, Any]:
        target = VisionPlanner._extract_message_target(task_text)
        if VisionPlanner._looks_like_message_open_task(task_text) and target:
            return VisionPlanner._build_message_open_plan(task_text, target, existing=plan)
        return plan

    @staticmethod
    def _looks_like_message_open_task(text: str) -> bool:
        target = VisionPlanner._extract_message_target(text)
        has_open_verb = any(
            keyword in text for keyword in ("查看", "进入", "打开", "定位", "切到", "切换到", "找", "找到")
        )
        has_chat_object = any(
            keyword in text for keyword in ("消息", "群", "群聊", "会话", "聊天", "私聊", "对话")
        )
        has_non_message_module = any(
            keyword in text for keyword in ("云文档", "文档", "日历", "会议", "邮箱")
        )
        return has_open_verb and (has_chat_object or (bool(target) and not has_non_message_module))

    @staticmethod
    def _build_message_open_plan(
        task_text: str,
        target: str,
        existing: Optional[dict[str, Any]] = None,
    ) -> dict[str, Any]:
        plan = dict(existing or {})
        plan["feasible"] = True
        plan["goal"] = plan.get("goal") or f"打开目标会话「{target}」"
        plan.setdefault("task_kind", "message_open")
        plan.setdefault("preferred_path", "列表直达")
        plan.setdefault("fallback_path", "搜索定位")
        plan.setdefault(
            "expected_transition",
            VisionPlanner._make_expected_transition(
                from_page="im_main",
                to_page="im_chat",
                target_name=target,
            ),
        )
        steps = [
            VisionPlanner._enrich_message_step(step, target)
            for step in plan.get("steps", [])
        ]
        if not steps:
            steps = [
                {
                    "id": 1,
                    "description": f"打开目标会话「{target}」",
                    "type": "list_item_open",
                    "success_signal": {
                        "target_page": "im_chat",
                        "target_name": target,
                        "expected_transition": VisionPlanner._make_expected_transition(
                            from_page="im_main",
                            to_page="im_chat",
                            target_name=target,
                        ),
                        "text": f"已进入「{target}」聊天页",
                    },
                }
            ]
        plan["steps"] = steps
        plan["reasoning"] = (
            plan.get("reasoning")
            or "消息会话任务保留首选路径和备选路径，由执行层根据当前状态决定走列表直达还是搜索定位。"
        )
        risk_notes = plan.get("risk_notes") or []
        if isinstance(risk_notes, str):
            risk_notes = [risk_notes]
        if "单次截图可能看不到完整会话列表，因此保留搜索 fallback。" not in risk_notes:
            risk_notes.append("单次截图可能看不到完整会话列表，因此保留搜索 fallback。")
        plan["risk_notes"] = risk_notes
        return VisionPlanner._normalize_plan(plan)

    @staticmethod
    def _enrich_message_step(step: dict[str, Any], target: str) -> dict[str, Any]:
        enriched = dict(step)
        description = str(enriched.get("description", "") or "")
        step_type = str(enriched.get("type", "") or "")
        signal = enriched.get("success_signal")

        if step_type == "module_navigation":
            enriched.setdefault("preferred_path", "左侧导航切换")
        elif step_type == "list_item_open":
            enriched.setdefault("preferred_path", "列表直达")
            enriched.setdefault("fallback_path", "搜索定位")
        elif step_type in {"search_open", "search_select"}:
            enriched.setdefault("preferred_path", "搜索定位")

        if step_type in {"list_item_open", "search_select"}:
            current_target = ""
            if isinstance(signal, dict):
                current_target = str(
                    signal.get("target_name", "") or signal.get("target", "") or ""
                ).strip()
            if (not isinstance(signal, dict)) or not VisionPlanner._is_valid_message_target(current_target):
                signal_text = (
                    str(signal.get("text", "") or description)
                    if isinstance(signal, dict)
                    else str(signal or description or f"已进入「{target}」聊天页")
                )
                enriched["success_signal"] = {
                    "target_page": "im_chat",
                    "target_name": target,
                    "expected_transition": VisionPlanner._make_expected_transition(
                        from_page=(
                            "search"
                            if step_type == "search_select"
                            else "im_main"
                        ),
                        to_page="im_chat",
                        target_name=target,
                    ),
                    "text": signal_text,
                }
        elif step_type == "module_navigation" and not isinstance(signal, dict):
            enriched["success_signal"] = {
                "target_page": "im_main",
                "expected_transition": VisionPlanner._make_expected_transition(
                    to_page="im_main",
                ),
                "text": str(signal or description or "消息页已打开"),
            }
        return enriched

    @staticmethod
    def _extract_message_target(text: str) -> str:
        if not text:
            return ""
        for pattern in (
            r"[「“\"]([^」”\"]{1,20})[」”\"]",
            r"名为[「“\"]?([^」”\"，。:：\s]{1,20})",
            r"(?:打开|切到|切换到|进入|定位|找|找到)([^，。:：\s]{1,20})(?:群聊|聊天|会话)",
            r"进入([^\s，。、“”\"'「」『』]{1,20})(?:群聊|会话|聊天)",
            r"查看消息[-:： ]+([^\s，。、“”\"'「」『』]{1,20})",
            r"(?:打开|切到|切换到|进入|找|找到|定位)([^\s，。、“”\"'「」『』]{1,20})$",
        ):
            match = re.search(pattern, text)
            if match:
                candidate = match.group(1).strip()
                if VisionPlanner._is_valid_message_target(candidate):
                    return candidate
        return ""

    @staticmethod
    def _is_valid_message_target(candidate: str) -> bool:
        if not candidate or len(candidate) > 20:
            return False
        invalid_parts = (
            "消息界面",
            "界面并进入名为",
            "聊天页",
            "群聊",
            "会话",
            "目标",
            "搜索",
            "列表",
            "结果",
            "云文档",
            "文档",
            "日历",
            "会议",
            "邮箱",
        )
        return not any(part in candidate for part in invalid_parts)

    @staticmethod
    def _infer_page_from_text(text: str) -> str:
        if any(keyword in text for keyword in ("消息页", "消息界面", "会话列表")):
            return "im_main"
        if any(keyword in text for keyword in ("聊天页", "群聊", "会话")):
            return "im_chat"
        if "云文档" in text:
            return "docs"
        if any(keyword in text for keyword in ("日历", "日程")):
            return "calendar"
        if any(keyword in text for keyword in ("搜索框", "搜索浮层", "全局搜索")):
            return "search"
        return ""
