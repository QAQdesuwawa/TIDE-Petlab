"""Alpha 0.4 M5 pure P4 input-lineage bridge.

This module does not calculate state impact.  It proves that every canonical
state-eligible exact ID still points to the D1 live vector that produced its M3
numeric witness, while content-only address evidence remains outside P4.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType

from core.interaction_geometry import ROOT_ORDER
from ops.memory_contracts import NeutralMemoryReadout
from ops.memory_join import CanonicalRecallBatch


RECALL_STATE_INPUT_VERSION = "recall-state-input-v1"


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer timestamp")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer timestamp") from exc
    if parsed < 0 or parsed != value:
        raise ValueError(f"{field} must be a non-negative integer timestamp")
    return parsed


def _finite(value, field):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _unit(value, field):
    parsed = _finite(value, field)
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{field} must be between zero and one")
    return parsed


def _root_vector(value, field):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    return MappingProxyType({
        axis: _finite(value[axis], f"{field}.{axis}") for axis in ROOT_ORDER
    })


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({str(key): _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("receipt values must be finite")
        return value
    raise ValueError("receipt must contain serializable values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class RecallStateInputItem:
    event_id: str
    trigger_event_id: str
    evaluation_time_ms: int
    pre_recall_live_vector: Mapping[str, float]
    activation: float
    condition: float
    scheduler_version: str
    neutral_readout_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        for field in ("event_id", "trigger_event_id", "scheduler_version",
                      "neutral_readout_version"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if self.event_id == self.trigger_event_id:
            raise ValueError("historical state input cannot be its own trigger")
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(self, "pre_recall_live_vector", _root_vector(
            self.pre_recall_live_vector, "pre_recall_live_vector"))
        object.__setattr__(self, "activation", _unit(self.activation, "activation"))
        condition = _unit(self.condition, "condition")
        if condition >= 1.0:
            raise ValueError("condition must be strictly below one")
        if self.activation < condition:
            raise ValueError("state input activation must be at or above condition")
        object.__setattr__(self, "condition", condition)
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "pre_recall_live_vector": dict(self.pre_recall_live_vector),
            "activation": self.activation,
            "condition": self.condition,
            "scheduler_version": self.scheduler_version,
            "neutral_readout_version": self.neutral_readout_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "event_id", "trigger_event_id", "evaluation_time_ms",
            "pre_recall_live_vector", "activation", "condition", "scheduler_version",
            "neutral_readout_version", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("recall state input item schema is unsupported")
        if record.get("schema_version") != 1:
            raise ValueError("recall state input item schema is unsupported")
        return cls(
            event_id=record["event_id"],
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            pre_recall_live_vector=record["pre_recall_live_vector"],
            activation=record["activation"],
            condition=record["condition"],
            scheduler_version=record["scheduler_version"],
            neutral_readout_version=record["neutral_readout_version"],
            receipt=record["receipt"],
        )


@dataclass(frozen=True)
class RecallStateInputBatch:
    items: tuple[RecallStateInputItem, ...]
    trigger_event_id: str
    evaluation_time_ms: int
    current_trigger_vector: Mapping[str, float]
    current_readout_version: str
    operator_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        items = tuple(self.items)
        if any(not isinstance(item, RecallStateInputItem) for item in items):
            raise ValueError("items must contain RecallStateInputItem values")
        ids = [item.event_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("state input exact IDs must be unique")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "trigger_event_id", _identifier(
            self.trigger_event_id, "trigger_event_id"))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(self, "current_trigger_vector", _root_vector(
            self.current_trigger_vector, "current_trigger_vector"))
        object.__setattr__(self, "current_readout_version", _identifier(
            self.current_readout_version, "current_readout_version"))
        object.__setattr__(self, "operator_version", _identifier(
            self.operator_version, "operator_version"))
        for item in items:
            if item.trigger_event_id != self.trigger_event_id:
                raise ValueError("state inputs must share one trigger exact ID")
            if item.evaluation_time_ms != self.evaluation_time_ms:
                raise ValueError("state inputs must share one evaluation time")
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "recall_state_input_batch",
            "items": [item.to_record() for item in self.items],
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "current_trigger_vector": dict(self.current_trigger_vector),
            "current_readout_version": self.current_readout_version,
            "operator_version": self.operator_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "kind", "items", "trigger_event_id",
            "evaluation_time_ms", "current_trigger_vector", "current_readout_version",
            "operator_version", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("recall state input batch schema is unsupported")
        if record.get("schema_version") != 1 or record.get("kind") != "recall_state_input_batch":
            raise ValueError("recall state input batch schema is unsupported")
        if not isinstance(record["items"], list):
            raise ValueError("recall state input batch items must be a list")
        return cls(
            items=tuple(RecallStateInputItem.from_record(item) for item in record["items"]),
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            current_trigger_vector=record["current_trigger_vector"],
            current_readout_version=record["current_readout_version"],
            operator_version=record["operator_version"],
            receipt=record["receipt"],
        )


def _index_readouts(readouts: Sequence[NeutralMemoryReadout]):
    indexed = {}
    for readout in tuple(readouts):
        if not isinstance(readout, NeutralMemoryReadout):
            raise ValueError("historical_readouts must contain NeutralMemoryReadout values")
        event_id = readout.event.event_id
        if event_id in indexed:
            raise ValueError("historical D1 readout exact IDs must be unique")
        indexed[event_id] = readout
    return indexed


def _receipt_value(receipt, field):
    if field not in receipt:
        raise ValueError(f"numeric witness receipt missing {field}")
    return receipt[field]


def prepare_recall_state_inputs(
    *,
    canonical_batch: CanonicalRecallBatch,
    historical_readouts: Sequence[NeutralMemoryReadout],
    current_readout: NeutralMemoryReadout,
) -> RecallStateInputBatch:
    """Resolve canonical state authority to its original D1 vectors, without P4 math."""
    if not isinstance(canonical_batch, CanonicalRecallBatch):
        raise ValueError("canonical_batch must be CanonicalRecallBatch")
    if not isinstance(current_readout, NeutralMemoryReadout):
        raise ValueError("current_readout must be NeutralMemoryReadout")

    state_items = tuple(item for item in canonical_batch.items if item.state_eligible)
    state_ids = tuple(item.event_id for item in state_items)
    readouts_by_id = _index_readouts(historical_readouts)
    if set(readouts_by_id) != set(state_ids):
        missing = sorted(set(state_ids) - set(readouts_by_id))
        extra = sorted(set(readouts_by_id) - set(state_ids))
        raise ValueError(f"historical D1 readouts must exactly match state IDs; missing={missing}; extra={extra}")

    decisions = {
        item.event_id: (
            "prepared_from_numeric_d1" if item.state_eligible else "content_only_no_state"
        )
        for item in canonical_batch.items
    }
    prepared = []
    for item in sorted(state_items, key=lambda row: row.event_id):
        witness = item.numeric_witness
        if witness is None or not witness.eligible:
            raise ValueError("state-eligible canonical item requires an eligible numeric witness")
        receipt = witness.receipt
        target_id = _identifier(_receipt_value(receipt, "target_event_id"), "target_event_id")
        trigger_id = _identifier(_receipt_value(receipt, "trigger_event_id"), "trigger_event_id")
        evaluation_time = _timestamp(
            _receipt_value(receipt, "evaluation_time_ms"), "evaluation_time_ms")
        neutral_version = _identifier(
            _receipt_value(receipt, "neutral_readout_version"),
            "neutral_readout_version",
        )
        current_version = _identifier(
            _receipt_value(receipt, "current_readout_version"),
            "current_readout_version",
        )
        receipt_activation = _unit(_receipt_value(receipt, "activation"), "activation")
        receipt_condition = _unit(_receipt_value(receipt, "condition"), "condition")
        historical = readouts_by_id[item.event_id]

        if target_id != item.event_id or historical.event.event_id != item.event_id:
            raise ValueError("numeric witness target does not match historical exact ID")
        if trigger_id != current_readout.event.event_id:
            raise ValueError("numeric witness trigger does not match current D1 exact ID")
        if historical.event.committed_at_ms >= current_readout.event.committed_at_ms:
            raise ValueError("historical D1 readout must be strictly earlier than current trigger")
        if evaluation_time != historical.as_of_ms or evaluation_time != current_readout.as_of_ms:
            raise ValueError("numeric witness and D1 readouts must share one evaluation time")
        if neutral_version != witness.neutral_readout_version:
            raise ValueError("numeric witness neutral version fields disagree")
        if historical.formula_version != neutral_version:
            raise ValueError("historical D1 formula version does not match numeric witness")
        if current_readout.formula_version != current_version:
            raise ValueError("current D1 formula version does not match numeric witness")
        if abs(receipt_activation - witness.activation) > 1e-12:
            raise ValueError("numeric witness activation fields disagree")
        if abs(receipt_condition - witness.condition) > 1e-12:
            raise ValueError("numeric witness condition fields disagree")

        prepared.append(RecallStateInputItem(
            event_id=item.event_id,
            trigger_event_id=trigger_id,
            evaluation_time_ms=evaluation_time,
            pre_recall_live_vector=historical.live_vector,
            activation=witness.activation,
            condition=witness.condition,
            scheduler_version=witness.scheduler_version,
            neutral_readout_version=neutral_version,
            receipt={
                "operator_version": RECALL_STATE_INPUT_VERSION,
                "canonical_join_version": canonical_batch.operator_version,
                "event_id": item.event_id,
                "trigger_event_id": trigger_id,
                "evaluation_time_ms": evaluation_time,
                "scheduler_version": witness.scheduler_version,
                "neutral_readout_version": neutral_version,
                "activation": witness.activation,
                "condition": witness.condition,
                "historical_lineage": tuple(historical.lineage),
                "input_rule": "exact_id_state_authority_to_original_d1_live_vector",
            },
        ))

    return RecallStateInputBatch(
        items=tuple(prepared),
        trigger_event_id=current_readout.event.event_id,
        evaluation_time_ms=current_readout.as_of_ms,
        current_trigger_vector=current_readout.live_vector,
        current_readout_version=current_readout.formula_version,
        operator_version=RECALL_STATE_INPUT_VERSION,
        receipt={
            "operator_version": RECALL_STATE_INPUT_VERSION,
            "canonical_join_version": canonical_batch.operator_version,
            "state_event_ids": tuple(item.event_id for item in prepared),
            "content_only_event_ids": tuple(sorted(
                item.event_id for item in canonical_batch.items if not item.state_eligible
            )),
            "decisions": decisions,
            "direction_source": "d1_pre_recall_live_vector",
            "strength_source": "m3_numeric_activation_and_condition_evidence_only",
            "impact_formula_status": "not_defined_in_input_bridge",
            "reconsolidation_status": "not_performed",
        },
    )
