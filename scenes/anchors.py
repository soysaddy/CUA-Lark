from scenes.base import UIAnchor


SEARCH_BOX = UIAnchor(
    name="search_box",
    description="飞书顶部搜索框",
    ax_role="AXTextField",
    visual_description="顶部搜索输入框",
    relative_position=(0.18, 0.05),
)

MESSAGE_INPUT = UIAnchor(
    name="message_input",
    description="消息输入框",
    ax_role="AXTextArea",
    visual_description="聊天底部消息输入框",
    relative_position=(0.5, 0.92),
)

SEND_BUTTON = UIAnchor(
    name="send_button",
    description="发送按钮",
    ax_role="AXButton",
    visual_description="消息发送按钮",
    relative_position=(0.93, 0.92),
)

NEW_DOC_BUTTON = UIAnchor(
    name="new_doc_button",
    description="新建文档按钮",
    ax_role="AXButton",
    visual_description="新建文档按钮",
    relative_position=(0.88, 0.12),
)
