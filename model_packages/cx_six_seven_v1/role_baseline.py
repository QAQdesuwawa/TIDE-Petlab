"""CX67 seven-constant role baseline projection.

This is reference-model mathematics.  It owns no runtime state, product
configuration, clock, memory selection, expression assembly, or send policy.
"""
from __future__ import annotations

import math
from collections.abc import Mapping


def calculate_baseline(constants: Mapping[str, object]) -> dict[str, float]:
    """Project the seven role constants onto the normalized three-drive basis."""
    w_agg = float(constants["a1"]) + float(constants["a2"])
    w_def = (
        float(constants["b1"])
        + float(constants["b2"])
        + float(constants["b3"])
    )
    w_cha = float(constants["g1"]) + float(constants["g2"])

    magnitude = math.sqrt(w_agg ** 2 + w_def ** 2 + w_cha ** 2)
    if magnitude == 0:
        return {"E_aggro": 0.0, "E_defend": 0.0, "E_chaos": 0.0}

    return {
        "E_aggro": round(w_agg / magnitude, 3),
        "E_defend": round(w_def / magnitude, 3),
        "E_chaos": round(w_cha / magnitude, 3),
    }
