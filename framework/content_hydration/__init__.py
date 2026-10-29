"""History-bound exact-ID hydration of opaque realization material.

The framework verifies authorization, identity, visibility already frozen by
History, and temporal ordering.  It neither interprets hydrated payloads nor
turns them into model-package realization resources.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from framework.history import HistoryRead
from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.temporal_domain import TemporalOrderProof, TemporalPosition


OPAQUE_CONTENT_HYDRATION_VERSION = "opaque-content-hydration-v1"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _identifiers(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an identifier sequence")
    normalized = tuple(_identifier(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} cannot contain duplicate identifiers")
    return normalized


def _ordinal(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _opaque(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an opaque mapping")
    return freeze_model_payload(value)


@dataclass(frozen=True)
class OpaqueContentRecord:
    version: str
    fact_id: str
    session_binding_fingerprint: str
    temporal_position: TemporalPosition
    accepted_runtime_ordinal: int
    payload: Mapping[str, Any]
    source_receipt_refs: tuple[str, ...]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        fact_id: str,
        session_binding_fingerprint: str,
        temporal_position: TemporalPosition,
        accepted_runtime_ordinal: int,
        payload: Mapping[str, Any],
        source_receipt_refs: Sequence[str],
    ) -> "OpaqueContentRecord":
        if not isinstance(temporal_position, TemporalPosition):
            raise ValueError("temporal_position must be TemporalPosition")
        temporal_position.verify_integrity()
        frozen_payload = _opaque(payload, "payload")
        material = {
            "version": OPAQUE_CONTENT_HYDRATION_VERSION,
            "fact_id": _identifier(fact_id, "fact_id"),
            "session_binding_fingerprint": _identifier(
                session_binding_fingerprint,
                "session_binding_fingerprint",
            ),
            "temporal_position": temporal_position.to_record(),
            "accepted_runtime_ordinal": _ordinal(
                accepted_runtime_ordinal,
                "accepted_runtime_ordinal",
            ),
            "payload": thaw_model_data(frozen_payload),
            "source_receipt_refs": list(_identifiers(
                source_receipt_refs, "source_receipt_refs")),
        }
        return cls(
            version=material["version"],
            fact_id=material["fact_id"],
            session_binding_fingerprint=material[
                "session_binding_fingerprint"],
            temporal_position=temporal_position,
            accepted_runtime_ordinal=material[
                "accepted_runtime_ordinal"],
            payload=frozen_payload,
            source_receipt_refs=tuple(material["source_receipt_refs"]),
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        expected = OpaqueContentRecord.create(
            fact_id=self.fact_id,
            session_binding_fingerprint=self.session_binding_fingerprint,
            temporal_position=self.temporal_position,
            accepted_runtime_ordinal=self.accepted_runtime_ordinal,
            payload=self.payload,
            source_receipt_refs=self.source_receipt_refs,
        )
        if expected != self:
            raise ValueError("opaque content record integrity is invalid")


@dataclass(frozen=True)
class OpaqueContentRead:
    record: OpaqueContentRecord
    order_proof: TemporalOrderProof

    def __post_init__(self) -> None:
        if not isinstance(self.record, OpaqueContentRecord):
            raise ValueError("opaque content read requires a content record")
        self.record.verify_integrity()
        if not isinstance(self.order_proof, TemporalOrderProof):
            raise ValueError("opaque content read requires an order proof")
        self.order_proof.verify_integrity()
        if (
            self.order_proof.from_position.fingerprint
            != self.record.temporal_position.fingerprint
        ):
            raise ValueError("content order proof does not start at its fact")


@dataclass(frozen=True)
class OpaqueContentBatch:
    reader_ref: str
    records: tuple[OpaqueContentRecord, ...]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "reader_ref", _identifier(
            self.reader_ref, "reader_ref"))
        records = tuple(self.records)
        if any(not isinstance(item, OpaqueContentRecord) for item in records):
            raise ValueError("opaque content batch records are invalid")
        if len({item.fact_id for item in records}) != len(records):
            raise ValueError("opaque content batch fact IDs must be unique")
        if not isinstance(self.receipt, Mapping):
            raise ValueError("opaque content batch receipt must be a mapping")
        object.__setattr__(self, "records", records)
        object.__setattr__(self, "receipt", MappingProxyType(dict(self.receipt)))


def _verify_history_read(history_read: HistoryRead) -> None:
    if not isinstance(history_read, HistoryRead):
        raise ValueError("hydration requires an authorized HistoryRead")
    facts_by_id = {fact.fact_id: fact for fact in history_read.facts}
    restored = HistoryRead.from_record(
        history_read.to_record(), facts_by_id=facts_by_id)
    if restored != history_read:
        raise ValueError("authorized HistoryRead integrity is invalid")


def hydrate_authorized_content(
    *,
    history_read: HistoryRead,
    selected_fact_ids: Sequence[str],
    evaluation_position: TemporalPosition,
    evaluation_runtime_ordinal: int,
    content_loader: Callable[[str], OpaqueContentRead],
) -> OpaqueContentBatch:
    """Hydrate an exact subset of one authorized History view."""
    _verify_history_read(history_read)
    selected = _identifiers(selected_fact_ids, "selected_fact_ids")
    if not set(selected).issubset(set(history_read.fact_ids)):
        raise ValueError(
            "selected fact IDs are outside the authorized history read")
    if not isinstance(evaluation_position, TemporalPosition):
        raise ValueError("evaluation_position must be TemporalPosition")
    evaluation_position.verify_integrity()
    evaluation_ordinal = _ordinal(
        evaluation_runtime_ordinal, "evaluation_runtime_ordinal")
    if not callable(content_loader):
        raise ValueError("content_loader must be callable")

    facts_by_id = {fact.fact_id: fact for fact in history_read.facts}
    records: list[OpaqueContentRecord] = []
    proof_refs: list[str] = []
    source_refs: list[str] = []
    for fact_id in selected:
        loaded = content_loader(fact_id)
        if not isinstance(loaded, OpaqueContentRead):
            raise ValueError("content_loader must return OpaqueContentRead")
        record = loaded.record
        proof = loaded.order_proof
        fact = facts_by_id[fact_id]
        if record.fact_id != fact_id:
            raise ValueError("loaded content exact ID differs from requested fact")
        if record.session_binding_fingerprint != (
                history_read.session_binding_fingerprint):
            raise ValueError("loaded content belongs to another runtime binding")
        if record.temporal_position.fingerprint != (
                fact.temporal_position_fingerprint):
            raise ValueError("loaded content position differs from History fact")
        if record.accepted_runtime_ordinal != fact.accepted_runtime_ordinal:
            raise ValueError("loaded content ordinal differs from History fact")
        if record.accepted_runtime_ordinal >= evaluation_ordinal:
            raise ValueError("hydrated content must be strictly prior")
        proof.verify_integrity()
        if proof.binding != evaluation_position.binding:
            raise ValueError("content proof binding differs from evaluation")
        if proof.from_position.fingerprint != record.temporal_position.fingerprint:
            raise ValueError("content proof does not start at loaded content")
        if proof.to_position.fingerprint != evaluation_position.fingerprint:
            raise ValueError("content proof does not end at evaluation position")
        records.append(record)
        proof_refs.append(proof.fingerprint)
        source_refs.extend(record.source_receipt_refs)

    material = {
        "version": OPAQUE_CONTENT_HYDRATION_VERSION,
        "history_read_fingerprint": history_read.fingerprint,
        "reader_ref": history_read.reader_ref,
        "selected_fact_ids": selected,
        "loaded_fact_ids": tuple(record.fact_id for record in records),
        "evaluation_position_fingerprint": evaluation_position.fingerprint,
        "evaluation_runtime_ordinal": evaluation_ordinal,
        "order_proof_fingerprints": tuple(proof_refs),
        "source_receipt_refs": tuple(dict.fromkeys(source_refs)),
        "payload_in_receipt": False,
        "selection_recomputed": False,
        "state_mutated": False,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return OpaqueContentBatch(
        reader_ref=history_read.reader_ref,
        records=tuple(records),
        receipt=receipt,
    )


__all__ = (
    "OPAQUE_CONTENT_HYDRATION_VERSION",
    "OpaqueContentBatch",
    "OpaqueContentRead",
    "OpaqueContentRecord",
    "hydrate_authorized_content",
)
