"""Package-neutral audit and publication of verified lifecycle outcomes."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.experiment_protocol import (
    ExperimentLifecycleEvidence,
    ExperimentLifecycleFailureEvidence,
    ExperimentRunRequest,
    FrameworkExperimentInterface,
)
from framework.model_api import model_data_fingerprint
from framework.receipt_dag import (
    LifecycleArtifactEvidence,
    LifecycleFailureEvidence,
    LifecycleReceiptAudit,
    LifecycleReceiptPlan,
    LifecycleStageEvidence,
    LifecycleStageSpec,
    audit_lifecycle_receipts,
)
from framework.runtime_kernel import RuntimeSessionBinding
from framework.temporal_domain import TemporalPosition


@dataclass(frozen=True)
class PublishedLifecycle:
    request: ExperimentRunRequest
    evidence: ExperimentLifecycleEvidence | ExperimentLifecycleFailureEvidence
    interface: FrameworkExperimentInterface
    audit: LifecycleReceiptAudit


def publish_successful_lifecycle(
    *, session_binding: RuntimeSessionBinding,
    terminal_position: TemporalPosition, audit_runtime_ordinal: int,
    terminal_runtime_ordinal: int, scope_ref: str, subject_ref: str,
    event_ref: str, action_ref: str | None, event_payload: Any,
    stage_specs: Sequence[LifecycleStageSpec],
    stage_evidence: Mapping[
        str,
        LifecycleStageEvidence | LifecycleArtifactEvidence
        | LifecycleFailureEvidence,
    ],
    result_payload: Any,
) -> PublishedLifecycle:
    """Audit, bind, and publish one immutable successful lifecycle result."""
    plan = LifecycleReceiptPlan.create(stage_specs)
    audit = audit_lifecycle_receipts(
        plan=plan,
        session_binding=session_binding,
        temporal_position=terminal_position,
        runtime_ordinal=audit_runtime_ordinal,
        scope_ref=scope_ref,
        subject_ref=subject_ref,
        event_ref=event_ref,
        action_ref=action_ref,
        evidence=stage_evidence,
    )
    if not audit.passed:
        raise ValueError(
            "cannot publish a lifecycle with an invalid receipt DAG: "
            f"{audit.first_failed_stage}:{audit.failure_code}")
    request = ExperimentRunRequest.create(
        session_binding=session_binding,
        event_ref=event_ref,
        subject_ref=subject_ref,
        scope_ref=scope_ref,
        target_position=terminal_position,
        event_payload=event_payload,
        capability_plan={stage.stage: stage.enabled for stage in plan.stages},
    )
    evidence = ExperimentLifecycleEvidence.create(
        request=request,
        terminal_position=terminal_position,
        terminal_runtime_ordinal=terminal_runtime_ordinal,
        stage_receipt_refs=audit.receipt_refs,
        lifecycle_receipt_ref=model_data_fingerprint({
            "receipt_dag": audit.fingerprint,
        }),
        result_payload=result_payload,
        receipt_dag_audit=audit,
    )

    def execute(candidate: ExperimentRunRequest) -> ExperimentLifecycleEvidence:
        if candidate.fingerprint != request.fingerprint:
            raise ValueError("lifecycle publication received a different request")
        return evidence

    interface = FrameworkExperimentInterface(
        session_binding=session_binding,
        execute_lifecycle=execute,
        require_receipt_dag_audit=True,
    )
    return PublishedLifecycle(
        request=request,
        evidence=evidence,
        interface=interface,
        audit=audit,
    )


def publish_failed_lifecycle(
    *, session_binding: RuntimeSessionBinding,
    terminal_position: TemporalPosition, audit_runtime_ordinal: int,
    terminal_runtime_ordinal: int, scope_ref: str, subject_ref: str,
    event_ref: str, action_ref: str | None, event_payload: Any,
    stage_specs: Sequence[LifecycleStageSpec],
    stage_evidence: Mapping[
        str,
        LifecycleStageEvidence | LifecycleArtifactEvidence
        | LifecycleFailureEvidence,
    ],
) -> PublishedLifecycle:
    """Audit and publish one immutable failed lifecycle result."""
    plan = LifecycleReceiptPlan.create(stage_specs)
    audit = audit_lifecycle_receipts(
        plan=plan,
        session_binding=session_binding,
        temporal_position=terminal_position,
        runtime_ordinal=audit_runtime_ordinal,
        scope_ref=scope_ref,
        subject_ref=subject_ref,
        event_ref=event_ref,
        action_ref=action_ref,
        evidence=stage_evidence,
    )
    if audit.passed:
        raise ValueError(
            "cannot publish a failed lifecycle with a passing receipt DAG")
    if audit.untrusted_downstream_stages:
        raise ValueError(
            "cannot publish a failed lifecycle with executed downstream stages")
    request = ExperimentRunRequest.create(
        session_binding=session_binding,
        event_ref=event_ref,
        subject_ref=subject_ref,
        scope_ref=scope_ref,
        target_position=terminal_position,
        event_payload=event_payload,
        capability_plan={stage.stage: stage.enabled for stage in plan.stages},
    )
    evidence = ExperimentLifecycleFailureEvidence.create(
        request=request,
        terminal_position=terminal_position,
        terminal_runtime_ordinal=terminal_runtime_ordinal,
        receipt_dag_audit=audit,
        lifecycle_failure_ref=model_data_fingerprint({
            "receipt_dag": audit.fingerprint,
            "failed_stage": audit.first_failed_stage,
        }),
    )

    def execute(
        candidate: ExperimentRunRequest,
    ) -> ExperimentLifecycleFailureEvidence:
        if candidate.fingerprint != request.fingerprint:
            raise ValueError("lifecycle publication received a different request")
        return evidence

    interface = FrameworkExperimentInterface(
        session_binding=session_binding,
        execute_lifecycle=execute,
        require_receipt_dag_audit=True,
    )
    return PublishedLifecycle(
        request=request,
        evidence=evidence,
        interface=interface,
        audit=audit,
    )


__all__ = (
    "PublishedLifecycle",
    "publish_failed_lifecycle",
    "publish_successful_lifecycle",
)
