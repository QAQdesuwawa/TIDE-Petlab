"""默认产品 Human→NPC direct 会话的不可变装配记录。

创建新 NPC 时，产品层需要一个明确的关系锚，而不是把旧 ``char_id`` 当作会话成员。
此处把 Human、NPC、identity B₀ binding 与 direct descriptor 一次写成一个不可变
identity record。它不创建 Actor、Store、event、projection、记忆、墙钟或 tick；已有
``ProductDirectConversationHost`` 若被产品采用，仍须从这一已登记 identity 显式装配。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import threading
from typing import Mapping

from interaction_extensions.multi_participant.conversations import (
    ConversationDescriptor,
    ConversationSchemaError,
)
from interaction_extensions.multi_participant.participants import (
    Participant,
    ParticipantSchemaError,
)
from interaction_extensions.multi_participant.sessions import (
    ProductDirectConversationSession,
)
from interaction_extensions.preference_calibration.relationship_bindings import (
    HumanNpcB0Binding,
    RelationshipBindingSchemaError,
    bind_human_to_npc,
)
from ops.storage import atomic_write_json


class ProductDefaultDirectProvisionError(ValueError):
    """默认 Human→NPC provision 不符合不可变身份合同。"""


def _required_text(value, label):
    text = str(value or "").strip()
    if not text:
        raise ProductDefaultDirectProvisionError("%s不能为空" % label)
    return text


@dataclass(frozen=True)
class ProductDefaultDirectProvision:
    """一条产品默认私聊的完整 identity 锚，零运行时职责。"""

    human: Participant
    npc: Participant
    session: ProductDirectConversationSession
    binding: HumanNpcB0Binding
    schema_version: int = 1

    def __post_init__(self):
        if self.schema_version != 1:
            raise ProductDefaultDirectProvisionError("不支持的 default direct provision schema_version")
        if not isinstance(self.human, Participant) or self.human.kind != "human":
            raise ProductDefaultDirectProvisionError("default provision 必须引用一名 human")
        if not isinstance(self.npc, Participant) or self.npc.kind != "npc":
            raise ProductDefaultDirectProvisionError("default provision 必须引用一名 npc")
        if not isinstance(self.session, ProductDirectConversationSession):
            raise ProductDefaultDirectProvisionError("default provision 必须引用 direct session")
        if not isinstance(self.binding, HumanNpcB0Binding):
            raise ProductDefaultDirectProvisionError("default provision 必须引用 HumanNpcB0Binding")
        members = self.session.participants_by_id
        if (members.get(self.human.participant_id) != self.human
                or members.get(self.npc.participant_id) != self.npc):
            raise ProductDefaultDirectProvisionError("session 必须精确包含 provision 的 human 与 npc")
        if (self.binding.human_participant_id != self.human.participant_id
                or self.binding.npc_participant_id != self.npc.participant_id):
            raise ProductDefaultDirectProvisionError("binding 必须精确指向 provision 的 human→npc")

    @property
    def conversation_id(self):
        return self.session.conversation.conversation_id

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "kind": "product_default_direct_provision",
            "human": self.human.as_record(),
            "npc": self.npc.as_record(),
            "session": self.session.as_record(),
            "binding": self.binding.as_record(),
        }

    @classmethod
    def from_record(cls, raw):
        if not isinstance(raw, Mapping):
            raise ProductDefaultDirectProvisionError("default direct provision 必须为对象")
        required = {"schema_version", "kind", "human", "npc", "session", "binding"}
        if set(raw) != required or raw.get("kind") != "product_default_direct_provision":
            raise ProductDefaultDirectProvisionError("default direct provision 字段不完整或类型错误")
        try:
            version = int(raw["schema_version"])
            human = Participant.from_record(raw["human"])
            npc = Participant.from_record(raw["npc"])
            session = ProductDirectConversationSession.from_record(raw["session"])
            binding = HumanNpcB0Binding.from_record(raw["binding"])
        except (TypeError, ValueError, ParticipantSchemaError, ConversationSchemaError,
                RelationshipBindingSchemaError) as exc:
            raise ProductDefaultDirectProvisionError("default direct provision 内容无效") from exc
        return cls(human=human, npc=npc, session=session, binding=binding,
                   schema_version=version)


class ProductDefaultDirectProvisionRegistry:
    """默认 direct identity 的单记录持久化 registry。

    每个 provision 自身是一份原子 JSON：session 与 identity B₀ binding 不会被拆成
    由 UI 再拼合的两次业务写入。读取路径绝不回填、补建或推断 legacy profile。
    """

    def __init__(self, base_dir="data", *, durable_writes=True):
        self.base_dir = os.path.abspath(os.fspath(base_dir))
        self.provisions_dir = os.path.abspath(
            os.path.join(self.base_dir, "product_direct_provisions"))
        if os.path.commonpath([self.base_dir, self.provisions_dir]) != self.base_dir:
            raise ProductDefaultDirectProvisionError("provision registry 必须位于 base_dir 内")
        self._durable_writes = bool(durable_writes)
        self._lock = threading.RLock()

    def _path_for(self, conversation_id):
        canonical = _required_text(conversation_id, "conversation_id")
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return os.path.join(self.provisions_dir, digest + ".json")

    def load(self, conversation_id):
        canonical = _required_text(conversation_id, "conversation_id")
        path = self._path_for(canonical)
        if not os.path.isfile(path):
            return None
        with self._lock:
            try:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ProductDefaultDirectProvisionError(
                    "default direct provision 记录不可读取") from exc
        provision = ProductDefaultDirectProvision.from_record(raw)
        if provision.conversation_id != canonical:
            raise ProductDefaultDirectProvisionError("provision 路径与 conversation identity 不一致")
        return provision

    def register(self, provision):
        if not isinstance(provision, ProductDefaultDirectProvision):
            raise ProductDefaultDirectProvisionError("只能登记 ProductDefaultDirectProvision")
        path = self._path_for(provision.conversation_id)
        with self._lock:
            if os.path.isfile(path):
                existing = self.load(provision.conversation_id)
                if existing != provision:
                    raise ProductDefaultDirectProvisionError("default direct provision 不可覆写")
                return existing
            os.makedirs(self.provisions_dir, exist_ok=True)
            atomic_write_json(path, provision.as_record(), durable=self._durable_writes)
        return provision


def create_default_direct_provision(*, human, npc, conversation_id):
    """纯装配默认 Human→NPC direct identity；没有偏好即 identity B₀。"""
    if not isinstance(human, Participant) or not isinstance(npc, Participant):
        raise ProductDefaultDirectProvisionError("default provision 必须引用 Participant")
    conversation = ConversationDescriptor(
        conversation_id=_required_text(conversation_id, "conversation_id"),
        kind="direct",
        active_member_ids=(human.participant_id, npc.participant_id),
        membership_version=1,
    )
    session = ProductDirectConversationSession(
        conversation=conversation, participants=(human, npc))
    return ProductDefaultDirectProvision(
        human=human,
        npc=npc,
        session=session,
        binding=bind_human_to_npc(human, npc),
    )
