"""Legacy conversation action protocol kept outside the neutral framework.

This compatibility contract preserves the existing required-reply and
autonomous-opening product path while new package-neutral compositions migrate
to :mod:`framework.action_protocol`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from framework.model_api import model_data_fingerprint
from framework.model_api.identity import receipt_fact_identity
from framework.model_api.action import ActionAssessment
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimePositionState,
    RuntimeSessionBinding,
)
from framework.temporal_domain import ObservationDueProof


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeKernelContractError(
            f"{field_name} must be a non-empty identifier")
    return normalized


@dataclass(frozen=True)
class RuntimeActionAuthorization:
    """Compatibility permission for the conversation product path."""

    session_binding_fingerprint: str
    mode: str
    conversation_id: str | None
    participant_id: str
    source_event_id: str
    source_position_fingerprint: str
    source_runtime_ordinal: int
    source_evidence_fingerprint: str
    observation_due_fingerprint: str | None
    action_assessment_receipt_fingerprint: str | None
    fingerprint: str

    @classmethod
    def create(
        cls, *, session_binding: RuntimeSessionBinding, mode: str,
        conversation_id: str | None, participant_id: str,
        source_event_id: str, current_position: RuntimePositionState,
        source_evidence_fingerprint: str,
        observation_due_fingerprint: str | None = None,
        action_assessment_receipt_fingerprint: str | None = None,
    ) -> "RuntimeActionAuthorization":
        session_binding.verify_integrity()
        current_position.verify_integrity(session_binding)
        if mode not in {"required_reply", "autonomous_opening"}:
            raise RuntimeKernelContractError("unknown action authorization mode")
        participant_id = _identifier(participant_id, "participant_id")
        source_event_id = _identifier(source_event_id, "source_event_id")
        source_evidence_fingerprint = _identifier(
            source_evidence_fingerprint, "source_evidence_fingerprint")
        if conversation_id is not None:
            conversation_id = _identifier(conversation_id, "conversation_id")
        if mode == "required_reply":
            if conversation_id is None:
                raise RuntimeKernelContractError(
                    "required reply authorization requires conversation identity")
            if (observation_due_fingerprint is not None
                    or action_assessment_receipt_fingerprint is not None):
                raise RuntimeKernelContractError(
                    "required reply authorization cannot carry autonomous evidence")
        else:
            observation_due_fingerprint = _identifier(
                observation_due_fingerprint, "observation_due_fingerprint")
            action_assessment_receipt_fingerprint = _identifier(
                action_assessment_receipt_fingerprint,
                "action_assessment_receipt_fingerprint")
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "mode": mode,
            "conversation_id": conversation_id,
            "participant_id": participant_id,
            "source_event_id": source_event_id,
            "source_position_fingerprint": current_position.position.fingerprint,
            "source_runtime_ordinal": current_position.runtime_ordinal,
            "source_evidence_fingerprint": source_evidence_fingerprint,
            "observation_due_fingerprint": observation_due_fingerprint,
            "action_assessment_receipt_fingerprint": (
                action_assessment_receipt_fingerprint),
        }
        return cls(**material, fingerprint=model_data_fingerprint(material))

    def verify_integrity(
        self, *, session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
    ) -> None:
        expected = RuntimeActionAuthorization.create(
            session_binding=session_binding, mode=self.mode,
            conversation_id=self.conversation_id,
            participant_id=self.participant_id,
            source_event_id=self.source_event_id,
            current_position=current_position,
            source_evidence_fingerprint=self.source_evidence_fingerprint,
            observation_due_fingerprint=self.observation_due_fingerprint,
            action_assessment_receipt_fingerprint=(
                self.action_assessment_receipt_fingerprint),
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "action authorization integrity or current position is invalid")


class RuntimeActionAuthorizationAuthority:
    """Compatibility validator for conversation product action sources."""

    def __init__(self, *, session_binding: RuntimeSessionBinding) -> None:
        if not isinstance(session_binding, RuntimeSessionBinding):
            raise RuntimeKernelContractError(
                "action authorization requires RuntimeSessionBinding")
        session_binding.verify_integrity()
        self.session_binding = session_binding

    def authorize_required_reply(
        self, *, current_position: RuntimePositionState,
        conversation_id: str, participant_id: str, source_event_id: str,
        response_policy: str, source_evidence_fingerprint: str,
    ) -> RuntimeActionAuthorization:
        if response_policy != "required":
            raise RuntimeKernelContractError(
                "direct reply authorization requires committed required policy")
        return RuntimeActionAuthorization.create(
            session_binding=self.session_binding, mode="required_reply",
            conversation_id=conversation_id, participant_id=participant_id,
            source_event_id=source_event_id,
            current_position=current_position,
            source_evidence_fingerprint=source_evidence_fingerprint,
        )

    def authorize_autonomous_opening(
        self, *, current_position: RuntimePositionState,
        due_proof: ObservationDueProof,
        assessment: ActionAssessment,
        accepted_action_kind: str = "autonomous_opening",
    ) -> RuntimeActionAuthorization:
        current_position.verify_integrity(self.session_binding)
        if not isinstance(due_proof, ObservationDueProof):
            raise RuntimeKernelContractError(
                "autonomous authorization requires observation due proof")
        try:
            due_proof.verify_integrity()
        except (TypeError, ValueError) as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        if due_proof.binding != self.session_binding.temporal_binding:
            raise RuntimeKernelContractError(
                "observation due proof temporal binding differs from session")
        if (due_proof.current_order.to_position.fingerprint
                != current_position.position.fingerprint):
            raise RuntimeKernelContractError(
                "observation due proof differs from current position")
        if not due_proof.due:
            raise RuntimeKernelContractError("autonomous observation is not due")
        if not isinstance(assessment, ActionAssessment):
            raise RuntimeKernelContractError(
                "autonomous authorization requires action assessment")
        accepted_action_kind = _identifier(
            accepted_action_kind, "accepted_action_kind")
        if assessment.action_kind != accepted_action_kind:
            raise RuntimeKernelContractError(
                "autonomous authorization action kind is not mapped by product")
        if not assessment.eligible:
            raise RuntimeKernelContractError(
                "autonomous opening assessment is not eligible")
        receipt = assessment.stage_receipt
        identity = receipt_fact_identity(receipt)
        binding = self.session_binding.model_binding
        if (
            receipt.stage != "assess_action"
            or receipt.session_id != self.session_binding.session_id
            or receipt.package_id != binding.package_id
            or receipt.package_version != binding.package_version
            or receipt.schema_fingerprint != binding.schema_fingerprint
            or receipt.parameter_fingerprint != binding.parameter_fingerprint
            or receipt.operator_version
            != binding.operator_versions.get("assess_action")
        ):
            raise RuntimeKernelContractError(
                "autonomous assessment model binding is invalid")
        if (
            identity.action_ref is not None or identity.subject_ref is None
            or identity.event_ref is None or receipt.temporal_position is None
            or receipt.runtime_ordinal is None
            or receipt.temporal_position.fingerprint
            != current_position.position.fingerprint
            or receipt.runtime_ordinal != current_position.runtime_ordinal
        ):
            raise RuntimeKernelContractError(
                "autonomous assessment tick or position identity is invalid")
        receipt_fingerprint = model_data_fingerprint(receipt.to_record())
        source_fingerprint = model_data_fingerprint({
            "due_proof_fingerprint": due_proof.fingerprint,
            "assessment_receipt_fingerprint": receipt_fingerprint,
        })
        return RuntimeActionAuthorization.create(
            session_binding=self.session_binding, mode="autonomous_opening",
            conversation_id=identity.scope_ref,
            participant_id=identity.subject_ref,
            source_event_id=identity.event_ref,
            current_position=current_position,
            source_evidence_fingerprint=source_fingerprint,
            observation_due_fingerprint=due_proof.fingerprint,
            action_assessment_receipt_fingerprint=receipt_fingerprint,
        )


@dataclass(frozen=True)
class RuntimeActionCommitPlan:
    """Compatibility plan for one conversation Writer candidate."""

    session_binding_fingerprint: str
    authorization_fingerprint: str
    action_id: str
    conversation_id: str
    participant_id: str
    source_event_id: str
    source_position_fingerprint: str
    source_runtime_ordinal: int
    writer_request_id: str
    packet_fingerprint: str
    candidate_fingerprint: str
    destination_evidence_fingerprint: str | None
    fingerprint: str

    def fingerprint_material(self) -> dict[str, Any]:
        material = {key: value for key, value in vars(self).items()
                    if key != "fingerprint"}
        if material.get("destination_evidence_fingerprint") is None:
            material.pop("destination_evidence_fingerprint", None)
        return material

    @classmethod
    def create(
        cls, *, session_binding: RuntimeSessionBinding,
        authorization: RuntimeActionAuthorization,
        current_position: RuntimePositionState, conversation_id: str,
        participant_id: str, source_event_id: str, writer_request_id: str,
        packet_fingerprint: str, candidate_fingerprint: str,
        destination_evidence_fingerprint: str | None = None,
    ) -> "RuntimeActionCommitPlan":
        session_binding.verify_integrity()
        current_position.verify_integrity(session_binding)
        authorization.verify_integrity(
            session_binding=session_binding,
            current_position=current_position)
        conversation_id = _identifier(conversation_id, "conversation_id")
        participant_id = _identifier(participant_id, "participant_id")
        source_event_id = _identifier(source_event_id, "source_event_id")
        writer_request_id = _identifier(writer_request_id, "writer_request_id")
        packet_fingerprint = _identifier(
            packet_fingerprint, "packet_fingerprint")
        candidate_fingerprint = _identifier(
            candidate_fingerprint, "candidate_fingerprint")
        if (authorization.participant_id != participant_id
                or authorization.source_event_id != source_event_id):
            raise RuntimeKernelContractError(
                "action commit identity differs from authorization")
        if authorization.mode == "required_reply":
            if authorization.conversation_id != conversation_id:
                raise RuntimeKernelContractError(
                    "required action conversation differs from authorization")
            if destination_evidence_fingerprint is not None:
                raise RuntimeKernelContractError(
                    "required action cannot carry destination evidence")
        elif authorization.mode == "autonomous_opening":
            if authorization.conversation_id is not None:
                raise RuntimeKernelContractError(
                    "autonomous grant cannot preselect a conversation")
            destination_evidence_fingerprint = _identifier(
                destination_evidence_fingerprint,
                "destination_evidence_fingerprint")
        else:
            raise RuntimeKernelContractError("unknown action authorization mode")
        identity_material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "authorization_fingerprint": authorization.fingerprint,
            "conversation_id": conversation_id,
            "participant_id": participant_id,
            "source_event_id": source_event_id,
            "source_position_fingerprint": current_position.position.fingerprint,
            "source_runtime_ordinal": current_position.runtime_ordinal,
            "writer_request_id": writer_request_id,
            "packet_fingerprint": packet_fingerprint,
            "candidate_fingerprint": candidate_fingerprint,
        }
        if destination_evidence_fingerprint is not None:
            identity_material["destination_evidence_fingerprint"] = (
                destination_evidence_fingerprint)
        action_id = "action:" + model_data_fingerprint(identity_material)
        material = dict(identity_material, action_id=action_id)
        constructor = dict(material)
        constructor["destination_evidence_fingerprint"] = (
            destination_evidence_fingerprint)
        return cls(**constructor, fingerprint=model_data_fingerprint(material))

    def verify_integrity(
        self, *, session_binding: RuntimeSessionBinding,
        authorization: RuntimeActionAuthorization,
        current_position: RuntimePositionState,
    ) -> None:
        expected = RuntimeActionCommitPlan.create(
            session_binding=session_binding, authorization=authorization,
            current_position=current_position,
            conversation_id=self.conversation_id,
            participant_id=self.participant_id,
            source_event_id=self.source_event_id,
            writer_request_id=self.writer_request_id,
            packet_fingerprint=self.packet_fingerprint,
            candidate_fingerprint=self.candidate_fingerprint,
            destination_evidence_fingerprint=(
                self.destination_evidence_fingerprint),
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "action commit plan integrity is invalid")


class RuntimeActionCommitAuthority:
    """Compatibility planner for the conversation persistence host."""

    def __init__(self, *, session_binding: RuntimeSessionBinding) -> None:
        if not isinstance(session_binding, RuntimeSessionBinding):
            raise RuntimeKernelContractError(
                "action commit authority requires RuntimeSessionBinding")
        session_binding.verify_integrity()
        self.session_binding = session_binding

    def prepare_required_action(self, **kwargs) -> RuntimeActionCommitPlan:
        return RuntimeActionCommitPlan.create(
            session_binding=self.session_binding, **kwargs)

    def prepare_autonomous_action(self, **kwargs) -> RuntimeActionCommitPlan:
        return RuntimeActionCommitPlan.create(
            session_binding=self.session_binding, **kwargs)


__all__ = (
    "RuntimeActionAuthorization",
    "RuntimeActionAuthorizationAuthority",
    "RuntimeActionCommitAuthority",
    "RuntimeActionCommitPlan",
)
