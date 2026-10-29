"""Package-neutral, receipt-first entry contract for lifecycle experiments."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.runtime_kernel import RuntimeSessionBinding
from framework.receipt_dag import LifecycleReceiptAudit
from framework.temporal_domain import TemporalPosition


EXPERIMENT_PROTOCOL_VERSION = "framework-experiment-protocol-v1"
FRAMEWORK_EXPERIMENT_STAGES = frozenset({
    "history_read",
    "history_append",
    "settlement_acceptance",
    "action_authorization",
    "realizer_request",
    "realizer_attempt",
    "candidate",
    "action_commit_plan",
    "action_commit",
})


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _receipt_ref(value: object, field: str) -> str:
    return _identifier(value, field)


@dataclass(frozen=True)
class ExperimentRunRequest:
    version: str
    session_binding: Mapping[str, Any]
    event_ref: str
    subject_ref: str
    scope_ref: str
    target_position: TemporalPosition
    event_payload: Any
    capability_plan: Mapping[str, bool]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_binding", freeze_model_payload(
            self.session_binding))
        object.__setattr__(self, "event_payload", freeze_model_payload(
            {"value": self.event_payload})["value"])
        object.__setattr__(self, "capability_plan", freeze_model_payload(
            self.capability_plan))

    @classmethod
    def create(
        cls, *, session_binding: RuntimeSessionBinding, event_ref: str,
        subject_ref: str, scope_ref: str, target_position: TemporalPosition,
        event_payload: Any, capability_plan: Mapping[str, bool],
    ) -> "ExperimentRunRequest":
        if not isinstance(session_binding, RuntimeSessionBinding):
            raise TypeError("experiment request requires RuntimeSessionBinding")
        session_binding.verify_integrity()
        if not isinstance(target_position, TemporalPosition):
            raise TypeError("experiment request requires TemporalPosition")
        target_position.verify_integrity()
        if target_position.binding != session_binding.temporal_binding:
            raise ValueError(
                "experiment target uses a different progress domain")
        if not isinstance(capability_plan, Mapping) or not capability_plan:
            raise ValueError("experiment capability plan must be non-empty")
        normalized_plan: dict[str, bool] = {}
        for raw_name, enabled in capability_plan.items():
            name = _identifier(raw_name, "capability name")
            if type(enabled) is not bool:
                raise ValueError("experiment capability states must be boolean")
            normalized_plan[name] = enabled
        if not any(normalized_plan.values()):
            raise ValueError("experiment capability plan enables no lifecycle stage")
        material = {
            "version": EXPERIMENT_PROTOCOL_VERSION,
            "session_binding": session_binding.to_record(),
            "event_ref": _identifier(event_ref, "event_ref"),
            "subject_ref": _identifier(subject_ref, "subject_ref"),
            "scope_ref": _identifier(scope_ref, "scope_ref"),
            "target_position": target_position.to_record(),
            "event_payload": thaw_model_data(freeze_model_payload(
                {"value": event_payload})["value"]),
            "capability_plan": dict(sorted(normalized_plan.items())),
        }
        return cls(
            version=material["version"],
            session_binding=material["session_binding"],
            event_ref=material["event_ref"],
            subject_ref=material["subject_ref"],
            scope_ref=material["scope_ref"],
            target_position=target_position,
            event_payload=material["event_payload"],
            capability_plan=material["capability_plan"],
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self, session_binding: RuntimeSessionBinding) -> None:
        expected = ExperimentRunRequest.create(
            session_binding=session_binding,
            event_ref=self.event_ref,
            subject_ref=self.subject_ref,
            scope_ref=self.scope_ref,
            target_position=self.target_position,
            event_payload=self.event_payload,
            capability_plan=self.capability_plan,
        )
        if expected != self:
            raise ValueError("experiment request integrity is invalid")


@dataclass(frozen=True)
class ExperimentLifecycleEvidence:
    version: str
    request_fingerprint: str
    terminal_position: TemporalPosition
    terminal_runtime_ordinal: int
    stage_receipt_refs: Mapping[str, str]
    lifecycle_receipt_ref: str
    result_payload: Any
    receipt_dag_audit: LifecycleReceiptAudit | None
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_receipt_refs", freeze_model_payload(
            self.stage_receipt_refs))
        object.__setattr__(self, "result_payload", freeze_model_payload(
            {"value": self.result_payload})["value"])

    @classmethod
    def create(
        cls, *, request: ExperimentRunRequest,
        terminal_position: TemporalPosition, terminal_runtime_ordinal: int,
        stage_receipt_refs: Mapping[str, str], lifecycle_receipt_ref: str,
        result_payload: Any,
        receipt_dag_audit: LifecycleReceiptAudit | None = None,
    ) -> "ExperimentLifecycleEvidence":
        if not isinstance(request, ExperimentRunRequest):
            raise TypeError("lifecycle evidence requires experiment request")
        if not isinstance(terminal_position, TemporalPosition):
            raise TypeError("lifecycle evidence requires terminal position")
        terminal_position.verify_integrity()
        if terminal_position != request.target_position:
            raise ValueError("lifecycle ended at a different target position")
        if (isinstance(terminal_runtime_ordinal, bool)
                or not isinstance(terminal_runtime_ordinal, int)
                or terminal_runtime_ordinal < 0):
            raise ValueError("terminal runtime ordinal must be non-negative")
        if not isinstance(stage_receipt_refs, Mapping):
            raise ValueError("stage receipt references must be a mapping")
        normalized_refs = {
            _identifier(name, "stage name"): _receipt_ref(ref, "stage receipt")
            for name, ref in stage_receipt_refs.items()
        }
        expected_stages = {
            name for name, enabled in request.capability_plan.items() if enabled
        }
        if set(normalized_refs) != expected_stages:
            raise ValueError(
                "stage receipts must exactly match enabled capabilities")
        if receipt_dag_audit is not None:
            if not isinstance(receipt_dag_audit, LifecycleReceiptAudit):
                raise TypeError(
                    "receipt_dag_audit must be LifecycleReceiptAudit")
            if not receipt_dag_audit.passed:
                raise ValueError(
                    "experiment evidence cannot publish a failed receipt DAG")
            audited_refs = dict(receipt_dag_audit.receipt_refs)
            if not set(audited_refs).issubset(normalized_refs):
                raise ValueError(
                    "receipt DAG contains stages outside lifecycle evidence")
            if any(normalized_refs[name] != ref
                   for name, ref in audited_refs.items()):
                raise ValueError(
                    "receipt DAG references differ from lifecycle evidence")
        material = {
            "version": EXPERIMENT_PROTOCOL_VERSION,
            "request_fingerprint": request.fingerprint,
            "terminal_position": terminal_position.to_record(),
            "terminal_runtime_ordinal": terminal_runtime_ordinal,
            "stage_receipt_refs": dict(sorted(normalized_refs.items())),
            "lifecycle_receipt_ref": _receipt_ref(
                lifecycle_receipt_ref, "lifecycle_receipt_ref"),
            "result_payload": thaw_model_data(freeze_model_payload(
                {"value": result_payload})["value"]),
            "receipt_dag_audit": (
                None if receipt_dag_audit is None
                else receipt_dag_audit.to_record()),
        }
        return cls(
            version=material["version"],
            request_fingerprint=material["request_fingerprint"],
            terminal_position=terminal_position,
            terminal_runtime_ordinal=terminal_runtime_ordinal,
            stage_receipt_refs=material["stage_receipt_refs"],
            lifecycle_receipt_ref=material["lifecycle_receipt_ref"],
            result_payload=material["result_payload"],
            receipt_dag_audit=receipt_dag_audit,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self, request: ExperimentRunRequest) -> None:
        expected = ExperimentLifecycleEvidence.create(
            request=request,
            terminal_position=self.terminal_position,
            terminal_runtime_ordinal=self.terminal_runtime_ordinal,
            stage_receipt_refs=self.stage_receipt_refs,
            lifecycle_receipt_ref=self.lifecycle_receipt_ref,
            result_payload=self.result_payload,
            receipt_dag_audit=self.receipt_dag_audit,
        )
        if expected != self:
            raise ValueError("experiment lifecycle evidence integrity is invalid")


@dataclass(frozen=True)
class ExperimentLifecycleFailureEvidence:
    """Public, content-free result for one verified failed lifecycle."""

    version: str
    request_fingerprint: str
    terminal_position: TemporalPosition
    terminal_runtime_ordinal: int
    stage_receipt_refs: Mapping[str, str]
    lifecycle_failure_ref: str
    failed_stage: str
    failure_kind: str
    receipt_dag_audit: LifecycleReceiptAudit
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "stage_receipt_refs", freeze_model_payload(
            self.stage_receipt_refs))

    @classmethod
    def create(
        cls, *, request: ExperimentRunRequest,
        terminal_position: TemporalPosition, terminal_runtime_ordinal: int,
        receipt_dag_audit: LifecycleReceiptAudit,
        lifecycle_failure_ref: str,
    ) -> "ExperimentLifecycleFailureEvidence":
        if not isinstance(request, ExperimentRunRequest):
            raise TypeError("lifecycle failure requires experiment request")
        if not isinstance(terminal_position, TemporalPosition):
            raise TypeError("lifecycle failure requires terminal position")
        terminal_position.verify_integrity()
        if terminal_position != request.target_position:
            raise ValueError("failed lifecycle ended at a different position")
        if (isinstance(terminal_runtime_ordinal, bool)
                or not isinstance(terminal_runtime_ordinal, int)
                or terminal_runtime_ordinal < 0):
            raise ValueError("terminal runtime ordinal must be non-negative")
        if not isinstance(receipt_dag_audit, LifecycleReceiptAudit):
            raise TypeError("lifecycle failure requires receipt DAG audit")
        if receipt_dag_audit.passed:
            raise ValueError("lifecycle failure requires a failed receipt DAG")
        failed_stage = _identifier(
            receipt_dag_audit.first_failed_stage, "failed stage")
        failure_kind = _identifier(
            receipt_dag_audit.failure_code, "failure kind")
        if receipt_dag_audit.untrusted_downstream_stages:
            raise ValueError(
                "failed lifecycle cannot publish executed downstream stages")
        enabled_stages = {
            name for name, enabled in request.capability_plan.items() if enabled
        }
        partition = (
            set(receipt_dag_audit.passed_stages)
            | {failed_stage}
            | set(receipt_dag_audit.not_executed_stages)
        )
        if partition != enabled_stages:
            raise ValueError(
                "failed receipt DAG does not cover enabled capabilities")
        refs = dict(receipt_dag_audit.receipt_refs)
        if set(refs) != set(receipt_dag_audit.passed_stages) | {failed_stage}:
            raise ValueError(
                "failed lifecycle requires receipts through the failed stage")
        material = {
            "version": EXPERIMENT_PROTOCOL_VERSION,
            "request_fingerprint": request.fingerprint,
            "terminal_position": terminal_position.to_record(),
            "terminal_runtime_ordinal": terminal_runtime_ordinal,
            "stage_receipt_refs": dict(sorted(refs.items())),
            "lifecycle_failure_ref": _receipt_ref(
                lifecycle_failure_ref, "lifecycle_failure_ref"),
            "failed_stage": failed_stage,
            "failure_kind": failure_kind,
            "receipt_dag_audit": receipt_dag_audit.to_record(),
        }
        return cls(
            version=material["version"],
            request_fingerprint=material["request_fingerprint"],
            terminal_position=terminal_position,
            terminal_runtime_ordinal=terminal_runtime_ordinal,
            stage_receipt_refs=material["stage_receipt_refs"],
            lifecycle_failure_ref=material["lifecycle_failure_ref"],
            failed_stage=failed_stage,
            failure_kind=failure_kind,
            receipt_dag_audit=receipt_dag_audit,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self, request: ExperimentRunRequest) -> None:
        expected = ExperimentLifecycleFailureEvidence.create(
            request=request,
            terminal_position=self.terminal_position,
            terminal_runtime_ordinal=self.terminal_runtime_ordinal,
            receipt_dag_audit=self.receipt_dag_audit,
            lifecycle_failure_ref=self.lifecycle_failure_ref,
        )
        if expected != self:
            raise ValueError(
                "experiment lifecycle failure integrity is invalid")


class FrameworkExperimentInterface:
    """Invoke one injected lifecycle host and validate its public evidence."""

    def __init__(
        self, *, session_binding: RuntimeSessionBinding,
        execute_lifecycle: Callable[[ExperimentRunRequest], object],
        require_receipt_dag_audit: bool = False,
    ) -> None:
        if not isinstance(session_binding, RuntimeSessionBinding):
            raise TypeError("experiment interface requires RuntimeSessionBinding")
        session_binding.verify_integrity()
        if not callable(execute_lifecycle):
            raise TypeError("experiment interface requires lifecycle executor")
        if type(require_receipt_dag_audit) is not bool:
            raise TypeError("receipt DAG audit requirement must be boolean")
        self._session_binding = session_binding
        self._execute_lifecycle = execute_lifecycle
        self._require_receipt_dag_audit = require_receipt_dag_audit

    def submit(
        self, request: ExperimentRunRequest,
    ) -> ExperimentLifecycleEvidence | ExperimentLifecycleFailureEvidence:
        if not isinstance(request, ExperimentRunRequest):
            raise TypeError("experiment interface requires ExperimentRunRequest")
        request.verify_integrity(self._session_binding)
        result = self._execute_lifecycle(request)
        if not isinstance(
            result,
            (ExperimentLifecycleEvidence, ExperimentLifecycleFailureEvidence),
        ):
            raise TypeError(
                "lifecycle executor must return verified lifecycle evidence")
        result.verify_integrity(request)
        if isinstance(result, ExperimentLifecycleFailureEvidence):
            return result
        if self._require_receipt_dag_audit:
            audit = result.receipt_dag_audit
            if audit is None:
                raise ValueError(
                    "experiment interface requires a receipt DAG audit")
            required_model_stages = {
                name for name, enabled in request.capability_plan.items()
                if enabled
                and name in self._session_binding.model_binding.operator_versions
            }
            if not required_model_stages.issubset(audit.receipt_refs):
                missing = sorted(required_model_stages - set(audit.receipt_refs))
                raise ValueError(
                    "receipt DAG does not cover enabled model stages: "
                    + ", ".join(missing))
        return result


__all__ = (
    "EXPERIMENT_PROTOCOL_VERSION",
    "FRAMEWORK_EXPERIMENT_STAGES",
    "ExperimentLifecycleEvidence",
    "ExperimentLifecycleFailureEvidence",
    "ExperimentRunRequest",
    "FrameworkExperimentInterface",
)
