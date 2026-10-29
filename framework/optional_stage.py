"""Auditable execution gate for optional Interaction Model stages."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from framework.model_api import (
    OPTIONAL_MODEL_CAPABILITIES_V2,
    ModelInvocationContext,
    ModelStageExecution,
    freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_runtime import BoundInteractionModel
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.runtime_kernel import RuntimeSessionBinding


OPTIONAL_STAGE_DECISION_VERSION = "optional-model-stage-decision-v1"


@dataclass(frozen=True)
class OptionalStageOutcome:
    stage: str
    status: str
    execution: ModelStageExecution | None
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.status not in {"executed", "disabled_by_variant"}:
            raise ValueError("optional stage outcome status is invalid")
        if (self.status == "executed") != isinstance(
                self.execution, ModelStageExecution):
            raise ValueError("optional stage outcome execution disagrees with status")
        object.__setattr__(self, "receipt", MappingProxyType(dict(self.receipt)))


def _verify_context(
    *,
    context: ModelInvocationContext | ModelInvocationContextV2,
    session_binding: RuntimeSessionBinding,
) -> None:
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("optional stage context must be a model invocation context")
    if context.session_id != session_binding.session_id:
        raise ValueError("optional stage context session differs from binding")
    if (
        context.temporal_position is None
        or context.temporal_position.binding != session_binding.temporal_binding
    ):
        raise ValueError("optional stage context position differs from binding")


def invoke_optional_model_stage(
    *, runtime: BoundInteractionModel,
    session_binding: RuntimeSessionBinding,
    stage: str,
    payload: Mapping[str, Any],
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> OptionalStageOutcome:
    """Invoke one declared optional stage or publish a zero-call fact."""
    normalized = str(stage or "").strip()
    if normalized not in OPTIONAL_MODEL_CAPABILITIES_V2:
        raise ValueError("stage is not an optional model capability")
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("optional stage requires BoundInteractionModel")
    if not isinstance(session_binding, RuntimeSessionBinding):
        raise ValueError("optional stage requires RuntimeSessionBinding")
    session_binding.verify_integrity()
    if (
        runtime.session_id != session_binding.session_id
        or runtime.binding != session_binding.model_binding
    ):
        raise ValueError("optional stage runtime differs from session binding")
    _verify_context(context=context, session_binding=session_binding)
    frozen_input = freeze_model_payload(payload)
    enabled = normalized in session_binding.model_binding.operator_versions
    execution = None
    if enabled:
        execution = runtime.invoke(
            normalized, payload=frozen_input, context=context)
    material = {
        "version": OPTIONAL_STAGE_DECISION_VERSION,
        "stage": normalized,
        "status": "executed" if enabled else "disabled_by_variant",
        "runtime_variant_fingerprint": (
            session_binding.runtime_variant_fingerprint),
        "interaction_model_family": (
            session_binding.model_binding.package_id),
        "interaction_model_version": (
            session_binding.model_binding.package_version),
        "operator_version": (
            session_binding.model_binding.operator_versions.get(normalized)),
        "input_fingerprint": model_data_fingerprint(frozen_input),
        "stage_receipt_fingerprint": (
            None if execution is None
            else model_data_fingerprint(execution.receipt.to_record())),
        "output_fingerprint": (
            None if execution is None
            else execution.receipt.output_fingerprint),
        "model_invoked": enabled,
        "fallback_used": False,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return OptionalStageOutcome(
        stage=normalized,
        status=material["status"],
        execution=execution,
        receipt=receipt,
    )


__all__ = (
    "OPTIONAL_STAGE_DECISION_VERSION",
    "OptionalStageOutcome",
    "invoke_optional_model_stage",
)
