from scenes.anchors import NEW_DOC_BUTTON
from scenes.base import PageObject


def build_lark_docs_page() -> PageObject:
    page = PageObject(
        name="lark_docs",
        description="飞书文档页",
        page_indicators=[{"type": "ax_element_text_contains", "text": "文档"}],
    )
    page.add_anchor(NEW_DOC_BUTTON)
    return page
