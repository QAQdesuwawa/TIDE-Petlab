"""Five-axis preference parameters for the six-state/seven-constant model.

This module owns the reference package's B0 schema and its mapping into the
package's seven constants.  The runtime host may select an immutable binding,
but it must not interpret these axes or reproduce this mapping.
"""
from __future__ import annotations

import math
from typing import Mapping


CONSTANT_ORDER = ("a1", "a2", "b1", "b2", "b3", "g1", "g2")
PREFERENCE_ORDER = (
    "Counter_Phobic", "Mirroring", "Avoidance", "Trauma_Recon", "Pragmatism",
)
VERSION = "effective-constants-v2"
B0_MAPPING_VERSION = "b0-to-c-effective-v1"
B0_MAX_BLEND = 0.25

# Reference-package data.  It is not a framework-wide parameter schema.
B0_TARGETS = {
    "Counter_Phobic": {"a1": .90, "a2": .70, "b1": .10, "b2": .30, "b3": .40, "g1": .20, "g2": .10},
    "Mirroring": {"a1": .30, "a2": .60, "b1": .50, "b2": .95, "b3": .80, "g1": .30, "g2": .20},
    "Avoidance": {"a1": .10, "a2": .10, "b1": .95, "b2": .60, "b3": .90, "g1": .15, "g2": .10},
    "Trauma_Recon": {"a1": .40, "a2": .50, "b1": .20, "b2": .20, "b3": .30, "g1": .90, "g2": 1.00},
    "Pragmatism": {"a1": .40, "a2": .20, "b1": .50, "b2": .20, "b3": .50, "g1": .05, "g2": .05},
}


def _constant_vector(value: Mapping[str, object], *, label: str) -> dict[str, float]:
    if not isinstance(value, Mapping) or set(value) != set(CONSTANT_ORDER):
        raise ValueError(f"{label} 必须完整覆盖七常量")
    parsed: dict[str, float] = {}
    for axis in CONSTANT_ORDER:
        try:
            parsed[axis] = float(value[axis])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}.{axis} 必须是数值") from exc
        if not math.isfinite(parsed[axis]):
            raise ValueError(f"{label}.{axis} 必须是有限数值")
    return parsed


def validate_preference_weights(value: Mapping[str, object]) -> dict[str, float]:
    """Validate and canonicalize this package's five-axis B0 vector."""
    if not isinstance(value, Mapping) or set(value) != set(PREFERENCE_ORDER):
        raise ValueError("B0 必须完整覆盖五维偏好")
    parsed: dict[str, float] = {}
    for axis in PREFERENCE_ORDER:
        try:
            parsed[axis] = float(value[axis])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"B0.{axis} 必须是数值") from exc
        if not math.isfinite(parsed[axis]) or not 0.0 <= parsed[axis] <= 1.0:
            raise ValueError(f"B0.{axis} 必须是 0 到 1 之间的有限数值")
    if abs(sum(parsed.values()) - 1.0) > 1e-6:
        raise ValueError("B0 五维偏好必须归一化为 1")
    return parsed


def neutral_preference_weights() -> dict[str, float]:
    """Return the package's explicit neutral/unspecified B0 value."""
    weight = 1.0 / len(PREFERENCE_ORDER)
    return {axis: weight for axis in PREFERENCE_ORDER}


def _b0_receipt(base: Mapping[str, float], *, b0: Mapping[str, object] | None,
                enabled: bool) -> dict[str, object]:
    if not enabled:
        return {"enabled": False, "mapping_version": None, "strength": 0.0,
                "target_constants": None,
                "delta": {axis: 0.0 for axis in CONSTANT_ORDER}}
    preference = validate_preference_weights(b0 or {})
    certainty = min(
        1.0,
        sum(abs(preference[axis] - .2) for axis in PREFERENCE_ORDER) / 1.6,
    )
    target = {
        constant: sum(
            preference[axis] * B0_TARGETS[axis][constant]
            for axis in PREFERENCE_ORDER
        )
        for constant in CONSTANT_ORDER
    }
    weight = B0_MAX_BLEND * certainty
    delta = {
        constant: weight * (target[constant] - float(base[constant]))
        for constant in CONSTANT_ORDER
    }
    return {"enabled": True, "mapping_version": B0_MAPPING_VERSION,
            "strength": weight, "certainty": certainty,
            "target_constants": target, "delta": delta}


def resolve_effective_constants(c_base: Mapping[str, object], *, b0_version: str,
                                b0: Mapping[str, object] | None = None,
                                b0_enabled: bool = False,
                                correction: Mapping[str, object] | None = None) -> dict[str, object]:
    """Resolve this package's effective constants from versioned parameters."""
    base = _constant_vector(c_base, label="C_base")
    b0_ref = str(b0_version or "").strip()
    if not b0_ref:
        raise ValueError("C_effective 必须绑定不可变 B0 版本")

    b0_part = _b0_receipt(base, b0=b0, enabled=bool(b0_enabled))
    correction_version: str | None = None
    review_delta = {axis: 0.0 for axis in CONSTANT_ORDER}
    if correction is not None:
        if not isinstance(correction, Mapping):
            raise ValueError("常量校正必须是版本化映射")
        if str(correction.get("status") or "") != "approved":
            raise ValueError("常量校正必须显式为 approved，shadow/candidate 不可参与")
        correction_version = str(correction.get("version") or "").strip()
        if not correction_version:
            raise ValueError("approved 常量校正必须带版本")
        review_delta = _constant_vector(
            correction.get("delta_constants"), label="approved_delta",
        )

    total_delta = {
        axis: b0_part["delta"][axis] + review_delta[axis]
        for axis in CONSTANT_ORDER
    }
    effective = {axis: base[axis] + total_delta[axis] for axis in CONSTANT_ORDER}
    out_of_range = {
        axis: value for axis, value in effective.items()
        if value < 0.0 or value > 1.0
    }
    if out_of_range:
        raise ValueError(
            "C_effective 越界；审核校正不得依赖隐式 clamp: "
            + ", ".join(sorted(out_of_range))
        )
    return {
        "version": VERSION,
        "effective_constants_version": (
            f"{VERSION}:{b0_ref}:{b0_part['mapping_version'] or 'disabled'}:"
            f"{correction_version or 'base'}"
        ),
        "b0_version": b0_ref,
        "b0_enabled": b0_part["enabled"],
        "b0_mapping_version": b0_part["mapping_version"],
        "b0_strength": b0_part["strength"],
        "b0_target_constants": b0_part["target_constants"],
        "b0_delta": b0_part["delta"],
        "correction_version": correction_version,
        "base_constants": base,
        "approved_delta": review_delta,
        "total_delta": total_delta,
        "effective_constants": effective,
        "derived_only": True,
    }
