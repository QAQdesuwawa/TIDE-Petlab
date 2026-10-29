"""Alpha 0.4 C3-A independent per-memory attention witness.

This pure operator compares two already role-conditioned continuous-root
projections at one evaluation time and then applies one separately receipted
time-accessibility value.  It does not read constants, roots, text, RAG,
tokens, writer state, tick state, candidate collections, or runtime objects.
It emits no threshold, eligibility, winner, rank, or sending authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from .continuous_root_projection import (
    CONTINUOUS_ROOT_PROJECTION_VERSION,
    PROJECTION_AXES,
    ContinuousRootProjection,
)


MEMORY_ATTENTION_VERSION = "memory-attention-witness-v1-shadow"
MEMORY_ATTENTION_V1 = {
    "version": MEMORY_ATTENTION_VERSION,
    "projection_formula_version": CONTINUOUS_ROOT_PROJECTION_VERSION,
    "projection_radius": math.sqrt(len(PROJECTION_AXES)),
}
_ZERO_TOLERANCE = 1e-15


def _freeze(value, field="value"):
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain only finite serializable values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


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


def _parameter_package(params):
    if not isinstance(params, Mapping):
        raise ValueError("an explicit versioned memory-attention package is required")
    required = {"version", "projection_formula_version", "projection_radius"}
    missing = required - set(params)
    if missing:
        raise ValueError("memory-attention package is missing: "
                         + ", ".join(sorted(missing)))
    version = _identifier(params.get("version"), "params.version")
    if version != MEMORY_ATTENTION_VERSION:
        raise ValueError("unknown memory-attention version")
    projection_version = _identifier(
        params.get("projection_formula_version"),
        "params.projection_formula_version")
    radius = _finite(params.get("projection_radius"), "params.projection_radius")
    if radius <= 0.0:
        raise ValueError("params.projection_radius must be positive")
    if projection_version != MEMORY_ATTENTION_V1["projection_formula_version"]:
        raise ValueError("memory-attention projection formula version is unregistered")
    if not math.isclose(radius, MEMORY_ATTENTION_V1["projection_radius"],
                        rel_tol=0.0, abs_tol=1e-15):
        raise ValueError("memory-attention package does not match its registered version")
    return version, projection_version, radius


@dataclass(frozen=True)
class ProjectionSnapshot:
    event_id: str
    evaluation_time_ms: int
    source_receipt_ref: str
    projection: ContinuousRootProjection

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        object.__setattr__(self, "source_receipt_ref", _identifier(
            self.source_receipt_ref, "source_receipt_ref"))
        if not isinstance(self.projection, ContinuousRootProjection):
            raise ValueError("projection must be a ContinuousRootProjection")


@dataclass(frozen=True)
class MemoryAccessibilityEvidence:
    target_event_id: str
    trigger_event_id: str
    evaluation_time_ms: int
    value: float
    formula_version: str
    receipt_ref: str

    def __post_init__(self):
        for field in ("target_event_id", "trigger_event_id", "formula_version",
                      "receipt_ref"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        value = _finite(self.value, "accessibility.value")
        if not 0.0 <= value <= 1.0:
            raise ValueError("accessibility.value must be between zero and one")
        object.__setattr__(self, "value", value)


@dataclass(frozen=True)
class MemoryAttentionWitness:
    target_event_id: str
    trigger_event_id: str
    evaluation_time_ms: int
    raw_drive: float
    formula_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        for field in ("target_event_id", "trigger_event_id", "formula_version"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        raw = _finite(self.raw_drive, "raw_drive")
        if not 0.0 <= raw <= 1.0:
            raise ValueError("raw_drive must be between zero and one")
        object.__setattr__(self, "raw_drive", raw)
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self):
        return {
            "target_event_id": self.target_event_id,
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "raw_drive": self.raw_drive,
            "formula_version": self.formula_version,
            "receipt": _thaw(self.receipt),
        }


def build_memory_attention_witness(
        *, historical: ProjectionSnapshot, current: ProjectionSnapshot,
        accessibility: MemoryAccessibilityEvidence,
        params: Mapping[str, object]) -> MemoryAttentionWitness:
    """Build one candidate-local raw drive without selecting any memory."""
    version, expected_projection_version, radius = _parameter_package(params)
    if not isinstance(historical, ProjectionSnapshot):
        raise ValueError("historical must be a ProjectionSnapshot")
    if not isinstance(current, ProjectionSnapshot):
        raise ValueError("current must be a ProjectionSnapshot")
    if not isinstance(accessibility, MemoryAccessibilityEvidence):
        raise ValueError("accessibility must be MemoryAccessibilityEvidence")
    if historical.evaluation_time_ms != current.evaluation_time_ms:
        raise ValueError("historical and current evaluation time must match")
    evaluation_time = historical.evaluation_time_ms
    if accessibility.evaluation_time_ms != evaluation_time:
        raise ValueError("accessibility evaluation time must match projections")
    if accessibility.target_event_id != historical.event_id:
        raise ValueError("accessibility target event does not match historical projection")
    if accessibility.trigger_event_id != current.event_id:
        raise ValueError("accessibility trigger event does not match current projection")
    for label, snapshot in (("historical", historical), ("current", current)):
        if snapshot.projection.formula_version != expected_projection_version:
            raise ValueError(f"{label} projection formula version does not match package")

    historical_vector = historical.projection.vector
    current_vector = current.projection.vector
    per_axis = {
        axis: {
            "historical": float(historical_vector[axis]),
            "current": float(current_vector[axis]),
            "dot_term": float(historical_vector[axis]) * float(current_vector[axis]),
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
        historical_norm > _ZERO_TOLERANCE and current_norm > _ZERO_TOLERANCE)
    if direction_available:
        signed_cosine = max(-1.0, min(1.0, dot / (
            historical_norm * current_norm)))
        directional_relation = abs(signed_cosine)
    else:
        signed_cosine = None
        directional_relation = 0.0
    interaction_strength = amplitude * directional_relation
    raw_drive = interaction_strength * accessibility.value
    receipt = {
        "operator_version": MEMORY_ATTENTION_VERSION,
        "parameter_version": version,
        "target_event_id": historical.event_id,
        "trigger_event_id": current.event_id,
        "evaluation_time_ms": evaluation_time,
        "historical_projection": {
            "source_receipt_ref": historical.source_receipt_ref,
            "formula_version": historical.projection.formula_version,
            "constant_binding_version": historical.projection.constant_binding_version,
            "vector": dict(historical_vector),
        },
        "current_projection": {
            "source_receipt_ref": current.source_receipt_ref,
            "formula_version": current.projection.formula_version,
            "constant_binding_version": current.projection.constant_binding_version,
            "vector": dict(current_vector),
        },
        "projection_radius": radius,
        "per_axis": per_axis,
        "dot_product": dot,
        "historical_norm": historical_norm,
        "current_norm": current_norm,
        "historical_normalized_magnitude": historical_magnitude,
        "current_normalized_magnitude": current_magnitude,
        "amplitude": amplitude,
        "direction_available": direction_available,
        "signed_cosine": signed_cosine,
        "directional_relation": directional_relation,
        "interaction_strength": interaction_strength,
        "accessibility": {
            "value": accessibility.value,
            "formula_version": accessibility.formula_version,
            "receipt_ref": accessibility.receipt_ref,
        },
        "raw_drive": raw_drive,
        "opposite_direction_is_relevant": True,
        "selection_performed": False,
        "candidate_collection_read": False,
        "constants_reapplied": False,
        "derived_only": True,
    }
    return MemoryAttentionWitness(
        target_event_id=historical.event_id,
        trigger_event_id=current.event_id,
        evaluation_time_ms=evaluation_time,
        raw_drive=raw_drive,
        formula_version=version,
        receipt=receipt,
    )
