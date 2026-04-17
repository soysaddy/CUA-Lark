from config import config
from scenes.base import State, TaskTemplate


def build_send_message_task() -> TaskTemplate:
    task = TaskTemplate(
        name="send_message",
        description="在飞书中向指定联系人发送消息",
        parameters=["contact", "message"],
    )

    task.add_state(
        State(
            name="ACTIVATE_LARK",
            description="激活飞书并标准化窗口",
            entry_actions=[
                {"type": "activate_app", "app_name": "Lark"},
                {"type": "wait", "seconds": 1.0},
                {"type": "ensure_window_size"},
            ],
            success_conditions=[
                {"type": "frontmost_app_is", "app_name": "Lark"},
                {"type": "window_size_matches"},
            ],
            recovery_actions=[
                {"type": "shell", "cmd": "open -a Lark"},
                {"type": "wait", "seconds": 3.0},
            ],
            next_state="OPEN_SEARCH",
            max_retries=config.max_retries_per_state,
            timeout=config.state_timeout,
        )
    )
    task.add_state(
        State(
            name="OPEN_SEARCH",
            description="打开全局搜索",
            entry_actions=[
                {"type": "hotkey", "keys": ["command", "k"]},
                {"type": "wait", "seconds": 0.8},
            ],
            success_conditions=[
                {
                    "type": "ax_element_exists",
                    "ax_role": "AXTextField",
                    "ax_description_contains": "搜索",
                    "focused": True,
                }
            ],
            recovery_actions=[
                {"type": "hotkey", "keys": ["escape"]},
                {"type": "wait", "seconds": 0.5},
            ],
            next_state="INPUT_CONTACT",
        )
    )
    task.add_state(
        State(
            name="INPUT_CONTACT",
            description="输入联系人名称",
            entry_actions=[
                {"type": "type_text", "text": "{contact}"},
                {"type": "wait", "seconds": 1.5},
            ],
            success_conditions=[
                {"type": "ax_element_text_contains", "text": "{contact}"},
                {
                    "type": "vision_check",
                    "query": "搜索结果中是否显示了联系人'{contact}'?",
                    "expected": True,
                    "fallback": True,
                },
            ],
            recovery_actions=[
                {"type": "hotkey", "keys": ["command", "a"]},
                {"type": "key_press", "key": "backspace"},
            ],
            next_state="SELECT_CONTACT",
        )
    )
    task.add_state(
        State(
            name="SELECT_CONTACT",
            description="选择联系人",
            entry_actions=[
                {
                    "type": "ax_click_element",
                    "text_contains": "{contact}",
                    "scope": "search_results",
                    "fallback": True,
                }
            ],
            success_conditions=[
                {"type": "ax_element_text_contains", "text": "{contact}"},
                {
                    "type": "vision_check",
                    "query": "当前是否进入了与'{contact}'的聊天窗口?",
                    "expected": True,
                    "fallback": True,
                },
            ],
            recovery_actions=[{"type": "hotkey", "keys": ["escape"]}],
            next_state="WAIT_CHAT_READY",
            fallback_state="OPEN_SEARCH",
        )
    )
    task.add_state(
        State(
            name="WAIT_CHAT_READY",
            description="等待聊天页加载完成",
            entry_actions=[{"type": "wait", "seconds": 1.0}],
            success_conditions=[
                {
                    "type": "ax_element_exists",
                    "ax_role": "AXTextArea",
                    "ax_description_contains": "消息",
                }
            ],
            next_state="INPUT_MESSAGE",
            timeout=5.0,
        )
    )
    task.add_state(
        State(
            name="INPUT_MESSAGE",
            description="输入消息内容",
            entry_actions=[
                {
                    "type": "ax_click_element",
                    "ax_role": "AXTextArea",
                    "ax_description_contains": "消息",
                    "fallback": True,
                },
                {"type": "wait", "seconds": 0.3},
                {"type": "type_text", "text": "{message}"},
            ],
            success_conditions=[
                {
                    "type": "ax_element_value_contains",
                    "ax_role": "AXTextArea",
                    "text": "{message}",
                },
                {
                    "type": "vision_check",
                    "query": "输入框中是否已输入'{message}'?",
                    "expected": True,
                    "fallback": True,
                },
            ],
            next_state="SEND_MESSAGE",
        )
    )
    task.add_state(
        State(
            name="SEND_MESSAGE",
            description="发送消息",
            entry_actions=[
                {"type": "key_press", "key": "return"},
                {"type": "wait", "seconds": 1.0},
            ],
            success_conditions=[
                {"type": "ax_element_value_is_empty", "ax_role": "AXTextArea"},
                {
                    "type": "vision_check",
                    "query": "聊天窗口中是否出现了已发送的消息'{message}'?",
                    "expected": True,
                    "fallback": True,
                },
            ],
            next_state="VERIFY_SENT",
        )
    )
    task.add_state(
        State(
            name="VERIFY_SENT",
            description="综合验证消息发送成功",
            entry_actions=[{"type": "wait", "seconds": 0.5}],
            success_conditions=[
                {
                    "type": "composite_check",
                    "checks": [
                        {"type": "ax_element_value_is_empty", "ax_role": "AXTextArea"},
                        {
                            "type": "vision_check",
                            "query": "消息列表最后一条是否为'{message}'且无发送失败标记?",
                            "expected": True,
                        },
                    ],
                }
            ],
            next_state="DONE",
        )
    )
    task.add_state(
        State(
            name="DONE",
            description="任务完成",
            success_conditions=[{"type": "always_true"}],
        )
    )
    return task
