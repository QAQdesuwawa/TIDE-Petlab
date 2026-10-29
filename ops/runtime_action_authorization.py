"""Package-neutral host authorization for one assessed action.

The Interaction Model owns ``action_kind`` and eligibility.  This host binds
that read-only assessment to the exact accepted settlement and to opaque host
target evidence.  It does not choose product vocabulary, realize content,
commit an action, route it, or send it.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.action_protocol import ActionAuthorization
from framework.model_api import ModelStageExecution, model_data_fingerprint
from framework.model_api.identity import receipt_fact_identity
from framework.model_api.action import ActionAssessment
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimePositionState,
    RuntimeSessionBinding,
    RuntimeStateCommitAcceptance,
)


def _receipt_fingerprint(execution: ModelStageExecution) -> str:
    if model_data_fingerprint(execution.payload) != execution.receipt.output_fingerprint:
        raise RuntimeKernelContractError(
            "action source payload differs from its stage receipt")
    return model_data_fingerprint(execution.receipt.to_record())


def _verify_model_binding(
    *,
    execution: ModelStageExecution,
    session_binding: RuntimeSessionBinding,
) -> None:
    receipt = execution.receipt
    binding = session_binding.model_binding
    if (
        receipt.session_id != session_binding.session_id
        or receipt.package_id != binding.package_id
        or receipt.package_version != binding.package_version
        or receipt.schema_fingerprint != binding.schema_fingerprint
        or receipt.parameter_fingerprint != binding.parameter_fingerprint
        or receipt.operator_version != binding.operator_versions.get(receipt.stage)
    ):
        raise RuntimeKernelContractError(
            "action source model binding differs from runtime session")


def authorize_runtime_action(
    *,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    assessment: ActionAssessment,
    source_settlement_execution: ModelStageExecution,
    source_settlement_acceptance: RuntimeStateCommitAcceptance,
    target_ref: str,
    host_authorization_evidence: Mapping[str, Any],
) -> ActionAuthorization:
    """Bind package intent and host target permission without product semantics."""
    if not isinstance(session_binding, RuntimeSessionBinding):
        raise RuntimeKernelContractError(
            "action authorization requires RuntimeSessionBinding")
    if not isinstance(current_position, RuntimePositionState):
        raise RuntimeKernelContractError(
            "action authorization requires RuntimePositionState")
    if not isinstance(assessment, ActionAssessment):
        raise RuntimeKernelContractError(
            "action authorization requires ActionAssessment")
    if not isinstance(source_settlement_execution, ModelStageExecution):
        raise RuntimeKernelContractError(
            "action authorization requires settlement execution")
    if not isinstance(source_settlement_acceptance, RuntimeStateCommitAcceptance):
        raise RuntimeKernelContractError(
            "action authorization requires settlement acceptance")
    if not isinstance(host_authorization_evidence, Mapping):
        raise RuntimeKernelContractError(
            "host authorization evidence must be a mapping")

    session_binding.verify_integrity()
    current_position.verify_integrity(session_binding)
    if not assessment.eligible:
        raise RuntimeKernelContractError(
            "ineligible package assessment cannot authorize an action")

    assessment_receipt = assessment.stage_receipt
    settlement_receipt = source_settlement_execution.receipt
    assessment_identity = receipt_fact_identity(assessment_receipt)
    settlement_identity = receipt_fact_identity(settlement_receipt)
    _verify_model_binding(
        execution=source_settlement_execution,
        session_binding=session_binding,
    )
    settlement_receipt_fingerprint = _receipt_fingerprint(
        source_settlement_execution)
    if (
        assessment_receipt.stage != "assess_action"
        or settlement_receipt.stage != "settle_state"
        or assessment_receipt.session_id != session_binding.session_id
        or assessment_receipt.package_id
        != session_binding.model_binding.package_id
        or assessment_receipt.package_version
        != session_binding.model_binding.package_version
        or assessment_receipt.schema_fingerprint
        != session_binding.model_binding.schema_fingerprint
        or assessment_receipt.parameter_fingerprint
        != session_binding.model_binding.parameter_fingerprint
        or assessment_receipt.operator_version
        != session_binding.model_binding.operator_versions.get("assess_action")
        or assessment_identity.event_ref is None
        or assessment_identity.subject_ref is None
        or assessment_identity.action_ref is not None
        or assessment_identity.event_ref != settlement_identity.event_ref
        or assessment_identity.subject_ref != settlement_identity.subject_ref
        or assessment_receipt.temporal_position is None
        or settlement_receipt.temporal_position is None
        or assessment_receipt.temporal_position.fingerprint
        != current_position.position.fingerprint
        or settlement_receipt.temporal_position.fingerprint
        != current_position.position.fingerprint
        or assessment_receipt.runtime_ordinal
        != current_position.runtime_ordinal
        or settlement_receipt.runtime_ordinal is None
        or settlement_receipt.runtime_ordinal + 1
        != current_position.runtime_ordinal
        or source_settlement_acceptance.session_binding_fingerprint
        != session_binding.fingerprint
        or source_settlement_acceptance.stage_receipt_fingerprint
        != settlement_receipt_fingerprint
        or source_settlement_acceptance.position != current_position
    ):
        raise RuntimeKernelContractError(
            "action assessment and settlement do not identify one source fact")

    assessment_receipt_fingerprint = model_data_fingerprint(
        assessment_receipt.to_record())
    package_evidence = {
        "assessment_receipt_fingerprint": assessment_receipt_fingerprint,
        "constraints_fingerprint": model_data_fingerprint(
            assessment.constraints),
        "eligible": True,
    }
    return ActionAuthorization.create(
        session_binding=session_binding,
        current_position=current_position,
        action_kind=assessment.action_kind,
        subject_ref=assessment_identity.subject_ref,
        source_fact_ref=assessment_identity.event_ref,
        source_settlement_fingerprint=settlement_receipt_fingerprint,
        target_ref=target_ref,
        package_eligibility_evidence=package_evidence,
        host_authorization_evidence=host_authorization_evidence,
    )


__all__ = ("authorize_runtime_action",)
