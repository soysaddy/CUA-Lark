from agent.guardrail import Guardrail, GuardrailSignal


def test_guardrail_replan_on_repeated_observation():
    guardrail = Guardrail()
    last = None
    for _ in range(5):
        last = guardrail.check(
            step_num=1,
            decision={
                "observation": "同一个页面状态",
                "confidence": "medium",
                "action": {"type": "wait"},
                "current_page": "im_main",
            },
            verify_result={"step_completed": False},
            history=[],
        )
    assert last.signal == GuardrailSignal.REPLAN
