import json

import gradio as gr

from agent.vision_loop import VisionDecisionLoop


def run_task(task_text: str) -> str:
    try:
        result = VisionDecisionLoop().run(task_text)
        payload = {
            "success": result.success,
            "goal": result.goal,
            "steps": len(result.steps),
            "duration": result.total_duration,
            "handoff_required": result.handoff_required,
            "handoff_reason": result.handoff_reason,
            "error": result.error,
            "plan": result.plan,
        }
    except Exception as exc:
        payload = {
            "success": False,
            "goal": task_text,
            "steps": 0,
            "duration": 0,
            "handoff_required": False,
            "handoff_reason": "",
            "error": str(exc),
            "plan": {},
        }
    return json.dumps(payload, ensure_ascii=False, indent=2)


demo = gr.Interface(
    fn=run_task,
    inputs=gr.Textbox(lines=3, label="任务指令"),
    outputs=gr.Code(label="执行结果", language="json"),
    title="CUA-Lark Demo",
    description="输入自然语言指令，调用 Vision 驱动 × AX 增强 × FSM 护栏的飞书自动化执行。",
)


if __name__ == "__main__":
    demo.launch(share=True)
