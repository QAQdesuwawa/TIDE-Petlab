"""Six/seven model interaction-object and capability contract.

This contract defines what objects exist inside the six/seven interaction
model and which model capabilities they own.  Conversation membership,
visibility and routing remain optional multi-participant composition concerns.
The framework kernel carries opaque subject references and does not interpret
the object kinds defined here.
"""
from dataclasses import dataclass
from typing import Dict, Mapping, Optional


_KINDS = frozenset({"human", "npc", "simulation"})
_CAPABILITY_FIELDS = (
    "submit_external_message",
    "receive_projection",
    "has_local_store",
    "has_symbolic_scheduler",
    "has_writer",
)


class ParticipantSchemaError(ValueError):
    """Participant identity or capability contract is invalid."""


def _nonempty(value, label):
    text = str(value or "").strip()
    if not text:
        raise ParticipantSchemaError("%s 不能为空" % label)
    return text


@dataclass(frozen=True)
class ParticipantCapabilities:
    """Model-object capabilities; these are not personality or root state."""

    submit_external_message: bool
    receive_projection: bool
    has_local_store: bool
    has_symbolic_scheduler: bool
    has_writer: bool

    def __post_init__(self):
        if self.has_symbolic_scheduler and not self.has_local_store:
            raise ParticipantSchemaError("拥有 scheduler 的参与者必须拥有本地 Store")
        if self.has_writer and not self.has_symbolic_scheduler:
            raise ParticipantSchemaError("拥有 writer 的参与者必须拥有 scheduler")

    def as_record(self):
        return {field: bool(getattr(self, field)) for field in _CAPABILITY_FIELDS}

    @classmethod
    def from_record(cls, raw):
        if not isinstance(raw, Mapping):
            raise ParticipantSchemaError("capabilities 必须为对象")
        unknown = set(raw) - set(_CAPABILITY_FIELDS)
        missing = set(_CAPABILITY_FIELDS) - set(raw)
        if unknown or missing:
            parts = []
            if unknown:
                parts.append("未知字段：" + ", ".join(sorted(unknown)))
            if missing:
                parts.append("缺少字段：" + ", ".join(sorted(missing)))
            raise ParticipantSchemaError("capabilities 合同错误（%s）" % "；".join(parts))
        if any(not isinstance(raw[field], bool) for field in _CAPABILITY_FIELDS):
            raise ParticipantSchemaError("capabilities 必须全部为布尔值")
        return cls(**{field: raw[field] for field in _CAPABILITY_FIELDS})


@dataclass(frozen=True)
class Participant:
    """Stable six/seven interaction-object identity without session state."""

    participant_id: str
    kind: str
    capabilities: ParticipantCapabilities
    c_base_ref: Optional[str] = None
    profile_id: Optional[str] = None
    schema_version: int = 1

    def __post_init__(self):
        participant_id = _nonempty(self.participant_id, "participant_id")
        kind = str(self.kind or "").strip().lower()
        if kind not in _KINDS:
            raise ParticipantSchemaError("不支持的 participant kind：%s" % kind)
        if not isinstance(self.capabilities, ParticipantCapabilities):
            raise ParticipantSchemaError("capabilities 必须为 ParticipantCapabilities")
        c_base_ref = (None if self.c_base_ref is None
                      else _nonempty(self.c_base_ref, "C_base 引用"))
        profile_id = (None if self.profile_id is None
                      else _nonempty(self.profile_id, "profile_id"))
        if kind == "human":
            if c_base_ref is not None or profile_id is not None:
                raise ParticipantSchemaError("真实用户不能拥有 C_base 或 NPC profile")
            if (self.capabilities.has_symbolic_scheduler
                    or self.capabilities.has_writer
                    or self.capabilities.has_local_store):
                raise ParticipantSchemaError("真实用户不能拥有自主运行、writer 或角色 Store")
            if not self.capabilities.submit_external_message:
                raise ParticipantSchemaError("真实用户必须能提交外部消息")
        elif kind == "npc":
            if c_base_ref is None or profile_id is None:
                raise ParticipantSchemaError("NPC 必须拥有 C_base 引用和 profile_id")
            if not (self.capabilities.has_local_store
                    and self.capabilities.has_symbolic_scheduler
                    and self.capabilities.has_writer):
                raise ParticipantSchemaError("NPC 必须拥有 Store、scheduler 与 writer")
            if self.capabilities.submit_external_message:
                raise ParticipantSchemaError("NPC 不得伪装为外部用户输入来源")
        else:
            if self.capabilities.has_symbolic_scheduler:
                if c_base_ref is None:
                    raise ParticipantSchemaError("自主 SimulatedActor 必须拥有 C_base 引用")
                if not self.capabilities.has_writer:
                    raise ParticipantSchemaError("自主 SimulatedActor 必须拥有 writer")
            elif self.capabilities.has_writer:
                raise ParticipantSchemaError("非自主 SimulatedActor 不能拥有 writer")
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "kind", kind)
        object.__setattr__(self, "c_base_ref", c_base_ref)
        object.__setattr__(self, "profile_id", profile_id)

    def as_record(self):
        return {
            "schema_version": int(self.schema_version),
            "participant_id": self.participant_id,
            "kind": self.kind,
            "capabilities": self.capabilities.as_record(),
            "c_base_ref": self.c_base_ref,
            "profile_id": self.profile_id,
        }

    @classmethod
    def from_record(cls, raw):
        if not isinstance(raw, Mapping):
            raise ParticipantSchemaError("participant 必须为对象")
        required = {"schema_version", "participant_id", "kind", "capabilities",
                    "c_base_ref", "profile_id"}
        missing = required - set(raw)
        if missing:
            raise ParticipantSchemaError("participant 缺少字段：" + ", ".join(sorted(missing)))
        try:
            version = int(raw["schema_version"])
        except (TypeError, ValueError) as exc:
            raise ParticipantSchemaError("schema_version 必须为整数") from exc
        if version != 1:
            raise ParticipantSchemaError("不支持的 participant schema_version：%s" % version)
        return cls(
            participant_id=raw["participant_id"],
            kind=raw["kind"],
            capabilities=ParticipantCapabilities.from_record(raw["capabilities"]),
            c_base_ref=raw["c_base_ref"],
            profile_id=raw["profile_id"],
            schema_version=version,
        )


@dataclass(frozen=True)
class NpcActorProfile:
    """Reusable six/seven NPC object before conversation composition."""

    participant: Participant
    profile_id: str
    schema_version: int = 1

    def __post_init__(self):
        if not isinstance(self.participant, Participant) or self.participant.kind != "npc":
            raise ParticipantSchemaError("NpcActorProfile 必须引用 NPC participant")
        profile_id = _nonempty(self.profile_id, "profile_id")
        if self.participant.profile_id != profile_id:
            raise ParticipantSchemaError("NpcActorProfile 的 profile_id 必须与 participant 一致")
        object.__setattr__(self, "profile_id", profile_id)

    def as_record(self):
        return {
            "schema_version": int(self.schema_version),
            "profile_id": self.profile_id,
            "participant": self.participant.as_record(),
        }


def make_human_participant(participant_id):
    return Participant(
        participant_id=participant_id,
        kind="human",
        capabilities=ParticipantCapabilities(
            submit_external_message=True,
            receive_projection=True,
            has_local_store=False,
            has_symbolic_scheduler=False,
            has_writer=False,
        ),
    )


def make_npc_profile(*, participant_id, profile_id, c_base_ref):
    participant = Participant(
        participant_id=participant_id,
        kind="npc",
        capabilities=ParticipantCapabilities(
            submit_external_message=False,
            receive_projection=True,
            has_local_store=True,
            has_symbolic_scheduler=True,
            has_writer=True,
        ),
        c_base_ref=c_base_ref,
        profile_id=profile_id,
    )
    return NpcActorProfile(participant=participant, profile_id=profile_id)


def make_simulated_participant(participant_id, *, autonomous=False, c_base_ref=None):
    """Create a fixture object; autonomous capability must be explicit."""
    autonomous = bool(autonomous)
    return Participant(
        participant_id=participant_id,
        kind="simulation",
        capabilities=ParticipantCapabilities(
            submit_external_message=not autonomous,
            receive_projection=True,
            has_local_store=autonomous,
            has_symbolic_scheduler=autonomous,
            has_writer=autonomous,
        ),
        c_base_ref=c_base_ref,
    )


class ParticipantRegistry:
    """In-memory six/seven object index; creates no Store or conversation."""

    def __init__(self):
        self._participants: Dict[str, Participant] = {}

    def register(self, participant):
        if not isinstance(participant, Participant):
            raise ParticipantSchemaError("registry 只能登记 Participant")
        if participant.participant_id in self._participants:
            raise ParticipantSchemaError("participant 已注册：%s" % participant.participant_id)
        self._participants[participant.participant_id] = participant
        return participant

    def get(self, participant_id):
        return self._participants.get(str(participant_id or ""))

    @staticmethod
    def from_record(raw):
        return Participant.from_record(raw)


__all__ = (
    "NpcActorProfile",
    "Participant",
    "ParticipantCapabilities",
    "ParticipantRegistry",
    "ParticipantSchemaError",
    "make_human_participant",
    "make_npc_profile",
    "make_simulated_participant",
)
