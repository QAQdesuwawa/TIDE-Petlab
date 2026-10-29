"""可选多参与者扩展的产品 conversation 不可变身份登记。

这里保存的是显式创建的 participant / conversation descriptor，不是角色的
``UserStore``，也不是会话 event journal。通用 session 只证明谁实际属于哪一个
direct/group Conversation；它不能由旧 ``profile.json`` 推断，不能创建 source event、
群聊调度或角色生活，也不会触碰 Actor、Runner、墙钟、记忆、B0 或任何公式状态。

``ProductDirectConversationSession`` 保留为已定义 human→NPC direct host 的窄 identity
类型。通用登记不使 bridge/journal/host 自动获得 group 或 NPC-only 输出语义。
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import os
import threading
from typing import Any, Callable, Mapping, Tuple

from model_packages.cx_six_seven_v1.multi_participant.conversations import (
    ConversationDescriptor,
    ConversationSchemaError,
)
from model_packages.cx_six_seven_v1.multi_participant.object_projection import (
    ParticipantProjectionError,
    require_participant_projection,
)
from ops.storage import atomic_write_json


class ProductConversationSessionError(ValueError):
    """产品 conversation session identity 不符合不可变登记合同。"""


def _conversation_id(value):
    result = str(value or "").strip()
    if not result:
        raise ProductConversationSessionError("conversation_id 不能为空")
    return result


def _canonical_session_participants(conversation, participants, *, label):
    """校验并按 descriptor 顺序冻结成员；不解释其行为或关系状态。"""
    if not isinstance(conversation, ConversationDescriptor):
        raise ProductConversationSessionError("%s必须引用 ConversationDescriptor" % label)
    if isinstance(participants, (str, bytes)):
        raise ProductConversationSessionError("participants 必须为 Participant 序列")
    participants = tuple(participants or ())
    if len(participants) != len(conversation.active_member_ids):
        raise ProductConversationSessionError("participants 必须与 conversation 成员一一对应")
    by_id = {}
    for participant in participants:
        try:
            require_participant_projection(participant, label="session participant")
        except ParticipantProjectionError as exc:
            raise ProductConversationSessionError(
                "participants 必须与 conversation 成员一一对应") from exc
        if participant.participant_id in by_id:
            raise ProductConversationSessionError("session participant 不能重复")
        by_id[participant.participant_id] = participant
    if set(by_id) != set(conversation.active_member_ids):
        raise ProductConversationSessionError("participants 必须与 conversation 成员精确一致")
    return tuple(by_id[participant_id]
                 for participant_id in conversation.active_member_ids)


@dataclass(frozen=True)
class ProductConversationSession:
    """任意已显式创建会话的纯身份锚。

    它允许合法的 direct 或 group descriptor、human/NPC/simulation 参与者组合；成员
    是否能够发送、接收、进入哪个 Actor、如何形成 source event 或是否有输出资格，均
    不属于此对象。它也不持有 B0、关系 binding、文本或根状态。
    """

    conversation: ConversationDescriptor
    participants: Tuple[Any, ...]
    schema_version: int = 1

    def __post_init__(self):
        if self.schema_version != 1:
            raise ProductConversationSessionError("不支持的 product conversation schema_version")
        ordered = _canonical_session_participants(
            self.conversation, self.participants, label="product session ")
        object.__setattr__(self, "participants", ordered)

    @property
    def participants_by_id(self):
        """调用方得到副本；不能通过此映射改写 session identity。"""
        return {participant.participant_id: participant for participant in self.participants}

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "kind": "product_conversation_session",
            "conversation": self.conversation.as_record(),
            "participants": [participant.as_record() for participant in self.participants],
        }

    @classmethod
    def from_record(cls, raw, *, participant_from_record=None):
        if not isinstance(raw, Mapping):
            raise ProductConversationSessionError("conversation session identity 必须为对象")
        required = {"schema_version", "kind", "conversation", "participants"}
        if set(raw) != required:
            raise ProductConversationSessionError("conversation session identity 字段不完整或包含未知字段")
        if raw.get("kind") != "product_conversation_session":
            raise ProductConversationSessionError("不是 product conversation session identity")
        try:
            schema_version = int(raw["schema_version"])
            conversation = ConversationDescriptor.from_record(raw["conversation"])
            serialized_participants = raw["participants"]
            if isinstance(serialized_participants, (str, bytes)):
                raise ProductConversationSessionError("participants 必须为记录序列")
            decoder = participant_from_record or _legacy_participant_from_record
            participants = tuple(decoder(item)
                                 for item in serialized_participants)
        except (ConversationSchemaError, ParticipantProjectionError,
                TypeError, ValueError) as exc:
            raise ProductConversationSessionError("conversation session identity 内容无效") from exc
        return cls(conversation=conversation, participants=participants,
                   schema_version=schema_version)


@dataclass(frozen=True)
class ProductDirectConversationSession:
    """一个已显式创建的产品 direct-session 身份锚。

    ``participants`` 的顺序会被规范成 descriptor 的 active-member 顺序。该对象不包含
    文本、B0、root state 或任意 event；因此它只回答“哪两名参与者属于哪一个 direct
    conversation”，不回答它们说过什么或下一步要不要说。
    """

    conversation: ConversationDescriptor
    participants: Tuple[Any, ...]
    schema_version: int = 1

    def __post_init__(self):
        if not isinstance(self.conversation, ConversationDescriptor):
            raise ProductConversationSessionError("direct session 必须引用 ConversationDescriptor")
        if self.conversation.kind != "direct":
            raise ProductConversationSessionError("产品 session 当前只支持 direct conversation")
        if self.schema_version != 1:
            raise ProductConversationSessionError("不支持的 product direct-session schema_version")
        ordered = _canonical_session_participants(
            self.conversation, self.participants, label="direct session ")
        kinds = {participant.kind for participant in ordered}
        if kinds != {"human", "npc"}:
            raise ProductConversationSessionError("产品 direct session 必须是一名 human 与一个 npc")
        object.__setattr__(self, "participants", ordered)

    @property
    def participants_by_id(self):
        """调用方得到副本；不能通过此映射改写 session identity。"""
        return {participant.participant_id: participant for participant in self.participants}

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "kind": "product_direct_conversation_session",
            "conversation": self.conversation.as_record(),
            "participants": [participant.as_record() for participant in self.participants],
        }

    @classmethod
    def from_record(cls, raw, *, participant_from_record=None):
        if not isinstance(raw, Mapping):
            raise ProductConversationSessionError("direct-session identity 必须为对象")
        required = {"schema_version", "kind", "conversation", "participants"}
        if set(raw) != required:
            raise ProductConversationSessionError("direct-session identity 字段不完整或包含未知字段")
        if raw.get("kind") != "product_direct_conversation_session":
            raise ProductConversationSessionError("不是 product direct-session identity")
        try:
            schema_version = int(raw["schema_version"])
        except (TypeError, ValueError) as exc:
            raise ProductConversationSessionError("direct-session schema_version 必须为整数") from exc
        try:
            conversation = ConversationDescriptor.from_record(raw["conversation"])
            serialized_participants = raw["participants"]
            if isinstance(serialized_participants, (str, bytes)):
                raise ProductConversationSessionError("participants 必须为记录序列")
            decoder = participant_from_record or _legacy_participant_from_record
            participants = tuple(decoder(item)
                                 for item in serialized_participants)
        except (ConversationSchemaError, ParticipantProjectionError,
                TypeError, ValueError) as exc:
            raise ProductConversationSessionError("direct-session identity 内容无效") from exc
        return cls(conversation=conversation, participants=participants,
                   schema_version=schema_version)


def _legacy_participant_from_record(raw):
    """Compatibility decoder for existing six/seven product records."""
    from model_packages.cx_six_seven_v1.participants import Participant
    return Participant.from_record(raw)


class ProductConversationSessionRegistry:
    """只登记显式创建的 conversation-session identity 的本地 registry。

    conversation ID 永远不会直接成为文件名：路径由它的 SHA-256 地址决定，读取后再
    校验 record 中的 ID。没有登记的 legacy profile 只会返回 ``None``，不会在 read
    path 中创建目录、补 participant 或改写角色档案。

    与 ``UserStore`` 一样，本实现只保证同一 registry 实例内的串行登记；多进程同时
    登记同一个新会话不是本切片的产品合同。
    """

    def __init__(self, base_dir="data", *, durable_writes=True,
                 participant_from_record: Callable[[Mapping[str, Any]], Any] | None = None):
        self.base_dir = os.path.abspath(os.fspath(base_dir))
        self.sessions_dir = os.path.abspath(os.path.join(self.base_dir, "conversations"))
        if os.path.commonpath([self.base_dir, self.sessions_dir]) != self.base_dir:
            raise ProductConversationSessionError("conversation registry 必须位于 base_dir 内")
        self._durable_writes = bool(durable_writes)
        self._participant_from_record = (
            participant_from_record or _legacy_participant_from_record)
        self._lock = threading.RLock()

    def _path_for(self, conversation_id):
        canonical = _conversation_id(conversation_id)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return os.path.join(self.sessions_dir, digest + ".json")

    def source_journal_path(self, conversation_id):
        """返回同一 identity 的 source journal 路径；本方法不创建目录或事件。"""
        canonical = _conversation_id(conversation_id)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return os.path.join(self.sessions_dir, digest + ".events.jsonl")

    def output_route_journal_path(self, conversation_id):
        """返回 canonical output route journal 路径；不执行路由或投递。"""
        canonical = _conversation_id(conversation_id)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return os.path.join(self.sessions_dir, digest + ".output-routes.jsonl")

    def output_lifecycle_journal_path(self, conversation_id):
        """返回 output lifecycle 恢复日志路径；不恢复或执行 action。"""
        canonical = _conversation_id(conversation_id)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return os.path.join(
            self.sessions_dir, digest + ".output-lifecycles.jsonl")

    def recipient_dispatch_journal_path(self, conversation_id):
        """返回 route→recipient continuation 日志路径；不执行投递。"""
        canonical = _conversation_id(conversation_id)
        digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        return os.path.join(
            self.sessions_dir, digest + ".recipient-dispatches.jsonl")

    def load_session(self, conversation_id):
        """只读加载已登记 generic/direct identity；缺锚不推断、不回填。"""
        canonical = _conversation_id(conversation_id)
        path = self._path_for(canonical)
        if not os.path.isfile(path):
            return None
        with self._lock:
            try:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ProductConversationSessionError("conversation-session identity 记录不可读取") from exc
        kind = raw.get("kind") if isinstance(raw, Mapping) else None
        if kind == "product_direct_conversation_session":
            session = ProductDirectConversationSession.from_record(
                raw, participant_from_record=self._participant_from_record)
        elif kind == "product_conversation_session":
            session = ProductConversationSession.from_record(
                raw, participant_from_record=self._participant_from_record)
        else:
            raise ProductConversationSessionError("未知 product conversation session identity")
        if session.conversation.conversation_id != canonical:
            raise ProductConversationSessionError("conversation-session identity 路径与内容不一致")
        return session

    def load_direct_session(self, conversation_id):
        """只读加载已登记的 human→NPC direct identity；不重释通用 session。"""
        session = self.load_session(conversation_id)
        if session is None:
            return None
        if not isinstance(session, ProductDirectConversationSession):
            raise ProductConversationSessionError("已登记 identity 不是 product direct-session")
        return session

    def registered_sessions(self):
        """精确枚举已登记 identity；不从 journal 或目录名猜 ID。"""
        if not os.path.isdir(self.sessions_dir):
            return ()
        sessions = []
        with self._lock:
            names = tuple(sorted(os.listdir(self.sessions_dir)))
        for name in names:
            if not name.endswith(".json"):
                continue
            path = os.path.join(self.sessions_dir, name)
            try:
                with open(path, "r", encoding="utf-8-sig") as handle:
                    raw = json.load(handle)
            except (OSError, ValueError, json.JSONDecodeError) as exc:
                raise ProductConversationSessionError(
                    "conversation-session identity 记录不可读") from exc
            if not isinstance(raw, Mapping):
                raise ProductConversationSessionError(
                    "conversation-session identity 记录必须为对象")
            conversation = raw.get("conversation")
            if not isinstance(conversation, Mapping):
                raise ProductConversationSessionError(
                    "conversation-session identity 缺少 conversation")
            canonical = _conversation_id(conversation.get("conversation_id"))
            if os.path.abspath(path) != os.path.abspath(self._path_for(canonical)):
                raise ProductConversationSessionError(
                    "conversation-session identity 路径与内容不一致")
            sessions.append(self.load_session(canonical))
        return tuple(sorted(
            sessions,
            key=lambda item: item.conversation.conversation_id,
        ))

    def _register_session(self, session):
        path = self._path_for(session.conversation.conversation_id)
        with self._lock:
            if os.path.isfile(path):
                existing = self.load_session(session.conversation.conversation_id)
                if existing != session:
                    raise ProductConversationSessionError("conversation-session identity 不可变，不能覆写")
                return existing
            os.makedirs(self.sessions_dir, exist_ok=True)
            atomic_write_json(path, session.as_record(), durable=self._durable_writes)
        return session

    def register_session(self, session):
        """首次登记通用 immutable identity；不创建 transport、Actor 或运行状态。"""
        if not isinstance(session, ProductConversationSession):
            raise ProductConversationSessionError("只能登记 ProductConversationSession")
        return self._register_session(session)

    def register_direct_session(self, session):
        """首次登记 identity；同 ID 只接受严格相同的记录，不允许覆写。"""
        if not isinstance(session, ProductDirectConversationSession):
            raise ProductConversationSessionError("只能登记 ProductDirectConversationSession")
        return self._register_session(session)
