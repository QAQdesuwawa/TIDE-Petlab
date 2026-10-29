"""Neutral execution host for one opaque realization request.

The host owns no persistence or commit authority.  A caller-supplied
implementation either returns one opaque candidate envelope or raises the
explicit, content-free failure signal defined here.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import freeze_model_payload, model_data_fingerprint
from framework.realizer_protocol import (
    RealizerAttempt,
    RealizerCandidate,
    RealizerFailure,
    RealizerRequest,
)


REALIZER_HOST_VERSION = "framework-realizer-host-v1"


class RealizerInvocationFailure(Exception):
    """Explicit content-free failure raised by an injected implementation."""

    def __init__(self, *, failure_kind: str, failure_source_ref: str):
        kind = str(failure_kind or "").strip()
        source_ref = str(failure_source_ref or "").strip()
        if not kind or not source_ref:
            raise ValueError("realizer failure identifiers must be non-empty")
        super().__init__(kind)
        self.failure_kind = kind
        self.failure_source_ref = source_ref


@dataclass(frozen=True)
class RealizerExecutionResult:
    version: str
    status: str
    request: RealizerRequest
    attempt: RealizerAttempt
    candidate: RealizerCandidate | None
    failure: RealizerFailure | None
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))

    def verify_integrity(self) -> None:
        self.attempt.verify_integrity(request=self.request)
        if self.status == "candidate_ready":
            if self.candidate is None or self.failure is not None:
                raise ValueError("candidate-ready result has invalid terminal data")
            self.candidate.verify_integrity(
                request=self.request, attempt=self.attempt)
        elif self.status == "failed":
            if self.failure is None or self.candidate is not None:
                raise ValueError("failed result has invalid terminal data")
            self.failure.verify_integrity(
                request=self.request, attempt=self.attempt)
        else:
            raise ValueError("realizer result status is invalid")
        material = _receipt_material(
            status=self.status,
            request=self.request,
            attempt=self.attempt,
            candidate=self.candidate,
            failure=self.failure,
        )
        expected = dict(material, fingerprint=model_data_fingerprint(material))
        if dict(self.receipt) != expected:
            raise ValueError("realizer execution receipt is invalid")


def _receipt_material(
    *, status: str, request: RealizerRequest, attempt: RealizerAttempt,
    candidate: RealizerCandidate | None, failure: RealizerFailure | None,
) -> dict[str, Any]:
    return {
        "version": REALIZER_HOST_VERSION,
        "status": status,
        "request_fingerprint": request.fingerprint,
        "attempt_fingerprint": attempt.fingerprint,
        "candidate_fingerprint": (
            None if candidate is None else candidate.fingerprint),
        "failure_fingerprint": (
            None if failure is None else failure.fingerprint),
        "authority": {
            "state_write": False,
            "history_write": False,
            "progress_write": False,
            "action_commit": False,
            "send": False,
        },
    }


def _result(
    *, status: str, request: RealizerRequest, attempt: RealizerAttempt,
    candidate: RealizerCandidate | None, failure: RealizerFailure | None,
) -> RealizerExecutionResult:
    material = _receipt_material(
        status=status,
        request=request,
        attempt=attempt,
        candidate=candidate,
        failure=failure,
    )
    result = RealizerExecutionResult(
        version=REALIZER_HOST_VERSION,
        status=status,
        request=request,
        attempt=attempt,
        candidate=candidate,
        failure=failure,
        receipt=dict(material, fingerprint=model_data_fingerprint(material)),
    )
    result.verify_integrity()
    return result


def execute_realizer(
    *, request: RealizerRequest, implementation_ref: str,
    invocation_evidence: Mapping[str, Any],
    invoke: Callable[[RealizerRequest], object],
) -> RealizerExecutionResult:
    if not isinstance(request, RealizerRequest):
        raise TypeError("realizer host requires RealizerRequest")
    if not callable(invoke):
        raise TypeError("realizer implementation must be callable")
    attempt = RealizerAttempt.create(
        request=request,
        implementation_ref=implementation_ref,
        invocation_evidence=invocation_evidence,
    )
    try:
        raw = invoke(request)
    except RealizerInvocationFailure as exc:
        failure = RealizerFailure.create(
            request=request,
            attempt=attempt,
            failure_kind=exc.failure_kind,
            failure_source_ref=exc.failure_source_ref,
        )
        return _result(
            status="failed",
            request=request,
            attempt=attempt,
            candidate=None,
            failure=failure,
        )
    if not isinstance(raw, Mapping):
        raise ValueError("realizer implementation must return a mapping")
    if set(raw) != {"candidate_payload", "realization_evidence"}:
        raise ValueError("realizer candidate envelope is invalid")
    candidate = RealizerCandidate.create(
        request=request,
        attempt=attempt,
        candidate_payload=raw["candidate_payload"],
        realization_evidence=raw["realization_evidence"],
    )
    return _result(
        status="candidate_ready",
        request=request,
        attempt=attempt,
        candidate=candidate,
        failure=None,
    )


__all__ = (
    "REALIZER_HOST_VERSION",
    "RealizerExecutionResult",
    "RealizerInvocationFailure",
    "execute_realizer",
)
