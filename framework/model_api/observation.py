"""Read-only model advice for the next formal observation.

This product is emitted after an accepted interaction state.  It is not an
action assessment, an ObservationTarget, a timer, or permission to schedule,
commit, or send anything.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    ModelStageReceipt,
    freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_api.identity import same_model_fact_identity
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)


_PLAN_FIELDS = {
    "version",
    "source_shared_projection_output_fingerprint",
    "observation_advice",
    "observation_projection",
    "action_assessment_authority",
    "observation_schedule_authority",
}


def _same_context(
    receipt: ModelStageReceipt | ModelStageReceiptV2,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> bool:
    return same_model_fact_identity(context, receipt)


def _verified_execution(
    execution: ModelStageExecution,
    *,
    stage: str,
    expected_context: ModelInvocationContext | ModelInvocationContextV2,
) -> ModelStageReceipt | ModelStageReceiptV2:
    if not isinstance(execution, ModelStageExecution):
        raise ValueError(f"{stage} requires a model stage execution")
    receipt = execution.receipt
    if receipt.stage != stage:
        raise ValueError(f"observation planning requires {stage} stage")
    if not _same_context(receipt, expected_context):
        raise ValueError(f"{stage} receipt context identity is invalid")
    if receipt.output_fingerprint != model_data_fingerprint(execution.payload):
        raise ValueError(f"{stage} payload differs from stage receipt")
    return receipt


@dataclass(frozen=True)
class ObservationPlanAdvice:
    """Opaque model advice with no scheduling or action authority."""

    version: str
    observation_advice: Mapping[str, object]
    observation_projection: Mapping[str, object]
    stage_receipt: ModelStageReceipt | ModelStageReceiptV2
    shared_projection_receipt: ModelStageReceipt | ModelStageReceiptV2

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_advice",
            freeze_model_payload({"value": self.observation_advice})["value"],
        )
        object.__setattr__(
            self,
            "observation_projection",
            freeze_model_payload({"value": self.observation_projection})["value"],
        )


def read_observation_plan(
    execution: ModelStageExecution,
    *,
    expected_context: ModelInvocationContext | ModelInvocationContextV2,
    shared_projection_execution: ModelStageExecution,
) -> ObservationPlanAdvice:
    """Verify one post-event planning result without interpreting its advice."""
    if not isinstance(
        expected_context, (ModelInvocationContext, ModelInvocationContextV2),
    ):
        raise ValueError("expected_context must be a model invocation context")
    shared_receipt = _verified_execution(
        shared_projection_execution,
        stage="project_shared_semantics",
        expected_context=expected_context,
    )
    receipt = _verified_execution(
        execution,
        stage="plan_observation",
        expected_context=expected_context,
    )
    plan = execution.payload.get("observation_plan")
    if not isinstance(plan, Mapping) or set(plan) != _PLAN_FIELDS:
        raise ValueError("observation plan fields are invalid")
    version = str(plan.get("version") or "").strip()
    if not version:
        raise ValueError("observation plan version is invalid")
    if (
        plan.get("source_shared_projection_output_fingerprint")
        != shared_receipt.output_fingerprint
    ):
        raise ValueError("observation plan differs from shared projection")
    advice = plan.get("observation_advice")
    projection = plan.get("observation_projection")
    if not isinstance(advice, Mapping) or not isinstance(projection, Mapping):
        raise ValueError("observation plan advice and projection must be mappings")
    if plan.get("action_assessment_authority") is not False:
        raise ValueError("observation plan cannot carry action authority")
    if plan.get("observation_schedule_authority") is not False:
        raise ValueError("observation plan cannot carry schedule authority")
    return ObservationPlanAdvice(
        version=version,
        observation_advice=advice,
        observation_projection=projection,
        stage_receipt=receipt,
        shared_projection_receipt=shared_receipt,
    )


__all__ = ("ObservationPlanAdvice", "read_observation_plan")
