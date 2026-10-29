"""Alpha 0.4 shared continuous projection over committed root transitions.

The operator sits upstream of both expression-token mapping and memory
attention.  It consumes one already-committed six-root transition plus one
explicit frozen seven-constant binding and emits a bounded three-direction
numeric receipt.  It has no token, memory-selection, writer, tick, Runner, or
UI dependency and grants no sending authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from collections.abc import Mapping

from .preference_parameters import CONSTANT_ORDER
from .interaction_geometry import (
    GEOMETRY_ORDER,
    ROOT_ORDER,
    normalized_shock,
    relationship_transition,
)


CONTINUOUS_ROOT_PROJECTION_VERSION = "continuous-root-projection-b-v1-shadow"
PROJECTION_AXES = ("exchange", "withhold", "tension")
_AXIS_CONSTANTS = {
    "exchange": ("a1", "a2"),
    "withhold": ("b1", "b2", "b3"),
    "tension": ("g1", "g2"),
}

# Explicit package for shadow callers.  The public function still requires the
# caller to pass a package; it never silently falls back to this object.
CONTINUOUS_ROOT_PROJECTION_B_V1 = {
    "version": CONTINUOUS_ROOT_PROJECTION_VERSION,
    "smooth_epsilon": 0.05,
    "output_gain": 1.0,
    "shock_scales": {
        "S": 2.0, "T": 2.0, "M": 1.0, "R_net": 2.0, "R_amb": 1.0,
    },
}


def _freeze(value, field="value"):
    if type(value) in (tuple, list) and all(type(item) is str for item in value):
        return tuple(value)
    if value is None or type(value) in (str, bool, int): return value
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
    if type(value) is tuple and all(type(item) is str for item in value):
        return list(value)
    if value is None or type(value) in (str, bool, int): return value
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


def _finite(value, field):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _vector(value, order, field, *, closed_unit=False):
    if not isinstance(value, Mapping) or set(value) != set(order):
        raise ValueError(f"{field} must contain exactly: " + ", ".join(order))
    parsed = {axis: _finite(value[axis], f"{field}.{axis}") for axis in order}
    if closed_unit:
        invalid = [axis for axis in order if not 0.0 <= parsed[axis] <= 1.0]
        if invalid:
            raise ValueError(f"{field}.{invalid[0]} must be between zero and one")
    return parsed


def _constant_binding(binding):
    if not isinstance(binding, Mapping):
        raise ValueError("constant_binding must be a mapping")
    required = {
        "version", "base_constants_version", "b0_version",
        "b0_mapping_version", "correction_version",
        "effective_constants_version", "effective_constants",
    }
    missing = required - set(binding)
    if missing:
        raise ValueError("constant binding is missing: " + ", ".join(sorted(missing)))
    for field in (
            "version", "base_constants_version", "b0_version",
            "effective_constants_version"):
        _identifier(binding.get(field), f"constant_binding.{field}")
    for field in ("b0_mapping_version", "correction_version"):
        if binding.get(field) is not None:
            _identifier(binding.get(field), f"constant_binding.{field}")
    constants = _vector(
        binding.get("effective_constants"), CONSTANT_ORDER,
        "constant_binding.effective_constants", closed_unit=True)
    normalized = dict(binding)
    normalized["effective_constants"] = constants
    return normalized


def _parameter_package(params):
    if not isinstance(params, Mapping):
        raise ValueError("an explicit versioned projection package is required")
    required = {"version", "smooth_epsilon", "output_gain", "shock_scales"}
    missing = required - set(params)
    if missing:
        raise ValueError("projection package is missing: " + ", ".join(sorted(missing)))
    version = _identifier(params.get("version"), "params.version")
    if version != CONTINUOUS_ROOT_PROJECTION_VERSION:
        raise ValueError("unknown continuous-root projection version")
    epsilon = _finite(params.get("smooth_epsilon"), "params.smooth_epsilon")
    gain = _finite(params.get("output_gain"), "params.output_gain")
    if epsilon <= 0.0:
        raise ValueError("params.smooth_epsilon must be positive")
    if gain <= 0.0:
        raise ValueError("params.output_gain must be positive")
    scales = _vector(params.get("shock_scales"), GEOMETRY_ORDER,
                     "params.shock_scales")
    if any(value <= 0.0 for value in scales.values()):
        raise ValueError("every shock scale must be positive")
    registered = CONTINUOUS_ROOT_PROJECTION_B_V1
    if (not math.isclose(epsilon, float(registered["smooth_epsilon"]),
                         rel_tol=0.0, abs_tol=1e-15)
            or not math.isclose(gain, float(registered["output_gain"]),
                                rel_tol=0.0, abs_tol=1e-15)
            or any(not math.isclose(scales[axis],
                                    float(registered["shock_scales"][axis]),
                                    rel_tol=0.0, abs_tol=1e-15)
                   for axis in GEOMETRY_ORDER)):
        raise ValueError("projection package does not match its registered version")
    return version, epsilon, gain, scales


def _validated_transition(transition):
    if not isinstance(transition, Mapping):
        raise ValueError("transition must be a relationship-transition receipt")
    before = transition.get("before")
    after = transition.get("after")
    if not isinstance(before, Mapping) or not isinstance(after, Mapping):
        raise ValueError("transition must contain before and after receipts")
    before_roots = _vector(before.get("roots"), ROOT_ORDER, "transition.before.roots")
    after_roots = _vector(after.get("roots"), ROOT_ORDER, "transition.after.roots")
    rebuilt = relationship_transition(before_roots, after_roots)
    for section, order in (("root_delta", ROOT_ORDER),
                           ("geometry_delta", GEOMETRY_ORDER)):
        supplied = _vector(transition.get(section), order, f"transition.{section}")
        if any(not math.isclose(supplied[axis], rebuilt[section][axis],
                                rel_tol=0.0, abs_tol=1e-9)
               for axis in order):
            raise ValueError(f"transition.{section} does not match before/after roots")
    return rebuilt


def _smooth_positive(value, epsilon):
    """Continuously differentiable positive part with no slope jump at zero."""
    positive = max(0.0, float(value))
    if positive == 0.0:
        return 0.0
    return (positive * positive) / (positive + epsilon)


def _smooth_magnitude(value, epsilon):
    value = float(value)
    return math.sqrt(value * value + epsilon * epsilon) - epsilon


def _semantic_conditions(roots, geometry, epsilon):
    s = float(geometry["S"])
    t = _smooth_positive(geometry["T"], epsilon)
    r_net_magnitude = _smooth_magnitude(geometry["R_net"], epsilon)
    relation_gate = r_net_magnitude / (1.0 + r_net_magnitude)
    ambiguity_gate = 1.0 / (1.0 + r_net_magnitude)
    need = _smooth_positive(roots["P_need"], epsilon)
    pressure = (_smooth_positive(roots["E_env"], epsilon) + t) / 3.0
    ambivalence = _smooth_positive(geometry["R_amb"], epsilon)
    return {
        "a1": _smooth_positive(s, epsilon),
        "a2": need * ambiguity_gate,
        "b1": pressure,
        "b2": need,
        "b3": _smooth_positive(-s, epsilon),
        "g1": pressure * ambivalence,
        "g2": need * relation_gate,
    }


@dataclass(frozen=True)
class ContinuousRootProjection:
    vector: Mapping[str, float]
    formula_version: str
    constant_binding_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        vector = _vector(self.vector, PROJECTION_AXES, "projection.vector")
        if any(not -1.0 <= value <= 1.0 for value in vector.values()):
            raise ValueError("projection.vector must stay inside the signed unit cube")
        object.__setattr__(self, "vector", MappingProxyType(vector))
        object.__setattr__(self, "formula_version", _identifier(
            self.formula_version, "formula_version"))
        object.__setattr__(self, "constant_binding_version", _identifier(
            self.constant_binding_version, "constant_binding_version"))
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self):
        return {
            "vector": dict(self.vector),
            "formula_version": self.formula_version,
            "constant_binding_version": self.constant_binding_version,
            "receipt": _thaw(self.receipt),
        }


def project_continuous_roots(*, transition: Mapping[str, object],
                             constant_binding: Mapping[str, object],
                             params: Mapping[str, object]) -> ContinuousRootProjection:
    """Project one committed six-root transition into three continuous axes.

    Each constant retains its named semantic condition.  The operator combines
    level and change only within that channel, then aggregates the channel
    vector on its declared axis.  There is no dense 7x6 matrix, group-mean
    personality scalar, lateral inhibition, or downstream feedback.
    """
    version, epsilon, gain, shock_scales = _parameter_package(params)
    binding = _constant_binding(constant_binding)
    constants = binding["effective_constants"]
    validated = _validated_transition(transition)
    before_roots = validated["before"]["roots"]
    after_roots = validated["after"]["roots"]
    before_geometry = validated["before"]["values"]
    after_geometry = validated["after"]["values"]
    before_conditions = _semantic_conditions(
        before_roots, before_geometry, epsilon)
    after_conditions = _semantic_conditions(
        after_roots, after_geometry, epsilon)
    shock = normalized_shock(validated["geometry_delta"], shock_scales)

    channel_conditions = {}
    channel_values = {}
    for constant in CONSTANT_ORDER:
        signed_change = after_conditions[constant] - before_conditions[constant]
        if constant.startswith("g"):
            change = _smooth_magnitude(signed_change, epsilon) + float(shock["norm"])
            # Tension reads magnitude; the signed semantic delta remains in
            # the receipt so later comparison can distinguish direction.
            combined = math.hypot(after_conditions[constant], change) / math.sqrt(2.0)
        else:
            change = signed_change
            combined = (after_conditions[constant] + change) / math.sqrt(2.0)
        channel_conditions[constant] = {
            "before": before_conditions[constant],
            "after": after_conditions[constant],
            "change": signed_change,
            "change_magnitude_used": change if constant.startswith("g") else None,
            "combined": combined,
        }
        channel_values[constant] = float(constants[constant]) * combined

    constant_terms = {
        axis: {
            constant: (channel_values[constant]
                       if constant in _AXIS_CONSTANTS[axis] else 0.0)
            for constant in CONSTANT_ORDER
        }
        for axis in PROJECTION_AXES
    }
    pre_bound = {
        axis: sum(constant_terms[axis].values()) / math.sqrt(
            len(_AXIS_CONSTANTS[axis]))
        for axis in PROJECTION_AXES
    }
    vector = {axis: math.tanh(gain * pre_bound[axis]) for axis in PROJECTION_AXES}
    receipt = {
        "operator_version": CONTINUOUS_ROOT_PROJECTION_VERSION,
        "parameter_version": version,
        "constant_binding": {
            "version": binding["version"],
            "base_constants_version": binding["base_constants_version"],
            "b0_version": binding["b0_version"],
            "b0_mapping_version": binding["b0_mapping_version"],
            "correction_version": binding["correction_version"],
            "effective_constants_version": binding["effective_constants_version"],
        },
        "effective_constants": dict(constants),
        "transition": validated,
        "smooth_epsilon": epsilon,
        "output_gain": gain,
        "channel_conditions": channel_conditions,
        "constant_terms": constant_terms,
        "axis_constant_membership": {
            axis: _AXIS_CONSTANTS[axis] for axis in PROJECTION_AXES
        },
        "shock": shock,
        "pre_bound_vector": pre_bound,
        "final_vector": vector,
        "legacy_winner_take_all_applied": False,
        "dense_weight_matrix_applied": False,
        "constants_applied_once": True,
        "derived_only": True,
    }
    return ContinuousRootProjection(
        vector=vector,
        formula_version=version,
        constant_binding_version=binding["effective_constants_version"],
        receipt=receipt,
    )
