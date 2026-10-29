"""Observation-only Alpha 0.4 M3 coefficient shadow matrix.

Callers must provide already reconstructed D1 readout pairs, frozen personality
constants and explicit coefficient profiles.  This module does not create profiles,
choose a winner, write product parameters, or call Runner/F10/P4/providers.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Sequence

from core.effective_constants import CONSTANT_ORDER
from ops.memory_contracts import NeutralMemoryReadout
from ops.memory_scheduler import derive_numeric_recall_witness


NUMERIC_SCHEDULER_SHADOW_VERSION = "numeric-memory-scheduler-shadow-v1"


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


def _constants(value):
    if not isinstance(value, Mapping) or set(value) != set(CONSTANT_ORDER):
        raise ValueError("effective_constants must contain exactly the seven constants")
    parsed = {}
    for axis in CONSTANT_ORDER:
        item = _finite(value[axis], f"effective_constants.{axis}")
        if not 0.0 <= item <= 1.0:
            raise ValueError(f"effective_constants.{axis} must be between zero and one")
        parsed[axis] = item
    return MappingProxyType(parsed)


def _profile_value(value, field, *, open_unit=False):
    parsed = _finite(value, field)
    if open_unit:
        if not 0.0 < parsed < 1.0:
            raise ValueError(f"{field} must be strictly between zero and one")
    elif parsed < 0.0:
        raise ValueError(f"{field} must be non-negative")
    return parsed


def _percentile(sorted_values, fraction):
    if len(sorted_values) == 1:
        return sorted_values[0]
    position = fraction * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return sorted_values[lower]
    weight = position - lower
    return sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight


def _distribution(values):
    ordered = sorted(float(value) for value in values)
    if not ordered:
        raise ValueError("shadow distribution cannot be empty")
    return {
        "count": len(ordered),
        "min": ordered[0],
        "p10": _percentile(ordered, 0.10),
        "p25": _percentile(ordered, 0.25),
        "median": _percentile(ordered, 0.50),
        "p75": _percentile(ordered, 0.75),
        "p90": _percentile(ordered, 0.90),
        "max": ordered[-1],
        "mean": sum(ordered) / len(ordered),
    }


@dataclass(frozen=True)
class NumericSchedulerShadowProfile:
    profile_id: str
    theta0: float
    instability_gain: float
    exchange_gain: float
    retention_gain: float
    parameter_version: str

    def __post_init__(self):
        object.__setattr__(self, "profile_id", _identifier(self.profile_id, "profile_id"))
        object.__setattr__(self, "theta0", _profile_value(
            self.theta0, "theta0", open_unit=True))
        for field in ("instability_gain", "exchange_gain", "retention_gain"):
            object.__setattr__(self, field, _profile_value(getattr(self, field), field))
        object.__setattr__(self, "parameter_version", _identifier(
            self.parameter_version, "parameter_version"))

    def to_record(self):
        return {
            "profile_id": self.profile_id,
            "theta0": self.theta0,
            "instability_gain": self.instability_gain,
            "exchange_gain": self.exchange_gain,
            "retention_gain": self.retention_gain,
            "parameter_version": self.parameter_version,
        }


@dataclass(frozen=True)
class NumericSchedulerShadowCase:
    scope_id: str
    d1_condition_id: str
    neutral_readout: NeutralMemoryReadout
    current_readout: NeutralMemoryReadout
    effective_constants: Mapping[str, float]
    effective_constants_version: str

    def __post_init__(self):
        object.__setattr__(self, "scope_id", _identifier(self.scope_id, "scope_id"))
        object.__setattr__(self, "d1_condition_id", _identifier(
            self.d1_condition_id, "d1_condition_id"))
        if not isinstance(self.neutral_readout, NeutralMemoryReadout):
            raise ValueError("neutral_readout must be NeutralMemoryReadout")
        if not isinstance(self.current_readout, NeutralMemoryReadout):
            raise ValueError("current_readout must be NeutralMemoryReadout")
        object.__setattr__(self, "effective_constants", _constants(
            self.effective_constants))
        object.__setattr__(self, "effective_constants_version", _identifier(
            self.effective_constants_version, "effective_constants_version"))

    @property
    def identity(self):
        return (
            self.scope_id,
            self.d1_condition_id,
            self.neutral_readout.event.event_id,
            self.current_readout.event.event_id,
        )

    def to_record(self):
        return {
            "scope_id": self.scope_id,
            "d1_condition_id": self.d1_condition_id,
            "neutral_readout": self.neutral_readout.to_record(),
            "current_readout": self.current_readout.to_record(),
            "effective_constants": dict(self.effective_constants),
            "effective_constants_version": self.effective_constants_version,
        }


def sweep_numeric_scheduler(
    *,
    cases: Sequence[NumericSchedulerShadowCase],
    profiles: Sequence[NumericSchedulerShadowProfile],
    source_receipt: Mapping[str, object] | None = None,
):
    """Evaluate explicit profiles over explicit D1-conditioned exact-ID cases."""
    normalized_cases = tuple(cases)
    normalized_profiles = tuple(profiles)
    if not normalized_cases:
        raise ValueError("cases must be explicitly provided and non-empty")
    if not normalized_profiles:
        raise ValueError("profiles must be explicitly provided and non-empty")
    if any(not isinstance(case, NumericSchedulerShadowCase) for case in normalized_cases):
        raise ValueError("cases must contain only NumericSchedulerShadowCase")
    if any(not isinstance(profile, NumericSchedulerShadowProfile)
           for profile in normalized_profiles):
        raise ValueError("profiles must contain only NumericSchedulerShadowProfile")

    case_ids = [case.identity for case in normalized_cases]
    if len(set(case_ids)) != len(case_ids):
        raise ValueError("shadow case identity must be unique")
    profile_ids = [profile.profile_id for profile in normalized_profiles]
    if len(set(profile_ids)) != len(profile_ids):
        raise ValueError("shadow profile_id must be unique")
    parameter_versions = [profile.parameter_version for profile in normalized_profiles]
    if len(set(parameter_versions)) != len(parameter_versions):
        raise ValueError("shadow parameter_version must be unique")
    if source_receipt is not None and not isinstance(source_receipt, Mapping):
        raise ValueError("source_receipt must be a mapping")

    ordered_cases = tuple(sorted(normalized_cases, key=lambda case: case.identity))
    ordered_profiles = tuple(sorted(
        normalized_profiles, key=lambda profile: profile.profile_id))
    rows = []
    buckets = {}
    for case in ordered_cases:
        for profile in ordered_profiles:
            witness = derive_numeric_recall_witness(
                neutral_readout=case.neutral_readout,
                current_readout=case.current_readout,
                effective_constants=case.effective_constants,
                effective_constants_version=case.effective_constants_version,
                theta0=profile.theta0,
                instability_gain=profile.instability_gain,
                exchange_gain=profile.exchange_gain,
                retention_gain=profile.retention_gain,
                parameter_version=profile.parameter_version,
            )
            row = {
                "scope_id": case.scope_id,
                "d1_condition_id": case.d1_condition_id,
                "profile_id": profile.profile_id,
                "target_event_id": case.neutral_readout.event.event_id,
                "trigger_event_id": case.current_readout.event.event_id,
                "witness": witness.to_record(),
            }
            rows.append(row)
            key = (case.scope_id, case.d1_condition_id, profile.profile_id)
            bucket = buckets.setdefault(key, {
                "activation": [], "condition": [], "priority": [], "eligible": 0,
            })
            bucket["activation"].append(witness.activation)
            bucket["condition"].append(witness.condition)
            bucket["priority"].append(witness.priority)
            bucket["eligible"] += int(witness.eligible)

    summary = []
    for key in sorted(buckets):
        scope_id, d1_condition_id, profile_id = key
        bucket = buckets[key]
        count = len(bucket["activation"])
        summary.append({
            "scope_id": scope_id,
            "d1_condition_id": d1_condition_id,
            "profile_id": profile_id,
            "event_pair_count": count,
            "eligible_count": bucket["eligible"],
            "eligible_rate": bucket["eligible"] / count,
            "activation_distribution": _distribution(bucket["activation"]),
            "condition_distribution": _distribution(bucket["condition"]),
            "priority_distribution": _distribution(bucket["priority"]),
        })

    return {
        "schema_version": 1,
        "kind": "numeric_memory_scheduler_shadow",
        "operator_version": NUMERIC_SCHEDULER_SHADOW_VERSION,
        "observation_only": True,
        "case_count": len(ordered_cases),
        "profile_count": len(ordered_profiles),
        "row_count": len(rows),
        "profiles": [profile.to_record() for profile in ordered_profiles],
        "source_receipt": deepcopy(dict(source_receipt or {})),
        "summary": summary,
        "rows": rows,
    }