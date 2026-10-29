"""Dimension-agnostic, read-only comparison of one identified model stage.

This is an F2 comparison seam, not a Runner hook or a state transition.  The
caller owns proof that the supplied baseline output and receipt reference came
from a committed event; this module requires their explicit same-event context
and never reads Store or infers an identity from time or paths.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelInvocationContext, ModelStageReceipt, freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)
from framework.model_runtime import BoundInteractionModel


@dataclass(frozen=True)
class ShadowStageComparison:
    baseline_receipt_ref: str
    baseline_output_fingerprint: str
    shadow_output_fingerprint: str
    matched: bool
    shadow_stage_receipt: ModelStageReceipt | ModelStageReceiptV2

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "model_shadow_stage_comparison",
            "baseline_receipt_ref": self.baseline_receipt_ref,
            "baseline_output_fingerprint": self.baseline_output_fingerprint,
            "shadow_output_fingerprint": self.shadow_output_fingerprint,
            "matched": self.matched,
            "shadow_stage_receipt": self.shadow_stage_receipt.to_record(),
        }


def compare_shadow_stage(
    runtime: BoundInteractionModel,
    stage: str,
    *,
    payload: Mapping[str, Any],
    context: ModelInvocationContext | ModelInvocationContextV2,
    baseline_output: Mapping[str, Any],
    baseline_context: ModelInvocationContext | ModelInvocationContextV2,
    baseline_receipt_ref: str,
) -> ShadowStageComparison:
    """Compare one stage; return evidence only, never a replacement output."""
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("shadow comparison requires a bound model runtime")
    if stage in {
        "initialize_constants", "initialize_parameters", "initialize_state",
        "resolve_constants",
    }:
        raise ValueError("creation stage cannot use event shadow comparison")
    context_types = (ModelInvocationContext, ModelInvocationContextV2)
    if not isinstance(context, context_types) or not isinstance(
        baseline_context, context_types):
        raise ValueError("shadow comparison requires explicit same-event contexts")
    identity = context_fact_identity(context)
    if identity.event_ref is None and identity.action_ref is None:
        raise ValueError("shadow comparison requires a committed event or action ID")
    if context.to_record() != baseline_context.to_record():
        raise ValueError("shadow comparison contexts are not the same-event identity")
    if not isinstance(baseline_receipt_ref, str) or not baseline_receipt_ref.strip():
        raise ValueError("shadow comparison requires a baseline receipt reference")

    baseline_fingerprint = model_data_fingerprint(
        freeze_model_payload(baseline_output))
    execution = runtime.invoke(stage, payload=payload, context=context)
    shadow_fingerprint = execution.receipt.output_fingerprint
    return ShadowStageComparison(
        baseline_receipt_ref=baseline_receipt_ref,
        baseline_output_fingerprint=baseline_fingerprint,
        shadow_output_fingerprint=shadow_fingerprint,
        matched=baseline_fingerprint == shadow_fingerprint,
        shadow_stage_receipt=execution.receipt,
    )
