from core.fsm_engine import FSMEngine
from core.task_router import TaskRouter
from scenes.tasks.create_doc import build_create_doc_task
from scenes.tasks.create_event import build_create_event_task
from scenes.tasks.send_message import build_send_message_task
from verification.reporter import ReportGenerator


TASK_REGISTRY = {
    "send_message": build_send_message_task,
    "create_event": build_create_event_task,
    "create_doc": build_create_doc_task,
}


class Coordinator:
    def __init__(self) -> None:
        self.router = TaskRouter()
        self.engine = FSMEngine()
        self.reporter = ReportGenerator()

    def run(self, user_input: str) -> dict:
        route_result = self.router.route(user_input)
        if not route_result.get("matched"):
            return {
                "success": False,
                "error": "无法识别任务意图",
                "supported_tasks": list(TASK_REGISTRY.keys()),
            }
        task_name = route_result["task_name"]
        if task_name not in TASK_REGISTRY:
            return {"success": False, "error": f"任务未实现: {task_name}"}
        trace = self.engine.run_task(TASK_REGISTRY[task_name](), route_result["params"])
        report_path = self.reporter.generate(trace)
        return {
            "success": trace.success,
            "task": task_name,
            "params": route_result["params"],
            "steps": len(trace.steps),
            "duration": trace.total_duration,
            "report": report_path,
            "error": trace.error,
        }
