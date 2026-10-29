"""Compatibility exports for CX67 expression-emission math.

The authoritative implementation belongs to the versioned reference package.
Legacy callers keep importing this path until the C-stage dependency cleanup.
"""
from model_packages.cx_six_seven_v1.expression_emission import (
    DEFAULT_EMISSION_UNIT,
    EMISSION_COMMIT_VERSION,
    EMISSION_PLAN_VERSION,
    EMISSION_QUANTIZATION_VERSION,
    MAPPING_DESCRIPTOR_VERSION,
    commit_expression_emission_plan,
    compile_expression_emission_plan,
    normalize_mapping_offset,
)


__all__ = (
    "DEFAULT_EMISSION_UNIT",
    "EMISSION_COMMIT_VERSION",
    "EMISSION_PLAN_VERSION",
    "EMISSION_QUANTIZATION_VERSION",
    "MAPPING_DESCRIPTOR_VERSION",
    "commit_expression_emission_plan",
    "compile_expression_emission_plan",
    "normalize_mapping_offset",
)
