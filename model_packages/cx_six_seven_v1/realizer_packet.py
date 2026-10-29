"""CX67 realization cards, fixed lanes, output identity, and packet assembly.

The package assembles already-selected resources into its structural lanes. It
does not rank model resources, select memories, call a provider, or authorize
or commit an action.  The generic framework sees only the opaque payload
published through ``framework.realizer_protocol``.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageReceipt,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import (
    context_fact_identity,
    same_model_fact_identity,
)
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)
from .expression_resources import ExpressionResourceBatch
from framework.temporal_domain import TemporalPosition


WRITER_PACKET_VERSION = "writer-packet-v2"
SUBCARD_WRITER_PACKET_VERSION = "writer-packet-v3-subcards"
PROVENANCE_WRITER_PACKET_VERSION = "writer-packet-v4-card-provenance"
# CX67 compatibility fallback only. Product hosts may supply an explicit
# technical budget; this value never selects model resources or actions.
DEFAULT_WRITER_PROMPT_BUDGET_CHARS = 6400
WRITER_LANE_ORDER = (
    "current_input",
    "persona",
    "conversation",
    "expression",
    "memory",
    "form",
)
_CALLER_LANES = frozenset(set(WRITER_LANE_ORDER) - {"expression"})
_BUDGET_POLICIES = frozenset({"normal", "preserve_selection"})


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _identifiers(
    value: Sequence[object],
    field: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an identifier sequence")
    result = tuple(_identifier(item, field) for item in value)
    if not allow_empty and not result:
        raise ValueError(f"{field} cannot be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicate identifiers")
    return result


def _same_context(
    receipt: ModelStageReceipt | ModelStageReceiptV2,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> bool:
    return same_model_fact_identity(context, receipt)


def _deduplicated(values: Sequence[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(values))


@dataclass(frozen=True)
class WriterOutputIdentity:
    """Explicit output actor and group visibility; addressing is never guessed."""

    speaker_participant_id: str
    speaker_kind: str
    audience_participant_ids: tuple[str, ...]
    addressed_participant_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        speaker = _identifier(
            self.speaker_participant_id, "speaker participant ID")
        audience = _identifiers(
            self.audience_participant_ids,
            "audience participant IDs",
            allow_empty=False,
        )
        addressed = _identifiers(
            self.addressed_participant_ids,
            "addressed participant IDs",
            allow_empty=True,
        )
        if speaker in audience:
            raise ValueError("audience participant IDs cannot contain the speaker")
        if not set(addressed).issubset(set(audience)):
            raise ValueError(
                "addressed participant IDs must be a subset of the audience")
        object.__setattr__(self, "speaker_participant_id", speaker)
        object.__setattr__(self, "speaker_kind", _identifier(
            self.speaker_kind, "speaker kind"))
        object.__setattr__(self, "audience_participant_ids", audience)
        object.__setattr__(self, "addressed_participant_ids", addressed)

    def to_record(self) -> dict[str, Any]:
        return {
            "speaker_participant_id": self.speaker_participant_id,
            "speaker_kind": self.speaker_kind,
            "audience_participant_ids": list(self.audience_participant_ids),
            "addressed_participant_ids": list(self.addressed_participant_ids),
        }


@dataclass(frozen=True)
class WriterSubcard:
    """One read-only child; provenance is archived, never rendered as prose.

    Children do not inherit a parent's preserve_selection budget exemption.
    This schema belongs to this model package, not the generic framework.
    """

    subcard_id: str
    kind: str
    source_id: str
    fields: Mapping[str, Any]
    source_receipt_refs: tuple[str, ...]
    provenance: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        for field in ("subcard_id", "kind", "source_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if not isinstance(self.fields, Mapping) or not self.fields:
            raise ValueError("writer subcard fields must be non-empty structured data")
        if self.provenance is not None and not isinstance(self.provenance, Mapping):
            raise ValueError("writer subcard provenance must be structured")
        object.__setattr__(self, "fields", freeze_model_payload(self.fields))
        object.__setattr__(self, "source_receipt_refs", _identifiers(
            self.source_receipt_refs, "writer subcard source receipt refs", allow_empty=False))
        object.__setattr__(self, "provenance", freeze_model_payload(
            {} if self.provenance is None else self.provenance))

    def to_record(self) -> dict[str, Any]:
        return {
            "subcard_id": self.subcard_id,
            "kind": self.kind,
            "source_id": self.source_id,
            "fields": thaw_model_data(self.fields),
            "source_receipt_refs": list(self.source_receipt_refs),
            "provenance": thaw_model_data(self.provenance),
        }


@dataclass(frozen=True)
class WriterCard:
    """One objective, already-selected Writer resource in one fixed lane."""

    card_id: str
    lane: str
    kind: str
    source_id: str
    fields: Mapping[str, Any]
    source_receipt_refs: tuple[str, ...]
    budget_policy: str = "normal"
    subcards: tuple[WriterSubcard, ...] = ()
    provenance: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        lane = _identifier(self.lane, "writer card lane")
        if lane not in WRITER_LANE_ORDER:
            raise ValueError(f"unknown writer card lane: {lane}")
        policy = _identifier(self.budget_policy, "writer card budget policy")
        if policy not in _BUDGET_POLICIES:
            raise ValueError("writer card budget policy is invalid")
        if not isinstance(self.fields, Mapping):
            raise ValueError("writer card fields must be structured")
        refs = _identifiers(
            self.source_receipt_refs,
            "writer card source receipt refs",
            allow_empty=False,
        )
        object.__setattr__(self, "card_id", _identifier(
            self.card_id, "writer card ID"))
        object.__setattr__(self, "lane", lane)
        object.__setattr__(self, "kind", _identifier(
            self.kind, "writer card kind"))
        object.__setattr__(self, "source_id", _identifier(
            self.source_id, "writer card source ID"))
        object.__setattr__(self, "fields", freeze_model_payload(self.fields))
        object.__setattr__(self, "source_receipt_refs", refs)
        object.__setattr__(self, "budget_policy", policy)
        if isinstance(self.subcards, (str, bytes)) or not isinstance(self.subcards, Sequence):
            raise ValueError("writer subcards must be a sequence")
        children = tuple(self.subcards)
        if any(not isinstance(child, WriterSubcard) for child in children):
            raise ValueError("writer subcards contain a non-subcard")
        if len({child.subcard_id for child in children}) != len(children):
            raise ValueError("writer subcard IDs must be unique")
        object.__setattr__(self, "subcards", children)
        if self.provenance is not None and not isinstance(self.provenance, Mapping):
            raise ValueError("writer card provenance must be structured")
        object.__setattr__(self, "provenance", freeze_model_payload(
            {} if self.provenance is None else self.provenance))

    def to_record(self) -> dict[str, Any]:
        return {
            "card_id": self.card_id,
            "lane": self.lane,
            "kind": self.kind,
            "source_id": self.source_id,
            "fields": thaw_model_data(self.fields),
            "source_receipt_refs": list(self.source_receipt_refs),
            "budget_policy": self.budget_policy,
            **({"subcards": [child.to_record() for child in self.subcards]}
               if self.subcards else {}),
            **({"provenance": thaw_model_data(self.provenance)} if self.provenance else {}),
        }


@dataclass(frozen=True)
class WriterPacket:
    """Immutable, lane-preserving input for a later realizer adapter."""

    version: str
    session_id: str
    conversation_id: str
    writer_request_id: str
    action_id: str | None
    event_id: str | None
    at_ms: int | None
    temporal_position: TemporalPosition | None
    runtime_ordinal: int | None
    output_identity: WriterOutputIdentity
    sections: Mapping[str, tuple[WriterCard, ...]]
    packet_fingerprint: str

    def __post_init__(self) -> None:
        has_position = self.temporal_position is not None
        has_ordinal = self.runtime_ordinal is not None
        if has_position != has_ordinal:
            raise ValueError(
                "writer packet temporal position and runtime ordinal must coexist")
        if has_position:
            if not isinstance(self.temporal_position, TemporalPosition):
                raise ValueError(
                    "writer packet temporal position must be TemporalPosition")
            self.temporal_position.verify_integrity()
            if (
                isinstance(self.runtime_ordinal, bool)
                or not isinstance(self.runtime_ordinal, int)
                or self.runtime_ordinal < 0
            ):
                raise ValueError(
                    "writer packet runtime ordinal must be non-negative")
            if self.at_ms is not None:
                raise ValueError(
                    "formal writer packet cannot mix opaque position with at_ms")
        elif self.at_ms is not None and (
            isinstance(self.at_ms, bool)
            or not isinstance(self.at_ms, int)
            or self.at_ms < 0
        ):
            raise ValueError("legacy writer packet at_ms must be non-negative")

    def _temporal_record(self) -> dict[str, Any]:
        if self.temporal_position is not None:
            return {
                "temporal_position": self.temporal_position.to_record(),
                "runtime_ordinal": self.runtime_ordinal,
            }
        return {"at_ms": self.at_ms}

    def content_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "writer_request_id": self.writer_request_id,
            "action_id": self.action_id,
            "event_id": self.event_id,
            **self._temporal_record(),
            "output_identity": self.output_identity.to_record(),
            "sections": {
                lane: [card.to_record() for card in cards]
                for lane, cards in self.sections.items()
            },
        }

    def receipt_record(self) -> dict[str, Any]:
        refs = _deduplicated(tuple(
            ref
            for cards in self.sections.values()
            for card in cards
            for ref in (
                *card.source_receipt_refs,
                *(ref for child in card.subcards for ref in child.source_receipt_refs),
            )
        ))
        return {
            "version": self.version,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "writer_request_id": self.writer_request_id,
            "action_id": self.action_id,
            "event_id": self.event_id,
            **self._temporal_record(),
            "speaker_participant_id": (
                self.output_identity.speaker_participant_id),
            "audience_participant_ids": list(
                self.output_identity.audience_participant_ids),
            "addressed_participant_ids": list(
                self.output_identity.addressed_participant_ids),
            "included_card_ids": {
                lane: [card.card_id for card in cards]
                for lane, cards in self.sections.items()
            },
            "source_receipt_refs": list(refs),
            "packet_fingerprint": self.packet_fingerprint,
            **({"included_subcard_ids": {
                card.card_id: [child.subcard_id for child in card.subcards]
                for cards in self.sections.values() for card in cards if card.subcards
            }} if self.version in (SUBCARD_WRITER_PACKET_VERSION, PROVENANCE_WRITER_PACKET_VERSION) else {}),
        }

    def to_record(self) -> dict[str, Any]:
        return {
            **self.content_record(),
            "packet_fingerprint": self.packet_fingerprint,
        }


def _validate_expression_batch(
    batch: ExpressionResourceBatch,
    *,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> None:
    if not isinstance(batch, ExpressionResourceBatch):
        raise ValueError(
            "expression resources must be ExpressionResourceBatch")
    if batch.stage_receipt.stage != "project_semantic_resources":
        raise ValueError("expression resource stage receipt is invalid")
    if batch.shared_projection_receipt.stage != "project_shared_semantics":
        raise ValueError("shared projection stage receipt is invalid")
    if (
        not _same_context(batch.stage_receipt, context)
        or not _same_context(batch.shared_projection_receipt, context)
    ):
        raise ValueError("expression resource receipt context is invalid")
    if (
        batch.source_shared_projection_output_fingerprint
        != batch.shared_projection_receipt.output_fingerprint
    ):
        raise ValueError("expression resource shared projection lineage is invalid")
    if not set(batch.selected_resource_ids).issubset(set(batch.resources)):
        raise ValueError("expression resource selection contains an unknown resource")


def _expression_cards(
    batch: ExpressionResourceBatch | None,
    *,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> tuple[WriterCard, ...]:
    if batch is None:
        return ()
    _validate_expression_batch(batch, context=context)
    cards = []
    for resource_id in batch.selected_resource_ids:
        resource = batch.resources[resource_id]
        cards.append(WriterCard(
            card_id=f"expression:{resource.resource_id}",
            lane="expression",
            kind="model_expression_resource",
            source_id=resource.resource_id,
            fields=resource.descriptor,
            source_receipt_refs=_deduplicated((
                *resource.source_receipt_refs,
                batch.shared_projection_receipt.output_fingerprint,
                batch.stage_receipt.output_fingerprint,
            )),
        ))
    return tuple(cards)


def assemble_writer_packet(
    *,
    context: ModelInvocationContext | ModelInvocationContextV2,
    writer_request_id: str | None = None,
    output_identity: WriterOutputIdentity,
    expression_resources: ExpressionResourceBatch | None,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
) -> WriterPacket:
    """Assemble lanes without interpreting, ranking, or dropping their content."""
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("context must be a model invocation context")
    identity = context_fact_identity(context)
    if not isinstance(output_identity, WriterOutputIdentity):
        raise ValueError("output_identity must be WriterOutputIdentity")
    conversation_id = _identifier(
        identity.scope_ref, "writer packet conversation ID")
    action_id = (
        _identifier(identity.action_ref, "writer packet action ID")
        if identity.action_ref is not None else None
    )
    if writer_request_id is None:
        if action_id is None:
            raise ValueError(
                "writer_request_id is required before a final action exists")
        # Compatibility for already-committed callers.  New pre-commit paths must
        # allocate their request identity from WriterAttemptJournal explicitly.
        request_id = f"compat-writer-request:{action_id}"
    else:
        request_id = _identifier(
            writer_request_id, "writer packet request ID")
    participant_id = _identifier(
        identity.subject_ref, "writer packet participant ID")
    if output_identity.speaker_participant_id != participant_id:
        raise ValueError(
            "writer output speaker differs from model context participant")
    if not isinstance(cards_by_lane, Mapping):
        raise ValueError("cards_by_lane must be a mapping")
    supplied_lanes = tuple(str(lane) for lane in cards_by_lane)
    unknown_lanes = set(supplied_lanes) - _CALLER_LANES
    if unknown_lanes:
        if "expression" in unknown_lanes:
            raise ValueError(
                "expression cards must come from model-selected resources")
        raise ValueError(
            "unknown caller writer lanes: " + ", ".join(sorted(unknown_lanes)))

    normalized: dict[str, tuple[WriterCard, ...]] = {}
    card_ids: set[str] = set()
    for lane in WRITER_LANE_ORDER:
        if lane == "expression":
            cards = _expression_cards(
                expression_resources, context=context)
        else:
            raw_cards = cards_by_lane.get(lane, ())
            if isinstance(raw_cards, (str, bytes)) or not isinstance(
                raw_cards, Sequence
            ):
                raise ValueError(f"writer lane {lane} must be a card sequence")
            cards = tuple(raw_cards)
            for card in cards:
                if not isinstance(card, WriterCard):
                    raise ValueError(f"writer lane {lane} contains a non-card")
                if card.lane != lane:
                    raise ValueError(
                        f"writer card {card.card_id} is in the wrong lane")
                if lane == "memory" and card.budget_policy != "preserve_selection":
                    raise ValueError(
                        "memory cards must preserve_selection after recall occupation")
        for card in cards:
            if card.card_id in card_ids:
                raise ValueError("writer packet card IDs must be unique")
            card_ids.add(card.card_id)
            for child in card.subcards:
                if child.subcard_id in card_ids:
                    raise ValueError("writer packet card and subcard IDs must be unique")
                card_ids.add(child.subcard_id)
        normalized[lane] = cards

    version = (
        PROVENANCE_WRITER_PACKET_VERSION
        if any(card.provenance for cards in normalized.values() for card in cards)
        else
        SUBCARD_WRITER_PACKET_VERSION
        if any(card.subcards for cards in normalized.values() for card in cards)
        else WRITER_PACKET_VERSION
    )
    provisional = {
        "version": version,
        "session_id": identity.session_id,
        "conversation_id": conversation_id,
        "writer_request_id": request_id,
        "action_id": action_id,
        "event_id": identity.event_ref,
        **(
            {
                "temporal_position": context.temporal_position.to_record(),
                "runtime_ordinal": context.runtime_ordinal,
            }
            if context.temporal_position is not None
            else {"at_ms": identity.legacy_scalar_position}
        ),
        "output_identity": output_identity.to_record(),
        "sections": {
            lane: [card.to_record() for card in cards]
            for lane, cards in normalized.items()
        },
    }
    return WriterPacket(
        version=version,
        session_id=identity.session_id,
        conversation_id=conversation_id,
        writer_request_id=request_id,
        action_id=action_id,
        event_id=identity.event_ref,
        at_ms=identity.legacy_scalar_position,
        temporal_position=context.temporal_position,
        runtime_ordinal=context.runtime_ordinal,
        output_identity=output_identity,
        sections=MappingProxyType(normalized),
        packet_fingerprint=model_data_fingerprint(provisional),
    )


def verify_writer_packet(packet: WriterPacket) -> dict[str, Any]:
    """Verify an assembled packet before a Semantic Realizer consumes it.

    This is an integrity and boundary check, not a second assembler.  It does
    not rank cards, rewrite fields, apply a prompt budget, or authorize output.
    """
    if not isinstance(packet, WriterPacket):
        raise ValueError("realizer input must be a WriterPacket")
    if packet.version not in (WRITER_PACKET_VERSION, SUBCARD_WRITER_PACKET_VERSION,
                              PROVENANCE_WRITER_PACKET_VERSION):
        raise ValueError("writer packet version is invalid")
    _identifier(packet.session_id, "writer packet session ID")
    _identifier(packet.conversation_id, "writer packet conversation ID")
    _identifier(packet.writer_request_id, "writer packet request ID")
    if packet.action_id is not None:
        _identifier(packet.action_id, "writer packet action ID")
    if packet.event_id is not None:
        _identifier(packet.event_id, "writer packet event ID")
    if not isinstance(packet.output_identity, WriterOutputIdentity):
        raise ValueError("writer packet output identity is invalid")
    if not isinstance(packet.sections, Mapping):
        raise ValueError("writer packet sections must be a mapping")
    if tuple(packet.sections) != WRITER_LANE_ORDER:
        raise ValueError("writer packet lanes do not match the framework contract")

    card_ids: set[str] = set()
    for lane in WRITER_LANE_ORDER:
        cards = packet.sections[lane]
        if not isinstance(cards, tuple):
            raise ValueError(f"writer packet lane {lane} must be immutable")
        for card in cards:
            if not isinstance(card, WriterCard):
                raise ValueError(f"writer packet lane {lane} contains a non-card")
            if card.lane != lane:
                raise ValueError(f"writer card {card.card_id} is in the wrong lane")
            if card.card_id in card_ids:
                raise ValueError("writer packet contains duplicate card IDs")
            card_ids.add(card.card_id)
            if not isinstance(card.subcards, tuple):
                raise ValueError("writer subcards must be immutable")
            for child in card.subcards:
                if not isinstance(child, WriterSubcard):
                    raise ValueError("writer subcard is invalid")
                if child.subcard_id in card_ids:
                    raise ValueError("writer packet contains duplicate subcard IDs")
                card_ids.add(child.subcard_id)
            if lane == "expression" and card.kind != "model_expression_resource":
                raise ValueError("writer expression lane bypassed model resources")
            if lane == "memory" and card.budget_policy != "preserve_selection":
                raise ValueError(
                    "writer memory cards must preserve_selection")

    has_children = any(card.subcards for cards in packet.sections.values() for card in cards)
    has_provenance = any(card.provenance for cards in packet.sections.values() for card in cards)
    expected_version = (PROVENANCE_WRITER_PACKET_VERSION if has_provenance else
                        SUBCARD_WRITER_PACKET_VERSION if has_children else WRITER_PACKET_VERSION)
    if packet.version != expected_version:
        raise ValueError("writer packet version differs from its subcard contract")
    expected = model_data_fingerprint(packet.content_record())
    if packet.packet_fingerprint != expected:
        raise ValueError("writer packet fingerprint does not match its content")
    return packet.receipt_record()


def _restore_subcards(value: object) -> tuple[WriterSubcard, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError("archived writer subcards must be a sequence")
    children = []
    expected_fields = {
        "subcard_id", "kind", "source_id", "fields",
        "source_receipt_refs", "provenance",
    }
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) != expected_fields:
            raise ValueError("archived writer subcard structure is invalid")
        children.append(WriterSubcard(**raw))
    return tuple(children)


def restore_writer_packet(
    record: Mapping[str, Any],
    *,
    expected_temporal_position: TemporalPosition | None = None,
) -> WriterPacket:
    """Restore one exact archived packet without assembling resources again."""
    if not isinstance(record, Mapping):
        raise ValueError("archived WriterPacket must be a mapping")
    raw = thaw_model_data(record)
    temporal_record = raw.get("temporal_position")
    if temporal_record is not None:
        if (
            not isinstance(expected_temporal_position, TemporalPosition)
            or not isinstance(temporal_record, Mapping)
            or temporal_record.get("fingerprint")
            != expected_temporal_position.fingerprint
        ):
            raise ValueError("archived WriterPacket temporal position is invalid")
        temporal_position = expected_temporal_position
        at_ms = None
    else:
        temporal_position = None
        at_ms = raw.get("at_ms")
    identity_raw = raw.get("output_identity")
    sections_raw = raw.get("sections")
    if not isinstance(identity_raw, Mapping) or not isinstance(sections_raw, Mapping):
        raise ValueError("archived WriterPacket structure is invalid")
    identity = WriterOutputIdentity(
        speaker_participant_id=identity_raw.get("speaker_participant_id"),
        speaker_kind=identity_raw.get("speaker_kind"),
        audience_participant_ids=tuple(
            identity_raw.get("audience_participant_ids") or ()),
        addressed_participant_ids=tuple(
            identity_raw.get("addressed_participant_ids") or ()),
    )
    sections = {}
    for lane in WRITER_LANE_ORDER:
        rows = sections_raw.get(lane)
        if not isinstance(rows, list):
            raise ValueError("archived WriterPacket lanes are invalid")
        sections[lane] = tuple(
            WriterCard(
                card_id=item.get("card_id"),
                lane=item.get("lane"),
                kind=item.get("kind"),
                source_id=item.get("source_id"),
                fields=item.get("fields"),
                source_receipt_refs=tuple(
                    item.get("source_receipt_refs") or ()),
                budget_policy=item.get("budget_policy", "normal"),
                subcards=_restore_subcards(item.get("subcards", ())),
                provenance=item.get("provenance"),
            )
            for item in rows
            if isinstance(item, Mapping)
        )
        if len(sections[lane]) != len(rows):
            raise ValueError("archived WriterPacket card is invalid")
    packet = WriterPacket(
        version=raw.get("version"),
        session_id=raw.get("session_id"),
        conversation_id=raw.get("conversation_id"),
        writer_request_id=raw.get("writer_request_id"),
        action_id=raw.get("action_id"),
        event_id=raw.get("event_id"),
        at_ms=at_ms,
        temporal_position=temporal_position,
        runtime_ordinal=raw.get("runtime_ordinal"),
        output_identity=identity,
        sections=MappingProxyType(sections),
        packet_fingerprint=raw.get("packet_fingerprint"),
    )
    verify_writer_packet(packet)
    if packet.to_record() != raw:
        raise ValueError("archived WriterPacket differs after restoration")
    return packet


__all__ = (
    "DEFAULT_WRITER_PROMPT_BUDGET_CHARS",
    "WRITER_LANE_ORDER",
    "WRITER_PACKET_VERSION",
    "SUBCARD_WRITER_PACKET_VERSION",
    "PROVENANCE_WRITER_PACKET_VERSION",
    "WriterCard",
    "WriterSubcard",
    "WriterOutputIdentity",
    "WriterPacket",
    "assemble_writer_packet",
    "restore_writer_packet",
    "verify_writer_packet",
)
