from scenes.base import PageObject, UIAnchor


def build_lark_calendar_page() -> PageObject:
    page = PageObject(
        name="lark_calendar",
        description="飞书日历页",
        page_indicators=[{"type": "ax_element_text_contains", "text": "日历"}],
    )
    page.add_anchor(
        UIAnchor(
            name="new_event_button",
            description="新建日程按钮",
            ax_role="AXButton",
            visual_description="日历中新建日程按钮",
            relative_position=(0.9, 0.14),
        )
    )
    return page
