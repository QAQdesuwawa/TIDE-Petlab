"""聊天记录的时间标记：纯展示投影，不参与墙钟或记忆计算。"""
from __future__ import annotations

import time
from typing import Optional


def _as_millis(value) -> Optional[int]:
    """将存储时间戳收敛为毫秒；坏记录不应让聊天界面崩溃。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def format_day(ts_ms) -> Optional[str]:
    """本地日历日标签，使用与界面时分相同的本地时区口径。"""
    ts = _as_millis(ts_ms)
    if ts is None:
        return None
    try:
        return time.strftime("%Y-%m-%d", time.localtime(ts / 1000))
    except (OverflowError, OSError, ValueError):
        return None


def markers_for_message(ts_ms, previous_ts_ms, *, gap_ms: int) -> dict:
    """给一条消息决定应显示的日期／时分分隔。

    日历日改变一定显示日期；同一天内仍沿用既有的长间隔时分标记。这里不修改
    任意事件时间，也不向时间总线写入“跨日”事件，避免 UI 反过来影响互动状态。
    """
    current = _as_millis(ts_ms)
    previous = _as_millis(previous_ts_ms)
    day = format_day(current)
    previous_day = format_day(previous)
    if current is None:
        return {"date": None, "show_time": False}
    return {
        "date": day if day and day != previous_day else None,
        "show_time": previous is None or current - previous > int(gap_ms),
    }
