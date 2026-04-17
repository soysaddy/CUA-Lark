from config import config
from scenes.base import State, TaskTemplate


def build_create_event_task() -> TaskTemplate:
    task = TaskTemplate(
        name="create_event",
        description="在飞书日历中创建事件",
        parameters=["title", "date", "time_start", "time_end", "attendees"],
    )

    task.add_state(
        State(
            name="ACTIVATE_LARK",
            description="激活飞书",
            entry_actions=[
                {"type": "activate_app", "app_name": "Lark"},
                {"type": "ensure_window_size"},
            ],
            success_conditions=[{"type": "frontmost_app_is", "app_name": "Lark"}],
            next_state="OPEN_CALENDAR",
            max_retries=config.max_retries_per_state,
            timeout=config.state_timeout,
        )
    )
    task.add_state(
        State(
            name="OPEN_CALENDAR",
            description="切换到日历",
            entry_actions=[
                {"type": "hotkey", "keys": ["command", "2"]},
                {"type": "wait", "seconds": 1.0},
            ],
            success_conditions=[
                {"type": "ax_element_text_contains", "text": "日历"},
                {
                    "type": "vision_check",
                    "query": "当前是否在飞书日历页面?",
                    "expected": True,
                    "fallback": True,
                },
            ],
            next_state="CREATE_EVENT",
        )
    )
    task.add_state(
        State(
            name="CREATE_EVENT",
            description="打开新建日程弹窗",
            entry_actions=[
                {
                    "type": "vision_click",
                    "instruction": "点击飞书日历中的新建日程按钮",
                    "fallback": True,
                },
                {"type": "wait", "seconds": 1.0},
            ],
            success_conditions=[
                {"type": "vision_check", "query": "当前是否打开了新建日程弹窗?", "expected": True}
            ],
            next_state="FILL_EVENT",
        )
    )
    task.add_state(
        State(
            name="FILL_EVENT",
            description="填写会议信息",
            entry_actions=[
                {"type": "type_text", "text": "{title}"},
                {"type": "wait", "seconds": 0.5},
            ],
            success_conditions=[
                {
                    "type": "vision_check",
                    "query": "新建日程表单中是否已包含标题'{title}'?",
                    "expected": True,
                }
            ],
            next_state="SAVE_EVENT",
        )
    )
    task.add_state(
        State(
            name="SAVE_EVENT",
            description="保存日程",
            entry_actions=[
                {"type": "key_press", "key": "return"},
                {"type": "wait", "seconds": 1.0},
            ],
            success_conditions=[
                {
                    "type": "vision_check",
                    "query": "日历中是否出现了标题为'{title}'的新日程?",
                    "expected": True,
                }
            ],
            next_state="DONE",
        )
    )
    task.add_state(State(name="DONE", description="任务完成", success_conditions=[{"type": "always_true"}]))
    return task
