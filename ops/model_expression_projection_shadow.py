"""Project expression resources only from a Kernel-accepted model settlement.

This is a read-only F3/F5 production seam.  It executes two model-owned pure
stages after the Kernel has accepted one settlement, but owns no Store,
Runner, Writer, provider, clock progression, action qualification, or commit.
"""
from __future__ import annotations
from ops.settlement_validation import verify_settlement

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_api.identity import (
    context_fact_identity,
    same_model_fact_identity,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from product_adapters.cx_six_seven_v1.expression_resources import (
    ExpressionResourceBatch,
    read_expression_resource_batch,
)
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import (
    RuntimeModelParameters,
    RuntimeModelState,
    RuntimePositionState,
    RuntimeSessionBinding,
    RuntimeStateCommitAcceptance,
)


EXPRESSION_PROJECTION_SHADOW_VERSION = "expression-projection-shadow-v1"


@dataclass(frozen=True)
class ExpressionProjectionShadow:
    context: ModelInvocationContext | ModelInvocationContextV2
    shared_projection_execution: ModelStageExecution
    semantic_resource_execution: ModelStageExecution
    batch: ExpressionResourceBatch
    receipt: Mapping[str, Any]


def _same_context(
    receipt_execution: ModelStageExecution,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> bool:
    return same_model_fact_identity(context, receipt_execution.receipt)


def _verify_runtime_stage(
    runtime: BoundInteractionModel,
    session_binding: RuntimeSessionBinding,
    execution: ModelStageExecution,
    *,
    stage: str,
) -> None:
    if not isinstance(execution, ModelStageExecution):
        raise ValueError(f"{stage} evidence must be ModelStageExecution")
    receipt = execution.receipt
    binding = session_binding.model_binding
    if (
        receipt.stage != stage
        or receipt.session_id != session_binding.session_id
        or receipt.package_id != binding.package_id
        or receipt.package_version != binding.package_version
        or receipt.schema_fingerprint != binding.schema_fingerprint
        or receipt.parameter_fingerprint != binding.parameter_fingerprint
        or receipt.operator_version != binding.operator_versions.get(stage)
        or receipt.output_fingerprint
        != model_data_fingerprint(execution.payload)
    ):
        raise ValueError(f"{stage} evidence does not match runtime binding")
    if runtime.session_id != session_binding.session_id:
        raise ValueError("model runtime session does not match runtime binding")
    if runtime.binding != binding:
        raise ValueError("model runtime package does not match runtime binding")


def _context_identity(
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> dict[str, Any]:
    identity = context_fact_identity(context)
    position = identity.temporal_position
    return {
        "session_id": identity.session_id,
        "scope_ref": identity.scope_ref,
        "subject_ref": identity.subject_ref,
        "event_ref": identity.event_ref,
        "action_ref": identity.action_ref,
        "temporal_position_fingerprint": (
            position.fingerprint if position is not None else None),
        "runtime_ordinal": identity.runtime_ordinal,
    }


def produce_expression_projection_shadow(
    *,
    runtime: BoundInteractionModel,
    session_binding: RuntimeSessionBinding,
    previous_state: RuntimeModelState,
    previous_position: RuntimePositionState,
    model_parameters: RuntimeModelParameters,
    settlement_context: ModelInvocationContext | ModelInvocationContextV2,
    settlement_execution: ModelStageExecution,
    settlement_acceptance: RuntimeStateCommitAcceptance,
    committed_remainders: Mapping[str, object],
    expression_scope_ref: str | None = None,
) -> ExpressionProjectionShadow:
    """Run ``P`` and expression mapping after one accepted ``Π`` result.

    The accepted settlement remains the only state source.  Parameters must
    come from the immutable product of one signed ``initialize_parameters``
    execution.  The function cannot read legacy root state or derive either
    input from prompt material.
    """
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("expression projection requires a bound model runtime")
    if not isinstance(session_binding, RuntimeSessionBinding):
        raise ValueError("expression projection requires RuntimeSessionBinding")
    session_binding.verify_integrity()
    if not isinstance(
        settlement_context, (ModelInvocationContext, ModelInvocationContextV2),
    ):
        raise ValueError("settlement context must be a model invocation context")
    if not isinstance(settlement_acceptance, RuntimeStateCommitAcceptance):
        raise ValueError("settlement acceptance is required")
    try:
        verify_settlement(settlement_acceptance,
            session_binding=session_binding,
            previous_state=previous_state,
            previous_position=previous_position,
        )
    except ValueError as exc:
        raise ValueError(f"settlement acceptance is invalid: {exc}") from exc

    _verify_runtime_stage(
        runtime, session_binding, settlement_execution, stage="settle_state")
    if not _same_context(settlement_execution, settlement_context):
        raise ValueError("settlement context differs from execution receipt")
    settlement_receipt_fingerprint = model_data_fingerprint(
        settlement_execution.receipt.to_record())
    if (
        settlement_acceptance.stage_receipt_fingerprint
        != settlement_receipt_fingerprint
    ):
        raise ValueError("settlement acceptance references another execution")
    settlement = settlement_execution.payload.get("settlement")
    if not isinstance(settlement, Mapping):
        raise ValueError("settlement execution lacks settlement output")
    if (
        "state_candidate" not in settlement
        or model_data_fingerprint(settlement["state_candidate"])
        != model_data_fingerprint(settlement_acceptance.state.state)
    ):
        raise ValueError("settlement acceptance state differs from execution")

    if not isinstance(model_parameters, RuntimeModelParameters):
        raise ValueError("expression projection requires RuntimeModelParameters")
    try:
        model_parameters.verify_integrity(session_binding)
    except ValueError as exc:
        raise ValueError(f"model parameters are invalid: {exc}") from exc
    parameters = model_parameters.parameters
    if not isinstance(committed_remainders, Mapping):
        raise ValueError("committed_remainders must be a mapping")
    frozen_remainders = freeze_model_payload(committed_remainders)

    accepted_position = settlement_acceptance.position
    if isinstance(settlement_context, ModelInvocationContextV2):
        context = ModelInvocationContextV2(
            session_id=session_binding.session_id,
            scope_ref=settlement_context.scope_ref,
            subject_ref=settlement_context.subject_ref,
            event_ref=settlement_context.event_ref,
            action_ref=settlement_context.action_ref,
            temporal_position=accepted_position.position,
            runtime_ordinal=accepted_position.runtime_ordinal,
            metadata=settlement_context.metadata,
        )
    else:
        context = ModelInvocationContext(
            session_id=session_binding.session_id,
            conversation_id=settlement_context.conversation_id,
            participant_id=settlement_context.participant_id,
            event_id=settlement_context.event_id,
            action_id=settlement_context.action_id,
            temporal_position=accepted_position.position,
            runtime_ordinal=accepted_position.runtime_ordinal,
            metadata=settlement_context.metadata,
        )
    # World progress keeps its original owner-local settlement identity.
    # A product may separately name the destination of this read-only projection.
    if expression_scope_ref is not None:
        if (not isinstance(expression_scope_ref, str) or not expression_scope_ref.strip()
                or not isinstance(context, ModelInvocationContextV2)
                or settlement_context.scope_ref is not None
                or settlement_context.action_ref is not None
                or settlement_context.metadata.get("committed_event_kind") != "tick"):
            raise ValueError("expression scope requires an owner-local tick settlement")
        context = replace(context, scope_ref=expression_scope_ref)
    shared = runtime.invoke(
        "project_shared_semantics",
        payload={
            "parameters": parameters,
            "settlement": settlement,
        },
        context=context,
    )
    shared_projection = shared.payload.get("shared_projection")
    if not isinstance(shared_projection, Mapping):
        raise ValueError(
            "project_shared_semantics execution lacks shared_projection")
    resources = runtime.invoke(
        "project_semantic_resources",
        payload={
            "shared_projection": shared_projection,
            "source_shared_projection_output_fingerprint": (
                shared.receipt.output_fingerprint),
            "committed_remainders": frozen_remainders,
        },
        context=context,
    )
    batch = read_expression_resource_batch(
        resources,
        expected_context=context,
        shared_projection_execution=shared,
    )
    receipt = freeze_model_payload({
        "version": (EXPRESSION_PROJECTION_SHADOW_VERSION if expression_scope_ref is None
                    else "expression-projection-shadow-v2-scoped"),
        **({} if expression_scope_ref is None else {
            "settlement_context_identity": _context_identity(settlement_context),
            "expression_scope_ref": expression_scope_ref}),
        "mode": "read_only_shadow",
        "context_identity": _context_identity(context),
        "parameters_stage_receipt_fingerprint": (
            model_parameters.initialization_receipt_fingerprint),
        "model_parameters_fingerprint": model_parameters.fingerprint,
        "settlement_stage_receipt_fingerprint": (
            settlement_receipt_fingerprint),
        "settlement_acceptance_fingerprint": settlement_acceptance.fingerprint,
        "committed_remainders_fingerprint": model_data_fingerprint(
            frozen_remainders),
        "shared_projection_stage_receipt": shared.receipt.to_record(),
        "semantic_resource_stage_receipt": resources.receipt.to_record(),
        "selected_resource_ids": batch.selected_resource_ids,
        "authority": {
            "state_write": False,
            "history_write": False,
            "writer_attempt": False,
            "provider_call": False,
            "commit": False,
            "send": False,
        },
    })
    return ExpressionProjectionShadow(
        context=context,
        shared_projection_execution=shared,
        semantic_resource_execution=resources,
        batch=batch,
        receipt=receipt,
    )


__all__ = (
    "EXPRESSION_PROJECTION_SHADOW_VERSION",
    "ExpressionProjectionShadow",
    "produce_expression_projection_shadow",
)
