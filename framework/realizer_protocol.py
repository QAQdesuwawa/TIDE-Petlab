"""Opaque realization request, attempt, and candidate records.

This protocol freezes lineage around an implementation call.  It does not
interpret model control data, factual material, or candidate contents, and it
owns no state, history, action, or commit authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.action_protocol import ActionAuthorization
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


REALIZER_PROTOCOL_VERSION = "framework-realizer-protocol-v1"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeKernelContractError(
            f"{field} must be a non-empty identifier")
    return normalized


def _opaque(
    value: object, field: str, *, allow_empty: bool,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or (not allow_empty and not value):
        qualifier = "" if allow_empty else " non-empty"
        raise RuntimeKernelContractError(
            f"{field} must be{qualifier} structured data")
    return freeze_model_payload(value)


@dataclass(frozen=True)
class RealizerRequest:
    version: str
    request_ref: str
    session_binding_fingerprint: str
    authorization_fingerprint: str
    action_kind: str
    subject_ref: str
    source_fact_ref: str
    source_settlement_fingerprint: str
    source_position_fingerprint: str
    source_runtime_ordinal: int
    target_ref: str
    control: Mapping[str, Any]
    readonly_facts: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
        authorization: ActionAuthorization,
        control: Mapping[str, Any],
        readonly_facts: Mapping[str, Any],
    ) -> "RealizerRequest":
        authorization.verify_integrity(
            session_binding=session_binding,
            current_position=current_position,
        )
        frozen_control = _opaque(control, "control", allow_empty=False)
        frozen_facts = _opaque(
            readonly_facts, "readonly_facts", allow_empty=True)
        material = {
            "version": REALIZER_PROTOCOL_VERSION,
            "session_binding_fingerprint": session_binding.fingerprint,
            "authorization_fingerprint": authorization.fingerprint,
            "action_kind": authorization.action_kind,
            "subject_ref": authorization.subject_ref,
            "source_fact_ref": authorization.source_fact_ref,
            "source_settlement_fingerprint": (
                authorization.source_settlement_fingerprint),
            "source_position_fingerprint": (
                current_position.position.fingerprint),
            "source_runtime_ordinal": current_position.runtime_ordinal,
            "target_ref": authorization.target_ref,
            "control": thaw_model_data(frozen_control),
            "readonly_facts": thaw_model_data(frozen_facts),
        }
        request_ref = "realizer-request:" + model_data_fingerprint(material)
        signed = dict(material, request_ref=request_ref)
        return cls(
            **{
                **signed,
                "control": frozen_control,
                "readonly_facts": frozen_facts,
            },
            fingerprint=model_data_fingerprint(signed),
        )

    def verify_integrity(
        self,
        *,
        session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
        authorization: ActionAuthorization,
    ) -> None:
        expected = RealizerRequest.create(
            session_binding=session_binding,
            current_position=current_position,
            authorization=authorization,
            control=self.control,
            readonly_facts=self.readonly_facts,
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "realizer request integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "request_ref": self.request_ref,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "authorization_fingerprint": self.authorization_fingerprint,
            "action_kind": self.action_kind,
            "subject_ref": self.subject_ref,
            "source_fact_ref": self.source_fact_ref,
            "source_settlement_fingerprint": (
                self.source_settlement_fingerprint),
            "source_position_fingerprint": self.source_position_fingerprint,
            "source_runtime_ordinal": self.source_runtime_ordinal,
            "target_ref": self.target_ref,
            "control": thaw_model_data(self.control),
            "readonly_facts": thaw_model_data(self.readonly_facts),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RealizerAttempt:
    version: str
    attempt_ref: str
    request_fingerprint: str
    implementation_ref: str
    invocation_evidence: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        request: RealizerRequest,
        implementation_ref: str,
        invocation_evidence: Mapping[str, Any],
    ) -> "RealizerAttempt":
        if not isinstance(request, RealizerRequest):
            raise RuntimeKernelContractError(
                "realizer attempt requires a request")
        implementation = _identifier(
            implementation_ref, "implementation_ref")
        evidence = _opaque(
            invocation_evidence, "invocation_evidence", allow_empty=False)
        material = {
            "version": REALIZER_PROTOCOL_VERSION,
            "request_fingerprint": request.fingerprint,
            "implementation_ref": implementation,
            "invocation_evidence": thaw_model_data(evidence),
        }
        attempt_ref = "realizer-attempt:" + model_data_fingerprint(material)
        signed = dict(material, attempt_ref=attempt_ref)
        return cls(
            **{**signed, "invocation_evidence": evidence},
            fingerprint=model_data_fingerprint(signed),
        )

    def verify_integrity(self, *, request: RealizerRequest) -> None:
        expected = RealizerAttempt.create(
            request=request,
            implementation_ref=self.implementation_ref,
            invocation_evidence=self.invocation_evidence,
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "realizer attempt integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "attempt_ref": self.attempt_ref,
            "request_fingerprint": self.request_fingerprint,
            "implementation_ref": self.implementation_ref,
            "invocation_evidence": thaw_model_data(
                self.invocation_evidence),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RealizerFailure:
    """Content-free terminal evidence for one unsuccessful attempt."""

    version: str
    failure_ref: str
    request_fingerprint: str
    attempt_fingerprint: str
    failure_kind: str
    failure_source_ref: str
    fingerprint: str

    @classmethod
    def create(
        cls, *, request: RealizerRequest, attempt: RealizerAttempt,
        failure_kind: str, failure_source_ref: str,
    ) -> "RealizerFailure":
        attempt.verify_integrity(request=request)
        material = {
            "version": REALIZER_PROTOCOL_VERSION,
            "request_fingerprint": request.fingerprint,
            "attempt_fingerprint": attempt.fingerprint,
            "failure_kind": _identifier(failure_kind, "failure_kind"),
            "failure_source_ref": _identifier(
                failure_source_ref, "failure_source_ref"),
        }
        failure_ref = "realizer-failure:" + model_data_fingerprint(material)
        signed = dict(material, failure_ref=failure_ref)
        return cls(**signed, fingerprint=model_data_fingerprint(signed))

    def verify_integrity(
        self, *, request: RealizerRequest, attempt: RealizerAttempt,
    ) -> None:
        expected = RealizerFailure.create(
            request=request,
            attempt=attempt,
            failure_kind=self.failure_kind,
            failure_source_ref=self.failure_source_ref,
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "realizer failure integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "failure_ref": self.failure_ref,
            "request_fingerprint": self.request_fingerprint,
            "attempt_fingerprint": self.attempt_fingerprint,
            "failure_kind": self.failure_kind,
            "failure_source_ref": self.failure_source_ref,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RealizerCandidate:
    version: str
    candidate_ref: str
    request_fingerprint: str
    attempt_fingerprint: str
    candidate_payload: Mapping[str, Any]
    realization_evidence: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        request: RealizerRequest,
        attempt: RealizerAttempt,
        candidate_payload: Mapping[str, Any],
        realization_evidence: Mapping[str, Any],
    ) -> "RealizerCandidate":
        attempt.verify_integrity(request=request)
        payload = _opaque(
            candidate_payload, "candidate_payload", allow_empty=False)
        evidence = _opaque(
            realization_evidence, "realization_evidence", allow_empty=False)
        material = {
            "version": REALIZER_PROTOCOL_VERSION,
            "request_fingerprint": request.fingerprint,
            "attempt_fingerprint": attempt.fingerprint,
            "candidate_payload": thaw_model_data(payload),
            "realization_evidence": thaw_model_data(evidence),
        }
        candidate_ref = (
            "realizer-candidate:" + model_data_fingerprint(material))
        signed = dict(material, candidate_ref=candidate_ref)
        return cls(
            **{
                **signed,
                "candidate_payload": payload,
                "realization_evidence": evidence,
            },
            fingerprint=model_data_fingerprint(signed),
        )

    def verify_integrity(
        self, *, request: RealizerRequest, attempt: RealizerAttempt,
    ) -> None:
        expected = RealizerCandidate.create(
            request=request,
            attempt=attempt,
            candidate_payload=self.candidate_payload,
            realization_evidence=self.realization_evidence,
        )
        if expected != self:
            raise RuntimeKernelContractError(
                "realizer candidate integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "candidate_ref": self.candidate_ref,
            "request_fingerprint": self.request_fingerprint,
            "attempt_fingerprint": self.attempt_fingerprint,
            "candidate_payload": thaw_model_data(self.candidate_payload),
            "realization_evidence": thaw_model_data(
                self.realization_evidence),
            "fingerprint": self.fingerprint,
        }


__all__ = (
    "REALIZER_PROTOCOL_VERSION",
    "RealizerAttempt",
    "RealizerCandidate",
    "RealizerFailure",
    "RealizerRequest",
)
