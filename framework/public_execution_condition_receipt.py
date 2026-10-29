"""Package-neutral receipt for one externally scheduled execution condition.

The receipt is a read-only projection at the framework boundary.  It binds an
opaque treatment to an execution condition, external boundary/fault evidence,
pre-registered metrics, and any public framework receipts produced while the
condition ran.  It neither embeds nor interprets model-package data.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)


PUBLIC_EXECUTION_CONDITION_RECEIPT_VERSION = (
    "framework-public-execution-condition-receipt-v1"
)
_FIELDS = {
    "version", "condition_id", "condition_fingerprint", "target_mechanism",
    "treatment_id", "treatment_fingerprint", "runtime_identity",
    "contract_status", "outcome", "boundary_receipt_refs",
    "fault_receipt_refs", "condition_metrics",
    "framework_result_fingerprints", "framework_receipt_fingerprints",
    "checkpoint_receipt_fingerprints", "replay_receipt_fingerprints",
    "provider_calls", "fingerprint",
}
_CONTRACT_STATUSES = {"compliant", "violated"}


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"public condition receipt {field} is invalid")
    return normalized


def _optional_identifier(value: object, field: str) -> str | None:
    if value is None:
        return None
    return _identifier(value, field)


def _reference_sequence(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"public condition receipt {field} is invalid")
    normalized = tuple(_identifier(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"public condition receipt {field} has duplicates")
    return normalized


def _boundary_references(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError(
            "public condition receipt boundary references are invalid")
    normalized = {
        _identifier(role, "boundary role"): _identifier(
            reference, "boundary reference")
        for role, reference in value.items()
    }
    if len(normalized) != len(value):
        raise ValueError(
            "public condition receipt boundary roles contain duplicates")
    return normalized


def _metrics(value: object, *, evidence_refs: set[str]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("public condition receipt metrics are invalid")
    result: dict[str, Any] = {}
    for raw_name, raw_metric in value.items():
        name = _identifier(raw_name, "metric name")
        if not isinstance(raw_metric, Mapping) or set(raw_metric) != {
            "applicable", "value", "evidence_receipt_id", "reason",
        }:
            raise ValueError("public condition receipt metric is invalid")
        applicable = raw_metric["applicable"]
        if not isinstance(applicable, bool):
            raise ValueError(
                "public condition receipt metric applicability is invalid")
        evidence = _identifier(
            raw_metric["evidence_receipt_id"], "metric evidence reference")
        if evidence not in evidence_refs:
            raise ValueError(
                "public condition receipt metric evidence is unbound")
        reason = raw_metric["reason"]
        if applicable:
            if raw_metric["value"] is None or reason is not None:
                raise ValueError(
                    "public condition receipt observed metric is invalid")
        else:
            if raw_metric["value"] is not None:
                raise ValueError(
                    "public condition receipt unavailable metric has a value")
            reason = _identifier(reason, "metric unavailability reason")
        result[name] = {
            "applicable": applicable,
            "value": thaw_model_data(raw_metric["value"]),
            "evidence_receipt_id": evidence,
            "reason": reason,
        }
    return result


@dataclass(frozen=True)
class PublicExecutionConditionReceipt:
    version: str
    condition_id: str
    condition_fingerprint: str
    target_mechanism: str | None
    treatment_id: str
    treatment_fingerprint: str
    runtime_identity: str
    contract_status: str
    outcome: str
    boundary_receipt_refs: Mapping[str, str]
    fault_receipt_refs: tuple[str, ...]
    condition_metrics: Mapping[str, Any]
    framework_result_fingerprints: tuple[str, ...]
    framework_receipt_fingerprints: tuple[str, ...]
    checkpoint_receipt_fingerprints: tuple[str, ...]
    replay_receipt_fingerprints: tuple[str, ...]
    provider_calls: int
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "boundary_receipt_refs",
            freeze_model_payload(self.boundary_receipt_refs),
        )
        object.__setattr__(
            self, "condition_metrics", freeze_model_payload(
                self.condition_metrics),
        )

    @classmethod
    def create(
        cls, *, condition_id: str, condition_fingerprint: str,
        target_mechanism: str | None, treatment_id: str,
        treatment_fingerprint: str, runtime_identity: str,
        contract_status: str, outcome: str,
        boundary_receipt_refs: Mapping[str, str],
        fault_receipt_refs: Sequence[str],
        condition_metrics: Mapping[str, Any],
        framework_result_fingerprints: Sequence[str],
        framework_receipt_fingerprints: Sequence[str],
        checkpoint_receipt_fingerprints: Sequence[str],
        replay_receipt_fingerprints: Sequence[str],
        provider_calls: int,
    ) -> "PublicExecutionConditionReceipt":
        status = _identifier(contract_status, "contract status")
        if status not in _CONTRACT_STATUSES:
            raise ValueError(
                "public condition receipt contract status is invalid")
        if (
            isinstance(provider_calls, bool)
            or not isinstance(provider_calls, int)
            or provider_calls < 0
        ):
            raise ValueError(
                "public condition receipt provider calls are invalid")
        boundary_refs = _boundary_references(boundary_receipt_refs)
        fault_refs = _reference_sequence(
            fault_receipt_refs, "fault references")
        result_refs = _reference_sequence(
            framework_result_fingerprints, "framework result references")
        receipt_refs = _reference_sequence(
            framework_receipt_fingerprints,
            "framework receipt references",
        )
        checkpoint_refs = _reference_sequence(
            checkpoint_receipt_fingerprints, "checkpoint references")
        replay_refs = _reference_sequence(
            replay_receipt_fingerprints, "replay references")
        if status == "violated" and any((
            result_refs, receipt_refs, checkpoint_refs, replay_refs,
        )):
            raise ValueError(
                "contract-negative receipt cannot publish compliant runtime evidence")
        evidence_refs = set(boundary_refs.values()) | set(fault_refs) | set(
            result_refs) | set(receipt_refs) | set(checkpoint_refs) | set(
                replay_refs)
        metrics = _metrics(condition_metrics, evidence_refs=evidence_refs)
        material = {
            "version": PUBLIC_EXECUTION_CONDITION_RECEIPT_VERSION,
            "condition_id": _identifier(condition_id, "condition identity"),
            "condition_fingerprint": _identifier(
                condition_fingerprint, "condition fingerprint"),
            "target_mechanism": _optional_identifier(
                target_mechanism, "target mechanism"),
            "treatment_id": _identifier(treatment_id, "treatment identity"),
            "treatment_fingerprint": _identifier(
                treatment_fingerprint, "treatment fingerprint"),
            "runtime_identity": _identifier(
                runtime_identity, "runtime identity"),
            "contract_status": status,
            "outcome": _identifier(outcome, "outcome"),
            "boundary_receipt_refs": boundary_refs,
            "fault_receipt_refs": list(fault_refs),
            "condition_metrics": metrics,
            "framework_result_fingerprints": list(result_refs),
            "framework_receipt_fingerprints": list(receipt_refs),
            "checkpoint_receipt_fingerprints": list(checkpoint_refs),
            "replay_receipt_fingerprints": list(replay_refs),
            "provider_calls": provider_calls,
        }
        return cls(
            version=material["version"],
            condition_id=material["condition_id"],
            condition_fingerprint=material["condition_fingerprint"],
            target_mechanism=material["target_mechanism"],
            treatment_id=material["treatment_id"],
            treatment_fingerprint=material["treatment_fingerprint"],
            runtime_identity=material["runtime_identity"],
            contract_status=material["contract_status"],
            outcome=material["outcome"],
            boundary_receipt_refs=boundary_refs,
            fault_receipt_refs=fault_refs,
            condition_metrics=metrics,
            framework_result_fingerprints=result_refs,
            framework_receipt_fingerprints=receipt_refs,
            checkpoint_receipt_fingerprints=checkpoint_refs,
            replay_receipt_fingerprints=replay_refs,
            provider_calls=provider_calls,
            fingerprint=model_data_fingerprint(material),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "condition_id": self.condition_id,
            "condition_fingerprint": self.condition_fingerprint,
            "target_mechanism": self.target_mechanism,
            "treatment_id": self.treatment_id,
            "treatment_fingerprint": self.treatment_fingerprint,
            "runtime_identity": self.runtime_identity,
            "contract_status": self.contract_status,
            "outcome": self.outcome,
            "boundary_receipt_refs": thaw_model_data(
                self.boundary_receipt_refs),
            "fault_receipt_refs": list(self.fault_receipt_refs),
            "condition_metrics": thaw_model_data(self.condition_metrics),
            "framework_result_fingerprints": list(
                self.framework_result_fingerprints),
            "framework_receipt_fingerprints": list(
                self.framework_receipt_fingerprints),
            "checkpoint_receipt_fingerprints": list(
                self.checkpoint_receipt_fingerprints),
            "replay_receipt_fingerprints": list(
                self.replay_receipt_fingerprints),
            "provider_calls": self.provider_calls,
            "fingerprint": self.fingerprint,
        }


def verify_public_execution_condition_receipt_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ValueError("public condition receipt fields are invalid")
    record = thaw_model_data(value)
    expected = PublicExecutionConditionReceipt.create(**{
        field: record[field]
        for field in _FIELDS - {"version", "fingerprint"}
    })
    if (
        record.get("version") != expected.version
        or record != expected.to_record()
    ):
        raise ValueError("public condition receipt integrity is invalid")
    return record


__all__ = (
    "PUBLIC_EXECUTION_CONDITION_RECEIPT_VERSION",
    "PublicExecutionConditionReceipt",
    "verify_public_execution_condition_receipt_record",
)
