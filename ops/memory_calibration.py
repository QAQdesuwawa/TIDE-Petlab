"""Observation-only Alpha 0.4 memory calibration helpers.

This module never chooses a product parameter or memory.  Callers must supply every
candidate tau0 and evaluation offset explicitly; the result is a deterministic,
JSON-serializable shadow matrix over the frozen M2 operator.
"""
from __future__ import annotations

import math
from statistics import fmean
from typing import Mapping, Sequence

from ops.memory_contracts import MemoryRecallEvent
from ops.memory_trajectory import (
    MemoryTrajectoryAnchor,
    NEUTRAL_TRAJECTORY_VERSION,
    reconstruct_neutral_memory,
)


TAU0_SHADOW_SCHEMA = 1
TAU0_SHADOW_VERSION = "neutral-memory-tau0-shadow-v1"


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _positive_candidates(values):
    rows = []
    seen = set()
    for value in values:
        try:
            parsed = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("tau0_values_ms must contain finite positive values") from exc
        if not math.isfinite(parsed) or parsed <= 0.0:
            raise ValueError("tau0_values_ms must contain finite positive values")
        if parsed in seen:
            raise ValueError("tau0_values_ms must not contain duplicates")
        seen.add(parsed)
        rows.append(parsed)
    if not rows:
        raise ValueError("tau0_values_ms cannot be empty")
    return tuple(rows)


def _offsets(values):
    rows = []
    seen = set()
    for value in values:
        if isinstance(value, bool):
            raise ValueError("evaluation_offsets_ms must contain non-negative integers")
        try:
            parsed = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "evaluation_offsets_ms must contain non-negative integers") from exc
        if parsed != value or parsed < 0:
            raise ValueError("evaluation_offsets_ms must contain non-negative integers")
        if parsed in seen:
            raise ValueError("evaluation_offsets_ms must not contain duplicates")
        seen.add(parsed)
        rows.append(parsed)
    if not rows:
        raise ValueError("evaluation_offsets_ms cannot be empty")
    return tuple(rows)


def _percentile(sorted_values, fraction):
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = (len(sorted_values) - 1) * fraction
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _distribution(values):
    ordered = sorted(float(value) for value in values)
    return {
        "min": ordered[0],
        "q25": _percentile(ordered, 0.25),
        "median": _percentile(ordered, 0.50),
        "q75": _percentile(ordered, 0.75),
        "p90": _percentile(ordered, 0.90),
        "max": ordered[-1],
        "mean": fmean(ordered),
    }


def sweep_neutral_tau0(
        *, anchors_by_scope: Mapping[str, Sequence[MemoryTrajectoryAnchor]],
        tau0_values_ms: Sequence[float], evaluation_offsets_ms: Sequence[int],
        parameter_version_prefix: str,
        recall_events_by_scope: Mapping[str, Sequence[MemoryRecallEvent]] | None = None,
        source_receipt: Mapping[str, object] | None = None) -> dict[str, object]:
    """Evaluate explicit tau0 candidates without ranking, eligibility or writes."""

    if not isinstance(anchors_by_scope, Mapping) or not anchors_by_scope:
        raise ValueError("anchors_by_scope must be a non-empty mapping")
    tau_values = _positive_candidates(tuple(tau0_values_ms))
    offsets = _offsets(tuple(evaluation_offsets_ms))
    version_prefix = _identifier(parameter_version_prefix, "parameter_version_prefix")
    recall_map = {} if recall_events_by_scope is None else recall_events_by_scope
    if not isinstance(recall_map, Mapping):
        raise ValueError("recall_events_by_scope must be a mapping")
    if source_receipt is not None and not isinstance(source_receipt, Mapping):
        raise ValueError("source_receipt must be a mapping")

    normalized_scopes = {}
    all_latest = []
    for raw_scope_id, raw_anchors in anchors_by_scope.items():
        scope_id = _identifier(raw_scope_id, "scope_id")
        if scope_id in normalized_scopes:
            raise ValueError("scope_id must be unique")
        anchors = tuple(raw_anchors)
        if not anchors or any(not isinstance(row, MemoryTrajectoryAnchor) for row in anchors):
            raise ValueError("each scope must contain MemoryTrajectoryAnchor rows")
        anchors = tuple(sorted(anchors, key=lambda row: (row.committed_at_ms, row.event_id)))
        normalized_scopes[scope_id] = anchors
        all_latest.append(max(row.committed_at_ms for row in anchors))
    unknown_recall_scopes = set(recall_map) - set(normalized_scopes)
    if unknown_recall_scopes:
        raise ValueError("recall_events_by_scope contains an unknown scope")

    baseline_checkpoint_ms = max(all_latest)
    scopes_receipt = {
        scope_id: {
            "event_count": len(anchors),
            "first_committed_at_ms": anchors[0].committed_at_ms,
            "last_committed_at_ms": anchors[-1].committed_at_ms,
            "event_ids": [anchor.event_id for anchor in anchors],
        }
        for scope_id, anchors in sorted(normalized_scopes.items())
    }
    rows = []
    for tau0_ms in tau_values:
        parameter_version = f"{version_prefix}:tau0_ms={tau0_ms:g}"
        for offset_ms in offsets:
            evaluation_time_ms = baseline_checkpoint_ms + offset_ms
            checkpoint_kind = "frozen_baseline" if offset_ms == 0 else "hypothetical_silence"
            for scope_id, anchors in sorted(normalized_scopes.items()):
                recall_events = tuple(recall_map.get(scope_id, ()))
                events = []
                magnitudes = []
                rhos = []
                for anchor in anchors:
                    readout = reconstruct_neutral_memory(
                        anchors=anchors,
                        target_event_id=anchor.event_id,
                        as_of_ms=evaluation_time_ms,
                        recall_events=recall_events,
                        tau0_ms=tau0_ms,
                        parameter_version=parameter_version,
                    )
                    record = readout.to_record()
                    events.append({
                        "event_id": anchor.event_id,
                        "committed_at_ms": anchor.committed_at_ms,
                        "readout": record,
                    })
                    magnitudes.append(readout.magnitude)
                    rhos.append(float(readout.receipt["rho"]))
                rows.append({
                    "scope_id": scope_id,
                    "tau0_ms": tau0_ms,
                    "parameter_version": parameter_version,
                    "evaluation_offset_ms": offset_ms,
                    "evaluation_time_ms": evaluation_time_ms,
                    "checkpoint_kind": checkpoint_kind,
                    "event_count": len(events),
                    "magnitude_distribution": _distribution(magnitudes),
                    "rho_distribution": _distribution(rhos),
                    "events": events,
                })

    return {
        "schema_version": TAU0_SHADOW_SCHEMA,
        "kind": "neutral_memory_tau0_shadow",
        "shadow_version": TAU0_SHADOW_VERSION,
        "operator_version": NEUTRAL_TRAJECTORY_VERSION,
        "parameter_version_prefix": version_prefix,
        "baseline_checkpoint_ms": baseline_checkpoint_ms,
        "tau0_values_ms": list(tau_values),
        "evaluation_offsets_ms": list(offsets),
        "checkpoint_policy": {
            "offset_zero": "frozen_baseline",
            "positive_offset": "hypothetical_silence",
            "commits_synthetic_events": False,
        },
        "source_receipt": dict(source_receipt or {}),
        "scopes": scopes_receipt,
        "rows": rows,
    }