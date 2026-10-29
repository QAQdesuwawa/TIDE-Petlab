"""Alpha 0.4 bounded six-root transition for settled actual recall impact.

The impact vector has already passed actual-recall selection and the one global
radial projection.  This layer only applies that vector to the current six-root
state and enforces the roots' existing closed-unit storage domain.  It does not
re-run role response, recall gates, attention, or persistence.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType

from .interaction_geometry import ROOT_ORDER
from .memory_actual_state import (
    ACTUAL_RECALL_STATE_IMPACT_VERSION,
    ActualRecallStateImpactBatch,
)


ACTUAL_RECALL_ROOT_TRANSITION_VERSION = "actual-recall-root-transition-v1"


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


def _vector(value, field, *, closed_unit=False):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    result = {}
    for axis in ROOT_ORDER:
        try:
            number = float(value[axis])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}.{axis} must be finite") from exc
        if not math.isfinite(number):
            raise ValueError(f"{field}.{axis} must be finite")
        if closed_unit and not 0.0 <= number <= 1.0:
            raise ValueError(f"{field}.{axis} must be between zero and one")
        result[axis] = number
    return MappingProxyType(result)


def _same_vector(left, right):
    return all(math.isclose(
        float(left[axis]), float(right[axis]), rel_tol=1e-12, abs_tol=1e-12)
        for axis in ROOT_ORDER)


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
class ActualRecallRootTransition:
    transition_id: str
    trigger_event_id: str
    evaluation_time_ms: int
    source_event_ids: tuple[str, ...]
    x_before: Mapping[str, float]
    requested_delta_x_memory: Mapping[str, float]
    actual_delta_x_memory: Mapping[str, float]
    x_after: Mapping[str, float]
    impact_operator_version: str
    operator_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "transition_id", _identifier(
            self.transition_id, "transition_id"))
        object.__setattr__(self, "trigger_event_id", _identifier(
            self.trigger_event_id, "trigger_event_id"))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        ids = tuple(_identifier(item, "source_event_id")
                    for item in self.source_event_ids)
        if len(ids) != len(set(ids)):
            raise ValueError("source event IDs must be unique")
        object.__setattr__(self, "source_event_ids", ids)
        before = _vector(self.x_before, "x_before", closed_unit=True)
        requested = _vector(
            self.requested_delta_x_memory, "requested_delta_x_memory")
        actual = _vector(self.actual_delta_x_memory, "actual_delta_x_memory")
        after = _vector(self.x_after, "x_after", closed_unit=True)
        expected_after = {
            axis: min(1.0, max(0.0, before[axis] + requested[axis]))
            for axis in ROOT_ORDER
        }
        expected_actual = {
            axis: expected_after[axis] - before[axis] for axis in ROOT_ORDER
        }
        if not _same_vector(after, expected_after):
            raise ValueError("x_after must equal bounded x_before plus requested delta")
        if not _same_vector(actual, expected_actual):
            raise ValueError("actual delta must equal x_after minus x_before")
        object.__setattr__(self, "x_before", before)
        object.__setattr__(self, "requested_delta_x_memory", requested)
        object.__setattr__(self, "actual_delta_x_memory", actual)
        object.__setattr__(self, "x_after", after)
        object.__setattr__(self, "impact_operator_version", _identifier(
            self.impact_operator_version, "impact_operator_version"))
        object.__setattr__(self, "operator_version", _identifier(
            self.operator_version, "operator_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "actual_recall_root_transition",
            "transition_id": self.transition_id,
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "source_event_ids": list(self.source_event_ids),
            "x_before": dict(self.x_before),
            "requested_delta_x_memory": dict(self.requested_delta_x_memory),
            "actual_delta_x_memory": dict(self.actual_delta_x_memory),
            "x_after": dict(self.x_after),
            "impact_operator_version": self.impact_operator_version,
            "operator_version": self.operator_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "kind", "transition_id", "trigger_event_id", "evaluation_time_ms",
            "source_event_ids", "x_before", "requested_delta_x_memory",
            "actual_delta_x_memory", "x_after", "impact_operator_version",
            "operator_version", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("actual recall root transition schema is unsupported")
        if (record.get("schema_version") != 1
                or record.get("kind") != "actual_recall_root_transition"):
            raise ValueError("actual recall root transition schema is unsupported")
        return cls(
            transition_id=record["transition_id"],
            trigger_event_id=record["trigger_event_id"],
            evaluation_time_ms=record["evaluation_time_ms"],
            source_event_ids=tuple(record["source_event_ids"]),
            x_before=record["x_before"],
            requested_delta_x_memory=record["requested_delta_x_memory"],
            actual_delta_x_memory=record["actual_delta_x_memory"],
            x_after=record["x_after"],
            impact_operator_version=record["impact_operator_version"],
            operator_version=record["operator_version"],
            receipt=record["receipt"],
        )


def derive_actual_recall_root_transition(
        *, impact_batch: ActualRecallStateImpactBatch,
        x_before: Mapping[str, object]) -> ActualRecallRootTransition:
    """Apply the already-settled impact once; perform no second weighting."""
    if not isinstance(impact_batch, ActualRecallStateImpactBatch):
        raise ValueError("impact_batch must be ActualRecallStateImpactBatch")
    if impact_batch.operator_version != ACTUAL_RECALL_STATE_IMPACT_VERSION:
        raise ValueError("actual recall impact version is unsupported")
    before = _vector(x_before, "x_before", closed_unit=True)
    requested = dict(impact_batch.delta_x_memory)
    after = {
        axis: min(1.0, max(0.0, before[axis] + requested[axis]))
        for axis in ROOT_ORDER
    }
    actual = {axis: after[axis] - before[axis] for axis in ROOT_ORDER}
    source_ids = tuple(item.event_id for item in impact_batch.items)
    clipped = tuple(axis for axis in ROOT_ORDER if not math.isclose(
        actual[axis], requested[axis], rel_tol=1e-12, abs_tol=1e-12))
    return ActualRecallRootTransition(
        transition_id=f"memory-root-transition:{impact_batch.trigger_event_id}",
        trigger_event_id=impact_batch.trigger_event_id,
        evaluation_time_ms=impact_batch.evaluation_time_ms,
        source_event_ids=source_ids,
        x_before=before,
        requested_delta_x_memory=requested,
        actual_delta_x_memory=actual,
        x_after=after,
        impact_operator_version=impact_batch.operator_version,
        operator_version=ACTUAL_RECALL_ROOT_TRANSITION_VERSION,
        receipt={
            "operator_version": ACTUAL_RECALL_ROOT_TRANSITION_VERSION,
            "transition_id": f"memory-root-transition:{impact_batch.trigger_event_id}",
            "impact_operator_version": impact_batch.operator_version,
            "trigger_event_id": impact_batch.trigger_event_id,
            "evaluation_time_ms": impact_batch.evaluation_time_ms,
            "source_event_ids": source_ids,
            "application_rule": "apply_settled_delta_once_then_clamp_root_domain",
            "post_selection_rescale_performed": False,
            "root_domain_clamp_only": True,
            "clipped_axes": clipped,
            "commit_status": "not_performed",
            "writer_read": False,
            "tick_read": False,
            "ui_read": False,
        },
    )
