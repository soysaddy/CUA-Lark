from core.fsm_engine import FSMEngine
from scenes.base import State, TaskTemplate


def test_resolve_params():
    engine = FSMEngine(save_dir="runs/test")
    resolved = engine._resolve_params({"text": "{name}"}, {"name": "张三"})
    assert resolved["text"] == "张三"


def test_simple_state_machine():
    task = TaskTemplate(name="demo", description="demo", parameters=[])
    task.add_state(State(name="A", description="A", success_conditions=[{"type": "always_true"}], next_state=""))
    trace = FSMEngine(save_dir="runs/test").run_task(task, {})
    assert trace.steps
