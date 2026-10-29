"""Package-owned creation schema for the six-state/seven-constant model.

This module is pure model math.  It does not read presets, profiles, sessions,
Store, clocks, providers, UI, or product lifecycle state.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

from .preference_parameters import B0_TARGETS, CONSTANT_ORDER


ROOT_ORDER = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")
INITIAL_ROOT_STATE = {
    "E_env": 0.5,
    "L_self": 0.0,
    "L_opponent": 0.0,
    "P_need": 0.5,
    "V_attr": 0.0,
    "V_rep": 0.5,
}
WEIGHT_TOLERANCE = 0.01


def generate_base_constants(weights: Mapping[str, object]) -> dict[str, float]:
    """Mix any explicit sparse set of known prototypes into seven constants."""
    total_weight = sum(weights.values())
    if abs(total_weight - 1.0) > WEIGHT_TOLERANCE:
        raise ValueError("模型权重总和必须等于 1.0！")

    constants = {axis: 0.0 for axis in CONSTANT_ORDER}
    for prototype, weight in weights.items():
        if prototype not in B0_TARGETS:
            raise KeyError(f"未知的心理模型: {prototype}")
        for axis in CONSTANT_ORDER:
            constants[axis] += B0_TARGETS[prototype][axis] * weight
    return {axis: round(value, 3) for axis, value in constants.items()}


def normalize_initial_state(initial_vars=None) -> dict[str, float]:
    """Validate explicit ``X0`` while preserving the frozen cold start."""
    base = dict(INITIAL_ROOT_STATE)
    if initial_vars is None:
        return base
    if not isinstance(initial_vars, dict):
        raise ValueError("initial_state 必须是六轴映射")
    supplied = initial_vars.get("vars", initial_vars)
    if not isinstance(supplied, dict):
        raise ValueError("initial_state.vars 必须是六轴映射")
    unknown = set(supplied) - set(ROOT_ORDER)
    if unknown:
        raise ValueError("initial_state 含未知轴：" + ", ".join(sorted(unknown)))
    for axis, value in supplied.items():
        try:
            numeric = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"initial_state.{axis} 必须是数值") from exc
        if not math.isfinite(numeric):
            raise ValueError(f"initial_state.{axis} 必须是有限数值")
        base[axis] = min(1.0, max(0.0, numeric))
    return base


__all__ = (
    "INITIAL_ROOT_STATE",
    "ROOT_ORDER",
    "WEIGHT_TOLERANCE",
    "generate_base_constants",
    "normalize_initial_state",
)
