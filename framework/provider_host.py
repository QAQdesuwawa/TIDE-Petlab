"""Opaque provider invocation records with no domain or commit authority."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.runtime_kernel import RuntimePositionState, RuntimeSessionBinding


PROVIDER_HOST_VERSION = "framework-provider-host-v1"


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be a non-empty identifier")
    return result


def _opaque(
    value: object, field: str, *, allow_empty: bool,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or (not allow_empty and not value):
        raise ValueError(f"{field} must be structured data")
    return freeze_model_payload(value)


@dataclass(frozen=True)
class ProviderRequest:
    version: str
    request_ref: str
    session_binding_fingerprint: str
    temporal_position_fingerprint: str
    runtime_ordinal: int
    operation_kind: str
    source_fact_ref: str
    codec_contract: str
    request_payload: Mapping[str, Any]
    transport_options: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "request_payload", freeze_model_payload(
            self.request_payload))
        object.__setattr__(self, "transport_options", freeze_model_payload(
            self.transport_options))

    @classmethod
    def create(
        cls, *, session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState, operation_kind: str,
        source_fact_ref: str, codec_contract: str,
        request_payload: Mapping[str, Any],
        transport_options: Mapping[str, Any],
    ) -> "ProviderRequest":
        if not isinstance(session_binding, RuntimeSessionBinding):
            raise TypeError("provider request requires runtime session binding")
        if not isinstance(current_position, RuntimePositionState):
            raise TypeError("provider request requires runtime position")
        session_binding.verify_integrity()
        current_position.verify_integrity(session_binding)
        payload = _opaque(request_payload, "request_payload", allow_empty=False)
        options = _opaque(
            transport_options, "transport_options", allow_empty=True)
        material = {
            "version": PROVIDER_HOST_VERSION,
            "session_binding_fingerprint": session_binding.fingerprint,
            "temporal_position_fingerprint": (
                current_position.position.fingerprint),
            "runtime_ordinal": current_position.runtime_ordinal,
            "operation_kind": _identifier(operation_kind, "operation_kind"),
            "source_fact_ref": _identifier(
                source_fact_ref, "source_fact_ref"),
            "codec_contract": _identifier(codec_contract, "codec_contract"),
            "request_payload": thaw_model_data(payload),
            "transport_options": thaw_model_data(options),
        }
        request_ref = "provider-request:" + model_data_fingerprint(material)
        signed = dict(material, request_ref=request_ref)
        return cls(
            **{
                **signed,
                "request_payload": payload,
                "transport_options": options,
            },
            fingerprint=model_data_fingerprint(signed),
        )

    def verify_integrity(
        self, *, session_binding: RuntimeSessionBinding,
        current_position: RuntimePositionState,
    ) -> None:
        expected = ProviderRequest.create(
            session_binding=session_binding,
            current_position=current_position,
            operation_kind=self.operation_kind,
            source_fact_ref=self.source_fact_ref,
            codec_contract=self.codec_contract,
            request_payload=self.request_payload,
            transport_options=self.transport_options,
        )
        if expected != self:
            raise ValueError("provider request integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            **{
                field: getattr(self, field) for field in (
                    "version", "request_ref", "session_binding_fingerprint",
                    "temporal_position_fingerprint", "runtime_ordinal",
                    "operation_kind", "source_fact_ref", "codec_contract",
                )
            },
            "request_payload": thaw_model_data(self.request_payload),
            "transport_options": thaw_model_data(self.transport_options),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ProviderAttempt:
    version: str
    attempt_ref: str
    request_fingerprint: str
    implementation_ref: str
    invocation_evidence: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "invocation_evidence", freeze_model_payload(
            self.invocation_evidence))

    @classmethod
    def create(
        cls, *, request: ProviderRequest, implementation_ref: str,
        invocation_evidence: Mapping[str, Any],
    ) -> "ProviderAttempt":
        if not isinstance(request, ProviderRequest):
            raise TypeError("provider attempt requires ProviderRequest")
        evidence = _opaque(
            invocation_evidence, "invocation_evidence", allow_empty=False)
        material = {
            "version": PROVIDER_HOST_VERSION,
            "request_fingerprint": request.fingerprint,
            "implementation_ref": _identifier(
                implementation_ref, "implementation_ref"),
            "invocation_evidence": thaw_model_data(evidence),
        }
        attempt_ref = "provider-attempt:" + model_data_fingerprint(material)
        signed = dict(material, attempt_ref=attempt_ref)
        return cls(
            **{**signed, "invocation_evidence": evidence},
            fingerprint=model_data_fingerprint(signed),
        )

    def verify_integrity(self, *, request: ProviderRequest) -> None:
        if ProviderAttempt.create(
            request=request,
            implementation_ref=self.implementation_ref,
            invocation_evidence=self.invocation_evidence,
        ) != self:
            raise ValueError("provider attempt integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "attempt_ref": self.attempt_ref,
            "request_fingerprint": self.request_fingerprint,
            "implementation_ref": self.implementation_ref,
            "invocation_evidence": thaw_model_data(self.invocation_evidence),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ProviderResponse:
    version: str
    response_ref: str
    request_fingerprint: str
    attempt_fingerprint: str
    response_payload: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "response_payload", freeze_model_payload(
            self.response_payload))

    @classmethod
    def create(
        cls, *, request: ProviderRequest, attempt: ProviderAttempt,
        response_payload: Mapping[str, Any],
    ) -> "ProviderResponse":
        attempt.verify_integrity(request=request)
        payload = _opaque(
            response_payload, "response_payload", allow_empty=False)
        material = {
            "version": PROVIDER_HOST_VERSION,
            "request_fingerprint": request.fingerprint,
            "attempt_fingerprint": attempt.fingerprint,
            "response_payload": thaw_model_data(payload),
        }
        response_ref = "provider-response:" + model_data_fingerprint(material)
        signed = dict(material, response_ref=response_ref)
        return cls(
            **{**signed, "response_payload": payload},
            fingerprint=model_data_fingerprint(signed),
        )

    def verify_integrity(
        self, *, request: ProviderRequest, attempt: ProviderAttempt,
    ) -> None:
        if ProviderResponse.create(
            request=request, attempt=attempt,
            response_payload=self.response_payload,
        ) != self:
            raise ValueError("provider response integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "response_ref": self.response_ref,
            "request_fingerprint": self.request_fingerprint,
            "attempt_fingerprint": self.attempt_fingerprint,
            "response_payload": thaw_model_data(self.response_payload),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ProviderFailure:
    version: str
    failure_ref: str
    request_fingerprint: str
    attempt_fingerprint: str
    failure_kind: str
    failure_source_ref: str
    fingerprint: str

    @classmethod
    def create(
        cls, *, request: ProviderRequest, attempt: ProviderAttempt,
        failure_kind: str, failure_source_ref: str,
    ) -> "ProviderFailure":
        attempt.verify_integrity(request=request)
        material = {
            "version": PROVIDER_HOST_VERSION,
            "request_fingerprint": request.fingerprint,
            "attempt_fingerprint": attempt.fingerprint,
            "failure_kind": _identifier(failure_kind, "failure_kind"),
            "failure_source_ref": _identifier(
                failure_source_ref, "failure_source_ref"),
        }
        failure_ref = "provider-failure:" + model_data_fingerprint(material)
        signed = dict(material, failure_ref=failure_ref)
        return cls(**signed, fingerprint=model_data_fingerprint(signed))

    def verify_integrity(
        self, *, request: ProviderRequest, attempt: ProviderAttempt,
    ) -> None:
        if ProviderFailure.create(
            request=request, attempt=attempt,
            failure_kind=self.failure_kind,
            failure_source_ref=self.failure_source_ref,
        ) != self:
            raise ValueError("provider failure integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            field: getattr(self, field) for field in (
                "version", "failure_ref", "request_fingerprint",
                "attempt_fingerprint", "failure_kind",
                "failure_source_ref", "fingerprint",
            )
        }


class ProviderInvocationFailure(Exception):
    def __init__(self, *, failure_kind: str, failure_source_ref: str):
        self.failure_kind = _identifier(failure_kind, "failure_kind")
        self.failure_source_ref = _identifier(
            failure_source_ref, "failure_source_ref")
        super().__init__(self.failure_kind)


@dataclass(frozen=True)
class ProviderExecutionResult:
    version: str
    status: str
    request: ProviderRequest
    attempt: ProviderAttempt
    response: ProviderResponse | None
    failure: ProviderFailure | None
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))

    def verify_integrity(self) -> None:
        self.attempt.verify_integrity(request=self.request)
        if self.status == "response_ready":
            if self.response is None or self.failure is not None:
                raise ValueError("provider success terminal data is invalid")
            self.response.verify_integrity(
                request=self.request, attempt=self.attempt)
        elif self.status == "failed":
            if self.failure is None or self.response is not None:
                raise ValueError("provider failure terminal data is invalid")
            self.failure.verify_integrity(
                request=self.request, attempt=self.attempt)
        else:
            raise ValueError("provider result status is invalid")
        if dict(self.receipt) != _receipt(
            status=self.status, request=self.request, attempt=self.attempt,
            response=self.response, failure=self.failure,
        ):
            raise ValueError("provider execution receipt is invalid")


def _receipt(
    *, status: str, request: ProviderRequest, attempt: ProviderAttempt,
    response: ProviderResponse | None, failure: ProviderFailure | None,
) -> dict[str, Any]:
    material = {
        "version": PROVIDER_HOST_VERSION,
        "status": status,
        "request_fingerprint": request.fingerprint,
        "attempt_fingerprint": attempt.fingerprint,
        "response_fingerprint": (
            None if response is None else response.fingerprint),
        "failure_fingerprint": (
            None if failure is None else failure.fingerprint),
        "authority": {
            "state_write": False,
            "history_write": False,
            "progress_write": False,
            "action_authorization": False,
            "action_commit": False,
            "send": False,
        },
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


def execute_provider(
    *, request: ProviderRequest, implementation_ref: str,
    invocation_evidence: Mapping[str, Any],
    invoke: Callable[[ProviderRequest], object],
) -> ProviderExecutionResult:
    if not isinstance(request, ProviderRequest):
        raise TypeError("provider host requires ProviderRequest")
    if not callable(invoke):
        raise TypeError("provider implementation must be callable")
    attempt = ProviderAttempt.create(
        request=request,
        implementation_ref=implementation_ref,
        invocation_evidence=invocation_evidence,
    )
    try:
        raw = invoke(request)
    except ProviderInvocationFailure as exc:
        failure = ProviderFailure.create(
            request=request,
            attempt=attempt,
            failure_kind=exc.failure_kind,
            failure_source_ref=exc.failure_source_ref,
        )
        receipt = _receipt(
            status="failed", request=request, attempt=attempt,
            response=None, failure=failure,
        )
        result = ProviderExecutionResult(
            version=PROVIDER_HOST_VERSION,
            status="failed",
            request=request,
            attempt=attempt,
            response=None,
            failure=failure,
            receipt=receipt,
        )
    else:
        if not isinstance(raw, Mapping):
            raise ValueError("provider implementation must return a mapping")
        response = ProviderResponse.create(
            request=request, attempt=attempt, response_payload=raw)
        receipt = _receipt(
            status="response_ready", request=request, attempt=attempt,
            response=response, failure=None,
        )
        result = ProviderExecutionResult(
            version=PROVIDER_HOST_VERSION,
            status="response_ready",
            request=request,
            attempt=attempt,
            response=response,
            failure=None,
            receipt=receipt,
        )
    result.verify_integrity()
    return result


__all__ = (
    "PROVIDER_HOST_VERSION",
    "ProviderAttempt",
    "ProviderExecutionResult",
    "ProviderFailure",
    "ProviderInvocationFailure",
    "ProviderRequest",
    "ProviderResponse",
    "execute_provider",
)
