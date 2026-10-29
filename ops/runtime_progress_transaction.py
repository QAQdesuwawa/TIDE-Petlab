"""Atomic host transaction for one accepted monotonic progress fact."""
from __future__ import annotations
from ops.runtime_authorities import runtime_authorities
from ops.settlement_validation import accept_settlement

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    model_data_fingerprint,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimeProgressAcceptance,
    RuntimeProgressAuthority,
    RuntimeProgressPreparation,
    RuntimeStateCommitAcceptance,
    RuntimeStateCommitAuthority,
)
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_session_binding import (
    commit_runtime_session_progress_settlement,
    restore_runtime_progress_transactions,
    restore_runtime_session_model_snapshot,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession
from ops.runtime_stage_verification import verify_runtime_stage_execution


@dataclass(frozen=True)
class RuntimeProgressTransaction:
    """Pure result containing both Kernel acceptances and the next v3 value."""

    session: BootstrappedRuntimeSession
    context: ModelInvocationContext | ModelInvocationContextV2
    progress_acceptance: RuntimeProgressAcceptance
    progress_execution: ModelStageExecution
    settlement_execution: ModelStageExecution
    acceptance: RuntimeStateCommitAcceptance
    receipt: Mapping[str, Any]


@dataclass(frozen=True)
class RuntimePreparedProgress:
    """Verified progress contribution awaiting the fact's sole settlement."""

    context: ModelInvocationContext | ModelInvocationContextV2
    preparation: RuntimeProgressPreparation
    progress_execution: ModelStageExecution
    contribution: Any
    receipt: Mapping[str, Any]


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must identify a committed progress fact")
    return normalized


def prepare_runtime_progress_for_settlement(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    target_coordinate: object,
    scope_ref: str | None,
    subject_ref: str,
    event_ref: str,
    action_ref: str | None,
    context_metadata: Mapping[str, Any] | None = None,
) -> RuntimePreparedProgress:
    """Calculate verified progress without committing state or an ordinal."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise RuntimeKernelContractError(
            "progress preparation requires a bootstrapped runtime session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "progress preparation requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "progress preparation requires a registry-bound temporal runtime")
    if "advance_progress" not in model_runtime.manifest.capabilities:
        raise RuntimeKernelContractError(
            "bound model does not provide advance_progress")
    if context_metadata is not None and not isinstance(context_metadata, Mapping):
        raise RuntimeKernelContractError("context_metadata must be a mapping")

    scope = (
        None if scope_ref is None
        else _identifier(scope_ref, "scope_ref")
    )
    subject = _identifier(subject_ref, "subject_ref")
    event = _identifier(event_ref, "event_ref")
    session.session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )
    state_authority, progress_authority = runtime_authorities(
        session.session_binding,temporal_runtime)
    restored_position, restored_parameters, restored_state = (
        restore_runtime_session_model_snapshot(
            session.profile,
            session_binding=session.session_binding,
            authority=state_authority,
        )
    )
    if (
        restored_position != session.position
        or restored_parameters != session.parameters
        or restored_state != session.state
    ):
        raise RuntimeKernelContractError(
            "progress preparation products do not match persisted v3 snapshot")

    restore_runtime_progress_transactions(
        session.profile,
        materialize=False,
        session_binding=session.session_binding,
        progress_authority=progress_authority,
        state_authority=state_authority,
    )
    preparation = progress_authority.prepare_position(
        session.position, target_coordinate)
    context = progress_authority.invocation_context_v2(
        preparation.state,
        scope_ref=scope,
        subject_ref=subject,
        event_ref=event,
        action_ref=action_ref,
        metadata={} if context_metadata is None else context_metadata,
    )
    progress_input = {
        "parameters": session.parameters.parameters,
        "state": session.state.state,
        "progress_delta": preparation.progress.to_record(),
    }
    execution = model_runtime.invoke(
        "advance_progress", payload=progress_input, context=context)
    verify_runtime_stage_execution(
        execution,
        stage="advance_progress",
        runtime=model_runtime,
        context=context,
        invocation_payload=progress_input,
    )
    if "progress_contribution" not in execution.payload:
        raise RuntimeKernelContractError(
            "advance_progress output must contain progress_contribution")
    contribution = execution.payload["progress_contribution"]
    material = {
        "version": "runtime-prepared-progress-v1",
        "preparation_fingerprint": preparation.fingerprint,
        "progress_stage_receipt_fingerprint": model_data_fingerprint(
            execution.receipt.to_record()),
        "contribution_fingerprint": model_data_fingerprint(contribution),
    }
    return RuntimePreparedProgress(
        context=context,
        preparation=preparation,
        progress_execution=execution,
        contribution=contribution,
        receipt=dict(material, fingerprint=model_data_fingerprint(material)),
    )


def execute_runtime_progress_transaction(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    target_coordinate: object,
    scope_ref: str | None,
    subject_ref: str,
    event_ref: str,
    action_ref: str | None,
    context_metadata: Mapping[str, Any] | None = None,
) -> RuntimeProgressTransaction:
    """Advance one position and settle its model-owned progress contribution.

    The host never reads a clock.  ``target_coordinate`` is supplied by the
    caller and interpreted only by the bound TemporalDomain.
    """
    if not isinstance(session, BootstrappedRuntimeSession):
        raise RuntimeKernelContractError(
            "progress transaction requires a bootstrapped runtime session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "progress transaction requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "progress transaction requires a registry-bound temporal runtime")
    if "advance_progress" not in model_runtime.manifest.capabilities:
        raise RuntimeKernelContractError(
            "bound model does not provide advance_progress")
    if context_metadata is not None and not isinstance(context_metadata, Mapping):
        raise RuntimeKernelContractError("context_metadata must be a mapping")

    is_owner_local_tick = (
        scope_ref is None
        and action_ref is None
        and isinstance(context_metadata, Mapping)
        and context_metadata.get("committed_event_kind") == "tick"
    )
    if scope_ref is None and not is_owner_local_tick:
        raise RuntimeKernelContractError(
            "scope_ref may be absent only for an owner-local progress fact")
    scope = (
        None if scope_ref is None
        else _identifier(scope_ref, "scope_ref")
    )
    subject = _identifier(subject_ref, "subject_ref")
    event = _identifier(event_ref, "event_ref")
    session.session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )
    state_authority, progress_authority = runtime_authorities(
        session.session_binding,temporal_runtime)
    restored_position, restored_parameters, restored_state = (
        restore_runtime_session_model_snapshot(
            session.profile,
            session_binding=session.session_binding,
            authority=state_authority,
        )
    )
    if (
        restored_position != session.position
        or restored_parameters != session.parameters
        or restored_state != session.state
    ):
        raise RuntimeKernelContractError(
            "progress transaction products do not match persisted v3 snapshot")

    restore_runtime_progress_transactions(
        session.profile,
        materialize=False,
        session_binding=session.session_binding,
        progress_authority=progress_authority,
        state_authority=state_authority,
    )
    progress_acceptance = progress_authority.accept_position(
        session.position, target_coordinate)
    context = progress_authority.invocation_context_v2(
        progress_acceptance.state,
        scope_ref=scope,
        subject_ref=subject,
        event_ref=event,
        action_ref=action_ref,
        metadata={} if context_metadata is None else context_metadata,
    )

    progress_input = {
        "parameters": session.parameters.parameters,
        "state": session.state.state,
        "progress_delta": progress_acceptance.progress.to_record(),
    }
    progress_execution = model_runtime.invoke(
        "advance_progress", payload=progress_input, context=context)
    verify_runtime_stage_execution(
        progress_execution,
        stage="advance_progress",
        runtime=model_runtime,
        context=context,
        invocation_payload=progress_input,
    )
    if "progress_contribution" not in progress_execution.payload:
        raise RuntimeKernelContractError(
            "advance_progress output must contain progress_contribution")
    progress_contribution = progress_execution.payload["progress_contribution"]

    settlement_input = {
        "state": session.state.state,
        "progress_contribution": progress_contribution,
        "event_contribution": None,
    }
    settlement_execution = model_runtime.invoke(
        "settle_state", payload=settlement_input, context=context)
    verify_runtime_stage_execution(
        settlement_execution,
        stage="settle_state",
        runtime=model_runtime,
        context=context,
        invocation_payload=settlement_input,
    )
    acceptance = accept_settlement(state_authority,
        current_state=session.state,
        current_position=progress_acceptance.state,
        invocation_context=context,
        invocation_payload=settlement_input,
        execution=settlement_execution,
    )
    profile, receipt = commit_runtime_session_progress_settlement(
        session.profile,
        session_binding=session.session_binding,
        progress_authority=progress_authority,
        state_authority=state_authority,
        progress_acceptance=progress_acceptance,
        settlement_acceptance=acceptance,
        progress_stage_receipt_fingerprint=model_data_fingerprint(
            progress_execution.receipt.to_record()),
    )
    next_session = BootstrappedRuntimeSession(
        profile=profile,
        session_binding=session.session_binding,
        position=acceptance.position,
        parameters=session.parameters,
        state=acceptance.state,
        receipt=receipt,
    )
    return RuntimeProgressTransaction(
        session=next_session,
        context=context,
        progress_acceptance=progress_acceptance,
        progress_execution=progress_execution,
        settlement_execution=settlement_execution,
        acceptance=acceptance,
        receipt=receipt,
    )


__all__ = (
    "RuntimePreparedProgress",
    "RuntimeProgressTransaction",
    "execute_runtime_progress_transaction",
    "prepare_runtime_progress_for_settlement",
)
