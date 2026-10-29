"""Product bridge for the CX six/seven ranked expression-resource schema."""

from model_packages.cx_six_seven_v1.expression_resources import (
    ExpressionResource,
    ExpressionResourceBatch,
    read_expression_resource_batch,
)


__all__ = (
    "ExpressionResource",
    "ExpressionResourceBatch",
    "read_expression_resource_batch",
)
