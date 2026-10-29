"""六七包内可选多参与者能力的 Conversation 成员版本与只读场景合同。

Conversation 只描述“此刻谁在场”。它不保存任何角色根状态、B₀、记忆候选或调度结果；
历史可见性必须由已提交 event 的冻结 snapshot 解释，而不是回看当前成员名单。
"""
from dataclasses import dataclass
from typing import Iterable, Mapping, Tuple


class ConversationSchemaError(ValueError):
    """Conversation descriptor 或只读 frame 不符合合同。"""


def canonical_member_ids(values: Iterable[str], *, label="成员") -> Tuple[str, ...]:
    if isinstance(values, (str, bytes)):
        raise ConversationSchemaError("%s 必须是 ID 序列" % label)
    result = []
    for value in values or ():
        participant_id = str(value or "").strip()
        if not participant_id:
            raise ConversationSchemaError("%s ID 不能为空" % label)
        if participant_id in result:
            raise ConversationSchemaError("%s ID 不能重复：%s" % (label, participant_id))
        result.append(participant_id)
    if not result:
        raise ConversationSchemaError("%s不能为空" % label)
    return tuple(result)


@dataclass(frozen=True)
class ConversationDescriptor:
    """当前 membership version 的纯描述；它不解释任何历史 event。"""

    conversation_id: str
    kind: str
    active_member_ids: Tuple[str, ...]
    membership_version: int
    schema_version: int = 1

    def __post_init__(self):
        conversation_id = str(self.conversation_id or "").strip()
        if not conversation_id:
            raise ConversationSchemaError("conversation_id 不能为空")
        kind = str(self.kind or "").strip().lower()
        if kind not in {"direct", "group"}:
            raise ConversationSchemaError("conversation kind 只能是 direct 或 group")
        members = canonical_member_ids(self.active_member_ids)
        if kind == "direct" and len(members) != 2:
            raise ConversationSchemaError("direct conversation 必须恰有两名成员")
        if kind == "group" and len(members) < 2:
            raise ConversationSchemaError("group conversation 至少有两名成员")
        if not isinstance(self.membership_version, int) or self.membership_version < 1:
            raise ConversationSchemaError("membership_version 必须为正整数")
        if self.schema_version != 1:
            raise ConversationSchemaError("不支持的 conversation schema_version")
        object.__setattr__(self, "conversation_id", conversation_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "active_member_ids", members)

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "conversation_id": self.conversation_id,
            "kind": self.kind,
            "active_member_ids": list(self.active_member_ids),
            "membership_version": self.membership_version,
        }

    @classmethod
    def from_record(cls, raw):
        """严格恢复 descriptor；读取不会由当前成员状态重解释历史。"""
        if not isinstance(raw, Mapping):
            raise ConversationSchemaError("conversation descriptor 必须为对象")
        required = {
            "schema_version", "conversation_id", "kind", "active_member_ids",
            "membership_version",
        }
        if set(raw) != required:
            raise ConversationSchemaError("conversation descriptor 字段不完整或包含未知字段")
        try:
            schema_version = int(raw["schema_version"])
            membership_version = int(raw["membership_version"])
        except (TypeError, ValueError) as exc:
            raise ConversationSchemaError("conversation descriptor 版本必须为整数") from exc
        return cls(
            conversation_id=raw["conversation_id"],
            kind=raw["kind"],
            active_member_ids=tuple(raw["active_member_ids"]),
            membership_version=membership_version,
            schema_version=schema_version,
        )


@dataclass(frozen=True)
class ConversationFrame:
    """一次 ingress/output 可读取的场景事实，绝不是角色状态或行为脚本。"""

    conversation_id: str
    membership_version: int
    active_member_ids: Tuple[str, ...]
    focal_participant_id: str
    audience_participant_ids: Tuple[str, ...]
    # address 是本次已提交 source 的事实；audience 仅表示群内其余可见成员。
    # 两者绝不能互相推导或替代。
    addressed_participant_ids: Tuple[str, ...] = ()
    schema_version: int = 1

    def __post_init__(self):
        members = canonical_member_ids(self.active_member_ids)
        focal = str(self.focal_participant_id or "").strip()
        audience = canonical_member_ids(self.audience_participant_ids, label="audience")
        addressed = tuple(self.addressed_participant_ids or ())
        if addressed:
            addressed = canonical_member_ids(addressed, label="addressed participant")
        if focal not in members:
            raise ConversationSchemaError("focal participant 不在当前 conversation")
        if any(item not in members for item in audience):
            raise ConversationSchemaError("audience 必须属于当前 conversation")
        if any(item not in members for item in addressed):
            raise ConversationSchemaError("addressed participant 必须属于当前 conversation")
        if self.schema_version != 1:
            raise ConversationSchemaError("不支持的 conversation frame schema_version")
        object.__setattr__(self, "active_member_ids", members)
        object.__setattr__(self, "focal_participant_id", focal)
        object.__setattr__(self, "audience_participant_ids", audience)
        object.__setattr__(self, "addressed_participant_ids", addressed)

    @property
    def focal_is_addressed(self):
        """只读标记；不能据它创建 F03、P3 或任何状态。"""
        return self.focal_participant_id in self.addressed_participant_ids

    def as_record(self):
        """只读场景 frame 的无正文快照，可精确附着到同一 delivery/prompt receipt。"""
        record = {
            "schema_version": self.schema_version,
            "conversation_id": self.conversation_id,
            "membership_version": self.membership_version,
            "active_member_ids": list(self.active_member_ids),
            "focal_participant_id": self.focal_participant_id,
            "audience_participant_ids": list(self.audience_participant_ids),
        }
        # direct frame 的受话者早已由“两人唯一对端”表达；保持其持久记录形状。
        # group 才必须显式写出 address，避免把 audience 误当对象范围。
        if self.addressed_participant_ids and len(self.active_member_ids) > 2:
            record["addressed_participant_ids"] = list(self.addressed_participant_ids)
            record["focal_is_addressed"] = self.focal_is_addressed
        return record


@dataclass(frozen=True)
class ReadOnlyConversationObserverView:
    """会话外观看者的只读场景投影。

    ``observer_id`` 是 access principal，而不是 Conversation member / Participant event
    sender。它仅可让展示层知道当前可见的成员快照；绝不能传给 Router、local
    projection、ConversationFrame、Runner、memory 或 tick。
    """

    conversation_id: str
    membership_version: int
    active_member_ids: Tuple[str, ...]
    observer_id: str
    schema_version: int = 1

    def __post_init__(self):
        conversation_id = str(self.conversation_id or "").strip()
        observer_id = str(self.observer_id or "").strip()
        members = canonical_member_ids(self.active_member_ids)
        if not conversation_id:
            raise ConversationSchemaError("conversation_id 不能为空")
        if not observer_id:
            raise ConversationSchemaError("observer_id 不能为空")
        if observer_id in members:
            raise ConversationSchemaError("互动成员不能被重解释为会话外观察者")
        if not isinstance(self.membership_version, int) or self.membership_version < 1:
            raise ConversationSchemaError("membership_version 必须为正整数")
        if self.schema_version != 1:
            raise ConversationSchemaError("不支持的 observer view schema_version")
        object.__setattr__(self, "conversation_id", conversation_id)
        object.__setattr__(self, "observer_id", observer_id)
        object.__setattr__(self, "active_member_ids", members)

    def as_record(self):
        """只读 UI/access 投影；不包含 source、delivery 或任意运行时状态。"""
        return {
            "schema_version": self.schema_version,
            "kind": "read_only",
            "conversation_id": self.conversation_id,
            "membership_version": self.membership_version,
            "active_member_ids": list(self.active_member_ids),
            "observer_id": self.observer_id,
        }


def frame_for_member(conversation: ConversationDescriptor, participant_id: str,
                     *, addressed_participant_ids=()):
    """根据当前 descriptor 创建只读 frame；不读取历史也不写 Store。"""
    if not isinstance(conversation, ConversationDescriptor):
        raise ConversationSchemaError("必须从 ConversationDescriptor 创建 frame")
    participant_id = str(participant_id or "").strip()
    if not addressed_participant_ids and conversation.kind == "direct":
        # direct 的唯一 incoming message 必然 address 本 focal；群聊则绝不补猜。
        addressed_participant_ids = (participant_id,)
    audience = tuple(member for member in conversation.active_member_ids if member != participant_id)
    return ConversationFrame(
        conversation_id=conversation.conversation_id,
        membership_version=conversation.membership_version,
        active_member_ids=conversation.active_member_ids,
        focal_participant_id=participant_id,
        audience_participant_ids=audience,
        addressed_participant_ids=tuple(addressed_participant_ids or ()),
    )


def observer_view_for(conversation: ConversationDescriptor, observer_id: str):
    """从 immutable descriptor 生成会话外只读观看投影，不写任何互动事实。"""
    if not isinstance(conversation, ConversationDescriptor):
        raise ConversationSchemaError("必须从 ConversationDescriptor 创建 observer view")
    return ReadOnlyConversationObserverView(
        conversation_id=conversation.conversation_id,
        membership_version=conversation.membership_version,
        active_member_ids=conversation.active_member_ids,
        observer_id=observer_id,
    )
