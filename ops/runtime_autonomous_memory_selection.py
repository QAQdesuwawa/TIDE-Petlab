"""Read-only Memory product from one settled autonomous observation.

Reuses canonical History authorization and the registered package's existing
input producer. No event measurement, second settlement, Writer or dispatch.
"""
from dataclasses import dataclass
from collections.abc import Mapping

from framework.history import FRAMEWORK_HISTORY_PROFILE_KEY, HistoryAuthority, HistoryRead
from framework.model_api import ModelStageExecution, freeze_model_payload, model_data_fingerprint
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_api.memory import RecallDecision, read_recall_decision
from framework.model_runtime import BoundInteractionModel
from ops.runtime_autonomous_observation import RuntimeAutonomousObservationTransaction, _verify_settled_progress
from ops.runtime_committed_autonomous_grant import RuntimeCommittedAutonomousGrant, _stage_execution, _verified_receipt
from ops.runtime_full_memory_candidate_authorization import authorize_runtime_full_memory_candidates, RuntimeFullMemoryCandidateAuthorization
from ops.runtime_full_memory_input_product import prepare_runtime_full_memory_input_product_from_profile, RuntimeFullMemoryInputProduct
from ops.runtime_stage_verification import verify_runtime_stage_execution


@dataclass(frozen=True)
class RuntimeAutonomousMemorySelection:
    context: ModelInvocationContextV2
    candidates: RuntimeFullMemoryCandidateAuthorization
    inputs: RuntimeFullMemoryInputProduct
    recall_execution: ModelStageExecution
    recall: RecallDecision
    history_read: HistoryRead
    history_snapshot: Mapping
    receipt: Mapping

    def __post_init__(self):
        for name in ("history_snapshot", "receipt"):
            object.__setattr__(self, name, freeze_model_payload(getattr(self, name)))


def execute_runtime_autonomous_memory_selection(*, observation, model_runtime):
    if not isinstance(observation, (RuntimeAutonomousObservationTransaction, RuntimeCommittedAutonomousGrant)):
        raise TypeError("memory selection requires a formal autonomous observation")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise TypeError("memory selection requires a registered model runtime")
    session = observation.session
    if (session.session_binding.model_binding != model_runtime.binding
            or session.session_binding.session_id != model_runtime.session_id):
        raise ValueError("autonomous Memory binding differs from settled session")
    shared = observation.shared_projection_execution
    source = shared.receipt
    context = ModelInvocationContextV2(
        session_id=source.session_id, scope_ref=None, subject_ref=source.subject_ref,
        event_ref=source.event_ref, action_ref=None,
        temporal_position=session.position.position,
        runtime_ordinal=session.position.runtime_ordinal,
        metadata={"committed_event_kind": "tick"},
    )
    if isinstance(observation, RuntimeAutonomousObservationTransaction):
        _verify_settled_progress(observation.progress_transaction,
            participant_id=context.subject_ref, tick_event_id=context.event_ref)
        if session != observation.progress_transaction.session:
            raise ValueError("autonomous Memory session differs from progress settlement")
        settled = observation.progress_transaction.settlement_execution
    else:
        # Reuse the committed-grant validator; do not restore/reinvoke progress.
        _verified_receipt(observation.receipt, "committed autonomous observation")
        settled = _stage_execution(session=session,
            receipt_record=observation.settlement_execution.receipt.to_record(),
            payload=observation.settlement_execution.payload,
            expected_stage="settle_state", participant_id=context.subject_ref,
            event_id=context.event_ref)
        if (observation.authorization.participant_id != context.subject_ref
                or observation.authorization.source_event_id != context.event_ref):
            raise ValueError("autonomous Memory grant identity differs from tick")
    settlement = settled.payload["settlement"]
    if model_data_fingerprint(settlement["state_candidate"]) != model_data_fingerprint(session.state.state):
        raise ValueError("autonomous Memory settlement differs from accepted state")
    verify_runtime_stage_execution(shared, stage="project_shared_semantics",
        runtime=model_runtime, context=context, invocation_payload={
            "parameters": session.parameters.parameters, "settlement": settlement})
    if observation.receipt["shared_projection_receipt_fingerprint"] != model_data_fingerprint(source.to_record()):
        raise ValueError("autonomous Memory shared receipt differs from observation")
    candidates = authorize_runtime_full_memory_candidates(
        profile=session.profile, session_binding_fingerprint=session.session_binding.fingerprint,
        context=context)
    inputs = prepare_runtime_full_memory_input_product_from_profile(
        model_runtime=model_runtime, context=context, profile=session.profile,
        candidate_authorization=candidates)
    recall_input = {
        **dict(inputs.memory_recall_payload), "parameters": session.parameters.parameters,
        "settlement": settlement, "shared_projection": shared.payload["shared_projection"],
    }
    executed = model_runtime.invoke("recall_history", payload=recall_input, context=context)
    verify_runtime_stage_execution(executed, stage="recall_history",
        runtime=model_runtime, context=context, invocation_payload=recall_input)
    recall = read_recall_decision(executed, expected_context=context,
        candidate_event_ids=candidates.candidate_event_ids)
    stored = session.profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
    history = (HistoryAuthority.from_record(stored) if stored is not None else
        HistoryAuthority(session_binding_fingerprint=session.session_binding.fingerprint))
    read = history.authorize_read(
        reader_ref=context.subject_ref, trigger_fact_id=context.event_ref,
        before_runtime_ordinal=context.runtime_ordinal + 1,
        requested_fact_ids=recall.recalled_event_ids, authorization_ref={
            "observation_fingerprint": observation.receipt["fingerprint"],
            "recall_stage_receipt_fingerprint": model_data_fingerprint(executed.receipt.to_record()),
            "recall_output_fingerprint": executed.receipt.output_fingerprint,
        })
    material = {
        "version": "runtime-autonomous-memory-selection-v1",
        "session_binding_fingerprint": session.session_binding.fingerprint,
        "observation_fingerprint": observation.receipt["fingerprint"],
        "candidate_authorization_fingerprint": candidates.receipt["fingerprint"],
        "input_product_fingerprint": inputs.receipt["fingerprint"],
        "shared_stage_receipt_fingerprint": model_data_fingerprint(source.to_record()),
        "recall_stage_receipt_fingerprint": model_data_fingerprint(executed.receipt.to_record()),
        "recalled_event_ids": list(recall.recalled_event_ids),
        "selected_history_read_fingerprint": read.fingerprint,
        "state_commit_authority": False, "memory_commit_authority": False,
        "send_authority": False,
    }
    return RuntimeAutonomousMemorySelection(
        context=context, candidates=candidates, inputs=inputs,
        recall_execution=executed, recall=recall, history_read=read,
        history_snapshot=history.to_record(),
        receipt={**material, "fingerprint": model_data_fingerprint(material)})
