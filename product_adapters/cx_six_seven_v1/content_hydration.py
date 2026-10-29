"""CX67 product hydration over committed conversation content records.

Temporal ordering is supplied as domain-issued evidence.  This module never
interprets a position coordinate, chooses memories, or builds writer cards.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from interaction_extensions.content_attention import ContentOccupation
from framework.model_api import model_data_fingerprint
from framework.temporal_domain import TemporalOrderProof, TemporalPosition
from product_adapters.cx_six_seven_v1.realizer_packet import WriterCard


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _ids(value: object, field: str, *, allow_empty: bool = True) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an exact-ID sequence")
    result = tuple(_identifier(item, field) for item in value)
    if not allow_empty and not result:
        raise ValueError(f"{field} cannot be empty")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} exact IDs must be unique")
    return result


def _ordinal(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


@dataclass(frozen=True)
class CommittedContentRecord:
    """One committed, visible text fact independent of product event classes."""

    event_id: str
    conversation_id: str
    member_snapshot_ids: tuple[str, ...]
    sender_participant_id: str
    visible_recipient_ids: tuple[str, ...]
    addressed_participant_ids: tuple[str, ...]
    text: str
    temporal_position: TemporalPosition
    runtime_ordinal: int
    source_receipt_refs: tuple[str, ...]
    kind: str = "message"

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(
            self.event_id, "committed content event ID"))
        object.__setattr__(self, "conversation_id", _identifier(
            self.conversation_id, "committed content conversation ID"))
        kind = _identifier(self.kind, "committed content kind")
        if kind != "message":
            raise ValueError("committed content kind must be message")
        object.__setattr__(self, "kind", kind)
        members = _ids(
            self.member_snapshot_ids, "member_snapshot_ids", allow_empty=False)
        sender = _identifier(
            self.sender_participant_id, "sender_participant_id")
        visible = _ids(self.visible_recipient_ids, "visible_recipient_ids")
        addressed = _ids(
            self.addressed_participant_ids,
            "addressed_participant_ids",
            allow_empty=False,
        )
        if sender not in members:
            raise ValueError("committed content sender is outside member snapshot")
        if sender in visible or not set(visible).issubset(set(members)):
            raise ValueError("committed content visible recipients are invalid")
        if not set(addressed).issubset(set(visible)):
            raise ValueError("committed content addressed participants are not visible")
        text = str(self.text or "").strip()
        if not text:
            raise ValueError("committed content text cannot be empty")
        if not isinstance(self.temporal_position, TemporalPosition):
            raise ValueError("committed content position must be TemporalPosition")
        self.temporal_position.verify_integrity()
        refs = _ids(
            self.source_receipt_refs,
            "source_receipt_refs",
            allow_empty=False,
        )
        object.__setattr__(self, "member_snapshot_ids", members)
        object.__setattr__(self, "sender_participant_id", sender)
        object.__setattr__(self, "visible_recipient_ids", visible)
        object.__setattr__(self, "addressed_participant_ids", addressed)
        object.__setattr__(self, "text", text)
        object.__setattr__(self, "runtime_ordinal", _ordinal(
            self.runtime_ordinal, "committed content runtime ordinal"))
        object.__setattr__(self, "source_receipt_refs", refs)


@dataclass(frozen=True)
class CommittedContentRead:
    """One loaded record with a bound-domain proof that it is not future data."""

    record: CommittedContentRecord
    order_proof: TemporalOrderProof

    def __post_init__(self) -> None:
        if not isinstance(self.record, CommittedContentRecord):
            raise ValueError("content read record must be CommittedContentRecord")
        if not isinstance(self.order_proof, TemporalOrderProof):
            raise ValueError("content read order proof must be TemporalOrderProof")
        self.order_proof.verify_integrity()
        if (self.order_proof.from_position.fingerprint
                != self.record.temporal_position.fingerprint):
            raise ValueError("content read proof does not start at committed position")


@dataclass(frozen=True)
class CommittedContentBatch:
    """Hydrated content plus a content-free audit receipt."""

    owner_participant_id: str
    records: tuple[CommittedContentRecord, ...]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_participant_id", _identifier(
            self.owner_participant_id, "content batch owner participant ID"))
        records = tuple(self.records)
        if any(not isinstance(item, CommittedContentRecord) for item in records):
            raise ValueError("content batch records are invalid")
        if len({item.event_id for item in records}) != len(records):
            raise ValueError("content batch exact IDs must be unique")
        object.__setattr__(self, "records", records)
        if not isinstance(self.receipt, Mapping):
            raise ValueError("content batch receipt must be a mapping")
        object.__setattr__(self, "receipt", MappingProxyType(dict(self.receipt)))


def load_occupied_committed_content(
    *,
    canonical_event_ids: Sequence[str],
    occupation: ContentOccupation,
    evaluation_position: TemporalPosition,
    evaluation_runtime_ordinal: int,
    owner_participant_id: str,
    content_loader: Callable[[str], CommittedContentRead],
) -> CommittedContentBatch:
    """Load only occupied exact IDs and verify visibility/order evidence."""
    canonical = _ids(canonical_event_ids, "canonical_event_ids")
    if not isinstance(occupation, ContentOccupation):
        raise ValueError("occupation must be ContentOccupation")
    occupation.verify_integrity()
    if tuple(occupation.claim_event_ids) != canonical:
        raise ValueError("occupation claim IDs differ from canonical event IDs")
    selected = tuple(occupation.selected_for_hydration_ids)
    if not isinstance(evaluation_position, TemporalPosition):
        raise ValueError("evaluation_position must be TemporalPosition")
    evaluation_position.verify_integrity()
    evaluation_ordinal = _ordinal(
        evaluation_runtime_ordinal, "evaluation_runtime_ordinal")
    owner = _identifier(owner_participant_id, "owner_participant_id")
    if not callable(content_loader):
        raise ValueError("content_loader must be callable")

    records = []
    proof_refs = []
    source_refs = []
    for event_id in selected:
        loaded = content_loader(event_id)
        if not isinstance(loaded, CommittedContentRead):
            raise ValueError("content_loader must return CommittedContentRead")
        record = loaded.record
        proof = loaded.order_proof
        if record.event_id != event_id:
            raise ValueError("loaded content exact ID does not match requested exact ID")
        proof.verify_integrity()
        if proof.binding != evaluation_position.binding:
            raise ValueError("content order proof binding differs from evaluation position")
        if proof.from_position.fingerprint != record.temporal_position.fingerprint:
            raise ValueError("content proof does not start at committed position")
        if proof.to_position.fingerprint != evaluation_position.fingerprint:
            raise ValueError("content proof does not end at evaluation position")
        if record.runtime_ordinal > evaluation_ordinal:
            raise ValueError("future content runtime ordinal cannot be hydrated")
        if owner not in record.member_snapshot_ids:
            raise ValueError("content is outside the owner member snapshot")
        if (record.sender_participant_id != owner
                and owner not in record.visible_recipient_ids):
            raise ValueError("content was not visible to the owner")
        records.append(record)
        proof_refs.append(proof.fingerprint)
        source_refs.extend(record.source_receipt_refs)

    receipt = {
        "occupation_version": occupation.version,
        "canonical_event_ids": canonical,
        "selected_for_hydration_ids": selected,
        "loaded_event_ids": tuple(item.event_id for item in records),
        "evaluation_position_fingerprint": evaluation_position.fingerprint,
        "evaluation_runtime_ordinal": evaluation_ordinal,
        "order_proof_fingerprints": tuple(proof_refs),
        "source_receipt_refs": tuple(dict.fromkeys(source_refs)),
        "content_in_receipt": False,
        "selection_recomputed": False,
        "writer_read": False,
        "state_mutated": False,
    }
    return CommittedContentBatch(
        owner_participant_id=owner,
        records=tuple(records),
        receipt=receipt,
    )


def committed_content_writer_cards(
    batch: CommittedContentBatch,
) -> tuple[WriterCard, ...]:
    """Bridge verified content one-for-one without ranking or reselection."""
    if not isinstance(batch, CommittedContentBatch):
        raise ValueError("writer-card bridge requires CommittedContentBatch")
    record_ids = tuple(record.event_id for record in batch.records)
    receipt = batch.receipt
    selected = tuple(receipt.get("selected_for_hydration_ids", ()))
    loaded = tuple(receipt.get("loaded_event_ids", ()))
    if selected != record_ids or loaded != record_ids:
        raise ValueError("content batch selection lineage differs from records")
    if receipt.get("content_in_receipt") is not False:
        raise ValueError("content batch receipt must remain content-free")
    if receipt.get("selection_recomputed") is not False:
        raise ValueError("content batch selection cannot be recomputed")
    hydration_ref = "content-hydration:" + model_data_fingerprint({
        "owner_participant_id": batch.owner_participant_id,
        "receipt": dict(receipt),
    })
    cards = []
    for record in batch.records:
        speaker_scope = (
            "self" if record.sender_participant_id == batch.owner_participant_id
            else "peer"
        )
        cards.append(WriterCard(
            card_id=f"memory:{record.event_id}",
            lane="memory",
            kind="committed_content",
            source_id=record.event_id,
            fields={
                "event_id": record.event_id,
                "conversation_id": record.conversation_id,
                "temporal_position": record.temporal_position.to_record(),
                "runtime_ordinal": record.runtime_ordinal,
                "speaker_scope": speaker_scope,
                "speaker_participant_id": record.sender_participant_id,
                "addressed_participant_ids": list(
                    record.addressed_participant_ids),
                "text": record.text,
            },
            source_receipt_refs=tuple(dict.fromkeys((
                *record.source_receipt_refs,
                hydration_ref,
            ))),
            budget_policy="preserve_selection",
        ))
    return tuple(cards)


def numbered_committed_content_writer_card(batch: CommittedContentBatch) -> WriterCard | None:
    """CX display grouping, not a merged fact or a second memory selection.

    The original bridge validates selection lineage and source references.
    Only display order changes; original event identities remain in archived
    provenance. Clock/domain fingerprints are not model-visible semantics.
    """
    original_cards = committed_content_writer_cards(batch)
    if not original_cards:
        return None
    records = tuple(sorted(batch.records, key=lambda record: record.runtime_ordinal))
    ordinals = tuple(record.runtime_ordinal for record in records)
    if len(ordinals) != len(set(ordinals)):
        raise ValueError("numbered memory requires unique occurrence ordinals")
    selected = tuple(record.event_id for record in records)
    fields = {"相关记忆（按发生顺序）": [dict(
        编号=index, 说话者=record.sender_participant_id,
        对象=list(record.addressed_participant_ids),
        位置={"domain": record.temporal_position.binding.domain_id,
              "coordinate": record.temporal_position.to_record()["coordinate"]},
        推进序号=record.runtime_ordinal, 原话=record.text,
    ) for index, record in enumerate(records, 1)]}
    provenance = {
        "presentation_version": "cx-numbered-memory-v1",
        "selected_memory_ids": list(selected),
        "fields_fingerprint": model_data_fingerprint(fields),
        "hydration_receipt": dict(batch.receipt),
        "records": [dict(
            event_id=record.event_id, conversation_id=record.conversation_id,
            runtime_ordinal=record.runtime_ordinal,
            temporal_position_fingerprint=record.temporal_position.fingerprint,
            source_receipt_refs=list(record.source_receipt_refs),
        ) for record in records],
    }
    fingerprint = model_data_fingerprint(provenance)
    return WriterCard(
        card_id="memory:numbered:" + fingerprint, lane="memory",
        kind="numbered_committed_content", source_id="cx-numbered-memory-v1",
        fields=fields,
        source_receipt_refs=tuple(dict.fromkeys((
            *(ref for card in original_cards for ref in card.source_receipt_refs),
            "numbered-memory:" + fingerprint,
        ))),
        provenance=provenance, budget_policy="preserve_selection",
    )


__all__ = (
    "CommittedContentBatch",
    "CommittedContentRead",
    "CommittedContentRecord",
    "committed_content_writer_cards",
    "numbered_committed_content_writer_card",
    "load_occupied_committed_content",
)
