import json
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from loguru import logger

from execution.hybrid_executor import HybridExecutor
from execution.recovery import RecoveryManager
from perception.ax_inspector import AXInspector
from perception.screen_capturer import ScreenCapturer
from perception.som_annotator import SoMAnnotator
from perception.vision_client import VisionClient
from scenes.base import State, TaskTemplate
from utils.coord_transform import create_coord_system
from utils.window_manager import WindowManager
from verification.multi_verifier import MultiVerifier


@dataclass
class StepTrace:
    state_name: str
    status: str
    actions_executed: list = field(default_factory=list)
    verification_details: list = field(default_factory=list)
    screenshot_path: str = ""
    duration: float = 0.0
    retry_count: int = 0


@dataclass
class TaskTrace:
    task_name: str
    params: dict
    success: bool = False
    steps: list[StepTrace] = field(default_factory=list)
    total_duration: float = 0.0
    error: str = ""


class FSMEngine:
    def __init__(self, save_dir: str = "runs") -> None:
        self.executor = HybridExecutor()
        self.verifier = MultiVerifier()
        self.capturer = ScreenCapturer()
        self.som = SoMAnnotator()
        self.ax = AXInspector()
        self.vision = VisionClient()
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)

    def run_task(self, template: TaskTemplate, params: dict) -> TaskTrace:
        run_dir = self.save_dir / f"{template.name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        run_dir.mkdir(parents=True, exist_ok=True)
        trace = TaskTrace(task_name=template.name, params=params)
        started_at = time.time()
        current_state_name = template.initial_state

        while current_state_name:
            state = template.states.get(current_state_name)
            if not state:
                trace.error = f"未定义状态: {current_state_name}"
                break
            step_trace = self._execute_state(state, params, run_dir, len(trace.steps) + 1)
            trace.steps.append(step_trace)
            if step_trace.status == "success":
                current_state_name = state.next_state
                if not current_state_name:
                    trace.success = True
                    break
            else:
                if state.fallback_state:
                    current_state_name = state.fallback_state
                    continue
                trace.error = f"状态失败: {state.name}"
                break

        trace.total_duration = time.time() - started_at
        self._persist_trace(run_dir / "trace.json", trace)
        return trace

    def _execute_state(self, state: State, params: dict, run_dir: Path, step_num: int) -> StepTrace:
        step_trace = StepTrace(state_name=state.name, status="pending")
        state_started = time.time()
        for attempt in range(state.max_retries):
            step_trace.retry_count = attempt
            for action in state.entry_actions:
                resolved_action = self._resolve_params(action, params)
                if resolved_action["type"] in ("vision_click", "ax_click_element"):
                    self._update_perception_context()
                success = self.executor.execute(resolved_action)
                step_trace.actions_executed.append({"action": resolved_action, "success": success})
            time.sleep(0.5)
            screen = self.capturer.capture_lark_window()
            if screen:
                path = run_dir / f"step_{step_num:02d}_attempt_{attempt}.png"
                screen["image"].save(path)
                step_trace.screenshot_path = str(path)
            passed, details = self.verifier.check_conditions(state.success_conditions, params)
            step_trace.verification_details = details
            if passed:
                step_trace.status = "success"
                step_trace.duration = time.time() - state_started
                return step_trace
            if state.recovery_actions and attempt < state.max_retries - 1:
                for action in state.recovery_actions:
                    self.executor.execute(self._resolve_params(action, params))
            if time.time() - state_started > state.timeout:
                break
        recovered = RecoveryManager.attempt_recovery(self.executor, state.name)
        step_trace.status = "recovered" if recovered else "failed"
        step_trace.duration = time.time() - state_started
        return step_trace

    def _update_perception_context(self) -> None:
        bounds = WindowManager.get_window_bounds()
        screen = self.capturer.capture_lark_window()
        if not bounds or not screen:
            return
        ax_elements = self.ax.find_elements(max_depth=6)
        _, marks = self.som.annotate(
            screen["image"],
            ax_elements,
            window_offset=(bounds["x"], bounds["y"]),
            raw_screenshot_size=screen["raw_size"],
        )
        coord_sys = create_coord_system(bounds, screen["raw_size"], screen["image"].size)
        self.executor.update_context(coord_sys, marks)

    def _resolve_params(self, obj, params: dict):
        if isinstance(obj, str):
            for key, value in params.items():
                obj = obj.replace(f"{{{key}}}", str(value))
            return obj
        if isinstance(obj, dict):
            return {key: self._resolve_params(value, params) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self._resolve_params(item, params) for item in obj]
        return obj

    @staticmethod
    def _persist_trace(path: Path, trace: TaskTrace) -> None:
        payload = {
            "task": trace.task_name,
            "params": trace.params,
            "success": trace.success,
            "total_duration": trace.total_duration,
            "steps": [
                {
                    "state_name": step.state_name,
                    "status": step.status,
                    "duration": step.duration,
                    "retry_count": step.retry_count,
                    "screenshot_path": step.screenshot_path,
                    "verification_details": step.verification_details,
                }
                for step in trace.steps
            ],
            "error": trace.error,
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
