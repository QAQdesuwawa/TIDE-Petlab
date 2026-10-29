"""多参与者扩展由 source event + delivery 形成的本地事实投影。

本模块不持有 Store，也不写文件。它只确保未来的 Store adapter 不会把 shared history 或按时间
邻近猜出的记录写入某 NPC：每一条本地事实必须同时携带精确 source event 与 delivery 证据。
"""
from dataclasses import dataclass
from typing import Optional, Tuple

from .events import (
    CommittedConversationEvent,
)
from .router import ConversationDelivery


class LocalProjectionError(ValueError):
    """delivery 与 source event 不能形成合法的本地投影。"""


@dataclass(frozen=True)
class LocalProjection:
    """可被后续 Store adapter 原样写入的 recipient-local 事实。"""

    projection_id: str
    delivery_id: str
    source_event_id: str
    conversation_id: str
    membership_version: int
    recipient_participant_id: str
    sender_participant_id: str
    sender_action_id: Optional[str]
    event_kind: str
    committed_at_ms: int
    member_snapshot_ids: Tuple[str, ...]
    visible_recipient_ids: Tuple[str, ...]
    addressed_participant_ids: Tuple[str, ...]
    delivery_scope: str
    text: Optional[str]
    schema_version: int = 1

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "projection_id": self.projection_id,
            "delivery_id": self.delivery_id,
            "source_event_id": self.source_event_id,
            "conversation_id": self.conversation_id,
            "membership_version": self.membership_version,
            "recipient_participant_id": self.recipient_participant_id,
            "sender_participant_id": self.sender_participant_id,
            "sender_action_id": self.sender_action_id,
            "event_kind": self.event_kind,
            "committed_at_ms": self.committed_at_ms,
            "member_snapshot_ids": list(self.member_snapshot_ids),
            "visible_recipient_ids": list(self.visible_recipient_ids),
            "addressed_participant_ids": list(self.addressed_participant_ids),
            "delivery_scope": self.delivery_scope,
            "text": self.text,
        }


def project_delivery(event: CommittedConversationEvent, delivery: ConversationDelivery, *,
                     recipient_participant_id=None) -> LocalProjection:
    """验证 exact delivery 后构造本地 projection；调用者仍需显式决定是否写 Store。"""
    if not isinstance(event, CommittedConversationEvent):
        raise LocalProjectionError("projection 必须来自 CommittedConversationEvent")
    if not isinstance(delivery, ConversationDelivery):
        raise LocalProjectionError("projection 必须来自 ConversationDelivery")
    if (delivery.source_event_id != event.event_id
            or delivery.conversation_id != event.conversation_id
            or delivery.membership_version != event.membership_version
            or delivery.sender_participant_id != event.sender_participant_id
            or delivery.event_kind != event.kind
            or delivery.delivery_scope not in {"addressed", "observed"}):
        raise LocalProjectionError("delivery 与 source event 不匹配")
    recipient = delivery.recipient_participant_id
    requested = None if recipient_participant_id is None else str(recipient_participant_id or "").strip()
    if requested is not None and requested != recipient:
        raise LocalProjectionError("delivery recipient 与请求 recipient 不一致")
    if recipient not in event.visible_recipient_ids:
        raise LocalProjectionError("delivery recipient 不在 source event 的可见范围")
    expected_scope = "addressed" if recipient in event.addressed_participant_ids else "observed"
    if delivery.delivery_scope != expected_scope:
        raise LocalProjectionError("delivery address scope 与 source event 不一致")
    return LocalProjection(
        projection_id="projection:%s" % delivery.delivery_id,
        delivery_id=delivery.delivery_id,
        source_event_id=event.event_id,
        conversation_id=event.conversation_id,
        membership_version=event.membership_version,
        recipient_participant_id=recipient,
        sender_participant_id=event.sender_participant_id,
        sender_action_id=event.sender_action_id,
        event_kind=event.kind,
        committed_at_ms=event.committed_at_ms,
        member_snapshot_ids=event.member_snapshot_ids,
        visible_recipient_ids=event.visible_recipient_ids,
        addressed_participant_ids=event.addressed_participant_ids,
        delivery_scope=delivery.delivery_scope,
        text=event.text,
    )
