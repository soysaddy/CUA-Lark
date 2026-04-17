from perception.ax_inspector import AXInspector


def test_ax_inspector_constructs():
    inspector = AXInspector()
    assert inspector is not None
