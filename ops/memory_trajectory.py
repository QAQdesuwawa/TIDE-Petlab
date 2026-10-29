"""Alpha 0.4 M2 neutral live-memory trajectory and recall-DAG replay.

The pure operator deliberately accepts only committed exact IDs, absolute time,
six-root event imprints and append-only recall events.  It has no access to text,
personality, expression tokens, RAG, tick, writer, Runner or UI state.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Sequence

from core.interaction_geometry import ROOT_ORDER
from ops.memory_contracts import MemoryEventRef, MemoryRecallEvent, NeutralMemoryReadout


NEUTRAL_TRAJECTORY_VERSION = "neutral-memory-trajectory-v1"
_BOUND_RADIUS = math.sqrt(float(len(ROOT_ORDER)))


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


def _vector(value, field):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    return {axis: _finite(value[axis], f"{field}.{axis}") for axis in ROOT_ORDER}


def _zero_vector():
    return {axis: 0.0 for axis in ROOT_ORDER}


def _scale(vector, scalar):
    return {axis: vector[axis] * scalar for axis in ROOT_ORDER}


def _add(*vectors):
    return {axis: sum(vector[axis] for vector in vectors) for axis in ROOT_ORDER}


def _subtract(left, right):
    return {axis: left[axis] - right[axis] for axis in ROOT_ORDER}


def _norm(vector):
    return math.sqrt(sum(vector[axis] * vector[axis] for axis in ROOT_ORDER))


def _unique(items):
    seen = set()
    ordered = []
    for item in items:
        if item not in seen:
            seen.add(item)
            ordered.append(item)
    return tuple(ordered)


@dataclass(frozen=True)
class MemoryTrajectoryAnchor:
    """Minimal immutable numeric anchor accepted by D1."""

    event_id: str
    committed_at_ms: int
    event_delta: Mapping[str, float]

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "committed_at_ms", _timestamp(
            self.committed_at_ms, "committed_at_ms"))
        object.__setattr__(self, "event_delta", MappingProxyType(
            _vector(self.event_delta, "event_delta")))


def _visible_anchors(anchors, as_of_ms):
    normalized = []
    ids = set()
    for anchor in anchors:
        if not isinstance(anchor, MemoryTrajectoryAnchor):
            raise ValueError("anchors must contain only MemoryTrajectoryAnchor")
        if anchor.event_id in ids:
            raise ValueError("trajectory anchor event_id must be unique")
        ids.add(anchor.event_id)
        normalized.append(anchor)
    normalized.sort(key=lambda row: (row.committed_at_ms, row.event_id))
    for previous, current in zip(normalized, normalized[1:]):
        if current.committed_at_ms <= previous.committed_at_ms:
            raise ValueError("trajectory anchor time must be strictly increasing")
    return tuple(row for row in normalized if row.committed_at_ms <= as_of_ms)


def _select_stencil(visible, target_index):
    size = len(visible)
    if size == 1:
        return "single_zero_order", (visible[target_index],)
    if size == 2:
        return "linear_two", visible
    if target_index == 0:
        return "forward_three", visible[:3]
    if target_index == size - 1:
        return "backward_three", visible[-3:]
    return "centered_three", visible[target_index - 1:target_index + 2]


def _trajectory_terms(stencil, target):
    zero = _zero_vector()
    if len(stencil) == 1:
        return {
            "intervals_ms": (), "s01": zero, "s12": zero, "q": zero,
            "target_velocity": zero, "curvature": zero,
            "path_residual": zero,
        }

    first, second = stencil[0], stencil[1]
    h01 = second.committed_at_ms - first.committed_at_ms
    s01 = {
        axis: (second.event_delta[axis] - first.event_delta[axis]) / h01
        for axis in ROOT_ORDER
    }
    if len(stencil) == 2:
        mean_path = {
            axis: (first.event_delta[axis] + second.event_delta[axis]) / 2.0
            for axis in ROOT_ORDER
        }
        return {
            "intervals_ms": (h01,), "s01": s01, "s12": dict(s01),
            "q": zero, "target_velocity": dict(s01), "curvature": zero,
            "path_residual": _subtract(mean_path, target.event_delta),
        }

    third = stencil[2]
    h12 = third.committed_at_ms - second.committed_at_ms
    span = third.committed_at_ms - first.committed_at_ms
    s12 = {
        axis: (third.event_delta[axis] - second.event_delta[axis]) / h12
        for axis in ROOT_ORDER
    }
    q = {axis: (s12[axis] - s01[axis]) / span for axis in ROOT_ORDER}
    target_time = target.committed_at_ms
    velocity = {
        axis: s01[axis] + q[axis] * (
            (target_time - first.committed_at_ms)
            + (target_time - second.committed_at_ms))
        for axis in ROOT_ORDER
    }
    curvature = {axis: 2.0 * q[axis] for axis in ROOT_ORDER}
    mean_path = {
        axis: (
            h01 * (first.event_delta[axis] + second.event_delta[axis]) / 2.0
            + h12 * (second.event_delta[axis] + third.event_delta[axis]) / 2.0
        ) / span
        for axis in ROOT_ORDER
    }
    return {
        "intervals_ms": (h01, h12), "s01": s01, "s12": s12, "q": q,
        "target_velocity": velocity, "curvature": curvature,
        "path_residual": _subtract(mean_path, target.event_delta),
    }


def _age_term(vector, committed_at_ms, as_of_ms, tau0_ms):
    radial = _norm(vector) / _BOUND_RADIUS
    tau = tau0_ms * (1.0 + radial)
    age = as_of_ms - committed_at_ms
    rho = 1.0 / (1.0 + age / tau)
    return radial, tau, age, rho


def _reachable_recall_events(recall_events, anchors_by_id, target_event_id, as_of_ms):
    all_by_id = {}
    for event in recall_events:
        if not isinstance(event, MemoryRecallEvent):
            raise ValueError("recall_events must contain only MemoryRecallEvent")
        if event.recall_event_id in all_by_id:
            raise ValueError("recall_event_id must be unique")
        all_by_id[event.recall_event_id] = event

    eligible = {
        event_id: event for event_id, event in all_by_id.items()
        if event.committed_at_ms < as_of_ms
    }
    seeds = sorted(
        (event for event in eligible.values() if event.target_event_id == target_event_id),
        key=lambda row: (row.committed_at_ms, row.recall_event_id),
    )
    visiting = set()
    visited = set()
    ordered = []

    def visit(event_id):
        if event_id in visited:
            return
        if event_id in visiting:
            raise ValueError("recall lineage must be acyclic")
        event = eligible.get(event_id)
        if event is None:
            raise ValueError("reachable recall parent must be strictly earlier than evaluation")
        target = anchors_by_id.get(event.target_event_id)
        trigger = anchors_by_id.get(event.trigger_event_id)
        if target is None or trigger is None:
            raise ValueError("recall lineage references an unknown committed exact event ID")
        if not target.committed_at_ms < trigger.committed_at_ms <= event.committed_at_ms:
            raise ValueError("recall lineage must point from trigger to a strictly earlier target")
        visiting.add(event_id)
        for parent_id in event.parent_recall_event_ids:
            parent = eligible.get(parent_id)
            if parent is None or parent.committed_at_ms >= event.committed_at_ms:
                raise ValueError("recall parent must be known and strictly earlier than child")
            visit(parent_id)
        visiting.remove(event_id)
        visited.add(event_id)
        ordered.append(event)

    for seed in seeds:
        visit(seed.recall_event_id)
    return tuple(ordered)


def reconstruct_neutral_memory(
        *, anchors: Sequence[MemoryTrajectoryAnchor], target_event_id: str,
        as_of_ms: int, recall_events: Sequence[MemoryRecallEvent] = (),
        tau0_ms: float, parameter_version: str) -> NeutralMemoryReadout:
    """Reconstruct one exact memory's neutral live vector at a committed checkpoint."""

    target_event_id = _identifier(target_event_id, "target_event_id")
    as_of_ms = _timestamp(as_of_ms, "as_of_ms")
    tau0_ms = _finite(tau0_ms, "tau0_ms")
    if tau0_ms <= 0.0:
        raise ValueError("tau0_ms must be positive")
    parameter_version = _identifier(parameter_version, "parameter_version")

    anchor_rows = tuple(anchors)
    visible = _visible_anchors(anchor_rows, as_of_ms)
    anchors_by_id = {anchor.event_id: anchor for anchor in anchor_rows}
    target = anchors_by_id.get(target_event_id)
    if target is None:
        raise ValueError("target_event_id is not a committed trajectory anchor")
    if target.committed_at_ms > as_of_ms:
        raise ValueError("target memory is not visible at evaluation time")
    visible_index = {anchor.event_id: index for index, anchor in enumerate(visible)}
    target_index = visible_index.get(target_event_id)
    if target_index is None:
        raise ValueError("target memory is not visible at evaluation time")

    stencil_mode, stencil = _select_stencil(visible, target_index)
    trajectory = _trajectory_terms(stencil, target)
    base_delta = dict(target.event_delta)
    r0, intrinsic_tau, age, rho = _age_term(
        base_delta, target.committed_at_ms, as_of_ms, tau0_ms)
    path_envelope = rho * (1.0 - rho)
    base_contribution = _scale(base_delta, rho)
    path_contribution = _scale(trajectory["path_residual"], path_envelope)

    reachable = _reachable_recall_events(
        tuple(recall_events), anchors_by_id, target_event_id, as_of_ms)
    recall_terms = []
    recall_contributions = []
    for event in reachable:
        delta = dict(event.reconsolidation_delta)
        radial, tau, recall_age, recall_rho = _age_term(
            delta, event.committed_at_ms, as_of_ms, tau0_ms)
        contribution = _scale(delta, recall_rho)
        recall_contributions.append(contribution)
        recall_terms.append({
            "recall_event_id": event.recall_event_id,
            "trigger_event_id": event.trigger_event_id,
            "target_event_id": event.target_event_id,
            "parent_recall_event_ids": tuple(event.parent_recall_event_ids),
            "committed_at_ms": event.committed_at_ms,
            "age_ms": recall_age,
            "delta": delta,
            "delta_radial_magnitude": radial,
            "tau_ms": tau,
            "rho": recall_rho,
            "contribution": contribution,
        })

    pre_bound = _add(base_contribution, path_contribution, *recall_contributions)
    pre_bound_norm = _norm(pre_bound)
    bound_factor = max(1.0, pre_bound_norm / _BOUND_RADIUS)
    live_vector = _scale(pre_bound, 1.0 / bound_factor)
    magnitude = _norm(live_vector) / _BOUND_RADIUS
    anchor_ids = tuple(anchor.event_id for anchor in stencil)
    recall_ids = tuple(event.recall_event_id for event in reachable)
    ancestor_ids = _unique(event.target_event_id for event in reachable)
    lineage = _unique((*anchor_ids, *recall_ids))

    receipt = {
        "operator_version": NEUTRAL_TRAJECTORY_VERSION,
        "parameter_version": parameter_version,
        "target_event_id": target.event_id,
        "target_committed_at_ms": target.committed_at_ms,
        "evaluation_time_ms": as_of_ms,
        "stencil_mode": stencil_mode,
        "anchor_event_ids": anchor_ids,
        "intervals_ms": trajectory["intervals_ms"],
        "base_event_delta": base_delta,
        "s01": trajectory["s01"],
        "s12": trajectory["s12"],
        "q": trajectory["q"],
        "target_velocity": trajectory["target_velocity"],
        "curvature": trajectory["curvature"],
        "intrinsic_radial_magnitude": r0,
        "tau0_ms": tau0_ms,
        "intrinsic_tau_ms": intrinsic_tau,
        "age_ms": age,
        "rho": rho,
        "path_residual": trajectory["path_residual"],
        "path_envelope": path_envelope,
        "base_contribution": base_contribution,
        "path_contribution": path_contribution,
        "recall_event_ids": recall_ids,
        "ancestor_event_ids": ancestor_ids,
        "recall_terms": tuple(recall_terms),
        "pre_bound_vector": pre_bound,
        "bound_radius": _BOUND_RADIUS,
        "bound_factor": bound_factor,
        "final_vector": live_vector,
        "normalized_magnitude": magnitude,
    }
    return NeutralMemoryReadout(
        event=MemoryEventRef(target.event_id, target.committed_at_ms),
        as_of_ms=as_of_ms,
        live_vector=live_vector,
        magnitude=magnitude,
        formula_version=NEUTRAL_TRAJECTORY_VERSION,
        lineage=lineage,
        receipt=receipt,
    )