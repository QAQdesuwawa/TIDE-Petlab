"""Model-independent transaction host for one already committed event.

Temporal progress is deliberately outside this transaction.  The caller must
first supply a v3 session whose current position has already been accepted by
the Kernel.  This host then executes the model-owned event stages at that exact
position, obtains the sole Kernel settlement acceptance, and returns a new
profile value without writing storage or publishing Runner state.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import ModelInvocationContext, ModelStageExecution, model_data_fingerprint
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimeStateCommitAcceptance,
    RuntimeStateCommitAuthority,
    RuntimeProgressAuthority,
)
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_session_binding import (
    commit_runtime_session_fact_settlement,
    commit_runtime_session_settlement,
    restore_runtime_progress_transactions,
    restore_runtime_session_model_snapshot,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession
from ops.runtime_progress_transaction import RuntimePreparedProgress
from ops.runtime_stage_verification import verify_runtime_stage_execution


_EVENT_CONTRIBUTION_RESERVED_FIELDS = frozenset({
    "parameters", "state", "measurement", "progress_contribution",
})


@dataclass(frozen=True)
class RuntimeEventTransaction:
    """Pure result of one fully accepted event transaction."""

    session: BootstrappedRuntimeSession
    context: ModelInvocationContext | ModelInvocationContextV2
    measurement_execution: ModelStageExecution
    event_contribution_execution: ModelStageExecution
    settlement_execution: ModelStageExecution
    acceptance: RuntimeStateCommitAcceptance
    receipt: Mapping[str, Any]


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must identify a committed event fact")
    return normalized


def _stage_product(
    execution: ModelStageExecution, *, field_name: str,
) -> Any:
    if field_name not in execution.payload:
        raise RuntimeKernelContractError(
            f"model stage output must contain {field_name}")
    return execution.payload[field_name]


def _transaction_receipt(
    *,
    context: ModelInvocationContext | ModelInvocationContextV2,
    measurement_payload: Mapping[str, Any],
    event_contribution_inputs: Mapping[str, Any],
    measurement_execution: ModelStageExecution,
    event_contribution_execution: ModelStageExecution,
    settlement_execution: ModelStageExecution,
    acceptance: RuntimeStateCommitAcceptance,
    progress_preparation: RuntimePreparedProgress | None,
) -> dict[str, Any]:
    identity = context_fact_identity(context)
    material = {
        "version": "runtime-event-transaction-v3",
        "session_id": context.session_id,
        "scope_ref": identity.scope_ref,
        "subject_ref": identity.subject_ref,
        "event_ref": identity.event_ref,
        "action_ref": identity.action_ref,
        "temporal_position_fingerprint": (
            context.temporal_position.fingerprint
            if context.temporal_position is not None else None
        ),
        "runtime_ordinal": context.runtime_ordinal,
        "measurement_input_fingerprint": model_data_fingerprint(
            measurement_payload),
        "event_contribution_extension_fingerprint": model_data_fingerprint(
            event_contribution_inputs),
        "measurement_stage_receipt_fingerprint": model_data_fingerprint(
            measurement_execution.receipt.to_record()),
        "event_contribution_stage_receipt_fingerprint": model_data_fingerprint(
            event_contribution_execution.receipt.to_record()),
        "settlement_stage_receipt_fingerprint": model_data_fingerprint(
            settlement_execution.receipt.to_record()),
        "acceptance_fingerprint": acceptance.fingerprint,
        "progress_preparation_fingerprint": (
            None if progress_preparation is None
            else model_data_fingerprint(progress_preparation.receipt)
        ),
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


def execute_committed_event_transaction(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    scope_ref: str,
    subject_ref: str,
    event_ref: str,
    action_ref: str | None,
    measurement_payload: Mapping[str, Any],
    event_contribution_inputs: Mapping[str, Any] | None = None,
    context_metadata: Mapping[str, Any] | None = None,
    progress_preparation: RuntimePreparedProgress | None = None,
) -> RuntimeEventTransaction:
    """Settle one committed event at the session's accepted position.

    ``measurement_payload`` is package-owned input.  The host neither reads raw
    text nor creates semantic measurements from product state.
    """
    if not isinstance(session, BootstrappedRuntimeSession):
        raise RuntimeKernelContractError(
            "event transaction requires a bootstrapped runtime session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "event transaction requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "event transaction requires a registry-bound temporal runtime")
    if not isinstance(measurement_payload, Mapping):
        raise RuntimeKernelContractError(
            "measurement_payload must be a mapping")
    if (
        event_contribution_inputs is not None
        and not isinstance(event_contribution_inputs, Mapping)
    ):
        raise RuntimeKernelContractError(
            "event_contribution_inputs must be a mapping")
    contribution_extensions = dict(event_contribution_inputs or {})
    reserved = set(contribution_extensions).intersection(
        _EVENT_CONTRIBUTION_RESERVED_FIELDS)
    if reserved:
        raise ValueError(
            "event contribution inputs contain reserved transaction fields: "
            + ", ".join(sorted(reserved)))
    if context_metadata is not None and not isinstance(context_metadata, Mapping):
        raise RuntimeKernelContractError("context_metadata must be a mapping")

    scope = _identifier(scope_ref, "scope_ref")
    subject = _identifier(subject_ref, "subject_ref")
    event = _identifier(event_ref, "event_ref")
    session.session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )
    authority = RuntimeStateCommitAuthority(
        session_binding=session.session_binding)
    restored_position, restored_parameters, restored_state = (
        restore_runtime_session_model_snapshot(
            session.profile,
            session_binding=session.session_binding,
            authority=authority,
        )
    )
    if (
        restored_position != session.position
        or restored_parameters != session.parameters
        or restored_state != session.state
    ):
        raise RuntimeKernelContractError(
            "event transaction session products do not match persisted v3 snapshot")

    progress_authority = RuntimeProgressAuthority(
        session_binding=session.session_binding,
        temporal_runtime=temporal_runtime,
    )
    restore_runtime_progress_transactions(
        session.profile,
        materialize=False,
        session_binding=session.session_binding,
        progress_authority=progress_authority,
        state_authority=authority,
    )
    settlement_position = (
        session.position
        if progress_preparation is None
        else progress_preparation.preparation.state
    )
    if progress_preparation is not None:
        progress_preparation.preparation.verify_integrity(
            session_binding=session.session_binding,
            previous_state=session.position,
        )
    context = progress_authority.invocation_context_v2(
        settlement_position,
        scope_ref=scope,
        subject_ref=subject,
        event_ref=event,
        action_ref=action_ref,
        metadata={} if context_metadata is None else context_metadata,
    )

    measurement_input = dict(measurement_payload)
    measurement_execution = model_runtime.invoke(
        "measure_event", payload=measurement_input, context=context)
    verify_runtime_stage_execution(
        measurement_execution,
        stage="measure_event",
        runtime=model_runtime,
        context=context,
        invocation_payload=measurement_input,
    )
    measurement = _stage_product(
        measurement_execution, field_name="measurement")

    contribution_input = {
        "parameters": session.parameters.parameters,
        "state": session.state.state,
        "measurement": measurement,
    }
    contribution_input.update(contribution_extensions)
    if progress_preparation is not None:
        contribution_input["progress_contribution"] = (
            progress_preparation.contribution)
    event_contribution_execution = model_runtime.invoke(
        "derive_event_contribution",
        payload=contribution_input,
        context=context,
    )
    verify_runtime_stage_execution(
        event_contribution_execution,
        stage="derive_event_contribution",
        runtime=model_runtime,
        context=context,
        invocation_payload=contribution_input,
    )
    event_contribution = _stage_product(
        event_contribution_execution, field_name="event_contribution")

    settlement_input = {
        "state": session.state.state,
        "progress_contribution": (
            None if progress_preparation is None
            else progress_preparation.contribution
        ),
        "event_contribution": event_contribution,
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
    acceptance = authority.accept_settlement(
        current_state=session.state,
        current_position=settlement_position,
        invocation_context=context,
        invocation_payload=settlement_input,
        execution=settlement_execution,
    )
    if progress_preparation is None:
        profile = commit_runtime_session_settlement(
            session.profile,
            session_binding=session.session_binding,
            authority=authority,
            acceptance=acceptance,
        )
    else:
        profile, _progress_receipt = commit_runtime_session_fact_settlement(
            session.profile,
            session_binding=session.session_binding,
            progress_authority=progress_authority,
            state_authority=authority,
            progress_preparation=progress_preparation.preparation,
            settlement_acceptance=acceptance,
            progress_stage_receipt_fingerprint=model_data_fingerprint(
                progress_preparation.progress_execution.receipt.to_record()),
        )
    receipt = _transaction_receipt(
        context=context,
        measurement_payload=measurement_input,
        event_contribution_inputs=contribution_extensions,
        measurement_execution=measurement_execution,
        event_contribution_execution=event_contribution_execution,
        settlement_execution=settlement_execution,
        acceptance=acceptance,
        progress_preparation=progress_preparation,
    )
    next_session = BootstrappedRuntimeSession(
        profile=profile,
        session_binding=session.session_binding,
        position=acceptance.position,
        parameters=session.parameters,
        state=acceptance.state,
        receipt=receipt,
    )
    return RuntimeEventTransaction(
        session=next_session,
        context=context,
        measurement_execution=measurement_execution,
        event_contribution_execution=event_contribution_execution,
        settlement_execution=settlement_execution,
        acceptance=acceptance,
        receipt=receipt,
    )


__all__ = (
    "RuntimeEventTransaction",
    "execute_committed_event_transaction",
)
