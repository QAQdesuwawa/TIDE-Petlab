"""Content-free public receipt projection for framework experiments.

The projection is derived only from verified framework request/evidence.  It
does not expose or reinterpret model-package payloads and never becomes a
state authority.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from framework.experiment_protocol import (
    ExperimentLifecycleEvidence,
    ExperimentLifecycleFailureEvidence,
    ExperimentRunRequest,
)
from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)


PUBLIC_EXPERIMENT_RECEIPT_VERSION = "framework-public-experiment-receipt-v1"


def verify_public_experiment_receipt_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify a serialized public receipt without reading package internals."""
    if not isinstance(value, Mapping):
        raise TypeError("public experiment receipt must be a mapping")
    record = thaw_model_data(value)
    fingerprint = record.pop("fingerprint", None)
    success_fields = {
        "version", "request_fingerprint", "session_binding_fingerprint",
        "runtime_variant_fingerprint", "terminal_position_fingerprint",
        "terminal_runtime_ordinal", "lifecycle_receipt_ref",
        "stage_settlements", "receipt_dag_fingerprint",
        "action_commit_receipt_ref", "treatment_fingerprint",
    }
    failure_fields = {
        "version", "request_fingerprint", "session_binding_fingerprint",
        "runtime_variant_fingerprint", "terminal_position_fingerprint",
        "terminal_runtime_ordinal", "lifecycle_failure_ref", "failed_stage",
        "failure_kind", "stage_settlements", "receipt_dag_fingerprint",
    }
    fields = set(record)
    if fields not in (success_fields, failure_fields):
        raise ValueError("public experiment receipt fields are invalid")
    if record["version"] != PUBLIC_EXPERIMENT_RECEIPT_VERSION:
        raise ValueError("public experiment receipt version is unsupported")
    for field in (
        "request_fingerprint", "session_binding_fingerprint",
        "runtime_variant_fingerprint", "terminal_position_fingerprint",
    ):
        if not str(record[field] or "").strip():
            raise ValueError(f"public experiment receipt {field} is invalid")
    ordinal = record["terminal_runtime_ordinal"]
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("public experiment receipt ordinal is invalid")
    if not str(record["receipt_dag_fingerprint"] or "").strip():
        raise ValueError("public experiment receipt DAG fingerprint is invalid")
    settlements = record["stage_settlements"]
    if not isinstance(settlements, Mapping) or not settlements:
        raise ValueError("public experiment receipt stages are invalid")
    allowed = {"settled", "failed", "not_executed", "not_applicable"}
    for stage, settlement in settlements.items():
        if not str(stage or "").strip() or not isinstance(settlement, Mapping):
            raise ValueError("public experiment stage settlement is invalid")
        if set(settlement) != {
            "status", "receipt_ref", "parent_receipt_refs",
        } or settlement["status"] not in allowed:
            raise ValueError("public experiment stage settlement is invalid")
        has_receipt = settlement["status"] in {"settled", "failed"}
        if has_receipt != bool(str(settlement["receipt_ref"] or "").strip()):
            raise ValueError("public experiment stage receipt state is invalid")
        parents = settlement["parent_receipt_refs"]
        if (
            not isinstance(parents, list)
            or len(parents) != len(set(parents))
            or any(not str(parent or "").strip() for parent in parents)
        ):
            raise ValueError("public experiment stage parents are invalid")
    if fingerprint != model_data_fingerprint(record):
        raise ValueError("public experiment receipt fingerprint is invalid")
    return {**record, "fingerprint": fingerprint}


@dataclass(frozen=True)
class PublicExperimentReceipt:
    """Read-only settlement manifest published at the framework boundary."""

    version: str
    request_fingerprint: str
    session_binding_fingerprint: str
    runtime_variant_fingerprint: str
    terminal_position_fingerprint: str
    terminal_runtime_ordinal: int
    lifecycle_receipt_ref: str
    stage_settlements: Mapping[str, Mapping[str, Any]]
    receipt_dag_fingerprint: str
    action_commit_receipt_ref: str | None
    treatment_fingerprint: str
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stage_settlements",
            freeze_model_payload(self.stage_settlements),
        )

    @classmethod
    def create(
        cls,
        *,
        request: ExperimentRunRequest,
        evidence: ExperimentLifecycleEvidence,
        treatment_fingerprint: str,
    ) -> "PublicExperimentReceipt":
        evidence.verify_integrity(request)
        binding = thaw_model_data(request.session_binding)
        if not isinstance(binding, dict):
            raise ValueError("public receipt requires a session binding record")
        session_fingerprint = str(binding.get("fingerprint") or "").strip()
        variant_fingerprint = str(
            binding.get("runtime_variant_fingerprint") or "").strip()
        if not session_fingerprint or not variant_fingerprint:
            raise ValueError("public receipt requires verified binding identities")
        treatment_ref = str(treatment_fingerprint or "").strip()
        if not treatment_ref:
            raise ValueError("public receipt requires treatment fingerprint")
        if evidence.receipt_dag_audit is None:
            raise ValueError("public receipt requires a passed receipt DAG audit")

        refs = dict(evidence.stage_receipt_refs)
        audited_parents = dict(evidence.receipt_dag_audit.parent_receipt_refs)
        settlements: dict[str, dict[str, Any]] = {}
        for stage, enabled in sorted(request.capability_plan.items()):
            if enabled:
                settlements[stage] = {
                    "status": "settled",
                    "receipt_ref": refs[stage],
                    "parent_receipt_refs": list(
                        audited_parents.get(stage, ())),
                }
            else:
                settlements[stage] = {
                    "status": "not_applicable",
                    "receipt_ref": None,
                    "parent_receipt_refs": [],
                }
        commit_ref = refs.get("action_commit")
        audit_fingerprint = evidence.receipt_dag_audit.fingerprint
        material = {
            "version": PUBLIC_EXPERIMENT_RECEIPT_VERSION,
            "request_fingerprint": request.fingerprint,
            "session_binding_fingerprint": session_fingerprint,
            "runtime_variant_fingerprint": variant_fingerprint,
            "terminal_position_fingerprint": (
                evidence.terminal_position.fingerprint),
            "terminal_runtime_ordinal": evidence.terminal_runtime_ordinal,
            "lifecycle_receipt_ref": evidence.lifecycle_receipt_ref,
            "stage_settlements": settlements,
            "receipt_dag_fingerprint": audit_fingerprint,
            "action_commit_receipt_ref": commit_ref,
            "treatment_fingerprint": treatment_ref,
        }
        return cls(
            **material,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(
        self,
        *,
        request: ExperimentRunRequest,
        evidence: ExperimentLifecycleEvidence,
    ) -> None:
        expected = PublicExperimentReceipt.create(
            request=request,
            evidence=evidence,
            treatment_fingerprint=self.treatment_fingerprint,
        )
        if expected != self:
            raise ValueError("public experiment receipt integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "request_fingerprint": self.request_fingerprint,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "runtime_variant_fingerprint": self.runtime_variant_fingerprint,
            "terminal_position_fingerprint": self.terminal_position_fingerprint,
            "terminal_runtime_ordinal": self.terminal_runtime_ordinal,
            "lifecycle_receipt_ref": self.lifecycle_receipt_ref,
            "stage_settlements": thaw_model_data(self.stage_settlements),
            "receipt_dag_fingerprint": self.receipt_dag_fingerprint,
            "action_commit_receipt_ref": self.action_commit_receipt_ref,
            "treatment_fingerprint": self.treatment_fingerprint,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class PublicExperimentFailureReceipt:
    """Content-free public settlement for a verified failed lifecycle."""

    version: str
    request_fingerprint: str
    session_binding_fingerprint: str
    runtime_variant_fingerprint: str
    terminal_position_fingerprint: str
    terminal_runtime_ordinal: int
    lifecycle_failure_ref: str
    failed_stage: str
    failure_kind: str
    stage_settlements: Mapping[str, Mapping[str, Any]]
    receipt_dag_fingerprint: str
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "stage_settlements",
            freeze_model_payload(self.stage_settlements),
        )

    @classmethod
    def create(
        cls,
        *,
        request: ExperimentRunRequest,
        evidence: ExperimentLifecycleFailureEvidence,
    ) -> "PublicExperimentFailureReceipt":
        evidence.verify_integrity(request)
        binding = thaw_model_data(request.session_binding)
        if not isinstance(binding, dict):
            raise ValueError("public failure receipt requires session binding")
        session_fingerprint = str(binding.get("fingerprint") or "").strip()
        variant_fingerprint = str(
            binding.get("runtime_variant_fingerprint") or "").strip()
        if not session_fingerprint or not variant_fingerprint:
            raise ValueError(
                "public failure receipt requires verified binding identities")

        audit = evidence.receipt_dag_audit
        refs = dict(audit.receipt_refs)
        parents = dict(audit.parent_receipt_refs)
        passed = set(audit.passed_stages)
        not_executed = set(audit.not_executed_stages)
        settlements: dict[str, dict[str, Any]] = {}
        for stage, enabled in sorted(request.capability_plan.items()):
            if not enabled:
                status = "not_applicable"
            elif stage in passed:
                status = "settled"
            elif stage == evidence.failed_stage:
                status = "failed"
            elif stage in not_executed:
                status = "not_executed"
            else:
                raise ValueError(
                    "public failure receipt has an unclassified stage")
            settlements[stage] = {
                "status": status,
                "receipt_ref": refs.get(stage),
                "parent_receipt_refs": list(parents.get(stage, ())),
            }
        material = {
            "version": PUBLIC_EXPERIMENT_RECEIPT_VERSION,
            "request_fingerprint": request.fingerprint,
            "session_binding_fingerprint": session_fingerprint,
            "runtime_variant_fingerprint": variant_fingerprint,
            "terminal_position_fingerprint": (
                evidence.terminal_position.fingerprint),
            "terminal_runtime_ordinal": evidence.terminal_runtime_ordinal,
            "lifecycle_failure_ref": evidence.lifecycle_failure_ref,
            "failed_stage": evidence.failed_stage,
            "failure_kind": evidence.failure_kind,
            "stage_settlements": settlements,
            "receipt_dag_fingerprint": audit.fingerprint,
        }
        return cls(
            **material,
            fingerprint=model_data_fingerprint(material),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "request_fingerprint": self.request_fingerprint,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "runtime_variant_fingerprint": self.runtime_variant_fingerprint,
            "terminal_position_fingerprint": self.terminal_position_fingerprint,
            "terminal_runtime_ordinal": self.terminal_runtime_ordinal,
            "lifecycle_failure_ref": self.lifecycle_failure_ref,
            "failed_stage": self.failed_stage,
            "failure_kind": self.failure_kind,
            "stage_settlements": thaw_model_data(self.stage_settlements),
            "receipt_dag_fingerprint": self.receipt_dag_fingerprint,
            "fingerprint": self.fingerprint,
        }


__all__ = (
    "PUBLIC_EXPERIMENT_RECEIPT_VERSION",
    "PublicExperimentFailureReceipt",
    "PublicExperimentReceipt",
    "verify_public_experiment_receipt_record",
)
