"""Compatibility exports for the six/seven semantic measurement package.

New model code must import the package implementation directly.  This module
keeps legacy product callers stable while C-stage ownership migration proceeds;
it contains no second normalization implementation.
"""
from model_packages.cx_six_seven_v1.semantic_projection import (
    BEHAVIOR_PROTOTYPES,
    CallableSemanticProjector,
    SemanticProjection,
    VARIABLE_KEYS,
    blend_behavior_weights,
    coerce_projection,
)


__all__ = (
    "BEHAVIOR_PROTOTYPES",
    "CallableSemanticProjector",
    "SemanticProjection",
    "VARIABLE_KEYS",
    "blend_behavior_weights",
    "coerce_projection",
)
