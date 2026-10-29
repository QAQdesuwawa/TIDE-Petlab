"""One formal owner-local dynamic-tick observation transaction.

This host composes an already planned Temporal observation with one monotonic
progress settlement, a read-only shared projection, and the model's action
assessment.  Only an eligible assessment paired with the exact due proof may
produce a Kernel action authorization.  Conversation destination, Writer,
source Commit, routing, recipient ingress, and sending remain downstream.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelStageExecution,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_api.identity import context_fact_identity
from framework.model_api.action import ActionAssessment, read_action_assessment
from framework.model_runtime import BoundInteractionModel
from interaction_extensions.conversation_action_protocol import (
    RuntimeActionAuthorization,
    RuntimeActionAuthorizationAuthority,
)
from framework.runtime_kernel import (
    RuntimeKernelContractError,
)
from framework.temporal_domain import ObservationDueProof, ObservationTarget
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_progress_transaction import (
    RuntimeProgressTransaction,
    execute_runtime_progress_transaction,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession
from ops.runtime_stage_verification import verify_runtime_stage_execution


AUTONOMOUS_OBSERVATION_VERSION = "runtime-autonomous-observation-v1"


@dataclass(frozen=True)
class RuntimeAutonomousObservationTransaction:
    """A settled tick and its optional, non-sending action grant."""

    session: BootstrappedRuntimeSession
    progress_transaction: RuntimeProgressTransaction
    due_proof: ObservationDueProof
    shared_projection_execution: ModelStageExecution
    assessment_execution: ModelStageExecution
    assessment: ActionAssessment
    authorization: RuntimeActionAuthorization | None
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise RuntimeKernelContractError(f"{field} must be a non-empty identifier")
    return result


def _verify_settled_progress(
    transaction: RuntimeProgressTransaction,
    *,
    participant_id: str,
    tick_event_id: str,
) -> None:
    context = transaction.context
    identity = context_fact_identity(context)
    final = transaction.session
    settlement = transaction.settlement_execution
    acceptance = transaction.acceptance
    if (
        identity.scope_ref is not None
        or identity.subject_ref != participant_id
        or identity.event_ref != tick_event_id
        or identity.action_ref is not None
        or context.metadata.get("committed_event_kind") != "tick"
        or acceptance.position != final.position
        or acceptance.state != final.state
        or settlement.receipt.stage != "settle_state"
        or acceptance.stage_receipt_fingerprint
        != model_data_fingerprint(settlement.receipt.to_record())
    ):
        raise RuntimeKernelContractError(
            "autonomous observation progress lineage is invalid")
    settlement_value = settlement.payload.get("settlement")
    if (
        not isinstance(settlement_value, Mapping)
        or model_data_fingerprint(settlement_value.get("state_candidate"))
        != model_data_fingerprint(final.state.state)
    ):
        raise RuntimeKernelContractError(
            "autonomous observation settlement differs from accepted state")


def execute_runtime_autonomous_observation_transaction(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    observation_target: ObservationTarget,
    target_coordinate: object,
    participant_id: str,
    tick_event_id: str,
    assessment_payload: Mapping[str, Any],
) -> RuntimeAutonomousObservationTransaction:
    """Evaluate one due tick without choosing a Conversation destination."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise RuntimeKernelContractError(
            "autonomous observation requires a bootstrapped runtime session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "autonomous observation requires a bound interaction model")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "autonomous observation requires a bound temporal domain")
    if not isinstance(observation_target, ObservationTarget):
        raise RuntimeKernelContractError(
            "autonomous observation requires an ObservationTarget")
    participant = _identifier(participant_id, "participant_id")
    tick_event = _identifier(tick_event_id, "tick_event_id")
    if not isinstance(assessment_payload, Mapping):
        raise RuntimeKernelContractError("assessment_payload must be a mapping")
    if "shared_projection" in assessment_payload:
        raise RuntimeKernelContractError(
            "assessment_payload cannot replace shared_projection")
    frozen_assessment_payload = freeze_model_payload(assessment_payload)

    session.session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )
    try:
        observation_target.verify_integrity()
    except (TypeError, ValueError) as exc:
        raise RuntimeKernelContractError(str(exc)) from exc
    if observation_target.binding != session.session_binding.temporal_binding:
        raise RuntimeKernelContractError(
            "observation target temporal binding differs from session")
    if (
        observation_target.origin_position.fingerprint
        != session.position.position.fingerprint
    ):
        raise RuntimeKernelContractError(
            "observation target does not start at current runtime position")

    target_position = temporal_runtime.normalize_position(target_coordinate)
    due_proof = temporal_runtime.prove_observation_due(
        observation_target, target_position)
    if not due_proof.due:
        raise RuntimeKernelContractError("autonomous observation is not due")

    progress = execute_runtime_progress_transaction(
        session=session,
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        target_coordinate=target_coordinate,
        scope_ref=None,
        subject_ref=participant,
        event_ref=tick_event,
        action_ref=None,
        context_metadata={"committed_event_kind": "tick"},
    )
    _verify_settled_progress(
        progress, participant_id=participant, tick_event_id=tick_event)
    current = progress.session.position
    if (
        due_proof.current_order.to_position.fingerprint
        != current.position.fingerprint
    ):
        raise RuntimeKernelContractError(
            "due proof differs from settled autonomous position")

    context = ModelInvocationContextV2(
        session_id=progress.session.session_binding.session_id,
        scope_ref=None,
        subject_ref=participant,
        event_ref=tick_event,
        action_ref=None,
        temporal_position=current.position,
        runtime_ordinal=current.runtime_ordinal,
        metadata={"committed_event_kind": "tick"},
    )
    settlement = progress.settlement_execution.payload.get("settlement")
    shared_input = {
        "parameters": progress.session.parameters.parameters,
        "settlement": settlement,
    }
    shared = model_runtime.invoke(
        "project_shared_semantics", payload=shared_input, context=context)
    verify_runtime_stage_execution(
        shared,
        stage="project_shared_semantics",
        runtime=model_runtime,
        context=context,
        invocation_payload=shared_input,
    )
    shared_projection = shared.payload.get("shared_projection")
    if not isinstance(shared_projection, Mapping):
        raise RuntimeKernelContractError(
            "autonomous shared projection output is missing")

    assessment_input = {
        "shared_projection": shared_projection,
        **dict(thaw_model_data(frozen_assessment_payload)),
    }
    assessment_execution = model_runtime.invoke(
        "assess_action", payload=assessment_input, context=context)
    verify_runtime_stage_execution(
        assessment_execution,
        stage="assess_action",
        runtime=model_runtime,
        context=context,
        invocation_payload=assessment_input,
    )
    assessment = read_action_assessment(
        assessment_execution,
        source_fact_ref=tick_event,
        source_position=current.position,
        source_runtime_ordinal=current.runtime_ordinal,
    )
    authorization = None
    if assessment.eligible and assessment.action_kind == "autonomous_opening":
        authorization = RuntimeActionAuthorizationAuthority(
            session_binding=progress.session.session_binding,
        ).authorize_autonomous_opening(
            current_position=current,
            due_proof=due_proof,
            assessment=assessment,
        )

    material = {
        "version": AUTONOMOUS_OBSERVATION_VERSION,
        "status": "autonomous_observation_assessed",
        "session_binding_fingerprint": progress.session.session_binding.fingerprint,
        "participant_id": participant,
        "tick_event_id": tick_event,
        "target_fingerprint": observation_target.fingerprint,
        "due_proof_fingerprint": due_proof.fingerprint,
        "progress_transaction_fingerprint": model_data_fingerprint(
            progress.receipt),
        "settled_position_fingerprint": current.position.fingerprint,
        "settled_runtime_ordinal": current.runtime_ordinal,
        "shared_projection_receipt_fingerprint": model_data_fingerprint(
            shared.receipt.to_record()),
        "assessment_receipt_fingerprint": model_data_fingerprint(
            assessment_execution.receipt.to_record()),
        "assessment_payload_fingerprint": model_data_fingerprint(
            frozen_assessment_payload),
        "eligible": assessment.eligible,
        "observation_advice": thaw_model_data(assessment.observation_advice),
        "authorization_fingerprint": (
            None if authorization is None else authorization.fingerprint),
        "authority": {
            "destination": False,
            "writer_attempt": False,
            "provider_call": False,
            "action_commit": False,
            "source_write": False,
            "route": False,
            "recipient_ingress": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeAutonomousObservationTransaction(
        session=progress.session,
        progress_transaction=progress,
        due_proof=due_proof,
        shared_projection_execution=shared,
        assessment_execution=assessment_execution,
        assessment=assessment,
        authorization=authorization,
        receipt=receipt,
    )


__all__ = (
    "AUTONOMOUS_OBSERVATION_VERSION",
    "RuntimeAutonomousObservationTransaction",
    "execute_runtime_autonomous_observation_transaction",
)
