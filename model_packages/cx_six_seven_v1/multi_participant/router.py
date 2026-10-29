"""可选多参与者扩展的已提交 Conversation event 纯路由器。

Router 只把一个已经冻结的 source event 映射为每 recipient 一份 delivery receipt。它不导入
Runner、Store、B₀、writer 或 tick，因而没有能力产生消息、结算根量或修改成员关系。
"""
from dataclasses import dataclass
from typing import Tuple

from .events import (
    CommittedConversationEvent,
)


class ConversationRoutingError(ValueError):
    """Router 只接受已验证的 source event。"""


@dataclass(frozen=True)
class ConversationDelivery:
    """某个 recipient 对一条 source event 的本地投递凭据，尚未写入任何 Store。"""

    delivery_id: str
    source_event_id: str
    conversation_id: str
    membership_version: int
    recipient_participant_id: str
    sender_participant_id: str
    event_kind: str
    delivery_scope: str = "addressed"
    schema_version: int = 1

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "delivery_id": self.delivery_id,
            "source_event_id": self.source_event_id,
            "conversation_id": self.conversation_id,
            "membership_version": self.membership_version,
            "recipient_participant_id": self.recipient_participant_id,
            "sender_participant_id": self.sender_participant_id,
            "event_kind": self.event_kind,
            "delivery_scope": self.delivery_scope,
        }


def route_committed_event(event: CommittedConversationEvent) -> Tuple[ConversationDelivery, ...]:
    """对每个可见 recipient 仅创建一次 delivery，并冻结其 address/observe 身份。"""
    if not isinstance(event, CommittedConversationEvent):
        raise ConversationRoutingError("Router 只能路由已验证的 CommittedConversationEvent")
    return tuple(
        ConversationDelivery(
            delivery_id="delivery:%s:%s" % (event.event_id, recipient_id),
            source_event_id=event.event_id,
            conversation_id=event.conversation_id,
            membership_version=event.membership_version,
            recipient_participant_id=recipient_id,
            sender_participant_id=event.sender_participant_id,
            event_kind=event.kind,
            delivery_scope=("addressed" if recipient_id in event.addressed_participant_ids
                            else "observed"),
        )
        for recipient_id in event.visible_recipient_ids
    )
