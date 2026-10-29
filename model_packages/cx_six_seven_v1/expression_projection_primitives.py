"""Frozen six/seven vector primitives used by legacy expression projections.

The expression matrices predate the package-neutral runtime and use a column
order different from the newer scheduling geometry.  Keeping that order and
its reference point in the model package prevents a compatibility projection
from importing the old temporal implementation or silently permuting columns.
"""
from __future__ import annotations

from collections.abc import Mapping

from .preference_parameters import CONSTANT_ORDER


VERSION = "cx67-expression-projection-primitives-v1"
ROOT_ORDER = (
    "E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep",
)
ROOT_DEFAULTS_V1 = {
    "E_env": 0.5,
    "L_self": 0.0,
    "L_opponent": 0.0,
    "P_need": 0.5,
    "V_attr": 0.0,
    "V_rep": 0.5,
}
ROOT_REFERENCE_V1 = {
    "E_env": 0.5,
    "L_self": 0.0,
    "L_opponent": 0.0,
    "P_need": 0.5,
    "V_attr": 0.5,
    "V_rep": 0.5,
}


def _unit(value, default=0.0):
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def constant_vector(constants=None):
    values = constants if isinstance(constants, Mapping) else {}
    return tuple(_unit(values.get(axis, 0.0)) for axis in CONSTANT_ORDER)


def root_vector(variables=None):
    values = variables if isinstance(variables, Mapping) else {}
    return tuple(
        _unit(values.get(axis, ROOT_DEFAULTS_V1[axis]), ROOT_DEFAULTS_V1[axis])
        for axis in ROOT_ORDER
    )


def centered_root_vector(variables=None):
    values = root_vector(variables)
    return tuple(
        values[index] - ROOT_REFERENCE_V1[axis]
        for index, axis in enumerate(ROOT_ORDER)
    )


__all__ = (
    "VERSION",
    "CONSTANT_ORDER",
    "ROOT_ORDER",
    "ROOT_DEFAULTS_V1",
    "ROOT_REFERENCE_V1",
    "constant_vector",
    "root_vector",
    "centered_root_vector",
)
