"""Recoverable immutable grant emitted by one committed eligible tick.

The grant is not a second authorization or a pending table.  It is the narrow
downstream projection of products already committed in the observation-tick
journal, sufficient to resume Writer work without rerunning time or model
assessment.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelStageExecution,
    ModelStageReceipt,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import receipt_fact_identity
from framework.model_api.invocation_v2 import ModelStageReceiptV2
from framework.model_api.action import ActionAssessment, read_action_assessment
from interaction_extensions.conversation_action_protocol import (
    RuntimeActionAuthorization,
)
from ops.runtime_autonomous_observation import (
    RuntimeAutonomousObservationTransaction,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


@dataclass(frozen=True)
class RuntimeCommittedAutonomousGrant:
    session: BootstrappedRuntimeSession
    settlement_execution: ModelStageExecution
    shared_projection_execution: ModelStageExecution
    assessment_execution: ModelStageExecution
    assessment: ActionAssessment
    authorization: RuntimeActionAuthorization
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _verified_receipt(receipt: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise ValueError(f"{label} must be record data")
    record = dict(thaw_model_data(receipt))
    fingerprint = record.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(record):
        raise ValueError(f"{label} fingerprint is invalid")
    record["fingerprint"] = fingerprint
    return record


def _stage_execution(
    *,
    session: BootstrappedRuntimeSession,
    receipt_record: Mapping[str, Any],
    payload: Mapping[str, Any],
    expected_stage: str,
    participant_id: str,
    event_id: str,
) -> ModelStageExecution:
    if not isinstance(receipt_record, Mapping) or not isinstance(payload, Mapping):
        raise ValueError(f"committed {expected_stage} product is invalid")
    record = dict(thaw_model_data(receipt_record))
    temporal = record.pop("temporal_position", None)
    position = session.position.position
    if (
        not isinstance(temporal, Mapping)
        or temporal.get("fingerprint") != position.fingerprint
    ):
        raise ValueError(f"committed {expected_stage} position is invalid")
    stage_ordinal = record.get("runtime_ordinal")
    expected_ordinal = (
        session.position.runtime_ordinal - 1
        if expected_stage == "settle_state"
        else session.position.runtime_ordinal
    )
    if stage_ordinal != expected_ordinal:
        raise ValueError(f"committed {expected_stage} ordinal is invalid")
    common = {
        "package_id": record.get("package_id"),
        "package_version": record.get("package_version"),
        "stage": record.get("stage"),
        "operator_version": record.get("operator_version"),
        "schema_fingerprint": record.get("schema_fingerprint"),
        "parameter_fingerprint": record.get("parameter_fingerprint"),
        "session_id": record.get("session_id"),
        "input_fingerprint": record.get("input_fingerprint"),
        "output_fingerprint": record.get("output_fingerprint"),
        "temporal_position": position,
        "runtime_ordinal": stage_ordinal,
    }
    if "scope_ref" in record:
        receipt = ModelStageReceiptV2(
            **common,
            scope_ref=record.get("scope_ref"),
            subject_ref=record.get("subject_ref"),
            event_ref=record.get("event_ref"),
            action_ref=record.get("action_ref"),
        )
    else:
        receipt = ModelStageReceipt(
            **common,
            conversation_id=record.get("conversation_id"),
            participant_id=record.get("participant_id"),
            event_id=record.get("event_id"),
            action_id=record.get("action_id"),
            at_ms=record.get("at_ms"),
        )
    frozen_payload = freeze_model_payload(payload)
    binding = session.session_binding.model_binding
    identity = receipt_fact_identity(receipt)
    if (
        receipt.stage != expected_stage
        or receipt.session_id != session.session_binding.session_id
        or receipt.package_id != binding.package_id
        or receipt.package_version != binding.package_version
        or receipt.schema_fingerprint != binding.schema_fingerprint
        or receipt.parameter_fingerprint != binding.parameter_fingerprint
        or receipt.operator_version
        != binding.operator_versions.get(expected_stage)
        or identity.scope_ref is not None
        or identity.subject_ref != participant_id
        or identity.event_ref != event_id
        or identity.action_ref is not None
        or receipt.output_fingerprint != model_data_fingerprint(frozen_payload)
    ):
        raise ValueError(f"committed {expected_stage} lineage is invalid")
    return ModelStageExecution(payload=frozen_payload, receipt=receipt)


def grant_from_observation(
    observation: RuntimeAutonomousObservationTransaction,
) -> RuntimeCommittedAutonomousGrant:
    if not isinstance(observation, RuntimeAutonomousObservationTransaction):
        raise TypeError("committed grant requires formal observation")
    if observation.authorization is None or not observation.assessment.eligible:
        raise ValueError("committed grant requires eligible authorization")
    return RuntimeCommittedAutonomousGrant(
        session=observation.session,
        settlement_execution=observation.progress_transaction.settlement_execution,
        shared_projection_execution=observation.shared_projection_execution,
        assessment_execution=observation.assessment_execution,
        assessment=observation.assessment,
        authorization=observation.authorization,
        receipt=observation.receipt,
    )


def restore_committed_autonomous_grant(
    *,
    session: BootstrappedRuntimeSession,
    row: Mapping[str, Any],
) -> RuntimeCommittedAutonomousGrant:
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("committed grant requires runtime session")
    tick = _verified_receipt(row, "committed observation tick")
    observation_receipt = _verified_receipt(
        tick.get("observation_receipt"), "committed observation receipt")
    if (
        observation_receipt.get("eligible") is not True
        or observation_receipt.get("session_binding_fingerprint")
        != session.session_binding.fingerprint
        or observation_receipt.get("settled_position_fingerprint")
        != session.position.position.fingerprint
        or observation_receipt.get("settled_runtime_ordinal")
        != session.position.runtime_ordinal
    ):
        raise ValueError("committed observation grant differs from current session")
    settlement = _stage_execution(
        session=session,
        receipt_record=tick.get("settlement_stage_receipt"),
        payload=tick.get("settlement_output"),
        expected_stage="settle_state",
        participant_id=str(tick.get("participant_id") or ""),
        event_id=str(tick.get("tick_event_id") or ""),
    )
    shared = _stage_execution(
        session=session,
        receipt_record=tick.get("shared_projection_stage_receipt"),
        payload=tick.get("shared_projection_output"),
        expected_stage="project_shared_semantics",
        participant_id=str(tick.get("participant_id") or ""),
        event_id=str(tick.get("tick_event_id") or ""),
    )
    assessment_execution = _stage_execution(
        session=session,
        receipt_record=tick.get("assessment_stage_receipt"),
        payload=tick.get("assessment_output"),
        expected_stage="assess_action",
        participant_id=str(tick.get("participant_id") or ""),
        event_id=str(tick.get("tick_event_id") or ""),
    )
    tick_event_id = str(tick.get("tick_event_id") or "")
    settlement_value = settlement.payload.get("settlement")
    if (
        not isinstance(settlement_value, Mapping)
        or model_data_fingerprint(settlement_value.get("state_candidate"))
        != model_data_fingerprint(session.state.state)
    ):
        raise ValueError("committed settlement differs from current state")
    shared_input = {
        "parameters": session.parameters.parameters,
        "settlement": settlement_value,
    }
    if shared.receipt.input_fingerprint != model_data_fingerprint(shared_input):
        raise ValueError("committed shared projection input is invalid")
    assessment = read_action_assessment(
        assessment_execution,
        source_fact_ref=tick_event_id,
        source_position=session.position.position,
        source_runtime_ordinal=session.position.runtime_ordinal,
    )
    if not assessment.eligible:
        raise ValueError("committed observation grant is not eligible")
    raw_authorization = tick.get("authorization")
    if not isinstance(raw_authorization, Mapping):
        raise ValueError("committed observation authorization is missing")
    authorization = RuntimeActionAuthorization(**dict(raw_authorization))
    authorization.verify_integrity(
        session_binding=session.session_binding,
        current_position=session.position,
    )
    if (
        observation_receipt.get("authorization_fingerprint")
        != authorization.fingerprint
        or authorization.source_event_id != tick_event_id
        or authorization.action_assessment_receipt_fingerprint
        != model_data_fingerprint(assessment_execution.receipt.to_record())
    ):
        raise ValueError("committed observation authorization lineage is invalid")
    return RuntimeCommittedAutonomousGrant(
        session=session,
        settlement_execution=settlement,
        shared_projection_execution=shared,
        assessment_execution=assessment_execution,
        assessment=assessment,
        authorization=authorization,
        receipt=observation_receipt,
    )


def require_committed_autonomous_grant(value) -> RuntimeCommittedAutonomousGrant:
    if isinstance(value, RuntimeCommittedAutonomousGrant):
        return value
    if isinstance(value, RuntimeAutonomousObservationTransaction):
        return grant_from_observation(value)
    raise TypeError("autonomous lifecycle requires a committed grant")


__all__ = (
    "RuntimeCommittedAutonomousGrant",
    "grant_from_observation",
    "require_committed_autonomous_grant",
    "restore_committed_autonomous_grant",
)
