"""Model-neutral action authorization and commit planning protocol.

The framework validates lineage and immutable evidence only.  Interaction
Models define action kinds and eligibility; hosts define target authorization;
Realizers produce attempts and candidates.  No product action vocabulary is
defined here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimePositionState,
    RuntimeSessionBinding,
)


ACTION_PROTOCOL_VERSION = "framework-action-protocol-v1"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeKernelContractError(
            f"{field} must be a non-empty identifier")
    return normalized


def _evidence(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise RuntimeKernelContractError(
            f"{field} must be non-empty opaque evidence")
    return freeze_model_payload(value)


@dataclass(frozen=True)
class ActionAuthorization:
    version: str
    session_binding_fingerprint: str
    action_kind: str
    subject_ref: str
    source_fact_ref: str
    source_settlement_fingerprint: str
    source_position_fingerprint: str
    source_runtime_ordinal: int
    target_ref: str
    package_eligibility_evidence: Mapping[str, Any]
    host_authorization_evidence: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
        action_kind: str,
        subject_ref: str,
        source_fact_ref: str,
        source_settlement_fingerprint: str,
        target_ref: str,
        package_eligibility_evidence: Mapping[str, Any],
        host_authorization_evidence: Mapping[str, Any],
    ) -> "ActionAuthorization":
        session_binding.verify_integrity()
        current_position.verify_integrity(session_binding)
        material = {
            "version": ACTION_PROTOCOL_VERSION,
            "session_binding_fingerprint": session_binding.fingerprint,
            "action_kind": _identifier(action_kind, "action_kind"),
            "subject_ref": _identifier(subject_ref, "subject_ref"),
            "source_fact_ref": _identifier(
                source_fact_ref, "source_fact_ref"),
            "source_settlement_fingerprint": _identifier(
                source_settlement_fingerprint,
                "source_settlement_fingerprint",
            ),
            "source_position_fingerprint": (
                current_position.position.fingerprint),
            "source_runtime_ordinal": current_position.runtime_ordinal,
            "target_ref": _identifier(target_ref, "target_ref"),
            "package_eligibility_evidence": thaw_model_data(_evidence(
                package_eligibility_evidence,
                "package_eligibility_evidence",
            )),
            "host_authorization_evidence": thaw_model_data(_evidence(
                host_authorization_evidence,
                "host_authorization_evidence",
            )),
        }
        constructor = dict(material)
        constructor["package_eligibility_evidence"] = freeze_model_payload(
            material["package_eligibility_evidence"])
        constructor["host_authorization_evidence"] = freeze_model_payload(
            material["host_authorization_evidence"])
        return cls(
            **constructor,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(
        self,
        *,
        session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
    ) -> None:
        expected = ActionAuthorization.create(
            session_binding=session_binding,
            current_position=current_position,
            action_kind=self.action_kind,
            subject_ref=self.subject_ref,
            source_fact_ref=self.source_fact_ref,
            source_settlement_fingerprint=(
                self.source_settlement_fingerprint),
            target_ref=self.target_ref,
            package_eligibility_evidence=(
                self.package_eligibility_evidence),
            host_authorization_evidence=self.host_authorization_evidence,
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "action authorization integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "action_kind": self.action_kind,
            "subject_ref": self.subject_ref,
            "source_fact_ref": self.source_fact_ref,
            "source_settlement_fingerprint": (
                self.source_settlement_fingerprint),
            "source_position_fingerprint": self.source_position_fingerprint,
            "source_runtime_ordinal": self.source_runtime_ordinal,
            "target_ref": self.target_ref,
            "package_eligibility_evidence": thaw_model_data(
                self.package_eligibility_evidence),
            "host_authorization_evidence": thaw_model_data(
                self.host_authorization_evidence),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ActionCommitPlan:
    version: str
    authorization_fingerprint: str
    action_id: str
    action_kind: str
    subject_ref: str
    source_fact_ref: str
    source_position_fingerprint: str
    source_runtime_ordinal: int
    target_ref: str
    realizer_attempt_ref: str
    candidate_ref: str
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
        authorization: ActionAuthorization,
        action_kind: str,
        subject_ref: str,
        source_fact_ref: str,
        target_ref: str,
        realizer_attempt_ref: str,
        candidate_ref: str,
    ) -> "ActionCommitPlan":
        authorization.verify_integrity(
            session_binding=session_binding,
            current_position=current_position,
        )
        identities = {
            "action_kind": _identifier(action_kind, "action_kind"),
            "subject_ref": _identifier(subject_ref, "subject_ref"),
            "source_fact_ref": _identifier(
                source_fact_ref, "source_fact_ref"),
            "target_ref": _identifier(target_ref, "target_ref"),
        }
        if any(
            identities[field] != getattr(authorization, field)
            for field in identities
        ):
            raise RuntimeKernelContractError(
                "action commit identity differs from authorization")
        material = {
            "version": ACTION_PROTOCOL_VERSION,
            "authorization_fingerprint": authorization.fingerprint,
            **identities,
            "source_position_fingerprint": (
                current_position.position.fingerprint),
            "source_runtime_ordinal": current_position.runtime_ordinal,
            "realizer_attempt_ref": _identifier(
                realizer_attempt_ref, "realizer_attempt_ref"),
            "candidate_ref": _identifier(candidate_ref, "candidate_ref"),
        }
        action_id = "action:" + model_data_fingerprint(material)
        signed = dict(material, action_id=action_id)
        return cls(**signed, fingerprint=model_data_fingerprint(signed))

    def verify_integrity(
        self,
        *,
        session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
        authorization: ActionAuthorization,
    ) -> None:
        expected = ActionCommitPlan.create(
            session_binding=session_binding,
            current_position=current_position,
            authorization=authorization,
            action_kind=self.action_kind,
            subject_ref=self.subject_ref,
            source_fact_ref=self.source_fact_ref,
            target_ref=self.target_ref,
            realizer_attempt_ref=self.realizer_attempt_ref,
            candidate_ref=self.candidate_ref,
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "action commit plan integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "authorization_fingerprint": self.authorization_fingerprint,
            "action_id": self.action_id,
            "action_kind": self.action_kind,
            "subject_ref": self.subject_ref,
            "source_fact_ref": self.source_fact_ref,
            "source_position_fingerprint": (
                self.source_position_fingerprint),
            "source_runtime_ordinal": self.source_runtime_ordinal,
            "target_ref": self.target_ref,
            "realizer_attempt_ref": self.realizer_attempt_ref,
            "candidate_ref": self.candidate_ref,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ActionCommitReceipt:
    """Read-only proof that one plan resolved to one committed fact."""

    version: str
    session_binding_fingerprint: str
    authorization_fingerprint: str
    commit_plan_fingerprint: str
    action_id: str
    candidate_ref: str
    committed_fact_ref: str
    committed_fact_fingerprint: str
    source_position_fingerprint: str
    source_runtime_ordinal: int
    fingerprint: str

    @classmethod
    def create(
        cls, *, session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
        authorization: ActionAuthorization,
        commit_plan: ActionCommitPlan,
        committed_fact_ref: str,
        committed_fact_fingerprint: str,
    ) -> "ActionCommitReceipt":
        commit_plan.verify_integrity(
            session_binding=session_binding,
            current_position=current_position,
            authorization=authorization,
        )
        fact_ref = _identifier(committed_fact_ref, "committed_fact_ref")
        if fact_ref != commit_plan.action_id:
            raise RuntimeKernelContractError(
                "committed fact identity differs from action plan")
        material = {
            "version": ACTION_PROTOCOL_VERSION,
            "session_binding_fingerprint": session_binding.fingerprint,
            "authorization_fingerprint": authorization.fingerprint,
            "commit_plan_fingerprint": commit_plan.fingerprint,
            "action_id": commit_plan.action_id,
            "candidate_ref": commit_plan.candidate_ref,
            "committed_fact_ref": fact_ref,
            "committed_fact_fingerprint": _identifier(
                committed_fact_fingerprint,
                "committed_fact_fingerprint",
            ),
            "source_position_fingerprint": (
                current_position.position.fingerprint),
            "source_runtime_ordinal": current_position.runtime_ordinal,
        }
        return cls(**material, fingerprint=model_data_fingerprint(material))

    def verify_integrity(
        self, *, session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
        authorization: ActionAuthorization,
        commit_plan: ActionCommitPlan,
    ) -> None:
        expected = ActionCommitReceipt.create(
            session_binding=session_binding,
            current_position=current_position,
            authorization=authorization,
            commit_plan=commit_plan,
            committed_fact_ref=self.committed_fact_ref,
            committed_fact_fingerprint=self.committed_fact_fingerprint,
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "action commit receipt integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "authorization_fingerprint": self.authorization_fingerprint,
            "commit_plan_fingerprint": self.commit_plan_fingerprint,
            "action_id": self.action_id,
            "candidate_ref": self.candidate_ref,
            "committed_fact_ref": self.committed_fact_ref,
            "committed_fact_fingerprint": self.committed_fact_fingerprint,
            "source_position_fingerprint": self.source_position_fingerprint,
            "source_runtime_ordinal": self.source_runtime_ordinal,
            "fingerprint": self.fingerprint,
        }


__all__ = (
    "ACTION_PROTOCOL_VERSION",
    "ActionAuthorization",
    "ActionCommitPlan",
    "ActionCommitReceipt",
)
