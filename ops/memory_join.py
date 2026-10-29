"""Alpha 0.4 M5 pure exact-ID join for numeric and address evidence.

This module joins already-issued witnesses.  It does not calculate either lane,
requalify similarities, hydrate text, apply P4, or write actual recall history.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from ops.memory_contracts import (
    AddressReceipt,
    CanonicalRecallItem,
    NumericRecallWitness,
)


MEMORY_EVIDENCE_JOIN_VERSION = "memory-evidence-join-v1"


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _index_witnesses(values, expected_type, lane, closed_ids):
    indexed = {}
    for item in tuple(values):
        if not isinstance(item, expected_type):
            raise ValueError(f"{lane} witnesses contain an unsupported value")
        if item.event_id in indexed:
            raise ValueError(f"duplicate {lane} witness exact ID")
        if item.event_id not in closed_ids:
            raise ValueError(f"{lane} witness exact ID is outside the closed set")
        indexed[item.event_id] = item
    return indexed


@dataclass(frozen=True)
class CanonicalRecallBatch:
    items: tuple[CanonicalRecallItem, ...]
    operator_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        items = tuple(self.items)
        if any(not isinstance(item, CanonicalRecallItem) for item in items):
            raise ValueError("items must contain CanonicalRecallItem values")
        ids = [item.event_id for item in items]
        if len(set(ids)) != len(ids):
            raise ValueError("canonical item exact IDs must be unique")
        if any(not item.internal_recall_eligible for item in items):
            raise ValueError("canonical items must be internally recall eligible")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "operator_version", _identifier(
            self.operator_version, "operator_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "canonical_recall_batch",
            "operator_version": self.operator_version,
            "items": [item.to_record() for item in self.items],
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, Mapping):
            raise ValueError("canonical recall batch record must be a mapping")
        required = {"schema_version", "kind", "operator_version", "items", "receipt"}
        if set(record) != required or record.get("schema_version") != 1:
            raise ValueError("canonical recall batch schema is unsupported")
        if record.get("kind") != "canonical_recall_batch":
            raise ValueError("canonical recall batch kind is unsupported")
        if not isinstance(record["items"], list):
            raise ValueError("canonical recall batch items must be a list")
        return cls(
            items=tuple(CanonicalRecallItem.from_record(item) for item in record["items"]),
            operator_version=record["operator_version"],
            receipt=record["receipt"],
        )


def join_recall_evidence(
    *,
    event_ids: Sequence[str],
    numeric_witnesses: Sequence[NumericRecallWitness],
    address_receipts: Sequence[AddressReceipt],
) -> CanonicalRecallBatch:
    """Join two independent evidence lanes without combining their scores."""
    ids = tuple(_identifier(item, "event_id") for item in event_ids)
    if not ids:
        raise ValueError("event_ids must be a non-empty closed set")
    if len(set(ids)) != len(ids):
        raise ValueError("closed event_ids must be unique")
    closed_ids = frozenset(ids)
    numeric_by_id = _index_witnesses(
        numeric_witnesses, NumericRecallWitness, "numeric", closed_ids)
    address_by_id = _index_witnesses(
        address_receipts, AddressReceipt, "address", closed_ids)

    items = []
    decisions = {}
    canonical_ids = []
    omitted_ids = []
    for event_id in sorted(ids):
        numeric = numeric_by_id.get(event_id)
        address = address_by_id.get(event_id)
        numeric_eligible = bool(numeric is not None and numeric.eligible)
        address_eligible = bool(address is not None and address.status == "available")
        state_eligible = numeric_eligible
        content_eligible = numeric_eligible or address_eligible
        internal_eligible = content_eligible

        if numeric_eligible and address_eligible:
            lane_outcome = "dual"
        elif numeric_eligible:
            lane_outcome = "numeric_only"
        elif address_eligible:
            lane_outcome = "address_only"
        else:
            lane_outcome = "neither"

        decision = {
            "numeric_witness": "eligible" if numeric_eligible else (
                "ineligible" if numeric is not None else "absent"),
            "address_status": address.status if address is not None else "absent",
            "internal_recall_eligible": internal_eligible,
            "content_eligible": content_eligible,
            "state_eligible": state_eligible,
            "lane_outcome": lane_outcome,
            "outcome": (
                f"canonical_{lane_outcome}"
                if internal_eligible else "neither_omitted"
            ),
        }
        decisions[event_id] = decision
        if not internal_eligible:
            omitted_ids.append(event_id)
            continue
        items.append(CanonicalRecallItem(
            event_id=event_id,
            numeric_witness=numeric,
            address_receipt=address,
            internal_recall_eligible=True,
            content_eligible=content_eligible,
            state_eligible=state_eligible,
        ))
        canonical_ids.append(event_id)

    return CanonicalRecallBatch(
        items=tuple(items),
        operator_version=MEMORY_EVIDENCE_JOIN_VERSION,
        receipt={
            "operator_version": MEMORY_EVIDENCE_JOIN_VERSION,
            "candidate_policy": "explicit_closed_exact_ids",
            "join_key": "exact_event_id",
            "numeric_rule": "eligible_witness_grants_state_and_content",
            "address_rule": "available_only_grants_content_never_state",
            "closed_event_ids": tuple(sorted(ids)),
            "canonical_event_ids": tuple(canonical_ids),
            "omitted_event_ids": tuple(omitted_ids),
            "numeric_witness_count": len(numeric_by_id),
            "address_receipt_count": len(address_by_id),
            "decisions": decisions,
        },
    )
