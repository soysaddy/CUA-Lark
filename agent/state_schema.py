from typing import Any


CANONICAL_PAGES = {
    "im_main",
    "im_chat",
    "calendar",
    "docs",
    "search",
    "unknown",
    "current",
}

PAGE_ALIASES = {
    "消息": "im_main",
    "消息页": "im_main",
    "消息界面": "im_main",
    "消息模块": "im_main",
    "会话列表": "im_main",
    "im_main": "im_main",
    "聊天": "im_chat",
    "聊天页": "im_chat",
    "聊天界面": "im_chat",
    "群聊": "im_chat",
    "私聊": "im_chat",
    "会话": "im_chat",
    "im_chat": "im_chat",
    "日历": "calendar",
    "日历页": "calendar",
    "日历页面": "calendar",
    "calendar": "calendar",
    "云文档": "docs",
    "文档": "docs",
    "文档页": "docs",
    "文档页面": "docs",
    "docs": "docs",
    "搜索": "search",
    "搜索页": "search",
    "搜索浮层": "search",
    "搜索框": "search",
    "search": "search",
    "unknown": "unknown",
    "other": "unknown",
}


def normalize_page_id(value: Any, allow_current: bool = False) -> str:
    text = str(value or "").strip()
    if not text:
        return "unknown"
    lowered = text.lower()
    if lowered in CANONICAL_PAGES:
        if lowered == "current" and not allow_current:
            return "unknown"
        return lowered
    if text in PAGE_ALIASES:
        page = PAGE_ALIASES[text]
        if page == "current" and not allow_current:
            return "unknown"
        return page
    if lowered in PAGE_ALIASES:
        page = PAGE_ALIASES[lowered]
        if page == "current" and not allow_current:
            return "unknown"
        return page
    return "unknown"


def build_expected_transition(
    to_page: str,
    from_page: str = "current",
    target_name: str = "",
    text: str = "",
) -> dict[str, str]:
    normalized_to = normalize_page_id(to_page)
    normalized_from = normalize_page_id(from_page, allow_current=True)
    if normalized_from == "unknown" and str(from_page or "").strip().lower() == "current":
        normalized_from = "current"
    return {
        "from": normalized_from or "current",
        "to": normalized_to or "unknown",
        "target_page": normalized_to or "unknown",
        "target_name": str(target_name or "").strip(),
        "text": str(text or "").strip(),
    }


def normalize_expected_transition(value: Any, fallback_text: str = "") -> dict[str, str]:
    if isinstance(value, dict):
        return build_expected_transition(
            to_page=value.get("target_page") or value.get("to") or value.get("page") or "unknown",
            from_page=value.get("from") or "current",
            target_name=value.get("target_name") or "",
            text=value.get("text") or fallback_text,
        )

    text = str(value or fallback_text or "").strip()
    if "->" in text:
        left, right = text.split("->", 1)
        return build_expected_transition(
            to_page=right.strip(),
            from_page=left.strip() or "current",
            text=text,
        )

    page = normalize_page_id(text)
    if page != "unknown":
        return build_expected_transition(
            to_page=page,
            from_page="current",
            text=text,
        )

    return build_expected_transition(
        to_page="unknown",
        from_page="current",
        text=text,
    )


def target_page_from_transition(value: Any) -> str:
    return normalize_expected_transition(value).get("target_page", "unknown")


def page_satisfies_target(current_page: str, target_page: str) -> bool:
    current = normalize_page_id(current_page)
    target = normalize_page_id(target_page)
    if current == "unknown" or target == "unknown":
        return False
    if target == "im_main":
        return current in {"im_main", "im_chat"}
    return current == target
