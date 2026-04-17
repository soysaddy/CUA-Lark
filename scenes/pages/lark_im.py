from scenes.anchors import MESSAGE_INPUT, SEARCH_BOX, SEND_BUTTON
from scenes.base import PageObject


def build_lark_im_page() -> PageObject:
    page = PageObject(
        name="lark_im",
        description="飞书聊天页",
        page_indicators=[{"type": "ax_element_exists", "ax_role": "AXTextArea"}],
    )
    for anchor in (SEARCH_BOX, MESSAGE_INPUT, SEND_BUTTON):
        page.add_anchor(anchor)
    return page
