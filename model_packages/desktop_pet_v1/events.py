"""Typed pet content on one shared event identity; no scheduling authority."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, ClassVar

from framework.history import CommittedFact
from framework.model_api import freeze_model_payload, model_data_fingerprint, thaw_model_data
from framework.temporal_domain import TemporalDomainBinding, TemporalPosition

EVENT_VERSION = "desktop-pet-event-v1"
CONTENT_VERSION = "desktop-pet-content-v1"
EXPRESSION_CONTENT_VERSION = "desktop-pet-expression-content-v1"


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        raise ValueError(f"{label} requires an exact non-empty identifier")
    return value


def _refs(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{label} requires a sequence")
    result = tuple(_identifier(item, label) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{label} contains duplicate identifiers")
    return result


@dataclass(frozen=True, kw_only=True)
class Content:
    content_id: str
    description: str
    recipient_refs: tuple[str, ...] = ()
    semantic_key: str | None = None
    parameters: Mapping[str, Any] = field(default_factory=dict)
    kind: ClassVar[str] = "abstract"
    private: ClassVar[bool] = False
    prefix: ClassVar[str] = ""

    def __post_init__(self):
        if type(self) not in _CONTENT_TYPES.values():
            raise ValueError("content requires a registered concrete type")
        _identifier(self.content_id, "content_id")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("content requires a description")
        object.__setattr__(self, "recipient_refs", _refs(self.recipient_refs, "recipient_refs"))
        if self.semantic_key is not None:
            _identifier(self.semantic_key, "semantic_key")
        if self.kind in {"action", "self_amusement", "interaction"} and self.semantic_key is None:
            raise ValueError("behavior content requires a semantic_key")
        if self.kind=='expression' and (not self.parameters or self.semantic_key is not None):
            raise ValueError('expression requires presentation metadata only')
        if self.kind=='expression' or (self.kind=="speech" and self.parameters):
            from .face_expression import validate
            if set(self.parameters)!={"presentation"}:raise ValueError("speech only accepts presentation metadata")
            validate(self.parameters["presentation"])
        if self.kind in {"thought", "speech"} and (self.semantic_key is not None or (self.parameters and self.kind=="thought")):
            raise ValueError("verbal content cannot carry behavior parameters")
        if not isinstance(self.parameters, Mapping):
            raise ValueError("content parameters must be a mapping")
        object.__setattr__(self, "parameters", freeze_model_payload(self.parameters))

    def to_record(self) -> dict[str, Any]:
        return {
            "version": EXPRESSION_CONTENT_VERSION if self.kind=='expression' else CONTENT_VERSION, "content_id": self.content_id, "kind": self.kind,
            "description": self.description, "recipient_refs": list(self.recipient_refs),
            "semantic_key": self.semantic_key, "parameters": thaw_model_data(self.parameters),
        }

    def as_text(self) -> str:
        if self.parameters.get('capture_version') in ('pet-external-observation-v1','pet-external-observation-v2'):return '[网页观察] '+self.description
        return self.prefix + self.description

    @staticmethod
    def from_record(record: Mapping[str, Any]) -> Content:
        if not isinstance(record, Mapping) or set(record) != {
            "version", "content_id", "kind", "description", "recipient_refs", "semantic_key", "parameters",
        } or record.get("version") != (EXPRESSION_CONTENT_VERSION if record.get('kind')=='expression' else CONTENT_VERSION):
            raise ValueError("content record schema is invalid")
        concrete = _CONTENT_TYPES.get(record["kind"])
        if concrete is None:
            raise ValueError("content kind is not registered")
        return concrete(**{key: record[key] for key in (
            "content_id", "description", "recipient_refs", "semantic_key", "parameters",
        )})


class Thought(Content):
    kind = "thought"
    private = True
    prefix = "[内部自述] "


class SelfAmusement(Content):
    kind = "self_amusement"
    private = True
    prefix = "[自身活动] "


class Speech(Content):
    kind = "speech"
    prefix = "[角色话语] "


class Action(Content):
    kind = "action"
    prefix = "[角色行为] "


class Interaction(Content):
    kind = "interaction"
    prefix = "[用户交互] "


class Expression(Content):
    kind = 'expression'
    prefix = '[可见表情] '


_CONTENT_TYPES = {item.kind: item for item in (Thought, SelfAmusement, Speech, Action, Interaction, Expression)}
EVENT_SCHEMA = {
    "version": EVENT_VERSION, "content_version": CONTENT_VERSION,
    "content_types": [kind for kind in _CONTENT_TYPES if kind!='expression'], "direction_types": ["peer_input", "npc_output"],
    "authority": "source verified by product; settlement and History remain formal runtime owned",
}

# Legacy package manifest stays immutable. Composite consumers explicitly opt in
# to this extended declaration rather than changing saved package fingerprints.
COMPOSITE_EVENT_SCHEMA = {**EVENT_SCHEMA, 'version':'desktop-pet-composite-event-contract-v1',
                          'content_types':list(_CONTENT_TYPES),
                          'expression_content_version':EXPRESSION_CONTENT_VERSION}


@dataclass(frozen=True, kw_only=True)
class PetEvent:
    event_id: str
    source_ref: str
    source_receipt_ref: str
    owner_ref: str
    actor_ref: str
    target_refs: tuple[str, ...]
    scope_ref: str
    position: TemporalPosition
    event_kind: str
    contents: tuple[Content, ...]

    def __post_init__(self):
        for label in ("event_id", "source_ref", "source_receipt_ref", "owner_ref", "actor_ref", "scope_ref"):
            _identifier(getattr(self, label), label)
        object.__setattr__(self, "target_refs", _refs(self.target_refs, "target_refs"))
        if not isinstance(self.position, TemporalPosition):
            raise ValueError("event position must be a formal TemporalPosition")
        self.position.verify_integrity()
        if self.event_kind not in {"peer_input", "npc_output"}:
            raise ValueError("event direction requires an existing committed input/output path")
        if self.event_kind == "npc_output" and self.actor_ref != self.owner_ref:
            raise ValueError("own output actor differs from owner")
        if not isinstance(self.contents, (tuple, list)) or not self.contents:
            raise ValueError("event requires concrete content")
        contents = tuple(self.contents)
        if any(type(item) not in _CONTENT_TYPES.values() for item in contents):
            raise ValueError("event contains an invalid content type")
        if len({item.content_id for item in contents}) != len(contents):
            raise ValueError("event content identities must be unique")
        for content in contents:
            if content.private and any(ref != self.owner_ref for ref in content.recipient_refs):
                raise ValueError("private content can only be read by its owner")
        object.__setattr__(self, "contents", contents)

    def _material(self) -> dict[str, Any]:
        return {
            "version": EVENT_VERSION, "event_id": self.event_id, "source_ref": self.source_ref,
            "source_receipt_ref": self.source_receipt_ref, "owner_ref": self.owner_ref,
            "actor_ref": self.actor_ref, "target_refs": list(self.target_refs), "scope_ref": self.scope_ref,
            "position": self.position.to_record(), "event_kind": self.event_kind,
            "contents": [item.to_record() for item in self.contents],
        }

    @property
    def fingerprint(self) -> str:
        return model_data_fingerprint(self._material())

    def to_record(self) -> dict[str, Any]:
        return dict(self._material(), fingerprint=self.fingerprint)

    @property
    def source_binding_ref(self) -> str:
        """Bind the verified source receipt to this exact immutable event body."""
        return model_data_fingerprint({"version": "desktop-pet-source-binding-v1",
            "source_receipt_ref": self.source_receipt_ref, "event_fingerprint": self.fingerprint})

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> PetEvent:
        fields = {"version", "event_id", "source_ref", "source_receipt_ref", "owner_ref", "actor_ref",
                  "target_refs", "scope_ref", "position", "event_kind", "contents", "fingerprint"}
        if not isinstance(record, Mapping) or set(record) != fields or record["version"] != EVENT_VERSION:
            raise ValueError("event record schema is invalid")
        if not isinstance(record["contents"], (list, tuple)):
            raise ValueError("event contents require a sequence")
        position = record["position"]
        if not isinstance(position, Mapping) or set(position) != {"binding", "coordinate", "fingerprint"}:
            raise ValueError("event position record is invalid")
        binding_record = dict(position["binding"])
        binding_record["operator_versions"] = MappingProxyType(dict(binding_record["operator_versions"]))
        binding = TemporalDomainBinding(**binding_record)
        restored_position = TemporalPosition.create(binding=binding, coordinate=position["coordinate"])
        if restored_position.fingerprint != position["fingerprint"]:
            raise ValueError("event position fingerprint is invalid")
        event = cls(**{key: record[key] for key in (
            "event_id", "source_ref", "source_receipt_ref", "owner_ref", "actor_ref", "target_refs",
            "scope_ref", "event_kind",
        )}, position=restored_position, contents=tuple(Content.from_record(item) for item in record["contents"]))
        if event.fingerprint != record["fingerprint"]:
            raise ValueError("event fingerprint is invalid")
        return event

    def visible_contents(self, reader_ref: str) -> tuple[Content, ...]:
        _identifier(reader_ref, "reader_ref")
        if reader_ref == self.owner_ref:
            return self.contents
        return tuple(item for item in self.contents if not item.private and reader_ref in item.recipient_refs)

    def project_for_reader(self, reader_ref: str) -> dict[str, Any]:
        visible = self.visible_contents(reader_ref)
        if not visible:
            raise ValueError("event content is not visible to this reader")
        # A public projection contains no private parent body or numerical payload.
        return {"event_id": self.event_id, "source_ref": self.source_ref,
                "contents": [item.to_record() for item in visible]}


@dataclass(frozen=True)
class MemoryInstance:
    """An event plus its real formal footprint, shared by every content child."""
    event: PetEvent
    fact: CommittedFact

    def __post_init__(self):
        self.fact.verify_integrity()
        if (self.fact.fact_id != self.event.event_id
                or self.fact.source_ref != self.event.source_binding_ref
                or self.fact.scope_ref != self.event.scope_ref
                or self.fact.temporal_position_fingerprint != self.event.position.fingerprint
                or self.event.owner_ref not in self.fact.visible_to_refs):
            raise ValueError("memory event differs from its formal fact identity")

    @property
    def contents(self) -> tuple[MemoryContentInstance, ...]:
        return tuple(MemoryContentInstance(self.event, self.fact, item) for item in self.event.contents)

    @property
    def settlement_ref(self) -> Mapping[str, Any]:
        return self.fact.settlement_ref


@dataclass(frozen=True)
class MemoryContentInstance(MemoryInstance):
    content: Content

    def __post_init__(self):
        super().__post_init__()
        if self.content not in self.event.contents:
            raise ValueError("memory child does not belong to its source event")

