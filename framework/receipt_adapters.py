"""Verified projections from framework artifacts into receipt-DAG evidence.

Each adapter validates the authoritative object with its own contract before
creating a read-only audit projection.  The projection never replaces the
source artifact and carries no state, history, realization, or commit power.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from framework.action_protocol import (
    ActionAuthorization,
    ActionCommitPlan,
    ActionCommitReceipt,
)
from framework.history import CommittedFact, HistoryRead
from framework.model_api import thaw_model_data
from framework.provider_host import (
    ProviderAttempt,
    ProviderFailure,
    ProviderRequest,
    ProviderResponse,
)
from framework.provider_codec import ProviderCodecParseReceipt
from framework.realizer_protocol import (
    RealizerAttempt,
    RealizerCandidate,
    RealizerFailure,
    RealizerRequest,
)
from framework.receipt_dag import (
    LifecycleArtifactEvidence,
    LifecycleFailureEvidence,
)
from framework.runtime_kernel import (
    RuntimeModelState,
    RuntimePositionState,
    RuntimeSessionBinding,
    RuntimeStateCommitAcceptance,
)


def _context(
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
) -> None:
    if not isinstance(session_binding, RuntimeSessionBinding):
        raise TypeError("receipt adapter requires RuntimeSessionBinding")
    if not isinstance(current_position, RuntimePositionState):
        raise TypeError("receipt adapter requires RuntimePositionState")
    session_binding.verify_integrity()
    current_position.verify_integrity(session_binding)


def _evidence(
    *, stage: str, artifact_kind: str, receipt_ref: str,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    input_payload: Any, output_payload: Any,
    parent_receipt_refs: Sequence[str],
) -> LifecycleArtifactEvidence:
    return LifecycleArtifactEvidence.create(
        stage=stage,
        artifact_kind=artifact_kind,
        receipt_ref=receipt_ref,
        session_binding_fingerprint=session_binding.fingerprint,
        temporal_position_fingerprint=(
            current_position.position.fingerprint),
        runtime_ordinal=current_position.runtime_ordinal,
        input_payload=input_payload,
        output_payload=output_payload,
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_history_read(
    *, stage: str, history_read: HistoryRead,
    facts_by_id: Mapping[str, CommittedFact],
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(history_read, HistoryRead):
        raise TypeError("history read adapter requires HistoryRead")
    if not isinstance(facts_by_id, Mapping):
        raise TypeError("history read adapter requires facts_by_id")
    restored = HistoryRead.from_record(
        history_read.to_record(), facts_by_id=facts_by_id)
    if restored.fingerprint != history_read.fingerprint:
        raise ValueError("history read restoration differs from source")
    if (
        history_read.session_binding_fingerprint != session_binding.fingerprint
        or history_read.before_runtime_ordinal
        != current_position.runtime_ordinal
    ):
        raise ValueError("history read differs from runtime context")
    record = history_read.to_record()
    return _evidence(
        stage=stage,
        artifact_kind="history-read",
        receipt_ref=history_read.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "reader_ref": history_read.reader_ref,
            "trigger_fact_id": history_read.trigger_fact_id,
            "before_runtime_ordinal": history_read.before_runtime_ordinal,
            "fact_ids": list(history_read.fact_ids),
            "authorization_ref": thaw_model_data(
                history_read.authorization_ref),
        },
        output_payload=record,
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_committed_fact(
    *, stage: str, fact: CommittedFact,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(fact, CommittedFact):
        raise TypeError("committed fact adapter requires CommittedFact")
    fact.verify_integrity()
    if (
        fact.session_binding_fingerprint != session_binding.fingerprint
        or fact.temporal_position_fingerprint
        != current_position.position.fingerprint
        or fact.accepted_runtime_ordinal != current_position.runtime_ordinal
    ):
        raise ValueError("committed fact differs from runtime context")
    record = fact.to_record()
    return _evidence(
        stage=stage,
        artifact_kind="committed-fact",
        receipt_ref=fact.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "source_ref": fact.source_ref,
            "scope_ref": fact.scope_ref,
            "content_ref": thaw_model_data(fact.content_ref),
            "model_payload": thaw_model_data(fact.model_payload),
            "settlement_ref": thaw_model_data(fact.settlement_ref),
        },
        output_payload=record,
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_action_authorization(
    *, stage: str, authorization: ActionAuthorization,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(authorization, ActionAuthorization):
        raise TypeError(
            "action authorization adapter requires ActionAuthorization")
    authorization.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
    )
    record = authorization.to_record()
    return _evidence(
        stage=stage,
        artifact_kind="action-authorization",
        receipt_ref=authorization.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "source_fact_ref": authorization.source_fact_ref,
            "source_settlement_fingerprint": (
                authorization.source_settlement_fingerprint),
            "package_eligibility_evidence": thaw_model_data(
                authorization.package_eligibility_evidence),
            "host_authorization_evidence": thaw_model_data(
                authorization.host_authorization_evidence),
        },
        output_payload=record,
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_state_commit_acceptance(
    *, stage: str, acceptance: RuntimeStateCommitAcceptance,
    previous_state: RuntimeModelState,
    previous_position: RuntimePositionState,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(acceptance, RuntimeStateCommitAcceptance):
        raise TypeError(
            "settlement acceptance adapter requires "
            "RuntimeStateCommitAcceptance")
    acceptance.verify_integrity(
        session_binding=session_binding,
        previous_state=previous_state,
        previous_position=previous_position,
    )
    if acceptance.position != current_position:
        raise ValueError(
            "settlement acceptance differs from current runtime position")
    return _evidence(
        stage=stage,
        artifact_kind="state-commit-acceptance",
        receipt_ref=acceptance.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "previous_state_fingerprint": previous_state.fingerprint,
            "previous_position_fingerprint": previous_position.fingerprint,
            "stage_receipt_fingerprint": (
                acceptance.stage_receipt_fingerprint),
        },
        output_payload=acceptance.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_provider_request(
    *, stage: str, request: ProviderRequest,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(request, ProviderRequest):
        raise TypeError("provider request adapter requires ProviderRequest")
    request.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
    )
    return _evidence(
        stage=stage,
        artifact_kind="provider-request",
        receipt_ref=request.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "operation_kind": request.operation_kind,
            "source_fact_ref": request.source_fact_ref,
            "codec_contract": request.codec_contract,
            "request_payload": thaw_model_data(request.request_payload),
            "transport_options": thaw_model_data(request.transport_options),
        },
        output_payload=request.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_provider_attempt(
    *, stage: str, attempt: ProviderAttempt, request: ProviderRequest,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(attempt, ProviderAttempt):
        raise TypeError("provider attempt adapter requires ProviderAttempt")
    request.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
    )
    attempt.verify_integrity(request=request)
    return _evidence(
        stage=stage,
        artifact_kind="provider-attempt",
        receipt_ref=attempt.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "request_fingerprint": request.fingerprint,
            "implementation_ref": attempt.implementation_ref,
            "invocation_evidence": thaw_model_data(
                attempt.invocation_evidence),
        },
        output_payload=attempt.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_provider_response(
    *, stage: str, response: ProviderResponse, attempt: ProviderAttempt,
    request: ProviderRequest, session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(response, ProviderResponse):
        raise TypeError("provider response adapter requires ProviderResponse")
    request.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
    )
    response.verify_integrity(request=request, attempt=attempt)
    return _evidence(
        stage=stage,
        artifact_kind="provider-response",
        receipt_ref=response.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "request_fingerprint": request.fingerprint,
            "attempt_fingerprint": attempt.fingerprint,
        },
        output_payload=response.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_provider_failure(
    *, stage: str, failure: ProviderFailure, attempt: ProviderAttempt,
    request: ProviderRequest, session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleFailureEvidence:
    _context(session_binding, current_position)
    if not isinstance(failure, ProviderFailure):
        raise TypeError("provider failure adapter requires ProviderFailure")
    request.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
    )
    failure.verify_integrity(request=request, attempt=attempt)
    return LifecycleFailureEvidence.create(
        stage=stage,
        failure_kind=failure.failure_kind,
        failure_ref=failure.failure_ref,
        session_binding_fingerprint=session_binding.fingerprint,
        temporal_position_fingerprint=current_position.position.fingerprint,
        runtime_ordinal=current_position.runtime_ordinal,
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_provider_codec_parse(
    *, stage: str, parse_receipt: ProviderCodecParseReceipt,
    response: ProviderResponse, session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence | LifecycleFailureEvidence:
    _context(session_binding, current_position)
    if not isinstance(parse_receipt, ProviderCodecParseReceipt):
        raise TypeError("codec parse adapter requires ProviderCodecParseReceipt")
    parse_receipt.verify_integrity(response=response)
    if parse_receipt.status == "failed":
        return LifecycleFailureEvidence.create(
            stage=stage,
            failure_kind=parse_receipt.failure_kind,
            failure_ref=parse_receipt.parse_ref,
            session_binding_fingerprint=session_binding.fingerprint,
            temporal_position_fingerprint=(
                current_position.position.fingerprint),
            runtime_ordinal=current_position.runtime_ordinal,
            parent_receipt_refs=parent_receipt_refs,
        )
    return _evidence(
        stage=stage,
        artifact_kind="provider-codec-parse",
        receipt_ref=parse_receipt.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "provider_response_fingerprint": response.fingerprint,
            "codec_contract": parse_receipt.codec_contract,
        },
        output_payload=parse_receipt.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_realizer_request(
    *, stage: str, request: RealizerRequest,
    authorization: ActionAuthorization,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(request, RealizerRequest):
        raise TypeError("realizer request adapter requires RealizerRequest")
    request.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
        authorization=authorization,
    )
    return _evidence(
        stage=stage,
        artifact_kind="realizer-request",
        receipt_ref=request.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "authorization_fingerprint": authorization.fingerprint,
            "control": thaw_model_data(request.control),
            "readonly_facts": thaw_model_data(request.readonly_facts),
        },
        output_payload=request.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_realizer_attempt(
    *, stage: str, attempt: RealizerAttempt, request: RealizerRequest,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(attempt, RealizerAttempt):
        raise TypeError("realizer attempt adapter requires RealizerAttempt")
    attempt.verify_integrity(request=request)
    if (
        request.session_binding_fingerprint != session_binding.fingerprint
        or request.source_position_fingerprint
        != current_position.position.fingerprint
        or request.source_runtime_ordinal != current_position.runtime_ordinal
    ):
        raise ValueError("realizer attempt request differs from runtime context")
    return _evidence(
        stage=stage,
        artifact_kind="realizer-attempt",
        receipt_ref=attempt.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "request_fingerprint": request.fingerprint,
            "implementation_ref": attempt.implementation_ref,
            "invocation_evidence": thaw_model_data(
                attempt.invocation_evidence),
        },
        output_payload=attempt.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_realizer_candidate(
    *, stage: str, candidate: RealizerCandidate,
    attempt: RealizerAttempt, request: RealizerRequest,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(candidate, RealizerCandidate):
        raise TypeError(
            "realizer candidate adapter requires RealizerCandidate")
    candidate.verify_integrity(request=request, attempt=attempt)
    if (
        request.session_binding_fingerprint != session_binding.fingerprint
        or request.source_position_fingerprint
        != current_position.position.fingerprint
        or request.source_runtime_ordinal != current_position.runtime_ordinal
    ):
        raise ValueError("realizer candidate request differs from runtime context")
    return _evidence(
        stage=stage,
        artifact_kind="realizer-candidate",
        receipt_ref=candidate.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "request_fingerprint": request.fingerprint,
            "attempt_fingerprint": attempt.fingerprint,
            "candidate_payload": thaw_model_data(candidate.candidate_payload),
            "realization_evidence": thaw_model_data(
                candidate.realization_evidence),
        },
        output_payload=candidate.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_realizer_failure(
    *, stage: str, failure: RealizerFailure,
    attempt: RealizerAttempt, request: RealizerRequest,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleFailureEvidence:
    _context(session_binding, current_position)
    if not isinstance(failure, RealizerFailure):
        raise TypeError("realizer failure adapter requires RealizerFailure")
    failure.verify_integrity(request=request, attempt=attempt)
    if (
        request.session_binding_fingerprint != session_binding.fingerprint
        or request.source_position_fingerprint
        != current_position.position.fingerprint
        or request.source_runtime_ordinal != current_position.runtime_ordinal
    ):
        raise ValueError("realizer failure request differs from runtime context")
    return LifecycleFailureEvidence.create(
        stage=stage,
        failure_kind=failure.failure_kind,
        failure_ref=failure.failure_ref,
        session_binding_fingerprint=session_binding.fingerprint,
        temporal_position_fingerprint=(
            current_position.position.fingerprint),
        runtime_ordinal=current_position.runtime_ordinal,
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_action_commit_plan(
    *, stage: str, commit_plan: ActionCommitPlan,
    authorization: ActionAuthorization,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(commit_plan, ActionCommitPlan):
        raise TypeError("action commit adapter requires ActionCommitPlan")
    commit_plan.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
        authorization=authorization,
    )
    record = commit_plan.to_record()
    return _evidence(
        stage=stage,
        artifact_kind="action-commit-plan",
        receipt_ref=commit_plan.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "authorization_fingerprint": authorization.fingerprint,
            "realizer_attempt_ref": commit_plan.realizer_attempt_ref,
            "candidate_ref": commit_plan.candidate_ref,
        },
        output_payload=record,
        parent_receipt_refs=parent_receipt_refs,
    )


def adapt_action_commit_receipt(
    *, stage: str, commit_receipt: ActionCommitReceipt,
    commit_plan: ActionCommitPlan, authorization: ActionAuthorization,
    committed_fact: CommittedFact,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    parent_receipt_refs: Sequence[str] = (),
) -> LifecycleArtifactEvidence:
    _context(session_binding, current_position)
    if not isinstance(commit_receipt, ActionCommitReceipt):
        raise TypeError("action commit adapter requires ActionCommitReceipt")
    if not isinstance(committed_fact, CommittedFact):
        raise TypeError("action commit adapter requires CommittedFact")
    committed_fact.verify_integrity()
    commit_receipt.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
        authorization=authorization,
        commit_plan=commit_plan,
    )
    if (
        commit_receipt.committed_fact_ref != committed_fact.fact_id
        or commit_receipt.committed_fact_fingerprint
        != committed_fact.fingerprint
        or committed_fact.session_binding_fingerprint
        != session_binding.fingerprint
        or committed_fact.temporal_position_fingerprint
        != current_position.position.fingerprint
        or committed_fact.accepted_runtime_ordinal
        != current_position.runtime_ordinal
    ):
        raise ValueError("action commit receipt differs from committed fact")
    return _evidence(
        stage=stage,
        artifact_kind="action-commit",
        receipt_ref=commit_receipt.fingerprint,
        session_binding=session_binding,
        current_position=current_position,
        input_payload={
            "authorization_fingerprint": authorization.fingerprint,
            "commit_plan_fingerprint": commit_plan.fingerprint,
            "candidate_ref": commit_plan.candidate_ref,
            "committed_fact_ref": committed_fact.fact_id,
            "committed_fact_fingerprint": committed_fact.fingerprint,
        },
        output_payload=commit_receipt.to_record(),
        parent_receipt_refs=parent_receipt_refs,
    )


__all__ = (
    "adapt_action_authorization",
    "adapt_action_commit_plan",
    "adapt_action_commit_receipt",
    "adapt_committed_fact",
    "adapt_history_read",
    "adapt_provider_attempt",
    "adapt_provider_codec_parse",
    "adapt_provider_failure",
    "adapt_provider_request",
    "adapt_provider_response",
    "adapt_realizer_attempt",
    "adapt_realizer_candidate",
    "adapt_realizer_failure",
    "adapt_realizer_request",
    "adapt_state_commit_acceptance",
)
