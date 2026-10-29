"""P5f-2 的只读 conversation source→delivery/F03 追溯。

这不是 relay、恢复器或运行时观察器。它只从已经 append 的 source journal 和调用者显式
提供的 recipient-local Store receipt，按冻结的 sender/action 与 delivery reference 做精确
联结。缺失和重复都保留为诊断事实，绝不按时间、正文、队列或 UI 猜补。
"""
from __future__ import annotations

from typing import Mapping

from interaction_extensions.multi_participant.events import (
    CommittedConversationEvent,
)
from interaction_extensions.multi_participant.router import route_committed_event
from ops.product_conversation_journal import ProductConversationSourceJournal
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)


class ProductConversationRelayTraceError(ValueError):
    """只读 trace 缺少精确的 source identity 或本地 receipt 可见性。"""


def _text(value, label):
    result = str(value or "").strip()
    if not result:
        raise ProductConversationRelayTraceError("%s不能为空" % label)
    return result


def _status(count):
    if count == 1:
        return "exact"
    if count == 0:
        return "missing"
    return "ambiguous"


def _delivery_reference(event, delivery):
    """复用 canonical ingress 的 exact lineage 形状，而非创造一份近似 key。"""
    return {
        "event_id": event.event_id,
        "conversation_id": event.conversation_id,
        "delivery_id": delivery.delivery_id,
        "sender_participant_id": event.sender_participant_id,
        "membership_version": event.membership_version,
    }


def _matching_receipts(store, *, kind, reference):
    """仅检查调用方已有 Store 行；不投递、不跑 Actor、更不修改任意记录。"""
    rows = list(store.read_events() or ())
    return tuple(
        row for row in rows
        if isinstance(row, Mapping)
        and row.get("kind") == kind
        and row.get("delivery_reference") == reference
    )


def _trace_diagnostic(recipient_id, delivery_id, label, status):
    if status == "exact":
        return None
    return {
        "code": "%s_%s" % (label, status),
        "recipient_participant_id": recipient_id,
        "delivery_id": delivery_id,
    }


def build_product_conversation_relay_trace(*, registry, conversation_id,
                                           sender_participant_id, action_id,
                                           recipient_stores):
    """构建某一 generic source action 的只读 recipient 追溯视图。

    source key 固定为 ``(conversation_id, sender_participant_id, action_id)``；这是
    generic conversation 中 local action ID 的真实作用域。结果没有 source text、prompt 或
    Store 原行，只报告 source、预计 delivery 和各本地 actor 的 receipt 是否一一对应。
    """
    if not isinstance(registry, ProductConversationSessionRegistry):
        raise ProductConversationRelayTraceError(
            "relay trace 必须使用 ProductConversationSessionRegistry")
    if not isinstance(recipient_stores, Mapping):
        raise ProductConversationRelayTraceError("recipient_stores必须为映射")
    canonical_conversation_id = _text(conversation_id, "conversation_id")
    sender_id = _text(sender_participant_id, "sender_participant_id")
    sender_action_id = _text(action_id, "action_id")
    session = registry.load_session(canonical_conversation_id)
    if session is None:
        raise ProductConversationRelayTraceError("relay trace 只接受已登记 conversation-session identity")
    participants = session.participants_by_id
    if sender_id not in participants:
        raise ProductConversationRelayTraceError("sender_participant_id不属于已登记 session")

    journal = ProductConversationSourceJournal(
        registry=registry, conversation_id=canonical_conversation_id)
    source_matches = tuple(
        row for row in journal.read_source_receipts()
        if row.get("sender_action_id") == sender_action_id
        and (row.get("source_event") or {}).get("sender_participant_id") == sender_id
    )
    if len(source_matches) != 1:
        raise ProductConversationRelayTraceError(
            "source journal 必须存在唯一 sender/action_id identity")
    source_receipt = source_matches[0]
    event = CommittedConversationEvent.from_record(source_receipt["source_event"])
    deliveries = route_committed_event(event)

    local_recipient_ids = tuple(
        delivery.recipient_participant_id for delivery in deliveries
        if participants[delivery.recipient_participant_id].capabilities.has_local_store
    )
    if set(recipient_stores) != set(local_recipient_ids):
        raise ProductConversationRelayTraceError(
            "recipient_stores必须精确覆盖当前 delivery 的本地 recipient")
    for recipient_id in local_recipient_ids:
        if not callable(getattr(recipient_stores[recipient_id], "read_events", None)):
            raise ProductConversationRelayTraceError("recipient local Store 必须提供 read_events")

    trace_deliveries = []
    diagnostics = []
    for delivery in deliveries:
        recipient_id = delivery.recipient_participant_id
        recipient = participants[recipient_id]
        if not recipient.capabilities.has_local_store:
            trace_deliveries.append({
                "recipient_participant_id": recipient_id,
                "delivery_id": delivery.delivery_id,
                "local_actor_trace": "not_applicable",
                "delivery_receipt_status": "not_applicable",
                "f03_receipt_status": "not_applicable",
            })
            continue
        reference = _delivery_reference(event, delivery)
        delivery_status = _status(len(_matching_receipts(
            recipient_stores[recipient_id],
            kind="conversation_delivery_committed", reference=reference)))
        f03_status = _status(len(_matching_receipts(
            recipient_stores[recipient_id],
            kind="input_measurement_committed", reference=reference)))
        trace_deliveries.append({
            "recipient_participant_id": recipient_id,
            "delivery_id": delivery.delivery_id,
            "local_actor_trace": "store_read_only",
            "delivery_receipt_status": delivery_status,
            "f03_receipt_status": f03_status,
        })
        for label, status in (("delivery_receipt", delivery_status),
                              ("f03_receipt", f03_status)):
            diagnostic = _trace_diagnostic(recipient_id, delivery.delivery_id, label, status)
            if diagnostic is not None:
                diagnostics.append(diagnostic)

    return {
        "schema_version": 1,
        "read_only": True,
        "join_policy": "exact_conversation_sender_action_then_exact_delivery_reference_only",
        "source": {
            "event_id": event.event_id,
            "conversation_id": event.conversation_id,
            "membership_version": event.membership_version,
            "sender_participant_id": event.sender_participant_id,
            "sender_action_id": event.sender_action_id,
            "committed_at_ms": event.committed_at_ms,
            "visible_recipient_ids": list(event.visible_recipient_ids),
        },
        "deliveries": trace_deliveries,
        "diagnostics": diagnostics,
    }
