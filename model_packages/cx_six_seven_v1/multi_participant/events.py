"""六七包内可选多参与者能力的已提交 Conversation source event 合同。

本模块没有草稿类型：输入框草稿、typing 或未完成模型文本从构造时就不能成为 event，更不能
进入 Router 或本地 Store。所有 event 固化 membership snapshot，未来成员变化不得回溯改写它。
"""
from dataclasses import dataclass
import hashlib
from typing import Mapping, Optional, Tuple

from .conversations import (
    ConversationDescriptor,
    ConversationSchemaError,
    canonical_member_ids,
)


class ConversationEventSchemaError(ValueError):
    """source event 不符合已提交、可投递的最小合同。"""


def _text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ConversationEventSchemaError("%s 不能为空" % label)
    return text


def canonical_npc_output_event_id(*, conversation_id, sender_participant_id,
                                  action_id):
    """Return the one canonical source-event identity for an exact NPC F16.

    This is identity construction only.  It does not inspect text, UI output,
    recent speakers, tick state, or any Actor runtime object.  Both direct and
    group publication use the same function so downstream memory commits can
    resolve the source event from the already-committed local F16 facts.
    """
    conversation = _text(conversation_id, "conversation_id")
    sender = _text(sender_participant_id, "sender_participant_id")
    action = _text(action_id, "action_id")
    material = "\0".join((conversation, sender, action))
    return "evt:npc-output:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


def _recipients(values):
    try:
        return canonical_member_ids(values, label="visible recipient")
    except ConversationSchemaError as exc:
        raise ConversationEventSchemaError(str(exc)) from exc


@dataclass(frozen=True)
class CommittedConversationEvent:
    """历史解释唯一依据的 source event；绝不持有 Actor 本地状态。"""

    event_id: str
    kind: str
    conversation_id: str
    membership_version: int
    member_snapshot_ids: Tuple[str, ...]
    sender_participant_id: str
    committed_at_ms: int
    visible_recipient_ids: Tuple[str, ...]
    text: Optional[str] = None
    # 已提交 message 的对象范围；群聊不能靠正文或 visible 范围猜测它。
    addressed_participant_ids: Optional[Tuple[str, ...]] = None
    # 人类外部输入没有 action；NPC 输出只能由已提交 F16 的 exact action 归档。
    # 它是 source event 因果引用，不是 Actor 状态、tick 或 writer 材料。
    sender_action_id: Optional[str] = None
    before_membership_version: Optional[int] = None
    after_membership_version: Optional[int] = None
    affected_participant_id: Optional[str] = None
    schema_version: int = 1

    def __post_init__(self):
        event_id = _text(self.event_id, "event_id")
        kind = str(self.kind or "").strip().lower()
        if kind not in {"message", "membership_changed"}:
            raise ConversationEventSchemaError("draft 或未知 event 不能提交")
        conversation_id = _text(self.conversation_id, "conversation_id")
        sender = _text(self.sender_participant_id, "sender_participant_id")
        sender_action_id = (None if self.sender_action_id is None
                            else _text(self.sender_action_id, "sender_action_id"))
        if not isinstance(self.committed_at_ms, int) or self.committed_at_ms < 0:
            raise ConversationEventSchemaError("committed_at_ms 必须为非负整数")
        try:
            snapshot = canonical_member_ids(self.member_snapshot_ids, label="member snapshot")
        except ConversationSchemaError as exc:
            raise ConversationEventSchemaError(str(exc)) from exc
        recipients = _recipients(self.visible_recipient_ids)
        addressed = tuple(self.addressed_participant_ids or ())
        if addressed:
            addressed = _recipients(addressed)
        if not isinstance(self.membership_version, int) or self.membership_version < 1:
            raise ConversationEventSchemaError("membership_version 必须为正整数")
        if kind == "message":
            text = _text(self.text, "message text")
            if sender not in snapshot:
                raise ConversationEventSchemaError("message sender 必须在冻结成员快照内")
            if any(item not in snapshot for item in recipients):
                raise ConversationEventSchemaError("message recipient 必须在冻结成员快照内")
            if sender in recipients:
                raise ConversationEventSchemaError("message sender 不能同时作为自身 recipient")
            if not addressed:
                raise ConversationEventSchemaError("message 必须明确 address scope")
            if any(item not in recipients for item in addressed):
                raise ConversationEventSchemaError("addressed participant 必须是可见 recipient")
            if any(value is not None for value in (
                    self.before_membership_version, self.after_membership_version,
                    self.affected_participant_id)):
                raise ConversationEventSchemaError("message 不能携带成员变更字段")
        else:
            text = None
            before = self.before_membership_version
            after = self.after_membership_version
            affected = _text(self.affected_participant_id, "affected_participant_id")
            if not isinstance(before, int) or not isinstance(after, int) or after != before + 1:
                raise ConversationEventSchemaError("成员变更必须携带相邻的前后 membership version")
            if before != self.membership_version - 1:
                raise ConversationEventSchemaError("成员变更的 event version 必须是 after version")
            if sender not in snapshot or affected not in snapshot:
                raise ConversationEventSchemaError("成员变更 sender/affected 必须在旧成员快照内")
            # 允许被移出者收到移出事实，即使他不在变更后的 descriptor 中。
            if any(item not in snapshot for item in recipients):
                raise ConversationEventSchemaError("成员变更 recipient 必须来自旧成员快照")
        if self.schema_version != 1:
            raise ConversationEventSchemaError("不支持的 conversation event schema_version")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "conversation_id", conversation_id)
        object.__setattr__(self, "sender_participant_id", sender)
        object.__setattr__(self, "sender_action_id", sender_action_id)
        object.__setattr__(self, "member_snapshot_ids", snapshot)
        object.__setattr__(self, "visible_recipient_ids", recipients)
        object.__setattr__(self, "addressed_participant_ids", addressed)
        object.__setattr__(self, "text", text)
        if kind == "membership_changed":
            object.__setattr__(self, "affected_participant_id", affected)

    def as_record(self):
        """可由会话层 append 的 source receipt；不等于任一 Actor 的本地 Store 行。"""
        row = {
            "schema_version": self.schema_version,
            "event_id": self.event_id,
            "kind": self.kind,
            "conversation_id": self.conversation_id,
            "membership_version": self.membership_version,
            "member_snapshot_ids": list(self.member_snapshot_ids),
            "sender_participant_id": self.sender_participant_id,
            "sender_action_id": self.sender_action_id,
            "committed_at_ms": self.committed_at_ms,
            "visible_recipient_ids": list(self.visible_recipient_ids),
            "addressed_participant_ids": list(self.addressed_participant_ids),
            "text": self.text,
        }
        if self.kind == "membership_changed":
            row.update({
                "before_membership_version": self.before_membership_version,
                "after_membership_version": self.after_membership_version,
                "affected_participant_id": self.affected_participant_id,
            })
        return row

    @classmethod
    def from_record(cls, raw):
        """严格恢复已提交 source event；不从当前 descriptor 补字段。"""
        if not isinstance(raw, Mapping):
            raise ConversationEventSchemaError("conversation event 必须为对象")
        kind = str(raw.get("kind") or "").strip().lower()
        required = {
            "schema_version", "event_id", "kind", "conversation_id",
            "membership_version", "member_snapshot_ids", "sender_participant_id",
            "sender_action_id", "committed_at_ms", "visible_recipient_ids",
            "addressed_participant_ids", "text",
        }
        if kind == "membership_changed":
            required |= {
                "before_membership_version", "after_membership_version",
                "affected_participant_id",
            }
        if set(raw) != required:
            raise ConversationEventSchemaError("conversation event 字段不完整或包含未知字段")
        try:
            schema_version = int(raw["schema_version"])
            membership_version = int(raw["membership_version"])
            committed_at_ms = int(raw["committed_at_ms"])
        except (TypeError, ValueError) as exc:
            raise ConversationEventSchemaError("conversation event 数值字段必须为整数") from exc
        kwargs = {}
        if kind == "membership_changed":
            try:
                kwargs = {
                    "before_membership_version": int(raw["before_membership_version"]),
                    "after_membership_version": int(raw["after_membership_version"]),
                    "affected_participant_id": raw["affected_participant_id"],
                }
            except (TypeError, ValueError) as exc:
                raise ConversationEventSchemaError("成员变更版本必须为整数") from exc
        return cls(
            event_id=raw["event_id"], kind=kind,
            conversation_id=raw["conversation_id"],
            membership_version=membership_version,
            member_snapshot_ids=tuple(raw["member_snapshot_ids"]),
            sender_participant_id=raw["sender_participant_id"],
            sender_action_id=raw["sender_action_id"],
            committed_at_ms=committed_at_ms,
            visible_recipient_ids=tuple(raw["visible_recipient_ids"]),
            addressed_participant_ids=tuple(raw["addressed_participant_ids"]),
            text=raw["text"], schema_version=schema_version,
            **kwargs,
        )


def commit_message_event(conversation, *, event_id, sender_participant_id, text,
                         committed_at_ms, kind="message", sender_action_id=None,
                         addressed_participant_ids=None):
    """冻结已提交消息；群聊必须显式给出 address，不能从正文猜对象。"""
    if not isinstance(conversation, ConversationDescriptor):
        raise ConversationEventSchemaError("message 必须在 ConversationDescriptor 中提交")
    sender = _text(sender_participant_id, "sender_participant_id")
    if sender not in conversation.active_member_ids:
        raise ConversationEventSchemaError("sender 不在当前 conversation")
    visible = tuple(member for member in conversation.active_member_ids if member != sender)
    if addressed_participant_ids is None and str(kind or "").strip().lower() != "message":
        # 让 immutable event schema 先报告 draft/未知 kind；address 只属于正式 message。
        addressed_participant_ids = ()
    if addressed_participant_ids is None:
        if conversation.kind == "direct":
            addressed_participant_ids = visible
        else:
            raise ConversationEventSchemaError("group message 必须显式提供 address scope")
    return CommittedConversationEvent(
        event_id=event_id,
        kind=kind,
        conversation_id=conversation.conversation_id,
        membership_version=conversation.membership_version,
        member_snapshot_ids=conversation.active_member_ids,
        sender_participant_id=sender,
        sender_action_id=sender_action_id,
        committed_at_ms=committed_at_ms,
        visible_recipient_ids=visible,
        addressed_participant_ids=tuple(addressed_participant_ids),
        text=text,
    )


def commit_membership_removal(conversation, *, event_id, initiator_participant_id,
                              affected_participant_id, committed_at_ms):
    """提交一条成员移出事实，并返回它之后的新 descriptor。

    这条 event 仍投递给被移出者；以后用新 descriptor 提交的普通消息不再含该成员。
    """
    if not isinstance(conversation, ConversationDescriptor):
        raise ConversationEventSchemaError("成员变更必须在 ConversationDescriptor 中提交")
    initiator = _text(initiator_participant_id, "initiator_participant_id")
    affected = _text(affected_participant_id, "affected_participant_id")
    if initiator not in conversation.active_member_ids or affected not in conversation.active_member_ids:
        raise ConversationEventSchemaError("成员变更参与者必须在当前 conversation")
    after_members = tuple(member for member in conversation.active_member_ids if member != affected)
    try:
        after = ConversationDescriptor(
            conversation_id=conversation.conversation_id,
            kind=conversation.kind,
            active_member_ids=after_members,
            membership_version=conversation.membership_version + 1,
        )
    except ConversationSchemaError as exc:
        raise ConversationEventSchemaError("成员变更后的 conversation 无效：%s" % exc) from exc
    event = CommittedConversationEvent(
        event_id=event_id,
        kind="membership_changed",
        conversation_id=conversation.conversation_id,
        membership_version=after.membership_version,
        member_snapshot_ids=conversation.active_member_ids,
        sender_participant_id=initiator,
        committed_at_ms=committed_at_ms,
        visible_recipient_ids=conversation.active_member_ids,
        before_membership_version=conversation.membership_version,
        after_membership_version=after.membership_version,
        affected_participant_id=affected,
    )
    return after, event
