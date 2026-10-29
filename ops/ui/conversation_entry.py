"""产品会话的只读进入资格。

这个模块只判断桌面端是否已经拥有足以画出会话首屏的事实。它不导入
Runner/Actor/Store，不写入消息、状态、记忆、B₀、墙钟或 tick；后续历史页由展示层
自行只读回填。这样“进入房间”不再误成为离线 catch-up 的完成门槛。
"""
from __future__ import annotations

from typing import Mapping

from ops.conversations import ConversationDescriptor
from ops.fixture_provenance import is_non_dialogue_seed


def _is_committed_displayable_message(row):
    """只识别已经完整落盘、可作为聊天气泡显示的记录。

    Store 的 ``messages.jsonl`` 只在正式 append 时写入完整行，因此旧格式的普通
    user/npc message 也仍是 committed。显式 draft/typing/partial 标记一律 fail-closed；
    背景种子仍可进记忆层，却不能冒充会话首屏的聊天记录。
    """
    if not isinstance(row, Mapping) or is_non_dialogue_seed(dict(row)):
        return False
    if str(row.get("role") or "").strip() not in {"user", "npc"}:
        return False
    if not str(row.get("text") or "").strip():
        return False
    return not any(bool(row.get(field)) for field in (
        "draft", "typing", "partial", "pending", "uncommitted",
    ))


def build_conversation_entry_snapshot(conversation, newest_page, *,
                                      has_older_history=False,
                                      history_complete=False):
    """根据已读的最近页生成纯展示资格 snapshot。

    ``newest_page`` 可以只是 Store/transport 已经到达的最新完整页；不要求 archive
    全部在内存。非空未知历史必须先看到一条 committed 气泡才可进入。明确为空的
    新建会话可以直接进入，且不会为了“凑首条记录”产生任何运行时写入。
    """
    if not isinstance(conversation, ConversationDescriptor):
        raise TypeError("conversation 必须是 ConversationDescriptor")
    rows = list(newest_page or ())
    latest = next((dict(row) for row in reversed(rows)
                   if _is_committed_displayable_message(row)), None)
    if latest is not None:
        return {
            "ready": True,
            "reason": "latest_committed_displayable",
            "conversation_id": conversation.conversation_id,
            "latest_committed": latest,
            "backfill_pending": bool(has_older_history),
        }
    # 新建演示/背景 session 可能已有可 recall 的 non-dialogue seed；它们不是互动
    # 历史，因此“没有 committed 聊天气泡”仍是可确认的空互动首屏。draft/typing/partial
    # 则不具备此资格，不能被当作空会话绕过。
    only_non_dialogue_seed = bool(rows) and all(
        isinstance(row, Mapping) and is_non_dialogue_seed(dict(row)) for row in rows)
    if bool(history_complete) and (not rows or only_non_dialogue_seed):
        return {
            "ready": True,
            "reason": "confirmed_no_committed_interaction",
            "conversation_id": conversation.conversation_id,
            "latest_committed": None,
            "backfill_pending": False,
        }
    return {
        "ready": False,
        "reason": "latest_committed_displayable_missing",
        "conversation_id": conversation.conversation_id,
        "latest_committed": None,
        "backfill_pending": bool(has_older_history),
    }
