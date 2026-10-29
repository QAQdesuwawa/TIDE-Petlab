"""P5c：Actor 已提交输出到产品 direct source event 的 exact F16 适配。

这个适配器是 Actor output observer，不是 UI callback、writer 或调度器。它只接受
Actor 的 ``released``／``npc_msg`` 投影，并用其中的 exact action/time 回查发送 NPC
自身 local Store 的已提交 action receipt 与 message；不按正文相似度、source 名或最近
时间猜测。验证完成后才让已有 bridge 追加 source journal，且绝不自动 route/relay。
"""
from __future__ import annotations

from typing import Mapping

from interaction_extensions.multi_participant.events import (
    canonical_npc_output_event_id,
)
from ops.product_conversation_bridge import ProductConversationBridge


class ProductConversationOutputAdapterError(ValueError):
    """Actor output 没有足够的 exact F16 因果证据形成 source event。"""


def _text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ProductConversationOutputAdapterError("%s不能为空" % label)
    return text


class ProductConversationOutputAdapter:
    """只把已 committed 的某一 NPC 输出归档为当前 direct conversation source。

    它不保存自己的 schedule/状态，也不订阅 UI。宿主若选择将其注册为 Actor 的输出
    observer，仍必须只在 Actor 已经 F16 commit 后传入原始 ``released`` 或 ``npc_msg``
    payload；其他输出事件一律忽略。
    """

    _OBSERVED_KINDS = frozenset(("released", "npc_msg"))

    def __init__(self, *, bridge, sender_participant_id, store):
        if not isinstance(bridge, ProductConversationBridge):
            raise ProductConversationOutputAdapterError("output adapter 必须绑定 ProductConversationBridge")
        if bridge.source_journal is None:
            raise ProductConversationOutputAdapterError("output adapter 必须使用持久 source journal")
        sender_id = _text(sender_participant_id, "sender_participant_id")
        participant = bridge.participants.get(sender_id)
        if participant is None or participant.kind != "npc" or not participant.capabilities.has_writer:
            raise ProductConversationOutputAdapterError("sender 必须是当前 direct conversation 的 NPC")
        if not (callable(getattr(store, "read_action_receipts", None))
                and callable(getattr(store, "read_messages", None))):
            raise ProductConversationOutputAdapterError("output adapter 必须读取 NPC local Store")
        self.bridge = bridge
        self.sender_participant_id = sender_id
        self.store = store

    def _event_id_for(self, action_id):
        return canonical_npc_output_event_id(
            conversation_id=self.bridge.conversation.conversation_id,
            sender_participant_id=self.sender_participant_id,
            action_id=action_id,
        )

    @staticmethod
    def _payload_fields(kind, payload):
        if kind not in ProductConversationOutputAdapter._OBSERVED_KINDS:
            return None
        if not isinstance(payload, Mapping):
            raise ProductConversationOutputAdapterError("committed Actor output payload 必须为对象")
        action_id = _text(payload.get("action_id"), "action_id")
        reply = _text(payload.get("reply"), "reply")
        ts_key = "send_at" if kind == "released" else "ts"
        try:
            ts = int(payload[ts_key])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProductConversationOutputAdapterError(
                "%s output 缺少 formal %s" % (kind, ts_key)) from exc
        if ts < 0:
            raise ProductConversationOutputAdapterError("output ts 必须为非负整数")
        return action_id, reply, ts

    def _verify_exact_store_commit(self, *, action_id, reply, ts):
        receipts = list(self.store.read_action_receipts() or ())
        exact_receipts = []
        for row in receipts:
            if str(row.get("action_id") or "") != action_id:
                continue
            try:
                row_ts = int(row.get("ts"))
            except (TypeError, ValueError):
                continue
            if row_ts == ts:
                exact_receipts.append(row)
        if not exact_receipts:
            raise ProductConversationOutputAdapterError("找不到同一 action_id/ts 的 F16 action receipt")
        messages = list(self.store.read_messages() or ())
        exact_messages = []
        for row in messages:
            if row.get("role") != "npc" or str(row.get("action_id") or "") != action_id:
                continue
            try:
                row_ts = int(row.get("ts"))
            except (TypeError, ValueError):
                continue
            if row_ts == ts and str(row.get("text") or "") == reply:
                exact_messages.append(row)
        if not exact_messages:
            raise ProductConversationOutputAdapterError("找不到同一 action_id/ts/text 的已提交 NPC message")
        return {
            "schema_version": 1,
            "kind": "npc_output_committed",
            "action_id": action_id,
            "ts": ts,
        }

    def _existing_exact_source(self, *, event_id, action_id, reply, ts):
        rows = self.bridge.source_journal.read_source_receipts()
        matches = [row for row in rows if row.get("sender_action_id") == action_id]
        if not matches:
            return None
        if len(matches) != 1:
            raise ProductConversationOutputAdapterError("source journal 的 action identity 不唯一")
        source = matches[0]
        event = source.get("source_event") or {}
        if (source.get("event_id") != event_id
                or event.get("sender_participant_id") != self.sender_participant_id
                or event.get("sender_action_id") != action_id
                or event.get("text") != reply
                or int(event.get("committed_at_ms", -1) or -1) != ts):
            raise ProductConversationOutputAdapterError("已归档 action 与当前 F16 payload 不一致")
        return source

    def observe_actor_output(self, kind, payload):
        """观察单条 Actor projection；非 committed output kind 零副作用返回 ``None``。"""
        fields = self._payload_fields(kind, payload)
        if fields is None:
            return None
        action_id, reply, ts = fields
        f16_receipt = self._verify_exact_store_commit(
            action_id=action_id, reply=reply, ts=ts)
        event_id = self._event_id_for(action_id)
        existing = self._existing_exact_source(
            event_id=event_id, action_id=action_id, reply=reply, ts=ts)
        if existing is not None:
            return {"status": "already_committed", "source_receipt": existing}
        try:
            report = self.bridge.commit_npc_output(
                event_id=event_id, sender_participant_id=self.sender_participant_id,
                action_id=action_id, text=reply, committed_at_ms=ts,
                f16_receipt=f16_receipt,
            )
        except ValueError as exc:
            raise ProductConversationOutputAdapterError(str(exc)) from exc
        return {"status": "committed", **report}
