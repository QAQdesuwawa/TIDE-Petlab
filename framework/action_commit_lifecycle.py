"""Package-neutral candidate-to-Commit transaction for one runtime action."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from framework.action_protocol import (
    ActionAuthorization,
    ActionCommitPlan,
    ActionCommitReceipt,
)
from framework.history import CommittedFact, HistoryAuthority
from framework.realizer_protocol import (
    RealizerAttempt,
    RealizerCandidate,
    RealizerRequest,
)
from framework.receipt_adapters import (
    adapt_action_commit_plan,
    adapt_action_commit_receipt,
    adapt_committed_fact,
    adapt_realizer_attempt,
    adapt_realizer_candidate,
)
from framework.receipt_dag import LifecycleArtifactEvidence
from framework.runtime_kernel import RuntimePositionState, RuntimeSessionBinding


@dataclass(frozen=True)
class ActionCommitLifecycleResult:
    commit_plan: ActionCommitPlan
    committed_fact: CommittedFact
    commit_receipt: ActionCommitReceipt
    evidence: Mapping[str, LifecycleArtifactEvidence]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "evidence", MappingProxyType(dict(self.evidence)))


def execute_action_commit_lifecycle(
    *,
    history: HistoryAuthority,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    authorization: ActionAuthorization,
    realizer_request: RealizerRequest,
    realizer_attempt: RealizerAttempt,
    candidate: RealizerCandidate,
    scope_ref: str,
    visible_to_refs: Sequence[str],
    model_payload: Mapping[str, Any],
    authorization_receipt_ref: str,
    realizer_request_receipt_ref: str,
    additional_realizer_parent_receipt_refs: Sequence[str] = (),
) -> ActionCommitLifecycleResult:
    """Validate all pure artifacts, then append exactly one canonical fact."""
    if not isinstance(history, HistoryAuthority):
        raise TypeError("action Commit lifecycle requires HistoryAuthority")
    session_binding.verify_integrity()
    current_position.verify_integrity(session_binding)
    if history.session_binding_fingerprint != session_binding.fingerprint:
        raise ValueError("action Commit history uses a different session")

    additional = tuple(str(ref or "").strip()
                       for ref in additional_realizer_parent_receipt_refs)
    if any(not ref for ref in additional) or len(additional) != len(set(additional)):
        raise ValueError("additional realizer parent receipts are invalid")
    attempt_evidence = adapt_realizer_attempt(
        stage="realizer_attempt",
        attempt=realizer_attempt,
        request=realizer_request,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=(realizer_request_receipt_ref, *additional),
    )
    candidate_evidence = adapt_realizer_candidate(
        stage="candidate",
        candidate=candidate,
        attempt=realizer_attempt,
        request=realizer_request,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=(
            realizer_request_receipt_ref,
            attempt_evidence.receipt_ref,
            *additional,
        ),
    )
    commit_plan = ActionCommitPlan.create(
        session_binding=session_binding,
        current_position=current_position,
        authorization=authorization,
        action_kind=authorization.action_kind,
        subject_ref=authorization.subject_ref,
        source_fact_ref=authorization.source_fact_ref,
        target_ref=authorization.target_ref,
        realizer_attempt_ref=realizer_attempt.attempt_ref,
        candidate_ref=candidate.candidate_ref,
    )
    plan_evidence = adapt_action_commit_plan(
        stage="action_commit_plan",
        commit_plan=commit_plan,
        authorization=authorization,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=(
            authorization_receipt_ref,
            attempt_evidence.receipt_ref,
            candidate_evidence.receipt_ref,
        ),
    )
    fact = CommittedFact.create(
        fact_id=commit_plan.action_id,
        session_binding_fingerprint=session_binding.fingerprint,
        source_ref=candidate.candidate_ref,
        scope_ref=scope_ref,
        visible_to_refs=visible_to_refs,
        temporal_position_fingerprint=current_position.position.fingerprint,
        accepted_runtime_ordinal=current_position.runtime_ordinal,
        content_ref={"candidate_ref": candidate.candidate_ref},
        model_payload=model_payload,
        settlement_ref={"commit_plan": commit_plan.fingerprint},
    )
    commit_receipt = ActionCommitReceipt.create(
        session_binding=session_binding,
        current_position=current_position,
        authorization=authorization,
        commit_plan=commit_plan,
        committed_fact_ref=fact.fact_id,
        committed_fact_fingerprint=fact.fingerprint,
    )

    # This is the sole mutation. Everything above is verified and immutable.
    committed = history.append_fact(fact)
    history_evidence = adapt_committed_fact(
        stage="history_append",
        fact=committed,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=(plan_evidence.receipt_ref,),
    )
    commit_evidence = adapt_action_commit_receipt(
        stage="action_commit",
        commit_receipt=commit_receipt,
        commit_plan=commit_plan,
        authorization=authorization,
        committed_fact=committed,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=(
            candidate_evidence.receipt_ref,
            plan_evidence.receipt_ref,
            history_evidence.receipt_ref,
        ),
    )
    return ActionCommitLifecycleResult(
        commit_plan=commit_plan,
        committed_fact=committed,
        commit_receipt=commit_receipt,
        evidence={
            "realizer_attempt": attempt_evidence,
            "candidate": candidate_evidence,
            "action_commit_plan": plan_evidence,
            "history_append": history_evidence,
            "action_commit": commit_evidence,
        },
    )


__all__ = ("ActionCommitLifecycleResult", "execute_action_commit_lifecycle")
