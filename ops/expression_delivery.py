"""Compatibility import for the legacy six/seven expression delivery.

Formal v2 expression resources are assembled by the CX67 package Realizer adapter.
This module remains only so the frozen product Runner can keep its old import
until the post-C7 wiring phase.
"""

from ops.cx_six_seven_compat_expression_delivery import (  # noqa: F401
    ExpressionDelivery,
    compile_expression_delivery,
)

__all__ = ("ExpressionDelivery", "compile_expression_delivery")
