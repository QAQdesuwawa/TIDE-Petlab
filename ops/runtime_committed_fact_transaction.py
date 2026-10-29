"""Compose one already committed interaction fact without partial storage.

The two owned calculations remain in their existing pure hosts.  This module
only decides whether the caller-supplied canonical position needs progress,
then feeds the resulting session into the event transaction.  It owns no
Store, clock, Writer, provider, dynamic tick, send, or expression projection.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import model_data_fingerprint
from framework.model_api.identity import context_fact_identity
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import RuntimeKernelContractError, RuntimePositionState
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_event_transaction import (
    RuntimeEventTransaction,
    execute_committed_event_transaction,
)
from ops.runtime_progress_transaction import (
    RuntimePreparedProgress,
    prepare_runtime_progress_for_settlement,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


COMMITTED_FACT_TRANSACTION_VERSION = "runtime-committed-fact-transaction-v3"


@dataclass(frozen=True)
class RuntimeCommittedFactTransaction:
    """Pure progress/event composition ready for one host persistence step."""

    session: BootstrappedRuntimeSession
    event_input_session: BootstrappedRuntimeSession
    settlement_input_position: RuntimePositionState
    progress_preparation: RuntimePreparedProgress | None
    event_transaction: RuntimeEventTransaction
    receipt: Mapping[str, Any]


def _receipt(
    *,
    initial_session: BootstrappedRuntimeSession,
    event_input_session: BootstrappedRuntimeSession,
    progress_preparation: RuntimePreparedProgress | None,
    event_transaction: RuntimeEventTransaction,
) -> dict[str, Any]:
    fact_identity = context_fact_identity(event_transaction.context)
    material = {
        "version": COMMITTED_FACT_TRANSACTION_VERSION,
        "session_id": initial_session.session_binding.session_id,
        "event_ref": fact_identity.event_ref,
        "initial_position_fingerprint": initial_session.position.fingerprint,
        "initial_state_fingerprint": initial_session.state.fingerprint,
        "event_input_position_fingerprint": (
            event_input_session.position.fingerprint),
        "event_input_state_fingerprint": event_input_session.state.fingerprint,
        "progress_preparation_fingerprint": (
            None
            if progress_preparation is None
            else model_data_fingerprint(progress_preparation.receipt)
        ),
        "event_transaction_fingerprint": model_data_fingerprint(
            event_transaction.receipt),
        "final_position_fingerprint": event_transaction.session.position.fingerprint,
        "final_state_fingerprint": event_transaction.session.state.fingerprint,
        "authority": {
            "store_write": False,
            "history_write": False,
            "expression_projection": False,
            "writer_attempt": False,
            "provider_call": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


def execute_runtime_committed_fact_transaction(
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
) -> RuntimeCommittedFactTransaction:
    """Apply optional progress and one event to an already committed fact.

    The caller owns the source fact and target coordinate.  Equal canonical
    positions do not manufacture a progress fact.  A distinct target is
    interpreted only by the bound TemporalDomain through the existing formal
    progress transaction.
    """
    if not isinstance(session, BootstrappedRuntimeSession):
        raise RuntimeKernelContractError(
            "committed fact requires a bootstrapped runtime session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "committed fact requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "committed fact requires a registry-bound temporal runtime")
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

    event_transaction = execute_committed_event_transaction(
        session=event_input_session,
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        scope_ref=scope_ref,
        subject_ref=subject_ref,
        event_ref=event_ref,
        action_ref=action_ref,
        measurement_payload=measurement_payload,
        context_metadata=context_metadata,
        progress_preparation=progress_preparation,
    )
    receipt = _receipt(
        initial_session=session,
        event_input_session=event_input_session,
        progress_preparation=progress_preparation,
        event_transaction=event_transaction,
    )
    return RuntimeCommittedFactTransaction(
        session=event_transaction.session,
        event_input_session=event_input_session,
        settlement_input_position=(
            event_input_session.position
            if progress_preparation is None
            else progress_preparation.preparation.state
        ),
        progress_preparation=progress_preparation,
        event_transaction=event_transaction,
        receipt=receipt,
    )


__all__ = (
    "COMMITTED_FACT_TRANSACTION_VERSION",
    "RuntimeCommittedFactTransaction",
    "execute_runtime_committed_fact_transaction",
)
