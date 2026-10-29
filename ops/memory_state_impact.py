"""Alpha 0.4 M5 pure P4 state-impact operator.

The operator consumes the exact-ID D1/M3 lineage prepared by
``memory_state_inputs``.  It computes only per-memory root-vector impacts and one
globally bounded fused delta.  It does not write state, create recall events, run
D3 reconsolidation, hydrate text, or access RAG/expression/timing surfaces.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType

from core.interaction_geometry import ROOT_ORDER
from ops.memory_state_inputs import RecallStateInputBatch


RECALL_STATE_IMPACT_VERSION = "recall-state-impact-v1"
_LEVERAGE_FORMULA = "max(0,(activation-condition)/(1-condition))"


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


def _positive(value, field):
    parsed = _finite(value, field)
    if parsed <= 0.0:
        raise ValueError(f"{field} must be positive")
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


def _zero_vector():
    return {axis: 0.0 for axis in ROOT_ORDER}


def _scale(vector, scalar):
    return {axis: float(vector[axis]) * scalar for axis in ROOT_ORDER}


def _norm(vector):
    return math.sqrt(sum(float(vector[axis]) ** 2 for axis in ROOT_ORDER))


def _same_number(left, right):
    return abs(float(left) - float(right)) <= 1e-12 * max(
        1.0, abs(float(left)), abs(float(right)))


def _same_vector(left, right):
    return all(_same_number(left[axis], right[axis]) for axis in ROOT_ORDER)


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
class RecallStateImpactItem:
    event_id: str
    trigger_event_id: str
    activation: float
    condition: float
    leverage: float
    pre_recall_live_vector: Mapping[str, float]
    impact_vector: Mapping[str, float]
    receipt: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "trigger_event_id", _identifier(
            self.trigger_event_id, "trigger_event_id"))
        if self.event_id == self.trigger_event_id:
            raise ValueError("state impact target cannot be its own trigger")
        activation = _unit(self.activation, "activation")
        condition = _unit(self.condition, "condition")
        if condition >= 1.0:
            raise ValueError("condition must be strictly below one")
        if activation < condition:
            raise ValueError("state impact activation must be at or above condition")
        object.__setattr__(self, "activation", activation)
        object.__setattr__(self, "condition", condition)
        object.__setattr__(self, "leverage", _unit(self.leverage, "leverage"))
        object.__setattr__(self, "pre_recall_live_vector", _root_vector(
            self.pre_recall_live_vector, "pre_recall_live_vector"))
        object.__setattr__(self, "impact_vector", _root_vector(
            self.impact_vector, "impact_vector"))
        expected_impact = _scale(self.pre_recall_live_vector, self.leverage)
        if not _same_vector(self.impact_vector, expected_impact):
            raise ValueError("impact_vector must equal leverage times D1 live vector")
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "trigger_event_id": self.trigger_event_id,
            "activation": self.activation,
            "condition": self.condition,
            "leverage": self.leverage,
            "pre_recall_live_vector": dict(self.pre_recall_live_vector),
            "impact_vector": dict(self.impact_vector),
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "event_id", "trigger_event_id", "activation",
            "condition", "leverage", "pre_recall_live_vector", "impact_vector",
            "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("recall state impact item schema is unsupported")
        if record.get("schema_version") != 1:
            raise ValueError("recall state impact item schema is unsupported")
        return cls(
            event_id=record["event_id"],
            trigger_event_id=record["trigger_event_id"],
            activation=record["activation"],
            condition=record["condition"],
            leverage=record["leverage"],
            pre_recall_live_vector=record["pre_recall_live_vector"],
            impact_vector=record["impact_vector"],
            receipt=record["receipt"],
        )


@dataclass(frozen=True)
class RecallStateImpactBatch:
    items: tuple[RecallStateImpactItem, ...]
    trigger_event_id: str
    evaluation_time_ms: int
    raw_sum_vector: Mapping[str, float]
    raw_norm: float
    fusion_radius: float
    projection_factor: float
    delta_x_memory: Mapping[str, float]
    parameter_version: str
    operator_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        items = tuple(self.items)
        if any(not isinstance(item, RecallStateImpactItem) for item in items):
            raise ValueError("items must contain RecallStateImpactItem values")
        ids = [item.event_id for item in items]
        if len(ids) != len(set(ids)):
            raise ValueError("state impact exact IDs must be unique")
        object.__setattr__(self, "items", items)
        trigger = _identifier(self.trigger_event_id, "trigger_event_id")
        object.__setattr__(self, "trigger_event_id", trigger)
        if any(item.trigger_event_id != trigger for item in items):
            raise ValueError("state impacts must share one trigger exact ID")
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(self, "raw_sum_vector", _root_vector(
            self.raw_sum_vector, "raw_sum_vector"))
        object.__setattr__(self, "raw_norm", _finite(self.raw_norm, "raw_norm"))
        if self.raw_norm < 0.0:
            raise ValueError("raw_norm must be non-negative")
        object.__setattr__(self, "fusion_radius", _positive(
            self.fusion_radius, "fusion_radius"))
        projection = _finite(self.projection_factor, "projection_factor")
        if projection < 1.0:
            raise ValueError("projection_factor must be at least one")
        object.__setattr__(self, "projection_factor", projection)
        object.__setattr__(self, "delta_x_memory", _root_vector(
            self.delta_x_memory, "delta_x_memory"))
        object.__setattr__(self, "parameter_version", _identifier(
            self.parameter_version, "parameter_version"))
        object.__setattr__(self, "operator_version", _identifier(
            self.operator_version, "operator_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

        expected_sum = _zero_vector()
        for item in items:
            expected_sum = {
                axis: expected_sum[axis] + item.impact_vector[axis]
                for axis in ROOT_ORDER
            }
        if not _same_vector(self.raw_sum_vector, expected_sum):
            raise ValueError("raw_sum_vector must equal the per-event impact sum")
        expected_norm = _norm(self.raw_sum_vector)
        if not _same_number(self.raw_norm, expected_norm):
            raise ValueError("raw_norm must equal the raw vector norm")
        expected_projection = max(1.0, expected_norm / self.fusion_radius)
        if not _same_number(self.projection_factor, expected_projection):
            raise ValueError("projection_factor must follow the global radial rule")
        expected_delta = _scale(self.raw_sum_vector, 1.0 / expected_projection)
        if not _same_vector(self.delta_x_memory, expected_delta):
            raise ValueError("delta_x_memory must equal the projected raw vector")

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "recall_state_impact_batch",
            "items": [item.to_record() for item in self.items],
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "raw_sum_vector": dict(self.raw_sum_vector),
            "raw_norm": self.raw_norm,
            "fusion_radius": self.fusion_radius,
            "projection_factor": self.projection_factor,
            "delta_x_memory": dict(self.delta_x_memory),
            "parameter_version": self.parameter_version,
            "operator_version": self.operator_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "kind", "items", "trigger_event_id",
            "evaluation_time_ms", "raw_sum_vector", "raw_norm", "fusion_radius",
            "projection_factor", "delta_x_memory", "parameter_version",
            "operator_version", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("recall state impact batch schema is unsupported")
        if record.get("schema_version") != 1 or record.get("kind") != "recall_state_impact_batch":
            raise ValueError("recall state impact batch schema is unsupported")
        if not isinstance(record["items"], list):
            raise ValueError("recall state impact batch items must be a list")
        return cls(
            items=tuple(RecallStateImpactItem.from_record(item) for item in record["items"]),
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            raw_sum_vector=record["raw_sum_vector"],
            raw_norm=record["raw_norm"],
            fusion_radius=record["fusion_radius"],
            projection_factor=record["projection_factor"],
            delta_x_memory=record["delta_x_memory"],
            parameter_version=record["parameter_version"],
            operator_version=record["operator_version"],
            receipt=record["receipt"],
        )


def derive_recall_state_impact(
    *,
    state_inputs: RecallStateInputBatch,
    fusion_radius: float,
    parameter_version: str,
) -> RecallStateImpactBatch:
    """Compute traceable per-ID P4 impacts and one global radial projection."""
    if not isinstance(state_inputs, RecallStateInputBatch):
        raise ValueError("state_inputs must be RecallStateInputBatch")
    radius = _positive(fusion_radius, "fusion_radius")
    parameters = _identifier(parameter_version, "parameter_version")

    items = []
    raw_sum = _zero_vector()
    traces = []
    for source in sorted(state_inputs.items, key=lambda item: item.event_id):
        activation = _unit(source.activation, "activation")
        condition = _unit(source.condition, "condition")
        if condition >= 1.0:
            raise ValueError("condition must be strictly below one")
        if activation < condition:
            raise ValueError("state input activation must be at or above condition")
        leverage = max(0.0, (activation - condition) / (1.0 - condition))
        leverage = _unit(leverage, "leverage")
        impact_vector = _scale(source.pre_recall_live_vector, leverage)
        raw_sum = {
            axis: raw_sum[axis] + impact_vector[axis] for axis in ROOT_ORDER
        }
        receipt = {
            "operator_version": RECALL_STATE_IMPACT_VERSION,
            "parameter_version": parameters,
            "event_id": source.event_id,
            "trigger_event_id": source.trigger_event_id,
            "input_operator_version": state_inputs.operator_version,
            "activation": activation,
            "condition": condition,
            "leverage_formula": _LEVERAGE_FORMULA,
            "leverage": leverage,
            "direction_source": "d1_pre_recall_live_vector",
            "pre_recall_live_vector": dict(source.pre_recall_live_vector),
            "impact_vector": impact_vector,
            "input_receipt": source.receipt,
        }
        item = RecallStateImpactItem(
            event_id=source.event_id,
            trigger_event_id=source.trigger_event_id,
            activation=activation,
            condition=condition,
            leverage=leverage,
            pre_recall_live_vector=source.pre_recall_live_vector,
            impact_vector=impact_vector,
            receipt=receipt,
        )
        items.append(item)
        traces.append(receipt)

    raw_norm = _norm(raw_sum)
    projection_factor = max(1.0, raw_norm / radius)
    delta_x_memory = _scale(raw_sum, 1.0 / projection_factor)
    return RecallStateImpactBatch(
        items=tuple(items),
        trigger_event_id=state_inputs.trigger_event_id,
        evaluation_time_ms=state_inputs.evaluation_time_ms,
        raw_sum_vector=raw_sum,
        raw_norm=raw_norm,
        fusion_radius=radius,
        projection_factor=projection_factor,
        delta_x_memory=delta_x_memory,
        parameter_version=parameters,
        operator_version=RECALL_STATE_IMPACT_VERSION,
        receipt={
            "operator_version": RECALL_STATE_IMPACT_VERSION,
            "parameter_version": parameters,
            "input_operator_version": state_inputs.operator_version,
            "trigger_event_id": state_inputs.trigger_event_id,
            "evaluation_time_ms": state_inputs.evaluation_time_ms,
            "state_event_ids": tuple(item.event_id for item in items),
            "leverage_formula": _LEVERAGE_FORMULA,
            "per_event_traces": tuple(traces),
            "raw_sum_vector": raw_sum,
            "raw_norm": raw_norm,
            "fusion_radius": radius,
            "projection_rule": "single_global_radial_projection",
            "projection_factor": projection_factor,
            "delta_x_memory": delta_x_memory,
            "state_write_status": "not_performed",
            "reconsolidation_status": "not_performed",
        },
    )
