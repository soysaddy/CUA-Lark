from scenes.anchors import SEARCH_BOX
from scenes.base import PageObject


def build_lark_main_page() -> PageObject:
    page = PageObject(
        name="lark_main",
        description="飞书主界面",
        page_indicators=[
            {"type": "window_title_contains", "text": "飞书"},
            {"type": "window_title_contains", "text": "Lark", "fallback": True},
        ],
    )
    page.add_anchor(SEARCH_BOX)
    return page
