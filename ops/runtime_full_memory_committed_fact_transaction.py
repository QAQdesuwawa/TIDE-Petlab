"""Compose one committed fact through the capability-complete Memory path.

This host mirrors the generic committed-fact composition boundary: it may
advance an already-authorized temporal position, then evaluates one event.  A
Full-capability model additionally receives the complete owner-local candidate
authorization and produces one state acceptance plus its future-only node and
lineage.  The function is pure with respect to Store, messages, Writer, ticks,
routing, providers, and sending.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import model_data_fingerprint
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimePositionState,
    RuntimeProgressAuthority,
)
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_full_memory_candidate_authorization import (
    RuntimeFullMemoryCandidateAuthorization,
    authorize_runtime_full_memory_candidates,
)
from ops.runtime_full_memory_event_transaction import (
    RuntimeFullMemoryEventTransaction,
    execute_prepared_full_memory_event_transaction,
)
from ops.runtime_full_memory_input_product import (
    RuntimeFullMemoryInputProduct,
    prepare_runtime_full_memory_input_product_from_profile,
)
from ops.runtime_full_memory_profile_commit import (
    RuntimeFullMemoryProfileCommit,
    commit_runtime_full_memory_profile,
)
from ops.runtime_progress_transaction import (
    RuntimePreparedProgress,
    prepare_runtime_progress_for_settlement,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


FULL_MEMORY_COMMITTED_FACT_TRANSACTION_VERSION = (
    "runtime-full-memory-committed-fact-transaction-v3")
FULL_MEMORY_FACT_CAPABILITIES = frozenset({
    "advance_progress",
    "measure_event",
    "derive_event_contribution",
    "settle_state",
    "prepare_memory_inputs",
    "materialize_memory_event",
    "recall_history",
    "read_historical_influence",
    "aggregate_historical_influence",
})


@dataclass(frozen=True)
class RuntimeFullMemoryCommittedFactTransaction:
    """Pure complete profile value for one Full-capability committed fact."""

    session: BootstrappedRuntimeSession
    event_input_session: BootstrappedRuntimeSession
    settlement_input_position: RuntimePositionState
    progress_preparation: RuntimePreparedProgress | None
    candidate_authorization: RuntimeFullMemoryCandidateAuthorization
    memory_input_product: RuntimeFullMemoryInputProduct
    event_transaction: RuntimeFullMemoryEventTransaction
    profile_commit: RuntimeFullMemoryProfileCommit
    receipt: Mapping[str, Any]


def supports_full_memory_fact_transaction(
    model_runtime: BoundInteractionModel,
) -> bool:
    """Return whether the bound package exposes the complete Memory fact path."""
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "memory capability check requires a bound model runtime")
    return FULL_MEMORY_FACT_CAPABILITIES.issubset(
        set(model_runtime.manifest.capabilities))


def _context(
    *,
    session: BootstrappedRuntimeSession,
    temporal_runtime: BoundTemporalDomain,
    scope_ref: str,
    subject_ref: str,
    event_ref: str,
    action_ref: str | None,
    context_metadata: Mapping[str, Any] | None,
    position: RuntimePositionState | None = None,
) -> ModelInvocationContextV2:
    authority = RuntimeProgressAuthority(
        session_binding=session.session_binding,
        temporal_runtime=temporal_runtime,
    )
    return authority.invocation_context_v2(
        session.position if position is None else position,
        scope_ref=scope_ref,
        subject_ref=subject_ref,
        event_ref=event_ref,
        action_ref=action_ref,
        metadata={} if context_metadata is None else context_metadata,
    )


def execute_runtime_full_memory_committed_fact_transaction(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    target_coordinate: object,
    scope_ref: str,
    subject_ref: str,
    event_ref: str,
    action_ref: str | None,
    measurement_payload: Mapping[str, Any],
    context_metadata: Mapping[str, Any] | None = None,
) -> RuntimeFullMemoryCommittedFactTransaction:
    """Apply optional progress and one Full Memory event without persistence."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise RuntimeKernelContractError(
            "full memory committed fact requires a bootstrapped session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "full memory committed fact requires a bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "full memory committed fact requires a bound temporal runtime")
    if not supports_full_memory_fact_transaction(model_runtime):
        raise RuntimeKernelContractError(
            "bound model does not provide the complete Full Memory fact path")
    session.session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )

    target_position = temporal_runtime.normalize_position(target_coordinate)
    progress_preparation = None
    event_input_session = session
    if target_position.fingerprint != session.position.position.fingerprint:
        progress_preparation = prepare_runtime_progress_for_settlement(
            session=session,
            model_runtime=model_runtime,
            temporal_runtime=temporal_runtime,
            target_coordinate=target_coordinate,
            scope_ref=scope_ref,
            subject_ref=subject_ref,
            event_ref=event_ref,
            action_ref=action_ref,
            context_metadata=context_metadata,
        )

    settlement_input_position = (
        event_input_session.position
        if progress_preparation is None
        else progress_preparation.preparation.state
    )

    context = _context(
        session=event_input_session,
        temporal_runtime=temporal_runtime,
        scope_ref=scope_ref,
        subject_ref=subject_ref,
        event_ref=event_ref,
        action_ref=action_ref,
        context_metadata=context_metadata,
        position=settlement_input_position,
    )
    prepared_event = None
    query_policy = context.metadata.get("indexed_memory_query")
    if query_policy is not None:
        if query_policy.get("version") != "desktop-pet-indexed-memory-query-v1":
            raise ValueError("unsupported indexed memory query policy")
        from ops.runtime_full_memory_event_transaction import prepare_memory_event_stages
        from ops.runtime_indexed_memory_input import prepare_indexed_input
        prepared_event = prepare_memory_event_stages(session=event_input_session,model_runtime=model_runtime,
            context=context,measurement_payload=measurement_payload,progress_preparation=progress_preparation)
        authorization,memory_input = prepare_indexed_input(session=event_input_session,model_runtime=model_runtime,
            context=context,current_readout={"event_contribution":prepared_event.event_contribution_execution.payload["event_contribution"]},
            budget_ms=query_policy["budget_ms"])
    else:
        authorization = authorize_runtime_full_memory_candidates(
            profile=event_input_session.profile,
            session_binding_fingerprint=(
                event_input_session.session_binding.fingerprint),
            context=context,
        )
        memory_input = prepare_runtime_full_memory_input_product_from_profile(
            model_runtime=model_runtime,
            context=context,
            profile=event_input_session.profile,
            candidate_authorization=authorization,
        )
    event_transaction = execute_prepared_full_memory_event_transaction(
        session=event_input_session,
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        scope_ref=scope_ref,
        subject_ref=subject_ref,
        event_ref=event_ref,
        action_ref=action_ref,
        measurement_payload=measurement_payload,
        memory_input_product=memory_input,
        context_metadata=context_metadata,
        progress_preparation=progress_preparation,
        prepared_event=prepared_event,
    )
    profile_commit = commit_runtime_full_memory_profile(
        transaction=event_transaction,
        model_runtime=model_runtime,
    )
    material = {
        "version": FULL_MEMORY_COMMITTED_FACT_TRANSACTION_VERSION,
        "session_id": session.session_binding.session_id,
        "event_ref": event_ref,
        "initial_position_fingerprint": session.position.fingerprint,
        "initial_state_fingerprint": session.state.fingerprint,
        "event_input_position_fingerprint": (
            event_input_session.position.fingerprint),
        "event_input_state_fingerprint": event_input_session.state.fingerprint,
        "progress_preparation_fingerprint": (
            None if progress_preparation is None
            else model_data_fingerprint(progress_preparation.receipt)
        ),
        "candidate_authorization_fingerprint": model_data_fingerprint(
            authorization.receipt),
        "memory_input_product_fingerprint": model_data_fingerprint(
            memory_input.receipt),
        "event_transaction_fingerprint": model_data_fingerprint(
            event_transaction.receipt),
        "profile_commit_fingerprint": model_data_fingerprint(
            profile_commit.receipt),
        "final_position_fingerprint": (
            profile_commit.session.position.fingerprint),
        "final_state_fingerprint": profile_commit.session.state.fingerprint,
        "authority": {
            "store_write": False,
            "history_write": False,
            "expression_projection": False,
            "writer_attempt": False,
            "provider_call": False,
            "dynamic_tick": False,
            "route": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeFullMemoryCommittedFactTransaction(
        session=profile_commit.session,
        event_input_session=event_input_session,
        settlement_input_position=settlement_input_position,
        progress_preparation=progress_preparation,
        candidate_authorization=authorization,
        memory_input_product=memory_input,
        event_transaction=profile_commit.transaction,
        profile_commit=profile_commit,
        receipt=receipt,
    )


__all__ = (
    "FULL_MEMORY_COMMITTED_FACT_TRANSACTION_VERSION",
    "FULL_MEMORY_FACT_CAPABILITIES",
    "RuntimeFullMemoryCommittedFactTransaction",
    "execute_runtime_full_memory_committed_fact_transaction",
    "supports_full_memory_fact_transaction",
)
