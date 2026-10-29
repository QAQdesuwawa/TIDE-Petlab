"""Alpha 0.4 actual-recall to root-state pure bridge.

The dynamic recall layer has already settled which exact event IDs were recalled.
For those IDs this module resolves the previously settled six-root live influence,
sums it once, and applies one global radial bound.  Selection scalars never rescale
the root influence here.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType

from .interaction_geometry import ROOT_ORDER
from .memory_numeric_contracts import NeutralMemoryReadout


ACTUAL_RECALL_SELECTION_VERSION = "actual-recall-selection-v1"
ACTUAL_RECALL_STATE_INPUT_VERSION = "actual-recall-state-input-v2"
ACTUAL_RECALL_STATE_IMPACT_VERSION = "actual-recall-state-impact-v2"


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


def _root_vector(value, field):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    return MappingProxyType({
        axis: _finite(value[axis], f"{field}.{axis}") for axis in ROOT_ORDER
    })


def _zero_vector():
    return {axis: 0.0 for axis in ROOT_ORDER}


def _norm(vector):
    return math.sqrt(sum(float(vector[axis]) ** 2 for axis in ROOT_ORDER))


def _scale(vector, scalar):
    return {axis: float(vector[axis]) * float(scalar) for axis in ROOT_ORDER}


def _same_number(left, right):
    return math.isclose(float(left), float(right), rel_tol=1e-12, abs_tol=1e-12)


def _same_vector(left, right):
    return all(_same_number(left[axis], right[axis]) for axis in ROOT_ORDER)


def _freeze(value, field="receipt"):
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}") for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain finite serializable values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class ActualRecallStateInputItem:
    event_id: str
    trigger_event_id: str
    evaluation_time_ms: int
    live_influence_vector: Mapping[str, float]
    live_readout_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        event_id = _identifier(self.event_id, "event_id")
        trigger_id = _identifier(self.trigger_event_id, "trigger_event_id")
        if event_id == trigger_id:
            raise ValueError("historical state input cannot be its own trigger")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "trigger_event_id", trigger_id)
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(self, "live_influence_vector", _root_vector(
            self.live_influence_vector, "live_influence_vector"))
        object.__setattr__(self, "live_readout_version", _identifier(
            self.live_readout_version, "live_readout_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "live_influence_vector": dict(self.live_influence_vector),
            "live_readout_version": self.live_readout_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "event_id", "trigger_event_id", "evaluation_time_ms",
            "live_influence_vector", "live_readout_version", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("actual recall state input item schema is unsupported")
        if record.get("schema_version") != 1:
            raise ValueError("actual recall state input item schema is unsupported")
        return cls(
            event_id=record["event_id"],
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            live_influence_vector=record["live_influence_vector"],
            live_readout_version=record["live_readout_version"],
            receipt=record["receipt"],
        )


@dataclass(frozen=True)
class ActualRecallStateInputBatch:
    items: tuple[ActualRecallStateInputItem, ...]
    trigger_event_id: str
    evaluation_time_ms: int
    current_readout_version: str
    operator_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        items = tuple(self.items)
        if any(not isinstance(item, ActualRecallStateInputItem) for item in items):
            raise ValueError("items must contain ActualRecallStateInputItem values")
        ids = tuple(item.event_id for item in items)
        if len(ids) != len(set(ids)):
            raise ValueError("actual recall state input IDs must be unique")
        trigger_id = _identifier(self.trigger_event_id, "trigger_event_id")
        evaluation_time = _timestamp(self.evaluation_time_ms, "evaluation_time_ms")
        if any(item.trigger_event_id != trigger_id for item in items):
            raise ValueError("actual recall state inputs must share one trigger")
        if any(item.evaluation_time_ms != evaluation_time for item in items):
            raise ValueError("actual recall state inputs must share one evaluation time")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "trigger_event_id", trigger_id)
        object.__setattr__(self, "evaluation_time_ms", evaluation_time)
        object.__setattr__(self, "current_readout_version", _identifier(
            self.current_readout_version, "current_readout_version"))
        object.__setattr__(self, "operator_version", _identifier(
            self.operator_version, "operator_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "actual_recall_state_input_batch",
            "items": [item.to_record() for item in self.items],
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "current_readout_version": self.current_readout_version,
            "operator_version": self.operator_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "kind", "items", "trigger_event_id",
            "evaluation_time_ms", "current_readout_version", "operator_version",
            "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("actual recall state input batch schema is unsupported")
        if (record.get("schema_version") != 1
                or record.get("kind") != "actual_recall_state_input_batch"
                or not isinstance(record.get("items"), list)):
            raise ValueError("actual recall state input batch schema is unsupported")
        return cls(
            items=tuple(ActualRecallStateInputItem.from_record(item)
                        for item in record["items"]),
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            current_readout_version=record["current_readout_version"],
            operator_version=record["operator_version"],
            receipt=record["receipt"],
        )


@dataclass(frozen=True)
class ActualRecallStateImpactItem:
    event_id: str
    trigger_event_id: str
    live_influence_vector: Mapping[str, float]
    impact_vector: Mapping[str, float]
    receipt: Mapping[str, object]

    def __post_init__(self):
        event_id = _identifier(self.event_id, "event_id")
        trigger_id = _identifier(self.trigger_event_id, "trigger_event_id")
        if event_id == trigger_id:
            raise ValueError("historical impact cannot be its own trigger")
        object.__setattr__(self, "event_id", event_id)
        object.__setattr__(self, "trigger_event_id", trigger_id)
        live = _root_vector(self.live_influence_vector, "live_influence_vector")
        impact = _root_vector(self.impact_vector, "impact_vector")
        if not _same_vector(live, impact):
            raise ValueError("impact_vector must equal the settled live influence")
        object.__setattr__(self, "live_influence_vector", live)
        object.__setattr__(self, "impact_vector", impact)
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "event_id": self.event_id,
            "trigger_event_id": self.trigger_event_id,
            "live_influence_vector": dict(self.live_influence_vector),
            "impact_vector": dict(self.impact_vector),
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "event_id", "trigger_event_id",
            "live_influence_vector", "impact_vector", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("actual recall state impact item schema is unsupported")
        if record.get("schema_version") != 1:
            raise ValueError("actual recall state impact item schema is unsupported")
        return cls(
            event_id=record["event_id"],
            trigger_event_id=record["trigger_event_id"],
            live_influence_vector=record["live_influence_vector"],
            impact_vector=record["impact_vector"],
            receipt=record["receipt"],
        )


@dataclass(frozen=True)
class ActualRecallStateImpactBatch:
    items: tuple[ActualRecallStateImpactItem, ...]
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
        if any(not isinstance(item, ActualRecallStateImpactItem) for item in items):
            raise ValueError("items must contain ActualRecallStateImpactItem values")
        ids = tuple(item.event_id for item in items)
        if len(ids) != len(set(ids)):
            raise ValueError("actual recall state impact IDs must be unique")
        trigger_id = _identifier(self.trigger_event_id, "trigger_event_id")
        if any(item.trigger_event_id != trigger_id for item in items):
            raise ValueError("actual recall state impacts must share one trigger")
        object.__setattr__(self, "items", items)
        object.__setattr__(self, "trigger_event_id", trigger_id)
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        raw_sum = _root_vector(self.raw_sum_vector, "raw_sum_vector")
        raw_norm = _finite(self.raw_norm, "raw_norm")
        if raw_norm < 0.0:
            raise ValueError("raw_norm must be non-negative")
        radius = _positive(self.fusion_radius, "fusion_radius")
        factor = _finite(self.projection_factor, "projection_factor")
        if factor < 1.0:
            raise ValueError("projection_factor must be at least one")
        delta = _root_vector(self.delta_x_memory, "delta_x_memory")
        object.__setattr__(self, "raw_sum_vector", raw_sum)
        object.__setattr__(self, "raw_norm", raw_norm)
        object.__setattr__(self, "fusion_radius", radius)
        object.__setattr__(self, "projection_factor", factor)
        object.__setattr__(self, "delta_x_memory", delta)
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
                axis: expected_sum[axis] + float(item.impact_vector[axis])
                for axis in ROOT_ORDER
            }
        if not _same_vector(raw_sum, expected_sum):
            raise ValueError("raw_sum_vector must equal the per-event impact sum")
        expected_norm = _norm(raw_sum)
        if not _same_number(raw_norm, expected_norm):
            raise ValueError("raw_norm must equal the raw vector norm")
        expected_factor = max(1.0, expected_norm / radius)
        if not _same_number(factor, expected_factor):
            raise ValueError("projection_factor must follow the global radial rule")
        if not _same_vector(delta, _scale(raw_sum, 1.0 / expected_factor)):
            raise ValueError("delta_x_memory must equal the projected raw vector")

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "actual_recall_state_impact_batch",
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
            raise ValueError("actual recall state impact batch schema is unsupported")
        if (record.get("schema_version") != 1
                or record.get("kind") != "actual_recall_state_impact_batch"
                or not isinstance(record.get("items"), list)):
            raise ValueError("actual recall state impact batch schema is unsupported")
        return cls(
            items=tuple(ActualRecallStateImpactItem.from_record(item)
                        for item in record["items"]),
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


def resolve_actual_recall_identity(receipt):
    if not isinstance(receipt, Mapping):
        raise ValueError("actual_recall_receipt must be a mapping")
    required = {
        "version", "trigger_event_id", "evaluation_time_ms", "strategy",
        "recalled_event_ids", "references", "selection_completed",
        "content_included", "derived_only",
    }
    if set(receipt) != required:
        raise ValueError("actual recall receipt schema is unsupported")
    if receipt.get("version") != ACTUAL_RECALL_SELECTION_VERSION:
        raise ValueError("actual recall receipt version is unsupported")
    if receipt.get("selection_completed") is not True:
        raise ValueError("actual recall selection must be completed")
    if receipt.get("content_included") is not False:
        raise ValueError("actual recall state receipt cannot include content")
    trigger_id = _identifier(receipt.get("trigger_event_id"), "trigger_event_id")
    evaluation_time = _timestamp(
        receipt.get("evaluation_time_ms"), "evaluation_time_ms")
    _identifier(receipt.get("strategy"), "strategy")
    event_ids = tuple(_identifier(item, "recalled_event_id")
                      for item in tuple(receipt.get("recalled_event_ids") or ()))
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("actual recalled event IDs must be unique")
    references = tuple(receipt.get("references") or ())
    reference_ids = []
    for reference in references:
        if not isinstance(reference, Mapping):
            raise ValueError("actual recall references must be mappings")
        if reference.get("identity_kind") != "exact_committed_event":
            raise ValueError("actual recall state requires exact committed identities")
        reference_ids.append(_identifier(reference.get("event_id"), "reference.event_id"))
        _timestamp(reference.get("record_ts"), "reference.record_ts")
        _identifier(reference.get("source"), "reference.source")
    if tuple(reference_ids) != event_ids:
        raise ValueError("actual recall references must match recalled_event_ids")
    return trigger_id, evaluation_time, event_ids


def prepare_actual_recall_state_inputs(
        *, actual_recall_receipt: Mapping[str, object],
        historical_readouts: Sequence[NeutralMemoryReadout],
        current_readout: NeutralMemoryReadout) -> ActualRecallStateInputBatch:
    """Resolve actual-recalled exact IDs to their already-settled live vectors."""
    if not isinstance(current_readout, NeutralMemoryReadout):
        raise ValueError("current_readout must be NeutralMemoryReadout")
    trigger_id, evaluation_time, recalled_ids = resolve_actual_recall_identity(
        actual_recall_receipt)
    if current_readout.event.event_id != trigger_id:
        raise ValueError("actual recall trigger must match current readout")
    if current_readout.as_of_ms != evaluation_time:
        raise ValueError("actual recall and current readout must share one evaluation time")

    indexed = {}
    for readout in tuple(historical_readouts):
        if not isinstance(readout, NeutralMemoryReadout):
            raise ValueError("historical_readouts must contain NeutralMemoryReadout values")
        event_id = readout.event.event_id
        if event_id in indexed:
            raise ValueError("historical readout exact IDs must be unique")
        indexed[event_id] = readout
    if set(indexed) != set(recalled_ids):
        raise ValueError("historical readouts must exactly match actual recalled IDs")

    items = []
    for event_id in sorted(recalled_ids):
        readout = indexed[event_id]
        if readout.event.committed_at_ms >= current_readout.event.committed_at_ms:
            raise ValueError("historical readout must be strictly earlier than trigger")
        if readout.as_of_ms != evaluation_time:
            raise ValueError("historical and actual recall evaluation times must match")
        if readout.formula_version != current_readout.formula_version:
            raise ValueError("historical and current live readout versions must match")
        items.append(ActualRecallStateInputItem(
            event_id=event_id,
            trigger_event_id=trigger_id,
            evaluation_time_ms=evaluation_time,
            live_influence_vector=readout.live_vector,
            live_readout_version=readout.formula_version,
            receipt={
                "operator_version": ACTUAL_RECALL_STATE_INPUT_VERSION,
                "event_id": event_id,
                "trigger_event_id": trigger_id,
                "evaluation_time_ms": evaluation_time,
                "actual_recall_receipt_version": actual_recall_receipt["version"],
                "live_readout_version": readout.formula_version,
                "live_readout_lineage": tuple(readout.lineage),
                "state_rule": "resolve_actual_recalled_exact_id_to_settled_live_influence",
                "post_selection_rescale_performed": False,
            },
        ))
    return ActualRecallStateInputBatch(
        items=tuple(items),
        trigger_event_id=trigger_id,
        evaluation_time_ms=evaluation_time,
        current_readout_version=current_readout.formula_version,
        operator_version=ACTUAL_RECALL_STATE_INPUT_VERSION,
        receipt={
            "operator_version": ACTUAL_RECALL_STATE_INPUT_VERSION,
            "actual_recall_receipt_version": actual_recall_receipt["version"],
            "trigger_event_id": trigger_id,
            "evaluation_time_ms": evaluation_time,
            "state_event_ids": tuple(item.event_id for item in items),
            "source_rule": "actual_recalled_exact_ids_only",
            "post_selection_rescale_performed": False,
            "content_read": False,
        },
    )


def derive_actual_recall_state_impact(
        *, state_inputs: ActualRecallStateInputBatch,
        fusion_radius: float,
        parameter_version: str) -> ActualRecallStateImpactBatch:
    """Fuse already-settled recalled influences without a second gate or scale."""
    if not isinstance(state_inputs, ActualRecallStateInputBatch):
        raise ValueError("state_inputs must be ActualRecallStateInputBatch")
    radius = _positive(fusion_radius, "fusion_radius")
    parameters = _identifier(parameter_version, "parameter_version")
    items = []
    raw_sum = _zero_vector()
    traces = []
    for source in state_inputs.items:
        impact = dict(source.live_influence_vector)
        raw_sum = {
            axis: raw_sum[axis] + float(impact[axis]) for axis in ROOT_ORDER
        }
        receipt = {
            "operator_version": ACTUAL_RECALL_STATE_IMPACT_VERSION,
            "event_id": source.event_id,
            "trigger_event_id": source.trigger_event_id,
            "input_operator_version": state_inputs.operator_version,
            "live_readout_version": source.live_readout_version,
            "impact_vector": impact,
            "influence_rule": "settled_live_influence_identity",
            "post_selection_rescale_performed": False,
        }
        items.append(ActualRecallStateImpactItem(
            event_id=source.event_id,
            trigger_event_id=source.trigger_event_id,
            live_influence_vector=source.live_influence_vector,
            impact_vector=impact,
            receipt=receipt,
        ))
        traces.append(receipt)
    raw_norm = _norm(raw_sum)
    projection_factor = max(1.0, raw_norm / radius)
    delta = _scale(raw_sum, 1.0 / projection_factor)
    return ActualRecallStateImpactBatch(
        items=tuple(items),
        trigger_event_id=state_inputs.trigger_event_id,
        evaluation_time_ms=state_inputs.evaluation_time_ms,
        raw_sum_vector=raw_sum,
        raw_norm=raw_norm,
        fusion_radius=radius,
        projection_factor=projection_factor,
        delta_x_memory=delta,
        parameter_version=parameters,
        operator_version=ACTUAL_RECALL_STATE_IMPACT_VERSION,
        receipt={
            "operator_version": ACTUAL_RECALL_STATE_IMPACT_VERSION,
            "parameter_version": parameters,
            "input_operator_version": state_inputs.operator_version,
            "trigger_event_id": state_inputs.trigger_event_id,
            "evaluation_time_ms": state_inputs.evaluation_time_ms,
            "state_event_ids": tuple(item.event_id for item in items),
            "per_event_rule": "use_settled_live_influence_without_post_selection_rescale",
            "per_event_traces": tuple(traces),
            "raw_sum_vector": raw_sum,
            "raw_norm": raw_norm,
            "fusion_radius": radius,
            "projection_rule": "single_global_radial_projection",
            "projection_factor": projection_factor,
            "global_projection_applied_once": True,
            "delta_x_memory": delta,
            "state_write_status": "not_performed",
            "future_influence_status": "not_performed",
        },
    )
