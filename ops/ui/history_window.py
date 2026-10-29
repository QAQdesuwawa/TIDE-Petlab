"""聊天历史的展示窗口；不改变 archive 或任何互动状态。"""


def select_history_window(messages, visible_limit, maximum_limit):
    """返回最近的可见窗口与尚未渲染的条数，输入列表保持不变。"""
    rows = list(messages or ())
    maximum = max(1, int(maximum_limit))
    requested = max(1, int(visible_limit))
    limit = min(requested, maximum)
    return {
        "messages": rows[-limit:],
        "hidden_count": max(0, len(rows) - limit),
        "visible_limit": limit,
        "reached_render_limit": len(rows) > limit and limit >= maximum,
    }
