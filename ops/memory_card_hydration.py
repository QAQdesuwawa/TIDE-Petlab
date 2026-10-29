"""Alpha 0.4 C5 exact-ID late hydration for writer-facing MemoryCards.

This layer consumes an already joined canonical recall batch and an already
settled attention occupation.  It is the first point allowed to read committed
event text.  Unoccupied IDs are never passed to the loader.  The layer does not
reselect memories, call providers, calculate P4, write state, or assemble a
writer prompt.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType

from framework.model_api import model_data_fingerprint
from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterCard as FrameworkWriterCard,
)
from ops.conversation_events import CommittedConversationEvent
from ops.memory_actual_join import ActualRecallCanonicalBatch
from ops.memory_card_attention import (
    MEMORY_CARD_ATTENTION_VERSION,
    MemoryCardAttentionOccupation,
)
from ops.memory_content_attention import CONTENT_MEMORY_CARD_ATTENTION_VERSION
from ops.memory_content_loader import load_occupied_committed_events
from ops.memory_contracts import MemoryCard
from ops.memory_join import CanonicalRecallBatch


_SUPPORTED_CANONICAL_BATCH_TYPES = (
    CanonicalRecallBatch,
    ActualRecallCanonicalBatch,
)


MEMORY_CARD_HYDRATION_VERSION = "memory-card-late-hydration-v1"


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if parsed < 0 or parsed != value:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


def _freeze(value, field="value"):
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain only finite serializable values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class MemoryCardHydrationBatch:
    trigger_event_id: str
    evaluation_time_ms: int
    formula_version: str
    cards: tuple[MemoryCard, ...]
    receipt: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "trigger_event_id", _identifier(
            self.trigger_event_id, "trigger_event_id"))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(self, "formula_version", _identifier(
            self.formula_version, "formula_version"))
        cards = tuple(self.cards)
        if any(not isinstance(card, MemoryCard) for card in cards):
            raise ValueError("cards must contain MemoryCard values")
        card_ids = tuple(card.event_id for card in cards)
        if len(set(card_ids)) != len(card_ids):
            raise ValueError("hydrated MemoryCard exact IDs must be unique")
        object.__setattr__(self, "cards", cards)
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "memory_card_hydration_batch",
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "formula_version": self.formula_version,
            "cards": [card.to_record() for card in self.cards],
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, Mapping):
            raise ValueError("memory-card hydration record must be a mapping")
        required = {
            "schema_version", "kind", "trigger_event_id", "evaluation_time_ms",
            "formula_version", "cards", "receipt",
        }
        if set(record) != required or record.get("schema_version") != 1:
            raise ValueError("memory-card hydration schema is unsupported")
        if record.get("kind") != "memory_card_hydration_batch":
            raise ValueError("memory-card hydration kind is unsupported")
        if not isinstance(record.get("cards"), list):
            raise ValueError("memory-card hydration cards must be a list")
        return cls(
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            formula_version=record["formula_version"],
            cards=tuple(MemoryCard.from_record(item) for item in record["cards"]),
            receipt=record["receipt"],
        )


def memory_writer_cards_from_hydration(
        batch: MemoryCardHydrationBatch) -> tuple[FrameworkWriterCard, ...]:
    """Bridge an already occupied hydration result into framework memory cards.

    This adapter is deliberately one-for-one.  It does not score, reorder, cap,
    or otherwise reselect memories, and its lineage reference hashes the
    content-free hydration receipt rather than copying committed text.
    """
    if not isinstance(batch, MemoryCardHydrationBatch):
        raise ValueError("memory writer bridge requires a hydration batch")
    event_ids = tuple(card.event_id for card in batch.cards)
    receipt = batch.receipt
    for field, label in (
        ("occupied_event_ids", "occupied"),
        ("loaded_event_ids", "loaded"),
        ("hydrated_card_ids", "hydrated"),
    ):
        raw = receipt.get(field)
        if isinstance(raw, (str, bytes)) or not isinstance(raw, Sequence):
            raise ValueError(f"hydration receipt {label} IDs are invalid")
        if tuple(str(item) for item in raw) != event_ids:
            raise ValueError(
                f"hydration receipt {label} IDs differ from hydrated cards")
    if receipt.get("selection_recomputed") is not False:
        raise ValueError("hydration receipt selection must not be recomputed")
    if receipt.get("content_in_receipt") is not False:
        raise ValueError("hydration receipt content flag must be false")

    hydration_ref = "hydration:" + model_data_fingerprint({
        "formula_version": batch.formula_version,
        "trigger_event_id": batch.trigger_event_id,
        "evaluation_time_ms": batch.evaluation_time_ms,
        "receipt": batch.receipt,
    })
    result = []
    for card in batch.cards:
        if not card.receipt_refs:
            raise ValueError(
                f"hydrated memory {card.event_id} has no source receipt refs")
        result.append(FrameworkWriterCard(
            card_id=f"memory:{card.event_id}",
            lane="memory",
            kind="committed_event",
            source_id=card.event_id,
            fields={
                "event_id": card.event_id,
                "committed_at_ms": card.committed_at_ms,
                "speaker_scope": card.speaker_scope,
                "speaker_participant_id": card.speaker_participant_id,
                "text": card.text,
            },
            source_receipt_refs=tuple(dict.fromkeys((
                *card.receipt_refs, hydration_ref,
            ))),
            budget_policy="preserve_selection",
        ))
    return tuple(result)


def _receipt_refs(value, event_id):
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"receipt refs for {event_id} must be a non-empty sequence")
    refs = tuple(_identifier(item, f"receipt_refs[{event_id}]") for item in value)
    if not refs or len(set(refs)) != len(refs):
        raise ValueError(f"receipt refs for {event_id} must be non-empty and unique")
    return refs


def hydrate_memory_cards(
        *, canonical_batch: CanonicalRecallBatch | ActualRecallCanonicalBatch,
        attention_plan: MemoryCardAttentionOccupation,
        event_loader: Callable[[str], CommittedConversationEvent],
        owner_participant_id: str,
        receipt_refs_by_event_id: Mapping[str, Sequence[str]],
) -> MemoryCardHydrationBatch:
    """Hydrate only occupied exact IDs from canonical committed events."""
    if not isinstance(canonical_batch, _SUPPORTED_CANONICAL_BATCH_TYPES):
        raise ValueError("canonical_batch type is unsupported")
    if not isinstance(attention_plan, MemoryCardAttentionOccupation):
        raise ValueError("attention_plan must be MemoryCardAttentionOccupation")
    supported_attention_versions = {
        MEMORY_CARD_ATTENTION_VERSION,
        CONTENT_MEMORY_CARD_ATTENTION_VERSION,
    }
    if attention_plan.formula_version not in supported_attention_versions:
        raise ValueError("attention plan version is unsupported")
    if not callable(event_loader):
        raise ValueError("event_loader must be callable")
    owner_id = _identifier(owner_participant_id, "owner_participant_id")
    if not isinstance(receipt_refs_by_event_id, Mapping):
        raise ValueError("receipt_refs_by_event_id must be a mapping")

    canonical_by_id = {item.event_id: item for item in canonical_batch.items}
    canonical_ids = tuple(canonical_by_id)
    if set(attention_plan.recalled_event_ids) != set(canonical_ids):
        raise ValueError(
            "attention recalled IDs must exactly match canonical recall item IDs")
    selected_ids = tuple(attention_plan.selected_for_hydration_ids)
    ref_keys = {str(key) for key in receipt_refs_by_event_id}
    if ref_keys != set(selected_ids):
        raise ValueError("receipt refs must exactly match occupied event IDs")

    references = {}
    for event_id in selected_ids:
        item = canonical_by_id.get(event_id)
        if item is None:
            raise ValueError("occupied exact ID is outside the canonical recall batch")
        if not item.content_eligible:
            raise ValueError("occupied exact ID is not content eligible")
        references[event_id] = _receipt_refs(
            receipt_refs_by_event_id[event_id], event_id)

    loaded_events = load_occupied_committed_events(
        canonical_event_ids=canonical_ids,
        selected_for_hydration_ids=selected_ids,
        evaluation_time_ms=attention_plan.evaluation_time_ms,
        owner_participant_id=owner_id,
        event_loader=event_loader,
    )
    cards = []
    decisions = []
    for event_id, record in zip(selected_ids, loaded_events):
        speaker_scope = (
            "self" if record.sender_participant_id == owner_id else "peer")
        evidence_item = canonical_by_id[event_id]
        address_available = bool(
            evidence_item.address_receipt is not None
            and evidence_item.address_receipt.status == "available")
        if isinstance(canonical_batch, ActualRecallCanonicalBatch):
            lane = (
                "dual" if evidence_item.actual_recalled and address_available
                else "actual_recall_only" if evidence_item.state_eligible
                else "address_only"
            )
        else:
            lane = (
                "dual" if evidence_item.numeric_witness is not None and address_available
                else "numeric_only" if evidence_item.state_eligible
                else "address_only"
            )
        provenance = {
            "hydration_version": MEMORY_CARD_HYDRATION_VERSION,
            "canonical_record_kind": "committed_conversation_event",
            "canonical_event_id": record.event_id,
            "conversation_id": record.conversation_id,
            "membership_version": record.membership_version,
            "member_snapshot_ids": record.member_snapshot_ids,
            "visible_recipient_ids": record.visible_recipient_ids,
            "addressed_participant_ids": record.addressed_participant_ids,
            "sender_action_id": record.sender_action_id,
            "evidence_lane": lane,
        }
        cards.append(MemoryCard(
            event_id=record.event_id,
            committed_at_ms=record.committed_at_ms,
            speaker_scope=speaker_scope,
            speaker_participant_id=record.sender_participant_id,
            text=record.text,
            provenance=provenance,
            receipt_refs=references[event_id],
        ))
        decisions.append({
            "event_id": event_id,
            "content_eligible": True,
            "attention_occupied": True,
            "canonical_event_loaded": True,
            "memory_card_hydrated": True,
            "evidence_lane": lane,
        })

    not_loaded = tuple(
        event_id for event_id in canonical_ids if event_id not in set(selected_ids))
    receipt = {
        "operator_version": MEMORY_CARD_HYDRATION_VERSION,
        "join_operator_version": canonical_batch.operator_version,
        "attention_formula_version": attention_plan.formula_version,
        "trigger_event_id": attention_plan.trigger_event_id,
        "evaluation_time_ms": attention_plan.evaluation_time_ms,
        "canonical_event_ids": canonical_ids,
        "occupied_event_ids": selected_ids,
        "loaded_event_ids": selected_ids,
        "not_loaded_event_ids": not_loaded,
        "hydrated_card_ids": tuple(card.event_id for card in cards),
        "decisions": tuple(decisions),
        "content_in_receipt": False,
        "selection_recomputed": False,
        "numeric_witness_mutated": False,
        "p4_read": False,
        "writer_read": False,
        "store_written": False,
    }
    return MemoryCardHydrationBatch(
        trigger_event_id=attention_plan.trigger_event_id,
        evaluation_time_ms=attention_plan.evaluation_time_ms,
        formula_version=MEMORY_CARD_HYDRATION_VERSION,
        cards=tuple(cards),
        receipt=receipt,
    )
