import argparse
import subprocess
import sys
import time

from loguru import logger

from perception.ax_inspector import AXInspector
from utils.window_manager import WindowManager

logger.remove()
logger.add(sys.stderr, level="INFO", format="<green>{time:HH:mm:ss}</green> | <level>{message}</level>")

def check_environment() -> bool:
    print("🔍 环境检查...")
    ok = True
    try:
        import pyautogui

        pyautogui.position()
        print("  ✅ 辅助功能权限")
    except Exception:
        print("  ❌ 辅助功能权限 → 系统设置 → 隐私 → 辅助功能")
        ok = False

    try:
        from perception.screen_capturer import ScreenCapturer

        if ScreenCapturer().capture_full_screen():
            print("  ✅ 屏幕录制权限")
        else:
            print("  ❌ 屏幕录制权限 → 系统设置 → 隐私 → 屏幕录制")
            ok = False
    except Exception:
        print("  ❌ 屏幕录制权限 → 系统设置 → 隐私 → 屏幕录制")
        ok = False

    try:
        from PIL import Image  # noqa: F401

        print("  ✅ Pillow 依赖")
    except Exception:
        print("  ❌ 未安装 Pillow")
        ok = False

    ax = AXInspector()
    if not ax.get_lark_app_ref():
        print("  ⚠️ 飞书未运行, 尝试启动...")
        subprocess.run(["open", "-a", "Lark"], check=False)
        time.sleep(3)
        if ax.get_lark_app_ref():
            print("  ✅ 飞书已启动")
        else:
            print("  ❌ 飞书启动失败")
            ok = False
    else:
        print("  ✅ 飞书运行中")

    if WindowManager.ensure_standard_window():
        print("  ✅ 窗口已标准化")
    else:
        diag = WindowManager.diagnose()
        print("  ⚠️ 窗口标准化失败")
        print(f"    应用名: {diag['app_name']}")
        print(f"    当前窗口: {diag['current']}")
        print(f"    目标窗口: {diag['target']}")

    from config import config

    if config.openai_api_key:
        print("  ✅ API Key")
    else:
        print("  ❌ 未设置 OPENAI_API_KEY")
        ok = False

    return ok


def main() -> None:
    parser = argparse.ArgumentParser(description="CUA-Lark Agent")
    parser.add_argument("--task", type=str, help="自然语言任务")
    parser.add_argument("--check", action="store_true", help="环境检查")
    parser.add_argument("--dump-ax", action="store_true", help="打印飞书 AX 树")
    parser.add_argument("--interactive", action="store_true", help="交互模式")
    parser.add_argument("--ui", action="store_true", help="启动极简前台小窗口")
    args = parser.parse_args()

    if args.check:
        sys.exit(0 if check_environment() else 1)

    if args.dump_ax:
        print(AXInspector().dump_tree(max_depth=5))
        return

    if not check_environment():
        print("\n❌ 环境检查未通过")
        sys.exit(1)

    if args.task:
        from agent.vision_loop import VisionDecisionLoop

        agent = VisionDecisionLoop()
        result = agent.run(args.task)
        sys.exit(0 if result.success else 1)

    if args.interactive:
        from agent.vision_loop import VisionDecisionLoop

        agent = VisionDecisionLoop()
        print("\n🤖 CUA-Lark 交互模式 (输入 quit 退出)\n")
        while True:
            task = input("📝 任务: ").strip()
            if task.lower() in ("quit", "exit", "q"):
                break
            if not task:
                continue
            result = agent.run(task)
            print(f"\n{'✅' if result.success else '❌'} 步数:{len(result.steps)} 耗时:{result.total_duration:.1f}s\n")
        return

    if args.ui or not args.task:
        from ui.quick_command_window import main as launch_quick_window

        launch_quick_window()
        return


if __name__ == "__main__":
    main()
