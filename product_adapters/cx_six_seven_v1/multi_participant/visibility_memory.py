"""六七包内多参与者能力的群聊可见消息本地记忆归档适配器。

这一层只记录“成员看见过一条已提交 source”。它不调用 Runner ingress，不写六维状态，
不产生 P3/tick，也不决定回复。定向关系结算必须由独立的 addressed delivery 进入。
"""
from typing import Mapping

from model_packages.cx_six_seven_v1.multi_participant.events import (
    CommittedConversationEvent,
)


class GroupVisibilityMemoryError(ValueError):
    """群聊 visibility archive 不符合不可变 source 合同。"""


def _provenance(event: CommittedConversationEvent, owner_participant_id: str) -> dict:
    owner = str(owner_participant_id or "").strip()
    if event.kind != "message" or owner not in event.member_snapshot_ids:
        raise GroupVisibilityMemoryError("只有当前成员可归档已提交 group message")
    return {
        "event_id": event.event_id,
        "conversation_id": event.conversation_id,
        "membership_version": event.membership_version,
        "owner_participant_id": owner,
        "sender_participant_id": event.sender_participant_id,
        "member_snapshot_ids": list(event.member_snapshot_ids),
        "visible_recipient_ids": list(event.visible_recipient_ids),
        "addressed_participant_ids": list(event.addressed_participant_ids),
    }


def archive_visible_group_message(*, runner, event: CommittedConversationEvent,
                                  owner_participant_id: str) -> Mapping[str, object]:
    """幂等地写 owner 的可见记忆；绝不触及 state/clock/Runner ingress。"""
    if not isinstance(event, CommittedConversationEvent):
        raise GroupVisibilityMemoryError("visibility archive 必须引用已提交 source event")
    provenance = _provenance(event, owner_participant_id)
    store = getattr(runner, "store", None)
    memory = getattr(runner, "memory", None)
    if store is None or memory is None:
        raise GroupVisibilityMemoryError("visibility archive 必须使用本地 Runner 的 Store/Memory")
    for row in store.read_messages():
        seen = row.get("group_visibility_provenance")
        if (isinstance(seen, Mapping)
                and str(seen.get("event_id") or "") == event.event_id
                and str(seen.get("owner_participant_id") or "") == provenance["owner_participant_id"]):
            return {"status": "already_archived", "provenance": provenance}
    memory.remember_message(
        event.committed_at_ms, "group_visible", event.text or "", source="group_visible",
        event_id=event.event_id, group_visibility_provenance=provenance,
        participant_source_kind="npc",
    )
    receipt = {
        "schema_version": 1,
        "kind": "conversation_visibility_memory_committed",
        "event_id": event.event_id,
        "conversation_id": event.conversation_id,
        "membership_version": event.membership_version,
        "owner_participant_id": provenance["owner_participant_id"],
        "sender_participant_id": event.sender_participant_id,
        "addressed_participant_ids": list(event.addressed_participant_ids),
    }
    store.append_event(receipt)
    return {"status": "committed", "provenance": provenance, "receipt": receipt}
