"""P5a-2/P5f-2：已登记 conversation-session 的 append-only source-event journal。

它只保留已经发生的 Conversation source fact 与其 exact event/action identity。journal
不是任一 Actor 的 ``UserStore``，也不是 scheduler 或恢复队列：读取它不能生成投递、F03、
tick、F16、记忆选择或 UI 行为。
"""
from __future__ import annotations

import json
import os
import threading
from typing import Mapping

from interaction_extensions.multi_participant.events import (
    CommittedConversationEvent,
    ConversationEventSchemaError,
)
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)


class ProductConversationSourceJournalError(ValueError):
    """source journal 无法保持已登记会话的不可变外围事实。"""


class ProductConversationSourceJournal:
    """单一已登记 Conversation 的 source-event append-only journal。

    不存在 registered identity 时构造失败；这使“读一个旧 profile 就顺便建立会话”的
    路径在 API 层直接不可用。journal 只接受当前 immutable descriptor 当下冻结的 message
    snapshot。它不创建成员变更、recovery、relay 或 Actor 投递；group 的输出路由必须由
    外部的显式事务调用，不能因 journal append 自动发生。
    """

    _RECEIPT_KEYS = {
        "schema_version", "kind", "event_id", "conversation_id",
        "membership_version", "event_kind", "sender_action_id", "source_event",
    }

    def __init__(self, *, registry, conversation_id):
        if not isinstance(registry, ProductConversationSessionRegistry):
            raise ProductConversationSourceJournalError("source journal 必须使用 session registry")
        session = registry.load_session(conversation_id)
        if session is None:
            raise ProductConversationSourceJournalError("source journal 只接受已登记 conversation-session identity")
        self.session = session
        self.conversation = session.conversation
        self.conversation_id = session.conversation.conversation_id
        self.path = registry.source_journal_path(self.conversation_id)
        self._durable_writes = registry._durable_writes
        self._lock = threading.RLock()

    def _validate_event(self, event):
        if not isinstance(event, CommittedConversationEvent):
            raise ProductConversationSourceJournalError("journal 只接受 CommittedConversationEvent")
        if event.kind != "message":
            raise ProductConversationSourceJournalError("source journal 当前只接受 message")
        conversation = self.conversation
        if (event.conversation_id != conversation.conversation_id
                or event.membership_version != conversation.membership_version
                or event.member_snapshot_ids != conversation.active_member_ids):
            raise ProductConversationSourceJournalError("source event 不属于已登记 conversation-session snapshot")
        if event.sender_participant_id not in conversation.active_member_ids:
            raise ProductConversationSourceJournalError("source event sender 不属于已登记 session")
        return event

    @staticmethod
    def _receipt_for(event):
        return {
            "schema_version": 1,
            "kind": "conversation_event_committed",
            "event_id": event.event_id,
            "conversation_id": event.conversation_id,
            "membership_version": event.membership_version,
            "event_kind": event.kind,
            "sender_action_id": event.sender_action_id,
            "source_event": event.as_record(),
        }

    def _validate_receipt(self, raw):
        if not isinstance(raw, Mapping) or set(raw) != self._RECEIPT_KEYS:
            raise ProductConversationSourceJournalError("source journal receipt 字段无效")
        if raw.get("schema_version") != 1 or raw.get("kind") != "conversation_event_committed":
            raise ProductConversationSourceJournalError("source journal receipt schema 无效")
        try:
            event = CommittedConversationEvent.from_record(raw["source_event"])
        except (ConversationEventSchemaError, TypeError) as exc:
            raise ProductConversationSourceJournalError("source journal event 无效") from exc
        self._validate_event(event)
        expected = self._receipt_for(event)
        if dict(raw) != expected:
            raise ProductConversationSourceJournalError("source journal receipt 与 event 不一致")
        return expected

    def read_source_receipts(self):
        """只读审计入口；不会 route、投递、回放或调用 Actor。"""
        if not os.path.isfile(self.path):
            return ()
        with self._lock:
            try:
                with open(self.path, "r", encoding="utf-8-sig") as handle:
                    raw_rows = [json.loads(line) for line in handle if line.strip()]
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ProductConversationSourceJournalError("source journal 不可读取") from exc
        seen_events = set()
        # action_id 是各 Actor own local Store 内的 append-only identity；generic
        # conversation 不得把另一发送者的同编号 action 误判为重复。
        seen_sender_actions = set()
        rows = []
        for raw in raw_rows:
            receipt = self._validate_receipt(raw)
            event_id = receipt["event_id"]
            action_id = receipt["sender_action_id"]
            sender_id = (receipt.get("source_event") or {}).get("sender_participant_id")
            if event_id in seen_events:
                raise ProductConversationSourceJournalError("source journal 存在重复 event_id")
            sender_action_key = (sender_id, action_id)
            if action_id is not None and sender_action_key in seen_sender_actions:
                raise ProductConversationSourceJournalError("source journal 存在重复 sender/action_id identity")
            seen_events.add(event_id)
            if action_id is not None:
                seen_sender_actions.add(sender_action_key)
            rows.append(receipt)
        return tuple(rows)

    def append_source_event(self, event):
        """追加一次已提交 source fact；不 route，也不取得 delivery 完成的所有权。"""
        event = self._validate_event(event)
        with self._lock:
            existing = self.read_source_receipts()
            if any(row["event_id"] == event.event_id for row in existing):
                raise ProductConversationSourceJournalError("event_id 已在 source journal 提交")
            if (event.sender_action_id is not None
                    and any(
                        row["sender_action_id"] == event.sender_action_id
                        and (row.get("source_event") or {}).get("sender_participant_id")
                        == event.sender_participant_id
                        for row in existing
                    )):
                raise ProductConversationSourceJournalError(
                    "sender/action_id identity 已在 source journal 提交")
            receipt = self._receipt_for(event)
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            try:
                with open(self.path, "a", encoding="utf-8", newline="") as handle:
                    handle.write(json.dumps(receipt, ensure_ascii=False) + "\n")
                    handle.flush()
                    if self._durable_writes:
                        os.fsync(handle.fileno())
            except OSError as exc:
                raise ProductConversationSourceJournalError("source journal 写入失败") from exc
        return receipt
