"""Alpha 0.4 C1 root dynamics pure operators.

This module contains no Runner, Store, memory, token, writer, tick or UI code.
Agent 1's six-axis output is treated as a participant-independent stimulus.
`respond_to_root_stimulus` is the only role-specialisation step; `flow_root_time`
is the independent real-wall-clock flow.

Both operators require explicit versioned parameter packages.  The packages in
tests are evidence fixtures, not product defaults.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from .preference_parameters import CONSTANT_ORDER
from .interaction_geometry import ROOT_ORDER, relationship_geometry, root_snapshot


ROOT_EVENT_RESPONSE_VERSION = "root-event-response-directional-v1"
ROOT_TIME_FLOW_VERSION = "root-time-components-exponential-v1"
RECOVERABLE_AXES = ("E_env", "P_need", "V_attr", "V_rep")
LEVER_AXES = ("L_self", "L_opponent")
HOUR_MS = 3_600_000.0

# G-A keeps the seven named response channels separate until each reactive root
# reads its own semantically defined subset.  Membership is boolean and part of
# the operator version; callers cannot smuggle in a free seven-by-four matrix.
_EVENT_RESPONSE_MEMBERS = {
    "E_env": ("a1", "b1", "g1"),
    "P_need": ("a2", "b2", "g2"),
    "V_attr": ("a2", "g2"),
    "V_rep": ("a1", "b1", "b2", "b3", "g1"),
}

# Phi-A uses only semantically named retention memberships.  The tuple is part
# of the operator version, not a caller supplied 7x4 matrix.
_TIME_RETENTION_MEMBERS = {
    "E_env": ("b1", "g1"),
    "P_need": ("a2", "b2", "g2"),
    "V_attr": ("a2", "g2"),
    "V_rep": ("a1", "b1", "b2", "b3", "g1"),
}


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


def _closed_unit(value, field):
    parsed = _finite(value, field)
    if not 0.0 <= parsed <= 1.0:
        raise ValueError(f"{field} must be between zero and one")
    return parsed


def _constant_vector(constants):
    if not isinstance(constants, Mapping) or set(constants) != set(CONSTANT_ORDER):
        raise ValueError("C_effective must contain exactly the seven role constants")
    return {
        axis: _closed_unit(constants[axis], f"C_effective.{axis}")
        for axis in CONSTANT_ORDER
    }


def _root_vector(values, *, label):
    roots = root_snapshot(values)
    return {axis: _closed_unit(roots[axis], f"{label}.{axis}") for axis in ROOT_ORDER}


def _stimulus_vector(values):
    if not isinstance(values, Mapping) or set(values) != set(ROOT_ORDER):
        raise ValueError("stimulus must contain exactly the six root axes")
    out = {}
    for axis in ROOT_ORDER:
        value = _finite(values[axis], f"stimulus.{axis}")
        if not -1.0 <= value <= 1.0:
            raise ValueError(f"stimulus.{axis} must be between minus one and one")
        out[axis] = value
    return out


def _parameter_package(params, *, required):
    if not isinstance(params, Mapping):
        raise ValueError("an explicit versioned parameter package is required")
    version = _identifier(params.get("version"), "parameter version")
    missing = [key for key in required if key not in params]
    if missing:
        raise ValueError("parameter package is missing: " + ", ".join(missing))
    return version


def _freeze(value):
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(_freeze(item) for item in value)
    if isinstance(value, tuple):
        return tuple(_freeze(item) for item in value)
    return value


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class RootEventResponse:
    """Immutable result of one role-specialised committed-event response."""

    delta_x_event: Mapping[str, float]
    x_after: Mapping[str, float]
    receipt: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "delta_x_event", _freeze(dict(self.delta_x_event)))
        object.__setattr__(self, "x_after", _freeze(dict(self.x_after)))
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt)))

    def as_record(self):
        return {
            "delta_x_event": _thaw(self.delta_x_event),
            "x_after": _thaw(self.x_after),
            "receipt": _thaw(self.receipt),
        }


@dataclass(frozen=True)
class RootTimeFlow:
    """Immutable result of one analytic real-wall-clock segment."""

    delta_x_time: Mapping[str, float]
    x_after: Mapping[str, float]
    receipt: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "delta_x_time", _freeze(dict(self.delta_x_time)))
        object.__setattr__(self, "x_after", _freeze(dict(self.x_after)))
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt)))

    def as_record(self):
        return {
            "delta_x_time": _thaw(self.delta_x_time),
            "x_after": _thaw(self.x_after),
            "receipt": _thaw(self.receipt),
        }


def _response_conditions(roots):
    geometry = relationship_geometry(roots)["values"]
    s = float(geometry["S"])
    t = float(geometry["T"])
    r_net = float(geometry["R_net"])
    r_amb = float(geometry["R_amb"])
    # E_env and the two disclosure levers are three same-range root facts.
    # Their arithmetic mean keeps the condition in [0, 1] without a hidden
    # per-source weight.
    pressure_exposure = (roots["E_env"] + t) / 3.0
    return geometry, {
        "a1": max(0.0, s),
        "a2": roots["P_need"] * (1.0 - abs(r_net)),
        "b1": pressure_exposure,
        "b2": roots["P_need"],
        "b3": max(0.0, -s),
        "g1": pressure_exposure * r_amb,
        "g2": roots["P_need"] * abs(r_net),
    }


def respond_to_root_stimulus(
        constants: Mapping[str, object],
        x_before: Mapping[str, object],
        stimulus: Mapping[str, object],
        *,
        effective_constants_version: str,
        params: Mapping[str, object]) -> RootEventResponse:
    """G-A: apply role sensitivity once while preserving stimulus direction.

    Each reactive root reads a fixed, semantically named subset of the seven
    response channels.  No shared scalar is allowed to drive all four roots and
    no caller supplied seven-by-four matrix exists.  Disclosure levers pass
    through unchanged.  The final root transition records the actually
    applied, boundary-projected delta.
    """
    constants_version = _identifier(
        effective_constants_version, "effective_constants_version")
    parameter_version = _parameter_package(params, required=("response_scale",))
    response_scale = _finite(params["response_scale"], "response_scale")
    if response_scale < 0.0:
        raise ValueError("response_scale must be non-negative")

    role = _constant_vector(constants)
    before = _root_vector(x_before, label="X_before")
    measured = _stimulus_vector(stimulus)
    geometry, conditions = _response_conditions(before)
    channel_terms = {
        axis: role[axis] * conditions[axis] for axis in CONSTANT_ORDER
    }
    per_axis_terms = {}
    local_response = {}
    axis_gains = {}
    for root_axis in ROOT_ORDER:
        if root_axis in LEVER_AXES:
            axis_gains[root_axis] = 1.0
            continue
        members = _EVENT_RESPONSE_MEMBERS[root_axis]
        terms = {
            constant: (channel_terms[constant] if constant in members else 0.0)
            for constant in CONSTANT_ORDER
        }
        per_axis_terms[root_axis] = terms
        local_response[root_axis] = math.sqrt(
            sum(terms[constant] ** 2 for constant in members) / len(members))
        axis_gains[root_axis] = math.exp(
            response_scale * local_response[root_axis])
    raw_delta = {
        axis: measured[axis] * axis_gains[axis] for axis in ROOT_ORDER
    }
    projected = {
        axis: min(1.0, max(0.0, before[axis] + raw_delta[axis]))
        for axis in ROOT_ORDER
    }
    actual = {
        axis: projected[axis] - before[axis] for axis in ROOT_ORDER
    }
    clipped_axes = tuple(
        axis for axis in ROOT_ORDER
        if not math.isclose(raw_delta[axis], actual[axis], rel_tol=0.0, abs_tol=1e-12)
    )
    receipt = {
        "operator_version": ROOT_EVENT_RESPONSE_VERSION,
        "parameter_version": parameter_version,
        "effective_constants_version": constants_version,
        "inputs": {
            "C_effective": role,
            "X_before": before,
            "stimulus": measured,
        },
        "relation_geometry": geometry,
        "channel_conditions": conditions,
        "seven_channel_terms": channel_terms,
        "per_axis_response_rule": "rms_of_named_axis_local_channel_terms",
        "per_axis_response_members": {
            axis: tuple(_EVENT_RESPONSE_MEMBERS[axis]) for axis in RECOVERABLE_AXES
        },
        "per_axis_response_terms": per_axis_terms,
        "per_axis_local_response": local_response,
        "response_scale": response_scale,
        "axis_gains": axis_gains,
        "raw_delta": raw_delta,
        "projection": {
            "rule": "root_box_projection_0_1",
            "clipped_axes": clipped_axes,
        },
        "Delta_X_event": actual,
        "X_after": projected,
        "personality_application_count": 1,
        "lever_personality_scaled": False,
        "derived_only": True,
    }
    return RootEventResponse(actual, projected, receipt)


def _time_parameters(params):
    version = _parameter_package(
        params, required=("rest_point", "axis_rho_per_hour", "retention_scale"))
    rest_raw = params["rest_point"]
    rates_raw = params["axis_rho_per_hour"]
    if not isinstance(rest_raw, Mapping) or set(rest_raw) != set(RECOVERABLE_AXES):
        raise ValueError("rest_point must contain exactly the four recoverable axes")
    if not isinstance(rates_raw, Mapping) or set(rates_raw) != set(RECOVERABLE_AXES):
        raise ValueError("axis_rho_per_hour must contain exactly the four recoverable axes")
    rest = {
        axis: _closed_unit(rest_raw[axis], f"rest_point.{axis}")
        for axis in RECOVERABLE_AXES
    }
    rates = {}
    for axis in RECOVERABLE_AXES:
        rate = _finite(rates_raw[axis], f"axis_rho_per_hour.{axis}")
        if rate <= 0.0:
            raise ValueError(f"axis_rho_per_hour.{axis} must be positive")
        rates[axis] = rate
    retention_scale = _finite(params["retention_scale"], "retention_scale")
    if retention_scale < 0.0:
        raise ValueError("retention_scale must be non-negative")
    return version, rest, rates, retention_scale


def flow_root_time(
        constants: Mapping[str, object],
        x_before: Mapping[str, object],
        elapsed_ms: int | float,
        *,
        effective_constants_version: str,
        params: Mapping[str, object]) -> RootTimeFlow:
    """Phi-A: analytic component-preserving exponential wall-clock flow."""
    constants_version = _identifier(
        effective_constants_version, "effective_constants_version")
    role = _constant_vector(constants)
    before = _root_vector(x_before, label="X_before")
    parameter_version, rest, base_rates, retention_scale = _time_parameters(params)
    elapsed = _finite(elapsed_ms, "elapsed_ms")
    if elapsed < 0.0:
        raise ValueError("elapsed_ms must be non-negative")
    elapsed_hours = elapsed / HOUR_MS

    component_terms = {}
    effective_rates = {}
    after = dict(before)
    local_rate = {axis: 0.0 for axis in ROOT_ORDER}
    for root_axis in RECOVERABLE_AXES:
        members = _TIME_RETENTION_MEMBERS[root_axis]
        terms = {
            constant: (
                -retention_scale * role[constant] / len(members)
                if constant in members else 0.0
            )
            for constant in CONSTANT_ORDER
        }
        component_terms[root_axis] = terms
        rate = base_rates[root_axis] * math.exp(sum(terms.values()))
        effective_rates[root_axis] = rate
        after[root_axis] = (
            rest[root_axis]
            + (before[root_axis] - rest[root_axis]) * math.exp(-rate * elapsed_hours)
        )
        local_rate[root_axis] = -rate * (after[root_axis] - rest[root_axis])

    delta = {axis: after[axis] - before[axis] for axis in ROOT_ORDER}
    receipt = {
        "operator_version": ROOT_TIME_FLOW_VERSION,
        "parameter_version": parameter_version,
        "effective_constants_version": constants_version,
        "inputs": {
            "C_effective": role,
            "X_before": before,
            "elapsed_ms": int(round(elapsed)),
        },
        "elapsed_hours": elapsed_hours,
        "rest_point": rest,
        "base_rate_per_hour": base_rates,
        "retention_scale": retention_scale,
        "retention_members": {
            axis: tuple(_TIME_RETENTION_MEMBERS[axis]) for axis in RECOVERABLE_AXES
        },
        "seven_component_rate_terms": component_terms,
        "effective_rate_per_hour": effective_rates,
        "local_rate_per_hour": local_rate,
        "Delta_X_time": delta,
        "X_after": after,
        "levers_wall_clock_erased": False,
        "semigroup_formula": True,
        "derived_only": True,
    }
    return RootTimeFlow(delta, after, receipt)
