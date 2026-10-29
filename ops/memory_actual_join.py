"""Alpha 0.4 exact-ID join after dynamic actual recall has settled.

Actual-recalled IDs carry state and content authority.  Available address-only IDs
carry content authority only.  The join never reconstructs a numeric gate and never
combines address similarity with root-state evidence.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType

from ops.memory_actual_state import resolve_actual_recall_identity
from ops.memory_contracts import AddressReceipt


ACTUAL_RECALL_EVIDENCE_JOIN_VERSION = "actual-recall-evidence-join-v2"


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


@dataclass(frozen=True)
class ActualRecallCanonicalItem:
    event_id: str
    actual_recalled: bool
    address_receipt: AddressReceipt | None
    content_eligible: bool
    state_eligible: bool

    def __post_init__(self):
        event_id = _identifier(self.event_id, "event_id")
        object.__setattr__(self, "event_id", event_id)
        if not isinstance(self.actual_recalled, bool):
            raise ValueError("actual_recalled must be boolean")
        if self.address_receipt is not None:
            if not isinstance(self.address_receipt, AddressReceipt):
                raise ValueError("address_receipt must be AddressReceipt or None")
            if self.address_receipt.event_id != event_id:
                raise ValueError("address receipt must share the exact event ID")
        for field in ("content_eligible", "state_eligible"):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} must be boolean")
        if self.state_eligible != self.actual_recalled:
            raise ValueError("state eligibility must equal the actual recall fact")
        address_available = bool(
            self.address_receipt is not None
            and self.address_receipt.status == "available")
        if self.content_eligible != (self.actual_recalled or address_available):
            raise ValueError("content eligibility must come from recall or available address")
        if not self.content_eligible:
            raise ValueError("canonical items must be content eligible")

    def to_record(self):
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "actual_recalled": self.actual_recalled,
            "address_receipt": (
                self.address_receipt.to_record() if self.address_receipt else None),
            "content_eligible": self.content_eligible,
            "state_eligible": self.state_eligible,
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "event_id", "actual_recalled", "address_receipt",
            "content_eligible", "state_eligible",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("actual recall canonical item schema is unsupported")
        if record.get("schema_version") != 1:
            raise ValueError("actual recall canonical item schema is unsupported")
        address = (AddressReceipt.from_record(record["address_receipt"])
                   if record["address_receipt"] is not None else None)
        return cls(
            event_id=record["event_id"],
            actual_recalled=record["actual_recalled"],
            address_receipt=address,
            content_eligible=record["content_eligible"],
            state_eligible=record["state_eligible"],
        )


@dataclass(frozen=True)
class ActualRecallCanonicalBatch:
    items: tuple[ActualRecallCanonicalItem, ...]
    trigger_event_id: str
    evaluation_time_ms: int
    operator_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        items = tuple(self.items)
        if any(not isinstance(item, ActualRecallCanonicalItem) for item in items):
            raise ValueError("items must contain ActualRecallCanonicalItem values")
        ids = tuple(item.event_id for item in items)
        if len(ids) != len(set(ids)):
            raise ValueError("canonical item exact IDs must be unique")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "trigger_event_id", _identifier(
            self.trigger_event_id, "trigger_event_id"))
        if isinstance(self.evaluation_time_ms, bool):
            raise ValueError("evaluation_time_ms must be a non-negative integer")
        try:
            at_ms = int(self.evaluation_time_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "evaluation_time_ms must be a non-negative integer") from exc
        if at_ms < 0 or at_ms != self.evaluation_time_ms:
            raise ValueError("evaluation_time_ms must be a non-negative integer")
        object.__setattr__(self, "evaluation_time_ms", at_ms)
        object.__setattr__(self, "operator_version", _identifier(
            self.operator_version, "operator_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "actual_recall_canonical_batch",
            "items": [item.to_record() for item in self.items],
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "operator_version": self.operator_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "kind", "items", "trigger_event_id",
            "evaluation_time_ms", "operator_version", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("actual recall canonical batch schema is unsupported")
        if (record.get("schema_version") != 1
                or record.get("kind") != "actual_recall_canonical_batch"
                or not isinstance(record.get("items"), list)):
            raise ValueError("actual recall canonical batch schema is unsupported")
        return cls(
            items=tuple(ActualRecallCanonicalItem.from_record(item)
                        for item in record["items"]),
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            operator_version=record["operator_version"],
            receipt=record["receipt"],
        )


def join_actual_recall_and_address(
        *, actual_recall_receipt: Mapping[str, object],
        address_receipts: Sequence[AddressReceipt]) -> ActualRecallCanonicalBatch:
    """Join settled actual recall and address evidence without score fusion."""
    trigger_id, evaluation_time, recalled_ids = resolve_actual_recall_identity(
        actual_recall_receipt)
    recalled = frozenset(recalled_ids)
    address_by_id = {}
    for receipt in tuple(address_receipts or ()):
        if not isinstance(receipt, AddressReceipt):
            raise ValueError("address_receipts must contain AddressReceipt values")
        if receipt.event_id in address_by_id:
            raise ValueError("address receipt exact IDs must be unique")
        address_by_id[receipt.event_id] = receipt

    all_ids = sorted(recalled | set(address_by_id))
    items = []
    decisions = {}
    omitted = []
    for event_id in all_ids:
        actual = event_id in recalled
        address = address_by_id.get(event_id)
        address_available = bool(address is not None and address.status == "available")
        content = actual or address_available
        if not content:
            omitted.append(event_id)
            decisions[event_id] = {
                "actual_recalled": actual,
                "address_status": address.status if address else "absent",
                "state_eligible": False,
                "content_eligible": False,
                "outcome": "neither_omitted",
            }
            continue
        lane = "dual" if actual and address_available else (
            "actual_recall_only" if actual else "address_only")
        items.append(ActualRecallCanonicalItem(
            event_id=event_id,
            actual_recalled=actual,
            address_receipt=address,
            content_eligible=True,
            state_eligible=actual,
        ))
        decisions[event_id] = {
            "actual_recalled": actual,
            "address_status": address.status if address else "absent",
            "state_eligible": actual,
            "content_eligible": True,
            "outcome": lane,
        }

    return ActualRecallCanonicalBatch(
        items=tuple(items),
        trigger_event_id=trigger_id,
        evaluation_time_ms=evaluation_time,
        operator_version=ACTUAL_RECALL_EVIDENCE_JOIN_VERSION,
        receipt={
            "operator_version": ACTUAL_RECALL_EVIDENCE_JOIN_VERSION,
            "actual_recall_receipt_version": actual_recall_receipt["version"],
            "trigger_event_id": trigger_id,
            "evaluation_time_ms": evaluation_time,
            "actual_recalled_event_ids": tuple(sorted(recalled)),
            "canonical_event_ids": tuple(item.event_id for item in items),
            "state_event_ids": tuple(
                item.event_id for item in items if item.state_eligible),
            "content_event_ids": tuple(item.event_id for item in items),
            "omitted_event_ids": tuple(omitted),
            "decisions": decisions,
            "join_key": "exact_event_id",
            "score_fusion_performed": False,
            "state_rule": "actual_recall_only",
            "address_rule": "available_address_grants_content_only",
        },
    )
