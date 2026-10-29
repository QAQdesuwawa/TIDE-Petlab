"""多参与者扩展 canonical ingress 的“已收到参与者事件”合同。

它把 Router 已投递的本地 projection 与 legacy 入口区分开：只有前者会携带精确
event/conversation/delivery receipt 引用。legacy adapter 明确保持 unscoped，绝不伪造一条
群聊 delivery 来让旧历史看上去像新会话。
"""
from dataclasses import dataclass
from typing import Optional

from .conversations import ConversationFrame
from .local_projection import LocalProjection


class ReceivedParticipantEventError(ValueError):
    """canonical received-event ingress 的数据合同错误。"""


_PARTICIPANT_KINDS = frozenset(("human", "npc", "simulation"))


def _incoming_event_kind(sender_participant_kind):
    """同一 received-event 根链的来源标签，不是另一套调度公式。

    ``L_self/L_opponent`` 是 recipient-local 关系信息轴。因此任何对端已提交
    message 都是 input，和 own F16 output 的 ``npc_output`` 方向不同；具体可写轴
    由 semantic projection 的 event-kind contract 限制。
    """
    kind = str(sender_participant_kind or "").strip().lower()
    return {
        "human": "human_input",
        "npc": "peer_input",
        "simulation": "simulation_input",
    }.get(kind, "")


def _text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ReceivedParticipantEventError("%s 不能为空" % label)
    return text


@dataclass(frozen=True)
class ReceivedParticipantEvent:
    """一个已抵达某 Runner 的外部事实，而不是 draft、状态或回复计划。"""

    text: str
    response_policy: str
    source_kind: str
    sender_participant_kind: str
    preference_evidence: bool
    projection: Optional[LocalProjection] = None
    conversation_frame: Optional[ConversationFrame] = None
    schema_version: int = 1

    def __post_init__(self):
        text = _text(self.text, "received text")
        policy = str(self.response_policy or "").strip().lower()
        if policy not in {"required", "eligible"}:
            raise ReceivedParticipantEventError("response_policy 只能是 required 或 eligible")
        source_kind = str(self.source_kind or "").strip().lower()
        if source_kind not in {"delivery", "legacy_external", "legacy_peer"}:
            raise ReceivedParticipantEventError("未知 received source_kind")
        sender_kind = str(self.sender_participant_kind or "").strip().lower()
        if sender_kind not in _PARTICIPANT_KINDS:
            raise ReceivedParticipantEventError("sender_participant_kind 必须是 human/npc/simulation")
        if not isinstance(self.preference_evidence, bool):
            raise ReceivedParticipantEventError("preference_evidence 必须为布尔值")
        if sender_kind == "npc" and self.preference_evidence:
            raise ReceivedParticipantEventError("NPC peer 不能默认充当真人偏好证据")
        if source_kind == "legacy_external" and sender_kind != "human":
            raise ReceivedParticipantEventError("legacy external 必须是 human 来源")
        if source_kind == "legacy_peer" and sender_kind != "npc":
            raise ReceivedParticipantEventError("legacy peer 必须是 npc 来源")
        if source_kind == "delivery":
            if not isinstance(self.projection, LocalProjection):
                raise ReceivedParticipantEventError("delivery received event 必须携带 LocalProjection")
            if self.projection.event_kind != "message" or self.projection.text != text:
                raise ReceivedParticipantEventError("delivery received event 必须对应同一 message projection")
            if self.projection.delivery_scope != "addressed":
                raise ReceivedParticipantEventError("仅 addressed delivery 可以进入 Runner/F03")
            if self.conversation_frame is not None:
                frame = self.conversation_frame
                if not isinstance(frame, ConversationFrame):
                    raise ReceivedParticipantEventError("conversation_frame 必须为 ConversationFrame")
                expected_audience = tuple(
                    participant_id for participant_id in frame.active_member_ids
                    if participant_id != frame.focal_participant_id)
                if (frame.conversation_id != self.projection.conversation_id
                        or frame.membership_version != self.projection.membership_version
                        or frame.active_member_ids != self.projection.member_snapshot_ids
                        or frame.focal_participant_id != self.projection.recipient_participant_id
                        or frame.audience_participant_ids != expected_audience
                        or frame.addressed_participant_ids != self.projection.addressed_participant_ids
                        or not frame.focal_is_addressed
                        or self.projection.sender_participant_id not in frame.audience_participant_ids):
                    raise ReceivedParticipantEventError(
                        "conversation_frame 必须精确对应同一 delivery 的成员/recipient")
        elif self.projection is not None:
            raise ReceivedParticipantEventError("legacy received event 不能伪造 projection")
        elif self.conversation_frame is not None:
            raise ReceivedParticipantEventError("legacy received event 不能伪造 conversation_frame")
        if self.schema_version != 1:
            raise ReceivedParticipantEventError("不支持的 received event schema_version")
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "response_policy", policy)
        object.__setattr__(self, "source_kind", source_kind)
        object.__setattr__(self, "sender_participant_kind", sender_kind)

    @property
    def interaction_event_kind(self):
        """供 F03/P3/F08/semantic receipt 使用的真实已提交输入来源。"""
        return _incoming_event_kind(self.sender_participant_kind)

    def receipt_reference(self):
        """只返回原始 delivery 已经证明的 exact lineage；没有则明确为 None。"""
        if self.projection is None:
            return None
        return {
            "event_id": self.projection.source_event_id,
            "conversation_id": self.projection.conversation_id,
            "delivery_id": self.projection.delivery_id,
            "sender_participant_id": self.projection.sender_participant_id,
            "membership_version": self.projection.membership_version,
        }

    def delivery_receipt(self, *, turn_id, received_at_ms, params_sha):
        """构造 Runner 本地的外围 delivery receipt；不写状态也不代表 source event 再次提交。"""
        reference = self.receipt_reference()
        if reference is None:
            return None
        return {
            "schema_version": 1,
            "kind": "conversation_delivery_committed",
            "turn_id": str(turn_id),
            "ts": int(received_at_ms),
            "delivery_reference": reference,
            "recipient_participant_id": self.projection.recipient_participant_id,
            "source_committed_at_ms": int(self.projection.committed_at_ms),
            "sender_participant_kind": self.sender_participant_kind,
            "response_policy": self.response_policy,
            "preference_evidence": self.preference_evidence,
            "params_sha": str(params_sha),
            **({"conversation_frame": self.conversation_frame.as_record()}
               if self.conversation_frame is not None else {}),
        }

    def memory_provenance(self):
        """供 recipient-local Store 保留的冻结来源事实；不含任何当前状态或 recall。"""
        reference = self.receipt_reference()
        if reference is None:
            return None
        return {
            **reference,
            "recipient_participant_id": self.projection.recipient_participant_id,
            "visible_recipient_ids": list(self.projection.visible_recipient_ids),
            "member_snapshot_ids": list(self.projection.member_snapshot_ids),
        }


def received_from_projection(projection: LocalProjection, *, response_policy,
                             sender_participant_kind, preference_evidence=None,
                             conversation_frame=None):
    if not isinstance(projection, LocalProjection):
        raise ReceivedParticipantEventError("canonical delivery 必须来自 LocalProjection")
    if projection.event_kind != "message" or projection.text is None:
        raise ReceivedParticipantEventError("只有已投递 message 才能进入 Runner")
    sender_kind = str(sender_participant_kind or "").strip().lower()
    if sender_kind not in _PARTICIPANT_KINDS:
        raise ReceivedParticipantEventError("canonical delivery 必须提供 sender_participant_kind")
    if preference_evidence is None:
        preference_evidence = sender_kind == "human"
    return ReceivedParticipantEvent(
        text=projection.text,
        response_policy=response_policy,
        source_kind="delivery",
        sender_participant_kind=sender_kind,
        preference_evidence=bool(preference_evidence),
        projection=projection,
        conversation_frame=conversation_frame,
    )


def legacy_external_input(text):
    """旧 `on_user_message` 适配器：保留功能，但不伪造 Conversation delivery。"""
    return ReceivedParticipantEvent(
        text=text,
        response_policy="required",
        source_kind="legacy_external",
        sender_participant_kind="human",
        preference_evidence=True,
    )


def legacy_peer_input(text):
    """旧 `on_peer_message` 适配器：保留原有 eligible 直接回应语义。"""
    return ReceivedParticipantEvent(
        text=text,
        response_policy="eligible",
        source_kind="legacy_peer",
        sender_participant_kind="npc",
        preference_evidence=False,
    )
