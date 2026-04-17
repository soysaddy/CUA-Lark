from config import config
from scenes.base import State, TaskTemplate


def build_create_doc_task() -> TaskTemplate:
    task = TaskTemplate(
        name="create_doc",
        description="在飞书中新建文档",
        parameters=["title", "content"],
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
            next_state="OPEN_DOCS",
            max_retries=config.max_retries_per_state,
            timeout=config.state_timeout,
        )
    )
    task.add_state(
        State(
            name="OPEN_DOCS",
            description="切换到文档页",
            entry_actions=[
                {"type": "vision_click", "instruction": "点击侧边栏中的文档入口", "fallback": True},
                {"type": "wait", "seconds": 1.0},
            ],
            success_conditions=[
                {"type": "ax_element_text_contains", "text": "文档"},
                {
                    "type": "vision_check",
                    "query": "当前是否在飞书文档页面?",
                    "expected": True,
                    "fallback": True,
                },
            ],
            next_state="CREATE_DOC",
        )
    )
    task.add_state(
        State(
            name="CREATE_DOC",
            description="新建空白文档",
            entry_actions=[
                {"type": "vision_click", "instruction": "点击新建文档按钮", "fallback": True},
                {"type": "wait", "seconds": 1.0},
            ],
            success_conditions=[
                {"type": "vision_check", "query": "当前是否已打开空白文档编辑页?", "expected": True}
            ],
            next_state="FILL_DOC",
        )
    )
    task.add_state(
        State(
            name="FILL_DOC",
            description="填写标题和内容",
            entry_actions=[
                {"type": "type_text", "text": "{title}\n\n{content}"},
                {"type": "wait", "seconds": 0.8},
            ],
            success_conditions=[
                {
                    "type": "vision_check",
                    "query": "文档中是否已包含标题'{title}'和正文'{content}'?",
                    "expected": True,
                }
            ],
            next_state="DONE",
        )
    )
    task.add_state(State(name="DONE", description="任务完成", success_conditions=[{"type": "always_true"}]))
    return task
