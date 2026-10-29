"""Six/seven actual recall over one-time-evolved live projections.

Historical projections must already come from the C2 live readout at the
evaluation position. Time and prior recall edges therefore enter once
upstream. Each exact candidate is evaluated independently through recall
impulse and dynamic threshold readouts over the same bounded features.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from .continuous_root_projection import (
    CONTINUOUS_ROOT_PROJECTION_VERSION,
    PROJECTION_AXES,
    ContinuousRootProjection,
)
from framework.model_api import model_data_fingerprint
from .memory_attention import ProjectionSnapshot
from .temporal_position import reference_position_ms
from .core_operators import (
    _context,
    _plain_mapping,
    _require_payload_keys,
)


CX_ACTUAL_RECALL_DUAL_READOUT_VERSION = "cx67-actual-recall-dual-readout-v1"
CX_MEMORY_CONTENT_CLAIM_VERSION = "cx67-recall-impulse-content-claim-v1"
CX_ACTUAL_RECALL_DUAL_READOUT_V1 = {
    "version": CX_ACTUAL_RECALL_DUAL_READOUT_VERSION,
    "projection_formula_version": CONTINUOUS_ROOT_PROJECTION_VERSION,
    "projection_radius": math.sqrt(len(PROJECTION_AXES)),
    "threshold_weights": {
        "amplitude_deficit": 0.5,
        "relation_deficit": 0.5,
    },
}
_ZERO_TOLERANCE = 1e-15


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _timestamp(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer timestamp")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field} must be a non-negative integer timestamp") from exc
    if parsed < 0 or parsed != value:
        raise ValueError(f"{field} must be a non-negative integer timestamp")
    return parsed


def _finite(value: object, field: str) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _freeze(value: Any, field: str = "value") -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain only finite serializable values")


def _thaw(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _parameter_package(params: Mapping[str, Any] | None):
    if not isinstance(params, Mapping):
        raise ValueError(
            "an explicit versioned actual-recall package is required")
    required = {
        "version", "projection_formula_version", "projection_radius",
        "threshold_weights",
    }
    if set(params) != required:
        raise ValueError("actual-recall package fields do not match contract")
    version = _identifier(params["version"], "params.version")
    projection_version = _identifier(
        params["projection_formula_version"],
        "params.projection_formula_version")
    radius = _finite(params["projection_radius"], "params.projection_radius")
    weights = params["threshold_weights"]
    if not isinstance(weights, Mapping) or set(weights) != {
            "amplitude_deficit", "relation_deficit"}:
        raise ValueError(
            "actual-recall threshold weights do not match contract")
    amplitude_weight = _finite(
        weights["amplitude_deficit"],
        "params.threshold_weights.amplitude_deficit")
    relation_weight = _finite(
        weights["relation_deficit"],
        "params.threshold_weights.relation_deficit")
    if amplitude_weight < 0.0 or relation_weight < 0.0:
        raise ValueError("actual-recall threshold weights must be non-negative")
    if not math.isclose(
            amplitude_weight + relation_weight, 1.0,
            rel_tol=0.0, abs_tol=1e-15):
        raise ValueError("actual-recall threshold weights must sum to one")
    registered = CX_ACTUAL_RECALL_DUAL_READOUT_V1
    registered_weights = registered["threshold_weights"]
    if (
        version != registered["version"]
        or projection_version != registered["projection_formula_version"]
        or not math.isclose(
            radius, float(registered["projection_radius"]),
            rel_tol=0.0, abs_tol=1e-15)
        or not math.isclose(
            amplitude_weight,
            float(registered_weights["amplitude_deficit"]),
            rel_tol=0.0, abs_tol=1e-15)
        or not math.isclose(
            relation_weight,
            float(registered_weights["relation_deficit"]),
            rel_tol=0.0, abs_tol=1e-15)
    ):
        raise ValueError(
            "actual-recall package does not match its registered version")
    if radius <= 0.0:
        raise ValueError("params.projection_radius must be positive")
    return (
        version, projection_version, radius,
        amplitude_weight, relation_weight)


@dataclass(frozen=True)
class HistoricalLiveProjection:
    """Historical projection whose time/DAG evolution already happened."""

    event_id: str
    evaluation_time_ms: int
    live_readout_ref: str
    temporal_position_fingerprint: str
    projection: ContinuousRootProjection

    def __post_init__(self):
        object.__setattr__(
            self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(
            self, "evaluation_time_ms",
            _timestamp(self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(
            self, "live_readout_ref",
            _identifier(self.live_readout_ref, "live_readout_ref"))
        object.__setattr__(
            self, "temporal_position_fingerprint",
            _identifier(
                self.temporal_position_fingerprint,
                "temporal_position_fingerprint"))
        if not isinstance(self.projection, ContinuousRootProjection):
            raise ValueError("projection must be a ContinuousRootProjection")


@dataclass(frozen=True)
class ActualRecallCandidateEvidence:
    event_id: str
    impulse: float
    threshold: float
    margin: float
    recalled: bool
    formula_version: str
    receipt: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(
            self, "event_id", _identifier(self.event_id, "event_id"))
        for field in ("impulse", "threshold", "margin"):
            object.__setattr__(
                self, field, _finite(getattr(self, field), field))
        if not 0.0 <= self.impulse <= 1.0:
            raise ValueError("impulse must be between zero and one")
        if not 0.0 <= self.threshold <= 1.0:
            raise ValueError("threshold must be between zero and one")
        object.__setattr__(
            self, "formula_version",
            _identifier(self.formula_version, "formula_version"))
        object.__setattr__(
            self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "impulse": self.impulse,
            "threshold": self.threshold,
            "margin": self.margin,
            "recalled": self.recalled,
            "formula_version": self.formula_version,
            "receipt": _thaw(self.receipt),
        }


@dataclass(frozen=True)
class CxActualRecallDecision:
    decision_version: str
    trigger_event_id: str
    evaluation_time_ms: int
    candidate_event_ids: tuple[str, ...]
    recalled_event_ids: tuple[str, ...]
    candidate_evidence: Mapping[str, ActualRecallCandidateEvidence]
    receipt: Mapping[str, Any]

    def __post_init__(self):
        object.__setattr__(
            self, "decision_version",
            _identifier(self.decision_version, "decision_version"))
        object.__setattr__(
            self, "trigger_event_id",
            _identifier(self.trigger_event_id, "trigger_event_id"))
        object.__setattr__(
            self, "evaluation_time_ms",
            _timestamp(self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(
            self, "candidate_event_ids", tuple(self.candidate_event_ids))
        object.__setattr__(
            self, "recalled_event_ids", tuple(self.recalled_event_ids))
        object.__setattr__(
            self, "candidate_evidence",
            MappingProxyType(dict(self.candidate_evidence)))
        object.__setattr__(
            self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self) -> dict[str, Any]:
        return {
            "decision_version": self.decision_version,
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "candidate_event_ids": list(self.candidate_event_ids),
            "recalled_event_ids": list(self.recalled_event_ids),
            "candidate_evidence": {
                event_id: self.candidate_evidence[event_id].to_record()
                for event_id in self.candidate_event_ids
            },
            "receipt": _thaw(self.receipt),
        }

    def to_model_record(self) -> dict[str, Any]:
        return {
            "version": self.decision_version,
            "candidate_event_ids": list(self.candidate_event_ids),
            "recalled_event_ids": list(self.recalled_event_ids),
            "candidate_evidence": {
                event_id: self.candidate_evidence[event_id].to_record()
                for event_id in self.candidate_event_ids
            },
        }

    def to_content_claims_record(self) -> dict[str, Any]:
        """Publish recalled strength without leaking private threshold fields."""
        return {
            "version": CX_MEMORY_CONTENT_CLAIM_VERSION,
            "claims": [
                {
                    "event_id": event_id,
                    "strength": self.candidate_evidence[event_id].impulse,
                    "claim_version": CX_MEMORY_CONTENT_CLAIM_VERSION,
                    "source_evidence_fingerprint": model_data_fingerprint(
                        self.candidate_evidence[event_id].to_record()),
                }
                for event_id in self.recalled_event_ids
            ],
        }


def _candidate_evidence(
        *, historical: HistoricalLiveProjection,
        current: ProjectionSnapshot, version: str,
        expected_projection_version: str, radius: float,
        amplitude_weight: float,
        relation_weight: float) -> ActualRecallCandidateEvidence:
    if historical.evaluation_time_ms != current.evaluation_time_ms:
        raise ValueError(
            "historical and current evaluation time must match")
    for label, projection in (
            ("historical", historical.projection),
            ("current", current.projection)):
        if projection.formula_version != expected_projection_version:
            raise ValueError(
                f"{label} projection formula version does not match package")

    historical_vector = historical.projection.vector
    current_vector = current.projection.vector
    per_axis = {
        axis: {
            "historical": float(historical_vector[axis]),
            "current": float(current_vector[axis]),
            "dot_term": (
                float(historical_vector[axis])
                * float(current_vector[axis])),
            "historical_square": float(historical_vector[axis]) ** 2,
            "current_square": float(current_vector[axis]) ** 2,
        }
        for axis in PROJECTION_AXES
    }
    dot = sum(item["dot_term"] for item in per_axis.values())
    historical_norm = math.sqrt(sum(
        item["historical_square"] for item in per_axis.values()))
    current_norm = math.sqrt(sum(
        item["current_square"] for item in per_axis.values()))
    historical_magnitude = historical_norm / radius
    current_magnitude = current_norm / radius
    amplitude = math.sqrt(historical_magnitude * current_magnitude)
    direction_available = (
        historical_norm > _ZERO_TOLERANCE
        and current_norm > _ZERO_TOLERANCE)
    if direction_available:
        signed_cosine = max(-1.0, min(
            1.0, dot / (historical_norm * current_norm)))
        relation = abs(signed_cosine)
    else:
        signed_cosine = None
        relation = 0.0

    impulse = amplitude * relation
    threshold_terms = {
        "amplitude_deficit": amplitude_weight * (1.0 - amplitude),
        "relation_deficit": relation_weight * (1.0 - relation),
    }
    threshold = sum(threshold_terms.values())
    margin = impulse - threshold
    recalled = bool(direction_available and margin >= 0.0)
    receipt = {
        "operator_version": version,
        "target_event_id": historical.event_id,
        "trigger_event_id": current.event_id,
        "evaluation_time_ms": current.evaluation_time_ms,
        "historical_source": {
            "kind": "c2_live_projection",
            "live_readout_ref": historical.live_readout_ref,
            "temporal_position_fingerprint": (
                historical.temporal_position_fingerprint),
            "projection_formula_version": (
                historical.projection.formula_version),
            "constant_binding_version": (
                historical.projection.constant_binding_version),
            "time_effect_application_count": 1,
            "additional_time_multiplier_applied": False,
        },
        "current_source": {
            "source_receipt_ref": current.source_receipt_ref,
            "projection_formula_version": current.projection.formula_version,
            "constant_binding_version": (
                current.projection.constant_binding_version),
        },
        "per_axis": per_axis,
        "projection_radius": radius,
        "historical_normalized_magnitude": historical_magnitude,
        "current_normalized_magnitude": current_magnitude,
        "amplitude": amplitude,
        "direction_available": direction_available,
        "signed_cosine": signed_cosine,
        "directional_relation": relation,
        "dual_readout": {
            "common_features": {
                "amplitude": amplitude,
                "directional_relation": relation,
            },
            "impulse": impulse,
            "threshold_terms": threshold_terms,
            "threshold": threshold,
            "margin": margin,
            "recalled": recalled,
        },
        "candidate_collection_read": False,
        "fixed_threshold_applied": False,
        "p4_scale_authority": False,
        "selection_performed": True,
    }
    return ActualRecallCandidateEvidence(
        event_id=historical.event_id,
        impulse=impulse,
        threshold=threshold,
        margin=margin,
        recalled=recalled,
        formula_version=version,
        receipt=receipt,
    )


def decide_actual_recall(
        *, historical_live: Sequence[HistoricalLiveProjection],
        current: ProjectionSnapshot,
        params: Mapping[str, Any] | None) -> CxActualRecallDecision:
    """Settle exact recalled IDs without collection-level selection."""
    (
        version, projection_version, radius,
        amplitude_weight, relation_weight,
    ) = _parameter_package(params)
    if isinstance(historical_live, (str, bytes)) or not isinstance(
            historical_live, Sequence):
        raise ValueError("historical_live must be a candidate sequence")
    if not isinstance(current, ProjectionSnapshot):
        raise ValueError("current must be a ProjectionSnapshot")
    candidates = tuple(historical_live)
    if any(not isinstance(item, HistoricalLiveProjection)
           for item in candidates):
        raise ValueError(
            "every historical candidate must be HistoricalLiveProjection")
    event_ids = tuple(item.event_id for item in candidates)
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("historical candidates contain duplicate event IDs")
    if current.event_id in set(event_ids):
        raise ValueError("current trigger cannot be its own history candidate")

    evidence = {
        item.event_id: _candidate_evidence(
            historical=item, current=current, version=version,
            expected_projection_version=projection_version,
            radius=radius, amplitude_weight=amplitude_weight,
            relation_weight=relation_weight)
        for item in candidates
    }
    canonical_ids = tuple(sorted(evidence))
    recalled_ids = tuple(
        event_id for event_id in canonical_ids
        if evidence[event_id].recalled)
    receipt_material = {
        "operator_version": version,
        "trigger_event_id": current.event_id,
        "evaluation_time_ms": current.evaluation_time_ms,
        "candidate_event_ids": list(canonical_ids),
        "recalled_event_ids": list(recalled_ids),
        "candidate_evidence_fingerprints": {
            event_id: model_data_fingerprint(
                evidence[event_id].to_record())
            for event_id in canonical_ids
        },
        "candidate_evaluation_independent": True,
        "candidate_pool_statistics_read": False,
        "fixed_count_applied": False,
        "fixed_threshold_applied": False,
        "additional_time_multiplier_applied": False,
        "p4_scale_authority": False,
    }
    receipt = dict(
        receipt_material,
        fingerprint=model_data_fingerprint(receipt_material))
    return CxActualRecallDecision(
        decision_version=version,
        trigger_event_id=current.event_id,
        evaluation_time_ms=current.evaluation_time_ms,
        candidate_event_ids=canonical_ids,
        recalled_event_ids=recalled_ids,
        candidate_evidence={
            event_id: evidence[event_id] for event_id in canonical_ids
        },
        receipt=receipt,
    )


def _projection_from_record(value: object, field: str):
    record = _plain_mapping(value, field)
    _require_payload_keys(
        record,
        {"vector", "formula_version", "constant_binding_version", "receipt"},
        stage=field,
    )
    return ContinuousRootProjection(
        vector=_plain_mapping(record["vector"], f"{field}.vector"),
        formula_version=record["formula_version"],
        constant_binding_version=record["constant_binding_version"],
        receipt=_plain_mapping(record["receipt"], f"{field}.receipt"),
    )


def _event_ids(value: object, field: str):
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an event-id sequence")
    normalized = tuple(_identifier(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} contains duplicate event IDs")
    return normalized


class CxSixSevenMemoryRecallAdapter:
    """Signed model-stage adapter for the six/seven recall decision."""

    def recall_history(self, *, payload, context):
        context = _context(context)
        _require_payload_keys(
            payload,
            {"candidate_event_ids", "historical_live", "current", "params"},
            stage="recall_history",
        )
        candidate_event_ids = _event_ids(
            payload["candidate_event_ids"], "candidate_event_ids")
        raw_historical = payload["historical_live"]
        if isinstance(raw_historical, (str, bytes)) or not isinstance(
                raw_historical, Sequence):
            raise ValueError("historical_live must be a candidate sequence")
        historical = []
        for index, raw in enumerate(raw_historical):
            field = f"historical_live[{index}]"
            record = _plain_mapping(raw, field)
            _require_payload_keys(
                record,
                {
                    "event_id", "evaluation_time_ms", "live_readout_ref",
                    "temporal_position_fingerprint", "projection",
                },
                stage=field,
            )
            historical.append(HistoricalLiveProjection(
                event_id=record["event_id"],
                evaluation_time_ms=record["evaluation_time_ms"],
                live_readout_ref=record["live_readout_ref"],
                temporal_position_fingerprint=record[
                    "temporal_position_fingerprint"],
                projection=_projection_from_record(
                    record["projection"], f"{field}.projection"),
            ))
        historical_ids = tuple(item.event_id for item in historical)
        if (
            len(historical_ids) != len(candidate_event_ids)
            or set(historical_ids) != set(candidate_event_ids)
        ):
            raise ValueError(
                "historical live candidate set differs from kernel candidate set")

        current_record = _plain_mapping(payload["current"], "current")
        _require_payload_keys(
            current_record,
            {
                "event_id", "evaluation_time_ms",
                "source_receipt_ref", "projection",
            },
            stage="current",
        )
        current = ProjectionSnapshot(
            event_id=current_record["event_id"],
            evaluation_time_ms=current_record["evaluation_time_ms"],
            source_receipt_ref=current_record["source_receipt_ref"],
            projection=_projection_from_record(
                current_record["projection"], "current.projection"),
        )
        evaluation_ms = reference_position_ms(context)
        if (
            context.event_id is None
            or current.event_id != context.event_id
            or current.evaluation_time_ms != evaluation_ms
        ):
            raise ValueError(
                "current projection does not match invocation context")
        decision = decide_actual_recall(
            historical_live=tuple(historical),
            current=current,
            params=_plain_mapping(payload["params"], "params"),
        )
        if (
            len(decision.candidate_event_ids) != len(candidate_event_ids)
            or set(decision.candidate_event_ids) != set(candidate_event_ids)
        ):
            raise ValueError(
                "recall decision candidate set differs from kernel candidate set")
        return {
            "recall_decision": decision.to_model_record(),
            "memory_content_claims": decision.to_content_claims_record(),
        }


__all__ = (
    "CX_ACTUAL_RECALL_DUAL_READOUT_V1",
    "CX_ACTUAL_RECALL_DUAL_READOUT_VERSION",
    "CX_MEMORY_CONTENT_CLAIM_VERSION",
    "ActualRecallCandidateEvidence",
    "CxSixSevenMemoryRecallAdapter",
    "CxActualRecallDecision",
    "HistoricalLiveProjection",
    "ProjectionSnapshot",
    "decide_actual_recall",
)
