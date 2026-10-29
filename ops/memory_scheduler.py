"""Alpha 0.4 M3 personality-conditioned per-event memory scheduler.

The pure operator consumes one historical D1 readout, one current committed D1
readout and the existing versioned personality constants projection.  It cannot see
text, expression tokens, RAG, candidate counts, tick, writer, Runner or UI state.
"""
from __future__ import annotations

import math
from typing import Mapping

from core.interaction_geometry import ROOT_ORDER
from ops.memory_contracts import NeutralMemoryReadout, NumericRecallWitness
from ops.memory_state import p4_memory_basis


NUMERIC_MEMORY_SCHEDULER_VERSION = "numeric-memory-scheduler-v1"
_CURRENT_STENCIL_MODES = {"single_zero_order", "linear_two", "backward_three"}
_RESONANCE_FIELDS = (
    ("live", None),
    ("path", "path_residual"),
    ("velocity", "target_velocity"),
    ("curvature", "curvature"),
)


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


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


def _non_negative(value, field):
    parsed = _finite(value, field)
    if parsed < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return parsed


def _root_vector(value, field):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    return {axis: _finite(value[axis], f"{field}.{axis}") for axis in ROOT_ORDER}


def _norm(vector):
    return math.sqrt(sum(vector[axis] * vector[axis] for axis in ROOT_ORDER))


def _resonance(left, right):
    left_norm = _norm(left)
    right_norm = _norm(right)
    if left_norm == 0.0 or right_norm == 0.0:
        return {"available": False, "reason": "zero_vector", "value": None}
    cosine = sum(left[axis] * right[axis] for axis in ROOT_ORDER) / (
        left_norm * right_norm)
    cosine = max(-1.0, min(1.0, cosine))
    return {
        "available": True,
        "reason": "available",
        "value": (1.0 + cosine) / 2.0,
    }


def _receipt_vector(readout, key):
    receipt = readout.receipt
    if key not in receipt:
        raise ValueError(f"neutral readout receipt missing {key}")
    return _root_vector(receipt[key], f"neutral readout receipt.{key}")


def _rho(readout):
    if "rho" not in readout.receipt:
        raise ValueError("neutral readout receipt missing rho")
    return _unit(readout.receipt["rho"], "neutral readout receipt.rho")


def _sigmoid(value):
    if value >= 0.0:
        tail = math.exp(-value)
        return 1.0 / (1.0 + tail)
    head = math.exp(value)
    return head / (1.0 + head)


def derive_numeric_recall_witness(
    *,
    neutral_readout: NeutralMemoryReadout,
    current_readout: NeutralMemoryReadout,
    effective_constants: Mapping[str, object],
    effective_constants_version: str,
    theta0: float,
    instability_gain: float,
    exchange_gain: float,
    retention_gain: float,
    parameter_version: str,
) -> NumericRecallWitness:
    """Derive one exact-ID D2 witness without reading or competing with peers."""
    if not isinstance(neutral_readout, NeutralMemoryReadout):
        raise ValueError("neutral_readout must be NeutralMemoryReadout")
    if not isinstance(current_readout, NeutralMemoryReadout):
        raise ValueError("current_readout must be NeutralMemoryReadout")
    if neutral_readout.event.event_id == current_readout.event.event_id:
        raise ValueError("historical memory and current trigger must have distinct exact IDs")
    if neutral_readout.event.committed_at_ms >= current_readout.event.committed_at_ms:
        raise ValueError("historical memory must be strictly earlier than current trigger")
    if neutral_readout.as_of_ms != current_readout.as_of_ms:
        raise ValueError("historical and current D1 readouts must share one evaluation time")
    if neutral_readout.formula_version != current_readout.formula_version:
        raise ValueError("historical and current readouts must use one D1 formula version")

    current_mode = str(current_readout.receipt.get("stencil_mode") or "")
    if current_mode not in _CURRENT_STENCIL_MODES:
        raise ValueError("current trigger must use a committed backward-boundary D1 stencil")

    theta = _finite(theta0, "theta0")
    if not 0.0 < theta < 1.0:
        raise ValueError("theta0 must be strictly between zero and one")
    k_g = _non_negative(instability_gain, "instability_gain")
    k_a = _non_negative(exchange_gain, "exchange_gain")
    k_b = _non_negative(retention_gain, "retention_gain")
    constants_version = _identifier(
        effective_constants_version, "effective_constants_version")
    parameters_version = _identifier(parameter_version, "parameter_version")

    historical_live = _root_vector(
        neutral_readout.live_vector, "neutral_readout.live_vector")
    current_live = _root_vector(current_readout.live_vector, "current_readout.live_vector")
    historical_magnitude = _unit(neutral_readout.magnitude, "neutral_readout.magnitude")
    current_magnitude = _unit(current_readout.magnitude, "current_readout.magnitude")
    age_retention = _rho(neutral_readout)

    resonance = {}
    for label, receipt_key in _RESONANCE_FIELDS:
        if receipt_key is None:
            left, right = historical_live, current_live
        else:
            left = _receipt_vector(neutral_readout, receipt_key)
            right = _receipt_vector(current_readout, receipt_key)
        resonance[label] = _resonance(left, right)
    available_values = [
        item["value"] for item in resonance.values() if item["available"]
    ]
    resonance_mean = (
        sum(available_values) / len(available_values) if available_values else 0.0)

    raw_signal = math.sqrt(historical_magnitude * current_magnitude) * resonance_mean
    raw_signal = max(0.0, min(1.0, raw_signal))

    memory_basis = p4_memory_basis(effective_constants)
    basis = memory_basis["B_memory"]
    exponent = 1.0 + k_g * float(basis["instability"])
    activation = raw_signal ** exponent

    base_logit = math.log(theta / (1.0 - theta))
    exchange_term = -k_a * float(basis["exchange"])
    retention_age_term = (
        k_b * (1.0 - float(basis["retention"])) * (1.0 - age_retention))
    condition_logit = base_logit + exchange_term + retention_age_term
    condition = _sigmoid(condition_logit)
    priority = activation - condition
    eligible = activation >= condition

    receipt = {
        "operator_version": NUMERIC_MEMORY_SCHEDULER_VERSION,
        "parameter_version": parameters_version,
        "effective_constants_version": constants_version,
        "neutral_readout_version": neutral_readout.formula_version,
        "current_readout_version": current_readout.formula_version,
        "target_event_id": neutral_readout.event.event_id,
        "trigger_event_id": current_readout.event.event_id,
        "evaluation_time_ms": neutral_readout.as_of_ms,
        "historical_magnitude": historical_magnitude,
        "current_magnitude": current_magnitude,
        "age_retention": age_retention,
        "resonance": resonance,
        "resonance_mean": resonance_mean,
        "available_resonance_count": len(available_values),
        "raw_signal": raw_signal,
        "memory_basis": memory_basis,
        "parameters": {
            "theta0": theta,
            "instability_gain": k_g,
            "exchange_gain": k_a,
            "retention_gain": k_b,
        },
        "activation_exponent": exponent,
        "activation": activation,
        "condition_terms": {
            "base_logit": base_logit,
            "exchange": exchange_term,
            "retention_age": retention_age_term,
            "total_logit": condition_logit,
        },
        "condition": condition,
        "priority": priority,
        "eligible": eligible,
        "decision_reason": (
            "activation_at_or_above_condition"
            if eligible else "activation_below_condition"
        ),
    }
    return NumericRecallWitness(
        event_id=neutral_readout.event.event_id,
        activation=activation,
        condition=condition,
        priority=priority,
        eligible=eligible,
        scheduler_version=NUMERIC_MEMORY_SCHEDULER_VERSION,
        neutral_readout_version=neutral_readout.formula_version,
        receipt=receipt,
    )