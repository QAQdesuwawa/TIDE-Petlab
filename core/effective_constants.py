"""Deprecated compatibility exports for the six/seven reference package.

New framework and package code must not import this module.  It remains only
while legacy product paths are migrated to versioned model-package bindings.
"""
from model_packages.cx_six_seven_v1.preference_parameters import (
    B0_MAPPING_VERSION,
    B0_MAX_BLEND,
    B0_TARGETS,
    CONSTANT_ORDER,
    PREFERENCE_ORDER,
    VERSION,
    neutral_preference_weights,
    resolve_effective_constants,
    validate_preference_weights,
)

__all__ = [
    "B0_MAPPING_VERSION",
    "B0_MAX_BLEND",
    "B0_TARGETS",
    "CONSTANT_ORDER",
    "PREFERENCE_ORDER",
    "VERSION",
    "neutral_preference_weights",
    "resolve_effective_constants",
    "validate_preference_weights",
]
