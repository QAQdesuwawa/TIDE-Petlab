"""Six/seven historical influence readout over exact recalled event IDs.

This stage does not recall, rank, scale, aggregate, settle, or commit.  It
copies the already-evolved C2 live vector for every exact recalled event and
publishes forward-edge evidence for the later aggregate/transaction stages.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
import math
from typing import Any

from .interaction_geometry import ROOT_ORDER
from framework.model_api import model_data_fingerprint
from .memory_numeric_contracts import NeutralMemoryReadout
from .temporal_position import reference_position_ms
from .memory_runtime_order import readout_order
from .core_operators import _context, _plain_mapping, _require_payload_keys


CX_HISTORICAL_LIVE_INFLUENCE_VERSION = (
    "cx67-historical-live-influence-v1"
)
CX_HISTORICAL_FORWARD_EDGE_EVIDENCE_VERSION = (
    "cx67-historical-forward-edge-evidence-v1"
)
CX_HISTORICAL_AGGREGATION_VERSION = "cx67-historical-aggregate-v1"
CX_HISTORICAL_AGGREGATION_V1 = {
    "version": CX_HISTORICAL_AGGREGATION_VERSION,
    "root_axes": list(ROOT_ORDER),
    "fusion_radius": 1.0,
}


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _event_ids(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an event-id sequence")
    normalized = tuple(_identifier(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} contains duplicate event IDs")
    return normalized


def _finite(value: object, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _root_vector(value: object, field: str) -> dict[str, float]:
    record = _plain_mapping(value, field)
    if set(record) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    return {
        axis: _finite(record[axis], f"{field}.{axis}")
        for axis in ROOT_ORDER
    }


def _aggregation_package(params: object) -> tuple[str, float]:
    if not isinstance(params, Mapping):
        raise ValueError(
            "an explicit versioned historical-aggregation package is required")
    record = _plain_mapping(params, "params")
    if set(record) != {"version", "root_axes", "fusion_radius"}:
        raise ValueError(
            "historical-aggregation package fields do not match contract")
    version = _identifier(record["version"], "params.version")
    axes = record["root_axes"]
    if isinstance(axes, (str, bytes)) or not isinstance(axes, Sequence):
        raise ValueError("params.root_axes must be an axis sequence")
    normalized_axes = tuple(_identifier(axis, "params.root_axes") for axis in axes)
    radius = _finite(record["fusion_radius"], "params.fusion_radius")
    if radius <= 0.0:
        raise ValueError("params.fusion_radius must be positive")
    registered = CX_HISTORICAL_AGGREGATION_V1
    if (
        version != registered["version"]
        or normalized_axes != tuple(registered["root_axes"])
        or not math.isclose(
            radius, float(registered["fusion_radius"]),
            rel_tol=0.0, abs_tol=1e-15)
    ):
        raise ValueError(
            "historical-aggregation package does not match its registered version")
    return version, radius


def _readout(value: object, field: str) -> NeutralMemoryReadout:
    return NeutralMemoryReadout.from_record(_plain_mapping(value, field))


def read_selected_historical_influence(
        *, recalled_event_ids: Sequence[str],
        historical_readouts: Sequence[NeutralMemoryReadout],
        current_readout: NeutralMemoryReadout) -> dict[str, Any]:
    """Copy selected live vectors and expose non-committing later edges."""
    recalled_ids = _event_ids(recalled_event_ids, "recalled_event_ids")
    if isinstance(historical_readouts, (str, bytes)) or not isinstance(
            historical_readouts, Sequence):
        raise ValueError("historical_readouts must be a sequence")
    readouts = tuple(historical_readouts)
    if any(not isinstance(item, NeutralMemoryReadout) for item in readouts):
        raise ValueError(
            "historical_readouts must contain NeutralMemoryReadout values")
    if not isinstance(current_readout, NeutralMemoryReadout):
        raise ValueError("current_readout must be a NeutralMemoryReadout")

    historical_ids = tuple(item.event.event_id for item in readouts)
    if len(historical_ids) != len(set(historical_ids)):
        raise ValueError("historical_readouts contain duplicate event IDs")
    if (
        len(historical_ids) != len(recalled_ids)
        or set(historical_ids) != set(recalled_ids)
    ):
        raise ValueError(
            "historical readout set differs from recalled event set")

    by_id = {item.event.event_id: item for item in readouts}
    canonical_ids = tuple(sorted(recalled_ids))
    current_event = current_readout.event
    for event_id in canonical_ids:
        readout = by_id[event_id]
        parent_ordinal = readout_order(readout.receipt)
        child_ordinal = readout_order(current_readout.receipt)
        ordered = readout.event.committed_at_ms < current_event.committed_at_ms
        if parent_ordinal is not None and child_ordinal is not None:
            ordered = (readout.event.committed_at_ms <= current_event.committed_at_ms and
                       parent_ordinal < child_ordinal)
        if readout.event.event_id == current_event.event_id or not ordered:
            raise ValueError(
                "historical event must be strictly earlier than current event")
        if readout.as_of_ms != current_readout.as_of_ms:
            raise ValueError(
                "historical and current evaluation time must match")
        if readout.formula_version != current_readout.formula_version:
            raise ValueError(
                "historical and current live-readout formula version must match")

    event_influences: dict[str, dict[str, Any]] = {}
    later_edges: list[dict[str, Any]] = []
    for event_id in canonical_ids:
        readout = by_id[event_id]
        readout_record = readout.to_record()
        readout_fingerprint = model_data_fingerprint(readout_record)
        event_influences[event_id] = {
            "live_influence_vector": dict(readout.live_vector),
            "live_readout_version": readout.formula_version,
            "live_readout_fingerprint": readout_fingerprint,
            "lineage": list(readout.lineage),
            "post_selection_rescale_performed": False,
        }
        later_edges.append({
            "parent_event_id": event_id,
            "child_event_id": current_event.event_id,
            "edge_evidence": {
                "version": CX_HISTORICAL_FORWARD_EDGE_EVIDENCE_VERSION,
                "source_live_readout_fingerprint": readout_fingerprint,
                "state_commit_authority": False,
                "actual_delta_pending_aggregate": True,
            },
        })

    return {
        "version": CX_HISTORICAL_LIVE_INFLUENCE_VERSION,
        "recalled_event_ids": list(canonical_ids),
        "event_influences": event_influences,
        "later_edges": later_edges,
    }


def aggregate_selected_historical_influence(
        *, recalled_event_ids: Sequence[str],
        event_influences: Mapping[str, Mapping[str, Any]],
        source_influence_output_fingerprint: str,
        params: Mapping[str, Any] | None) -> dict[str, Any]:
    """Sum exact live influences, then apply one global radial projection."""
    version, radius = _aggregation_package(params)
    recalled_ids = _event_ids(recalled_event_ids, "recalled_event_ids")
    source_fingerprint = _identifier(
        source_influence_output_fingerprint,
        "source_influence_output_fingerprint")
    raw_influences = _plain_mapping(event_influences, "event_influences")
    normalized: dict[str, dict[str, Any]] = {}
    for raw_event_id, raw_record in raw_influences.items():
        event_id = _identifier(raw_event_id, "event influence event ID")
        record = _plain_mapping(raw_record, f"event_influences.{event_id}")
        required = {
            "live_influence_vector", "live_readout_version",
            "live_readout_fingerprint", "lineage",
            "post_selection_rescale_performed",
        }
        if set(record) != required:
            raise ValueError(
                "historical event influence fields do not match contract")
        if record["post_selection_rescale_performed"] is not False:
            raise ValueError(
                "historical event influence cannot carry a prior rescale")
        _identifier(
            record["live_readout_version"],
            f"event_influences.{event_id}.live_readout_version")
        _identifier(
            record["live_readout_fingerprint"],
            f"event_influences.{event_id}.live_readout_fingerprint")
        lineage = record["lineage"]
        if isinstance(lineage, (str, bytes)) or not isinstance(lineage, Sequence):
            raise ValueError("historical event influence lineage must be a sequence")
        normalized[event_id] = {
            **record,
            "live_influence_vector": _root_vector(
                record["live_influence_vector"],
                f"event_influences.{event_id}.live_influence_vector"),
        }
    if set(normalized) != set(recalled_ids):
        raise ValueError(
            "historical influence recalled event set differs from influences")

    canonical_ids = tuple(sorted(recalled_ids))
    raw_sum = {axis: 0.0 for axis in ROOT_ORDER}
    for event_id in canonical_ids:
        vector = normalized[event_id]["live_influence_vector"]
        for axis in ROOT_ORDER:
            raw_sum[axis] += vector[axis]
    raw_norm = math.sqrt(sum(value * value for value in raw_sum.values()))
    projection_factor = max(1.0, raw_norm / radius)
    delta = {
        axis: raw_sum[axis] / projection_factor for axis in ROOT_ORDER
    }
    projected_by_event = {
        event_id: {
            axis: normalized[event_id]["live_influence_vector"][axis]
            / projection_factor
            for axis in ROOT_ORDER
        }
        for event_id in canonical_ids
    }
    aggregate = {
        "raw_sum_vector": raw_sum,
        "raw_norm": raw_norm,
        "fusion_radius": radius,
        "projection_factor": projection_factor,
        "delta_x_memory": delta,
        "event_projected_contributions": projected_by_event,
        "per_event_rule": "settled_live_influence_identity",
        "global_projection_rule": "single_global_radial_projection",
        "global_projection_applied_once": True,
        "candidate_specific_rescale_performed": False,
        "state_commit_authority": False,
    }
    return {
        "version": version,
        "recalled_event_ids": list(canonical_ids),
        "source_influence_output_fingerprint": source_fingerprint,
        "aggregate": aggregate,
    }


class CxSixSevenHistoricalInfluenceAdapter:
    """Model-stage adapter for exact selected historical live vectors."""

    def read_historical_influence(self, *, payload, context):
        context = _context(context)
        _require_payload_keys(
            payload,
            {
                "recalled_event_ids", "historical_readouts",
                "current_readout",
            },
            stage="read_historical_influence",
        )
        recalled_ids = _event_ids(
            payload["recalled_event_ids"], "recalled_event_ids")
        raw_history = payload["historical_readouts"]
        if isinstance(raw_history, (str, bytes)) or not isinstance(
                raw_history, Sequence):
            raise ValueError("historical_readouts must be a sequence")
        historical = tuple(
            _readout(item, f"historical_readouts[{index}]")
            for index, item in enumerate(raw_history)
        )
        current = _readout(payload["current_readout"], "current_readout")
        evaluation_ms = reference_position_ms(context)
        if (
            context.event_id is None
            or current.event.event_id != context.event_id
            or current.as_of_ms != evaluation_ms
        ):
            raise ValueError(
                "current readout does not match invocation context")
        product = read_selected_historical_influence(
            recalled_event_ids=recalled_ids,
            historical_readouts=historical,
            current_readout=current,
        )
        return {"historical_influence": product}


class CxSixSevenHistoricalAggregateAdapter:
    """Model-stage adapter for one global selected-memory projection."""

    def aggregate_historical_influence(self, *, payload, context):
        context = _context(context)
        _require_payload_keys(
            payload,
            {
                "recalled_event_ids", "event_influences",
                "source_influence_output_fingerprint", "params",
            },
            stage="aggregate_historical_influence",
        )
        if context.event_id is None:
            raise ValueError(
                "aggregate invocation context requires current event identity")
        product = aggregate_selected_historical_influence(
            recalled_event_ids=payload["recalled_event_ids"],
            event_influences=payload["event_influences"],
            source_influence_output_fingerprint=payload[
                "source_influence_output_fingerprint"],
            params=payload["params"],
        )
        return {"historical_aggregate": product}


__all__ = (
    "CX_HISTORICAL_AGGREGATION_V1",
    "CX_HISTORICAL_AGGREGATION_VERSION",
    "CX_HISTORICAL_FORWARD_EDGE_EVIDENCE_VERSION",
    "CX_HISTORICAL_LIVE_INFLUENCE_VERSION",
    "CxSixSevenHistoricalAggregateAdapter",
    "CxSixSevenHistoricalInfluenceAdapter",
    "aggregate_selected_historical_influence",
    "read_selected_historical_influence",
)
