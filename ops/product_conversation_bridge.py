"""P5 产品会话桥的后端合同。

桥在 UI 之外将 *已提交* 的人类输入变成 source event，再经 Router / projection
投给目标 NPC Actor 的 canonical received-event ingress。它不管理 widget、墙钟、tick、
writer 或自主开口；NPC 输出也只有调用方拿到 exact F16 action receipt 后才可显式归档为
新的 source event，绝不自动回灌。
"""
from __future__ import annotations

from typing import Callable, Mapping, Optional

from interaction_extensions.multi_participant.events import commit_message_event
from interaction_extensions.multi_participant.router import route_committed_event
from interaction_extensions.multi_participant.conversations import (
    ConversationDescriptor,
    frame_for_member,
)
from interaction_extensions.multi_participant.local_projection import project_delivery
from interaction_extensions.multi_participant.participants import Participant
from interaction_extensions.multi_participant.received_events import (
    received_from_projection,
)


class ProductConversationBridgeError(ValueError):
    """产品桥请求没有满足已提交会话合同。"""


def _text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ProductConversationBridgeError("%s不能为空" % label)
    return text


class ProductConversationBridge:
    """一条产品会话的外围 event bridge，完全不持有 NPC 数学状态。

    ``npc_actors`` 只包含具有 local Store / scheduler / writer 的参与者。真实 human
    参与者没有 Actor，因而 NPC 的已发送输出可以写入会话 source journal，却不会被桥
    自动转成另一条入站事务。
    """

    def __init__(self, *, conversation, participants, npc_actors,
                 on_source_receipt: Optional[Callable[[dict], None]] = None,
                 on_delivery_receipt: Optional[Callable[[dict], None]] = None,
                 source_journal=None):
        if not isinstance(conversation, ConversationDescriptor):
            raise ProductConversationBridgeError("产品桥必须绑定 ConversationDescriptor")
        # 现阶段只有单一 human→NPC 产品直接会话的回应语义已定义。群聊中的
        # 多 NPC 响应资格、F16 fan-out 与成员事务尚未裁定，不能借通用 Router
        # 在这里默认为每人都 required reply。
        if conversation.kind != "direct":
            raise ProductConversationBridgeError("P5 后台桥当前只支持 direct conversation")
        if not isinstance(participants, Mapping) or not isinstance(npc_actors, Mapping):
            raise ProductConversationBridgeError("participants 与 npc_actors 必须为映射")
        if set(participants) != set(conversation.active_member_ids):
            raise ProductConversationBridgeError("participants 必须覆盖当前 conversation 成员")
        normalized = {}
        for participant_id in conversation.active_member_ids:
            participant = participants.get(participant_id)
            if not isinstance(participant, Participant) or participant.participant_id != participant_id:
                raise ProductConversationBridgeError("participants 必须按稳定 participant ID 对齐")
            normalized[participant_id] = participant
            actor = npc_actors.get(participant_id)
            if participant.capabilities.receive_projection and participant.capabilities.has_local_store:
                if actor is None or not callable(getattr(
                    actor, "post_compat_received_participant_event", None
                )):
                    raise ProductConversationBridgeError(
                        "NPC Actor 必须提供 compatibility received-event 入口")
            elif actor is not None:
                raise ProductConversationBridgeError("无本地 Store 的 participant 不能绑定 NPC Actor")
        unknown_actors = set(npc_actors) - set(conversation.active_member_ids)
        if unknown_actors:
            raise ProductConversationBridgeError("npc_actors 包含非当前成员")
        if source_journal is not None:
            journal_conversation = getattr(source_journal, "conversation", None)
            append_source = getattr(source_journal, "append_source_event", None)
            if journal_conversation != conversation or not callable(append_source):
                raise ProductConversationBridgeError("source journal 必须精确绑定当前 conversation")
        self.conversation = conversation
        self.participants = normalized
        self.npc_actors = dict(npc_actors)
        self.on_source_receipt = on_source_receipt
        self.on_delivery_receipt = on_delivery_receipt
        self.source_journal = source_journal
        self.source_receipts = []
        self.delivery_receipts = []
        self._event_ids = set()
        self._npc_action_ids = set()

    def _append_source(self, event, *, sender_action_id=None):
        if event.event_id in self._event_ids:
            raise ProductConversationBridgeError("event_id 已提交，不能覆盖历史")
        if sender_action_id != event.sender_action_id:
            raise ProductConversationBridgeError("source action identity 必须来自 event")
        if self.source_journal is not None:
            try:
                receipt = self.source_journal.append_source_event(event)
            except ValueError as exc:
                raise ProductConversationBridgeError(str(exc)) from exc
        else:
            receipt = {
                "schema_version": 1,
                "kind": "conversation_event_committed",
                "event_id": event.event_id,
                "conversation_id": event.conversation_id,
                "membership_version": event.membership_version,
                "event_kind": event.kind,
                "sender_action_id": event.sender_action_id,
                "source_event": event.as_record(),
            }
        self._event_ids.add(event.event_id)
        self.source_receipts.append(receipt)
        if self.on_source_receipt is not None:
            self.on_source_receipt(dict(receipt))
        return receipt

    def _route_to_local_actors(self, event, *, response_policy):
        dispatched = []
        for delivery in route_committed_event(event):
            recipient = self.participants[delivery.recipient_participant_id]
            # human 没有 local Store / Runner，不能把 NPC 的输出伪造成其 F03 输入。
            if not recipient.capabilities.has_local_store:
                continue
            projection = project_delivery(event, delivery)
            # P5 direct 场景只在已经冻结的 source/delivery 上附加本会话的只读事实。
            # 它不进入 Router、local projection、Store 或调度，也不会为 group 预设
            # 多人 response/fan-out 语义。
            received = received_from_projection(
                projection, response_policy=response_policy,
                sender_participant_kind=self.participants[event.sender_participant_id].kind,
                conversation_frame=frame_for_member(self.conversation, recipient.participant_id))
            actor = self.npc_actors[recipient.participant_id]
            actor.post_compat_received_participant_event(
                received, now_ms=event.committed_at_ms, delay_ms=None)
            receipt = {
                "schema_version": 1,
                "kind": "conversation_delivery_dispatched",
                "event_id": event.event_id,
                "conversation_id": event.conversation_id,
                "delivery_id": delivery.delivery_id,
                "membership_version": event.membership_version,
                "sender_participant_id": event.sender_participant_id,
                "sender_participant_kind": self.participants[event.sender_participant_id].kind,
                "preference_evidence": self.participants[event.sender_participant_id].kind == "human",
                "recipient_participant_id": recipient.participant_id,
            }
            self.delivery_receipts.append(receipt)
            if self.on_delivery_receipt is not None:
                self.on_delivery_receipt(dict(receipt))
            dispatched.append(receipt)
        return dispatched

    def submit_external_message(self, *, event_id, sender_participant_id, text, committed_at_ms):
        """唯一的人类产品输入入口；调用前文本须已由 UI 以 Enter 正式提交。"""
        sender_id = _text(sender_participant_id, "sender_participant_id")
        sender = self.participants.get(sender_id)
        if sender is None or not sender.capabilities.submit_external_message:
            raise ProductConversationBridgeError("sender 没有外部提交能力")
        event = commit_message_event(
            self.conversation, event_id=_text(event_id, "event_id"),
            sender_participant_id=sender_id, text=_text(text, "text"),
            committed_at_ms=int(committed_at_ms),
        )
        source_receipt = self._append_source(event)
        return {
            "source_receipt": source_receipt,
            "deliveries": self._route_to_local_actors(event, response_policy="required"),
        }

    def commit_npc_output(self, *, event_id, sender_participant_id, action_id, text,
                          committed_at_ms, f16_receipt):
        """归档一条已 F16 提交的 NPC 输出；不自动 relay 或驱动 tick。

        这里要求调用方给出同一 action 的最小 F16 receipt。桥既不从 UI 文本，也不从
        writer 草稿猜测 ``action_id`` / 时间；真实产品的 Actor output adapter 之后必须按
        exact action receipt 调用本方法。
        """
        sender_id = _text(sender_participant_id, "sender_participant_id")
        sender = self.participants.get(sender_id)
        if sender is None or sender.kind != "npc" or not sender.capabilities.has_writer:
            raise ProductConversationBridgeError("sender 不是当前 conversation 的 NPC")
        normalized_action_id = _text(action_id, "action_id")
        receipt = dict(f16_receipt or {})
        if (receipt.get("kind") != "npc_output_committed"
                or str(receipt.get("action_id") or "") != normalized_action_id
                or int(receipt.get("ts", -1) or -1) != int(committed_at_ms)):
            raise ProductConversationBridgeError("NPC 输出必须携带同一 action 的 F16 receipt")
        if normalized_action_id in self._npc_action_ids:
            raise ProductConversationBridgeError("action_id 已归档，不能重复生成 source event")
        event = commit_message_event(
            self.conversation, event_id=_text(event_id, "event_id"),
            sender_participant_id=sender_id, text=_text(text, "text"),
            committed_at_ms=int(committed_at_ms), sender_action_id=normalized_action_id,
        )
        source_receipt = self._append_source(event, sender_action_id=normalized_action_id)
        self._npc_action_ids.add(normalized_action_id)
        # NPC→NPC 是否作为对端输入，必须由未来的 product conversation coordinator
        # 明确选择；此处零自动回灌，避免 P5 偷偷变成中心轮流发言器。
        return {"source_receipt": source_receipt, "deliveries": []}
