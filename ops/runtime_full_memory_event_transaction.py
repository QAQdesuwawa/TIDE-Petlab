"""Model-independent Full event transaction with one Memory settlement.

The host sequences opaque model stages at one already accepted temporal
position.  It never interprets model dimensions, persists memory edges, writes
storage, schedules an opening, or grants a second state-commit authority.
"""
from __future__ import annotations
from ops.runtime_authorities import runtime_authorities
from ops.settlement_validation import accept_settlement

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    model_data_fingerprint,
)
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_api.memory import (
    HistoricalAggregate,
    HistoricalInfluenceProduct,
    RecallDecision,
    read_historical_aggregate,
    read_historical_influence,
    read_recall_decision,
)
from framework.model_runtime import BoundInteractionModel
from framework.history import HistoryRead
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimeProgressAuthority,
    RuntimeStateCommitAcceptance,
    RuntimeStateCommitAuthority,
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


_RECALL_RESERVED_FIELDS = frozenset({
    "parameters", "event_contribution", "current", "params",
})
_INFLUENCE_RESERVED_FIELDS = frozenset({
    "recalled_event_ids", "event_contribution", "current_readout",
    "parameters",
})


@dataclass(frozen=True)
class RuntimeFullMemoryEventTransaction:
    """Pure result of all Full stages and one Kernel state acceptance."""

    session: BootstrappedRuntimeSession
    context: ModelInvocationContext | ModelInvocationContextV2
    measurement_execution: ModelStageExecution
    event_contribution_execution: ModelStageExecution
    recall_execution: ModelStageExecution
    recall: RecallDecision
    influence_execution: ModelStageExecution
    influence: HistoricalInfluenceProduct
    aggregate_execution: ModelStageExecution
    aggregate: HistoricalAggregate
    settlement_execution: ModelStageExecution
    acceptance: RuntimeStateCommitAcceptance
    progress_preparation: RuntimePreparedProgress | None
    history_read: HistoryRead | None
    receipt: Mapping[str, Any]


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must identify a committed event fact")
    return normalized


def _event_ids(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field_name} must be an event-id sequence")
    normalized = tuple(_identifier(item, field_name) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field_name} cannot contain duplicate event IDs")
    return normalized


def _stage_product(
    execution: ModelStageExecution, *, field_name: str,
) -> Any:
    if field_name not in execution.payload:
        raise RuntimeKernelContractError(
            f"model stage output must contain {field_name}")
    return execution.payload[field_name]


def _reject_reserved(
    payload: Mapping[str, Any], *, reserved: frozenset[str], stage: str,
) -> None:
    attempted = set(payload).intersection(reserved)
    if attempted:
        raise ValueError(
            f"{stage} payload contains reserved transaction fields: "
            f"{sorted(attempted)}")


def _transaction_receipt(
    *,
    context: ModelInvocationContext | ModelInvocationContextV2,
    measurement_payload: Mapping[str, Any],
    memory_recall_payload: Mapping[str, Any],
    memory_influence_payload: Mapping[str, Any],
    executions: Mapping[str, ModelStageExecution],
    acceptance: RuntimeStateCommitAcceptance,
    progress_preparation: RuntimePreparedProgress | None,
) -> dict[str, Any]:
    identity = context_fact_identity(context)
    material = {
        "version": "runtime-full-memory-event-transaction-v3",
        "session_id": identity.session_id,
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
        "memory_recall_input_fingerprint": model_data_fingerprint(
            memory_recall_payload),
        "memory_influence_input_fingerprint": model_data_fingerprint(
            memory_influence_payload),
        "measurement_stage_receipt_fingerprint": model_data_fingerprint(
            executions["measurement"].receipt.to_record()),
        "event_contribution_stage_receipt_fingerprint": model_data_fingerprint(
            executions["event_contribution"].receipt.to_record()),
        "recall_stage_receipt_fingerprint": model_data_fingerprint(
            executions["recall"].receipt.to_record()),
        "influence_stage_receipt_fingerprint": model_data_fingerprint(
            executions["influence"].receipt.to_record()),
        "aggregate_stage_receipt_fingerprint": model_data_fingerprint(
            executions["aggregate"].receipt.to_record()),
        "settlement_stage_receipt_fingerprint": model_data_fingerprint(
            executions["settlement"].receipt.to_record()),
        "acceptance_fingerprint": acceptance.fingerprint,
        "progress_preparation_fingerprint": (
            None if progress_preparation is None
            else model_data_fingerprint(progress_preparation.receipt)
        ),
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


@dataclass(frozen=True)
class RuntimePreparedMemoryEvent:
    measurement_execution: ModelStageExecution
    event_contribution_execution: ModelStageExecution


def prepare_memory_event_stages(*,session,model_runtime,context,measurement_payload,progress_preparation=None):
    measurement_input=dict(measurement_payload)
    measurement_execution=model_runtime.invoke("measure_event",payload=measurement_input,context=context)
    verify_runtime_stage_execution(measurement_execution,stage="measure_event",runtime=model_runtime,
                                   context=context,invocation_payload=measurement_input)
    measurement=_stage_product(measurement_execution,field_name="measurement")
    payload={"parameters":session.parameters.parameters,"state":session.state.state,"measurement":measurement}
    if progress_preparation is not None:payload["progress_contribution"]=progress_preparation.contribution
    event_execution=model_runtime.invoke("derive_event_contribution",payload=payload,context=context)
    verify_runtime_stage_execution(event_execution,stage="derive_event_contribution",runtime=model_runtime,
                                   context=context,invocation_payload=payload)
    return RuntimePreparedMemoryEvent(measurement_execution,event_execution)


def execute_full_memory_event_transaction(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    scope_ref: str,
    subject_ref: str,
    event_ref: str,
    action_ref: str | None,
    measurement_payload: Mapping[str, Any],
    memory_recall_payload: Mapping[str, Any],
    memory_influence_payload: Mapping[str, Any],
    context_metadata: Mapping[str, Any] | None = None,
    progress_preparation: RuntimePreparedProgress | None = None,
    history_read: HistoryRead | None = None,
    prepared_event: RuntimePreparedMemoryEvent | None = None,
) -> RuntimeFullMemoryEventTransaction:
    """Execute all Full event stages before one Kernel settlement acceptance."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise RuntimeKernelContractError(
            "full memory event transaction requires a bootstrapped session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "full memory event transaction requires a bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "full memory event transaction requires a bound temporal runtime")
    for value, name in (
        (measurement_payload, "measurement_payload"),
        (memory_recall_payload, "memory_recall_payload"),
        (memory_influence_payload, "memory_influence_payload"),
    ):
        if not isinstance(value, Mapping):
            raise RuntimeKernelContractError(f"{name} must be a mapping")
    if context_metadata is not None and not isinstance(context_metadata, Mapping):
        raise RuntimeKernelContractError("context_metadata must be a mapping")

    recall_external = dict(memory_recall_payload)
    influence_external = dict(memory_influence_payload)
    _reject_reserved(
        recall_external,
        reserved=_RECALL_RESERVED_FIELDS,
        stage="recall_history",
    )
    _reject_reserved(
        influence_external,
        reserved=_INFLUENCE_RESERVED_FIELDS,
        stage="read_historical_influence",
    )
    if "candidate_event_ids" not in recall_external:
        raise ValueError(
            "memory_recall_payload must contain candidate_event_ids")
    candidate_event_ids = _event_ids(
        recall_external["candidate_event_ids"], "candidate_event_ids")

    scope = _identifier(scope_ref, "scope_ref")
    subject = _identifier(subject_ref, "subject_ref")
    event = _identifier(event_ref, "event_ref")
    session.session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )
    authority, progress_authority = runtime_authorities(
        session.session_binding,temporal_runtime)
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
            "full memory event session differs from persisted v3 snapshot")

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
    if history_read is not None:
        if not isinstance(history_read, HistoryRead):
            raise ValueError("history_read must be HistoryRead")
        if (
            history_read.trigger_fact_id != event
            or history_read.before_runtime_ordinal != context.runtime_ordinal + 1
            or history_read.fact_ids != candidate_event_ids
        ):
            raise ValueError("history read differs from Full Memory event input")

    measurement_input = dict(measurement_payload)
    if prepared_event is not None and not isinstance(prepared_event, RuntimePreparedMemoryEvent):
        raise ValueError("prepared event stages must be a signed stage bundle")
    measurement_execution = (model_runtime.invoke(
        "measure_event", payload=measurement_input, context=context)
        if prepared_event is None else prepared_event.measurement_execution)
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
    if progress_preparation is not None:
        contribution_input["progress_contribution"] = (
            progress_preparation.contribution)
    event_contribution_execution = (model_runtime.invoke(
        "derive_event_contribution",payload=contribution_input,context=context)
        if prepared_event is None else prepared_event.event_contribution_execution)
    verify_runtime_stage_execution(
        event_contribution_execution,
        stage="derive_event_contribution",
        runtime=model_runtime,
        context=context,
        invocation_payload=contribution_input,
    )
    event_contribution = _stage_product(
        event_contribution_execution, field_name="event_contribution")

    recall_input = dict(recall_external)
    recall_input.update({
        "parameters": session.parameters.parameters,
        "event_contribution": event_contribution,
    })
    recall_execution = model_runtime.invoke(
        "recall_history", payload=recall_input, context=context)
    verify_runtime_stage_execution(
        recall_execution,
        stage="recall_history",
        runtime=model_runtime,
        context=context,
        invocation_payload=recall_input,
    )
    recall = read_recall_decision(
        recall_execution,
        expected_context=context,
        candidate_event_ids=candidate_event_ids,
    )

    influence_input = dict(influence_external)
    influence_input.update({
        "recalled_event_ids": list(recall.recalled_event_ids),
        "event_contribution": event_contribution,
        "parameters": session.parameters.parameters,
    })
    influence_execution = model_runtime.invoke(
        "read_historical_influence",
        payload=influence_input,
        context=context,
    )
    verify_runtime_stage_execution(
        influence_execution,
        stage="read_historical_influence",
        runtime=model_runtime,
        context=context,
        invocation_payload=influence_input,
    )
    influence = read_historical_influence(
        influence_execution,
        expected_context=context,
        recalled_event_ids=recall.recalled_event_ids,
    )

    aggregate_input = {
        "recalled_event_ids": list(influence.recalled_event_ids),
        "event_influences": influence.event_influences,
        "source_influence_output_fingerprint": (
            influence.stage_receipt.output_fingerprint),
    }
    aggregate_execution = model_runtime.invoke(
        "aggregate_historical_influence",
        payload=aggregate_input,
        context=context,
    )
    verify_runtime_stage_execution(
        aggregate_execution,
        stage="aggregate_historical_influence",
        runtime=model_runtime,
        context=context,
        invocation_payload=aggregate_input,
    )
    aggregate = read_historical_aggregate(
        aggregate_execution,
        expected_context=context,
        influence=influence,
    )
    historical_aggregate = _stage_product(
        aggregate_execution, field_name="historical_aggregate")

    settlement_input = {
        "state": session.state.state,
        "parameters": session.parameters.parameters,
        "progress_contribution": (
            None if progress_preparation is None
            else progress_preparation.contribution
        ),
        "event_contribution": event_contribution,
        "historical_aggregate": historical_aggregate,
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
    acceptance = accept_settlement(authority,
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
    executions = {
        "measurement": measurement_execution,
        "event_contribution": event_contribution_execution,
        "recall": recall_execution,
        "influence": influence_execution,
        "aggregate": aggregate_execution,
        "settlement": settlement_execution,
    }
    receipt = _transaction_receipt(
        context=context,
        measurement_payload=measurement_input,
        memory_recall_payload=recall_external,
        memory_influence_payload=influence_external,
        executions=executions,
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
    return RuntimeFullMemoryEventTransaction(
        session=next_session,
        context=context,
        measurement_execution=measurement_execution,
        event_contribution_execution=event_contribution_execution,
        recall_execution=recall_execution,
        recall=recall,
        influence_execution=influence_execution,
        influence=influence,
        aggregate_execution=aggregate_execution,
        aggregate=aggregate,
        settlement_execution=settlement_execution,
        acceptance=acceptance,
        progress_preparation=progress_preparation,
        history_read=history_read,
        receipt=receipt,
    )


def execute_prepared_full_memory_event_transaction(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    scope_ref: str,
    subject_ref: str,
    event_ref: str,
    action_ref: str | None,
    measurement_payload: Mapping[str, Any],
    memory_input_product: object,
    context_metadata: Mapping[str, Any] | None = None,
    progress_preparation: RuntimePreparedProgress | None = None,
    prepared_event: RuntimePreparedMemoryEvent | None = None,
) -> RuntimeFullMemoryEventTransaction:
    """Consume one signed preparation product in the Full transaction."""
    from ops.runtime_full_memory_input_product import (
        RuntimeFullMemoryInputProduct,
    )

    if not isinstance(memory_input_product, RuntimeFullMemoryInputProduct):
        raise ValueError(
            "formal Full transaction requires RuntimeFullMemoryInputProduct")
    receipt = memory_input_product.receipt
    receipt_fingerprint = receipt.get("fingerprint")
    receipt_material = dict(receipt)
    receipt_material.pop("fingerprint", None)
    if receipt_fingerprint != model_data_fingerprint(receipt_material):
        raise ValueError("memory input product receipt fingerprint is invalid")
    if memory_input_product.model_binding_fingerprint != (
            model_data_fingerprint(model_runtime.binding.to_record())):
        raise ValueError("memory input product model binding is invalid")
    expected_identity = {
        "session_id": session.session_binding.session_id,
        "conversation_id": _identifier(scope_ref, "scope_ref"),
        "participant_id": _identifier(subject_ref, "subject_ref"),
        "current_event_id": _identifier(event_ref, "event_ref"),
        "temporal_position_fingerprint": (
            session.position.position.fingerprint
            if progress_preparation is None
            else progress_preparation.preparation.state.position.fingerprint
        ),
        "runtime_ordinal": (
            session.position.runtime_ordinal
            if progress_preparation is None
            else progress_preparation.preparation.state.runtime_ordinal
        ),
    }
    if any(receipt.get(field) != value for field, value in expected_identity.items()):
        raise ValueError("memory input product context differs from transaction")
    if receipt.get("state_commit_authority") is not False or (
            receipt.get("memory_commit_authority") is not False):
        raise ValueError("memory input product carries forbidden authority")
    execution = memory_input_product.preparation_execution
    if execution.receipt.stage != "prepare_memory_inputs" or (
        model_data_fingerprint(execution.payload)
        != execution.receipt.output_fingerprint
    ):
        raise ValueError("memory input preparation execution is invalid")
    if receipt.get("preparation_stage_receipt_fingerprint") != (
            model_data_fingerprint(execution.receipt.to_record())):
        raise ValueError("memory input product differs from preparation stage")
    if receipt.get("recall_payload_fingerprint") != model_data_fingerprint(
            memory_input_product.memory_recall_payload):
        raise ValueError("memory recall payload differs from signed product")
    if receipt.get("influence_payload_fingerprint") != model_data_fingerprint(
            memory_input_product.memory_influence_payload):
        raise ValueError("memory influence payload differs from signed product")

    return execute_full_memory_event_transaction(
        session=session,
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        scope_ref=scope_ref,
        subject_ref=subject_ref,
        event_ref=event_ref,
        action_ref=action_ref,
        measurement_payload=measurement_payload,
        memory_recall_payload=memory_input_product.memory_recall_payload,
        memory_influence_payload=memory_input_product.memory_influence_payload,
        context_metadata=context_metadata,
        progress_preparation=progress_preparation,
        history_read=memory_input_product.history_read,
        prepared_event=prepared_event,
    )


__all__ = (
    "RuntimeFullMemoryEventTransaction",
    "execute_full_memory_event_transaction",
    "execute_prepared_full_memory_event_transaction",
)
