"""Reference-package interpretation of one bound temporal position.

The framework keeps coordinates opaque.  This package's current Memory math
uses a non-negative integer coordinate as its millisecond parameter; decoding
that coordinate belongs here and never falls back to ``context.at_ms``.
"""
from __future__ import annotations

from framework.model_api import ModelInvocationContext


def reference_position_ms(context: ModelInvocationContext) -> int:
    if not isinstance(context, ModelInvocationContext):
        raise ValueError("reference time requires ModelInvocationContext")
    position = context.temporal_position
    if position is None:
        raise ValueError("reference time requires a temporal position")
    position.verify_integrity()
    coordinate = position.coordinate
    if isinstance(coordinate, bool) or not isinstance(coordinate, int) or coordinate < 0:
        raise ValueError(
            "reference Memory time requires a non-negative integer coordinate")
    return coordinate


__all__ = ("reference_position_ms",)
