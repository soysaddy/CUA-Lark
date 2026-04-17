from agent.planner import VisionPlanner


def test_planner_fallback_send_message():
    planner = VisionPlanner()
    result = planner._fallback_plan("给张三发消息：明天下午3点开会")
    assert result["feasible"] is True
    assert result["steps"]
