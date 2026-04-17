from utils.coord_transform import CoordSystem


def test_som_to_pyautogui():
    coord = CoordSystem(10, 20, 100, 100, 2, 200, 200, 100, 100)
    assert coord.som_to_pyautogui(50, 50) == (60, 70)


def test_ax_center_to_pyautogui():
    assert CoordSystem.ax_center_to_pyautogui((100, 200), (40, 20)) == (120, 210)
