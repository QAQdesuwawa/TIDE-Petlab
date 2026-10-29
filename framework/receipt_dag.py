"""Package-neutral lifecycle receipt DAG validation.

The validator is deliberately read-only.  It never invokes a model, advances a
progress domain, repairs evidence, or commits a fact.  Stage names and edges
come from the caller's explicit plan; this module only verifies the common
runtime contract carried by model-stage receipts and verified framework
artifact projections.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelStageExecution,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelStageReceiptV2
from framework.runtime_kernel import RuntimeSessionBinding
from framework.temporal_domain import TemporalPosition


RECEIPT_DAG_VERSION = "framework-receipt-dag-v2"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


@dataclass(frozen=True)
class LifecycleStageSpec:
    stage: str
    depends_on: tuple[str, ...]
    enabled: bool
    runtime_ordinal_offset: int

    @classmethod
    def create(
        cls, stage: str, *, depends_on: Sequence[str] = (), enabled: bool = True,
        runtime_ordinal_offset: int = 0,
    ) -> "LifecycleStageSpec":
        if type(enabled) is not bool:
            raise ValueError("stage enabled state must be boolean")
        if (isinstance(runtime_ordinal_offset, bool)
                or not isinstance(runtime_ordinal_offset, int)
                or runtime_ordinal_offset < 0):
            raise ValueError("stage runtime ordinal offset must be non-negative")
        dependencies = tuple(
            _identifier(item, "dependency stage") for item in depends_on)
        if len(dependencies) != len(set(dependencies)):
            raise ValueError("stage dependencies must be unique")
        name = _identifier(stage, "stage")
        if name in dependencies:
            raise ValueError("stage cannot depend on itself")
        return cls(
            stage=name,
            depends_on=dependencies,
            enabled=enabled,
            runtime_ordinal_offset=runtime_ordinal_offset,
        )


@dataclass(frozen=True)
class LifecycleReceiptPlan:
    version: str
    stages: tuple[LifecycleStageSpec, ...]
    fingerprint: str

    @classmethod
    def create(
        cls, stages: Sequence[LifecycleStageSpec],
    ) -> "LifecycleReceiptPlan":
        normalized = tuple(stages)
        if not normalized:
            raise ValueError("receipt plan must contain at least one stage")
        if not all(isinstance(item, LifecycleStageSpec) for item in normalized):
            raise TypeError("receipt plan stages must be LifecycleStageSpec")
        names = tuple(item.stage for item in normalized)
        if len(names) != len(set(names)):
            raise ValueError("receipt plan stages must be unique")
        prior: set[str] = set()
        enabled: dict[str, bool] = {}
        for item in normalized:
            missing = set(item.depends_on) - prior
            if missing:
                raise ValueError(
                    "stage dependencies must reference earlier stages: "
                    + ", ".join(sorted(missing)))
            disabled_parents = {
                parent for parent in item.depends_on if not enabled[parent]
            }
            if item.enabled and disabled_parents:
                raise ValueError(
                    "enabled stage cannot depend on a disabled stage: "
                    + ", ".join(sorted(disabled_parents)))
            prior.add(item.stage)
            enabled[item.stage] = item.enabled
        material = {
            "version": RECEIPT_DAG_VERSION,
            "stages": [
                {
                    "stage": item.stage,
                    "depends_on": list(item.depends_on),
                    "enabled": item.enabled,
                    "runtime_ordinal_offset": item.runtime_ordinal_offset,
                }
                for item in normalized
            ],
        }
        return cls(
            version=RECEIPT_DAG_VERSION,
            stages=normalized,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        if LifecycleReceiptPlan.create(self.stages) != self:
            raise ValueError("receipt plan integrity is invalid")


@dataclass(frozen=True)
class LifecycleStageEvidence:
    stage: str
    execution: ModelStageExecution
    invocation_payload: Any
    parent_receipt_refs: tuple[str, ...]
    receipt_ref: str
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "invocation_payload", freeze_model_payload(
            {"value": self.invocation_payload})["value"])

    @classmethod
    def create(
        cls, *, execution: ModelStageExecution, invocation_payload: Any,
        parent_receipt_refs: Sequence[str] = (),
    ) -> "LifecycleStageEvidence":
        if not isinstance(execution, ModelStageExecution):
            raise TypeError("stage evidence requires ModelStageExecution")
        if not isinstance(execution.receipt, ModelStageReceiptV2):
            raise TypeError("stage evidence requires a v2 model-stage receipt")
        parents = tuple(
            _identifier(item, "parent receipt reference")
            for item in parent_receipt_refs)
        if len(parents) != len(set(parents)):
            raise ValueError("parent receipt references must be unique")
        payload = freeze_model_payload({"value": invocation_payload})["value"]
        receipt_ref = model_data_fingerprint(execution.receipt.to_record())
        material = {
            "version": RECEIPT_DAG_VERSION,
            "stage": execution.receipt.stage,
            "receipt_ref": receipt_ref,
            "invocation_payload": thaw_model_data(payload),
            "parent_receipt_refs": list(parents),
            "output": thaw_model_data(execution.payload),
        }
        return cls(
            stage=_identifier(execution.receipt.stage, "receipt stage"),
            execution=execution,
            invocation_payload=payload,
            parent_receipt_refs=parents,
            receipt_ref=receipt_ref,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        if LifecycleStageEvidence.create(
            execution=self.execution,
            invocation_payload=self.invocation_payload,
            parent_receipt_refs=self.parent_receipt_refs,
        ) != self:
            raise ValueError("stage evidence integrity is invalid")


@dataclass(frozen=True)
class LifecycleArtifactEvidence:
    """Read-only projection of a verified non-model framework artifact."""

    stage: str
    artifact_kind: str
    receipt_ref: str
    session_binding_fingerprint: str
    temporal_position_fingerprint: str
    runtime_ordinal: int
    input_payload: Any
    output_payload: Any
    input_fingerprint: str
    output_fingerprint: str
    parent_receipt_refs: tuple[str, ...]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "input_payload", freeze_model_payload(
            {"value": self.input_payload})["value"])
        object.__setattr__(self, "output_payload", freeze_model_payload(
            {"value": self.output_payload})["value"])

    @classmethod
    def create(
        cls, *, stage: str, artifact_kind: str, receipt_ref: str,
        session_binding_fingerprint: str,
        temporal_position_fingerprint: str, runtime_ordinal: int,
        input_payload: Any, output_payload: Any,
        parent_receipt_refs: Sequence[str] = (),
    ) -> "LifecycleArtifactEvidence":
        if (isinstance(runtime_ordinal, bool)
                or not isinstance(runtime_ordinal, int)
                or runtime_ordinal < 0):
            raise ValueError("artifact runtime ordinal must be non-negative")
        input_value = freeze_model_payload({"value": input_payload})["value"]
        output_value = freeze_model_payload({"value": output_payload})["value"]
        parents = tuple(
            _identifier(item, "parent receipt reference")
            for item in parent_receipt_refs)
        if len(parents) != len(set(parents)):
            raise ValueError("parent receipt references must be unique")
        material = {
            "version": RECEIPT_DAG_VERSION,
            "stage": _identifier(stage, "stage"),
            "artifact_kind": _identifier(artifact_kind, "artifact kind"),
            "receipt_ref": _identifier(receipt_ref, "receipt reference"),
            "session_binding_fingerprint": _identifier(
                session_binding_fingerprint,
                "session binding fingerprint",
            ),
            "temporal_position_fingerprint": _identifier(
                temporal_position_fingerprint,
                "temporal position fingerprint",
            ),
            "runtime_ordinal": runtime_ordinal,
            "input_fingerprint": model_data_fingerprint(input_value),
            "output_fingerprint": model_data_fingerprint(output_value),
            "parent_receipt_refs": list(parents),
        }
        return cls(
            stage=material["stage"],
            artifact_kind=material["artifact_kind"],
            receipt_ref=material["receipt_ref"],
            session_binding_fingerprint=material[
                "session_binding_fingerprint"],
            temporal_position_fingerprint=material[
                "temporal_position_fingerprint"],
            runtime_ordinal=runtime_ordinal,
            input_payload=input_value,
            output_payload=output_value,
            input_fingerprint=material["input_fingerprint"],
            output_fingerprint=material["output_fingerprint"],
            parent_receipt_refs=parents,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        if LifecycleArtifactEvidence.create(
            stage=self.stage,
            artifact_kind=self.artifact_kind,
            receipt_ref=self.receipt_ref,
            session_binding_fingerprint=self.session_binding_fingerprint,
            temporal_position_fingerprint=self.temporal_position_fingerprint,
            runtime_ordinal=self.runtime_ordinal,
            input_payload=self.input_payload,
            output_payload=self.output_payload,
            parent_receipt_refs=self.parent_receipt_refs,
        ) != self:
            raise ValueError("artifact evidence integrity is invalid")


@dataclass(frozen=True)
class LifecycleFailureEvidence:
    """Content-free proof that one declared stage terminated in failure."""

    stage: str
    failure_kind: str
    failure_ref: str
    session_binding_fingerprint: str
    temporal_position_fingerprint: str
    runtime_ordinal: int
    parent_receipt_refs: tuple[str, ...]
    fingerprint: str

    @classmethod
    def create(
        cls, *, stage: str, failure_kind: str, failure_ref: str,
        session_binding_fingerprint: str,
        temporal_position_fingerprint: str, runtime_ordinal: int,
        parent_receipt_refs: Sequence[str] = (),
    ) -> "LifecycleFailureEvidence":
        if (isinstance(runtime_ordinal, bool)
                or not isinstance(runtime_ordinal, int)
                or runtime_ordinal < 0):
            raise ValueError("failure runtime ordinal must be non-negative")
        parents = tuple(
            _identifier(item, "parent receipt reference")
            for item in parent_receipt_refs)
        if len(parents) != len(set(parents)):
            raise ValueError("parent receipt references must be unique")
        material = {
            "version": RECEIPT_DAG_VERSION,
            "stage": _identifier(stage, "stage"),
            "failure_kind": _identifier(failure_kind, "failure kind"),
            "failure_ref": _identifier(failure_ref, "failure reference"),
            "session_binding_fingerprint": _identifier(
                session_binding_fingerprint,
                "session binding fingerprint",
            ),
            "temporal_position_fingerprint": _identifier(
                temporal_position_fingerprint,
                "temporal position fingerprint",
            ),
            "runtime_ordinal": runtime_ordinal,
            "parent_receipt_refs": list(parents),
        }
        return cls(
            stage=material["stage"],
            failure_kind=material["failure_kind"],
            failure_ref=material["failure_ref"],
            session_binding_fingerprint=material[
                "session_binding_fingerprint"],
            temporal_position_fingerprint=material[
                "temporal_position_fingerprint"],
            runtime_ordinal=runtime_ordinal,
            parent_receipt_refs=parents,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        if LifecycleFailureEvidence.create(
            stage=self.stage,
            failure_kind=self.failure_kind,
            failure_ref=self.failure_ref,
            session_binding_fingerprint=self.session_binding_fingerprint,
            temporal_position_fingerprint=self.temporal_position_fingerprint,
            runtime_ordinal=self.runtime_ordinal,
            parent_receipt_refs=self.parent_receipt_refs,
        ) != self:
            raise ValueError("failure evidence integrity is invalid")


@dataclass(frozen=True)
class LifecycleReceiptAudit:
    version: str
    plan_fingerprint: str
    passed: bool
    first_failed_stage: str | None
    failure_code: str | None
    failure_detail: str | None
    passed_stages: tuple[str, ...]
    not_executed_stages: tuple[str, ...]
    untrusted_downstream_stages: tuple[str, ...]
    receipt_refs: Mapping[str, str]
    parent_receipt_refs: Mapping[str, tuple[str, ...]]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt_refs", freeze_model_payload(
            self.receipt_refs))
        object.__setattr__(self, "parent_receipt_refs", freeze_model_payload(
            self.parent_receipt_refs))

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "plan_fingerprint": self.plan_fingerprint,
            "passed": self.passed,
            "first_failed_stage": self.first_failed_stage,
            "failure_code": self.failure_code,
            "failure_detail": self.failure_detail,
            "passed_stages": list(self.passed_stages),
            "not_executed_stages": list(self.not_executed_stages),
            "untrusted_downstream_stages": list(
                self.untrusted_downstream_stages),
            "receipt_refs": thaw_model_data(self.receipt_refs),
            "parent_receipt_refs": thaw_model_data(
                self.parent_receipt_refs),
            "fingerprint": self.fingerprint,
        }


def _audit_result(
    *, plan: LifecycleReceiptPlan, passed: bool,
    first_failed_stage: str | None, failure_code: str | None,
    failure_detail: str | None, passed_stages: Sequence[str],
    not_executed_stages: Sequence[str],
    untrusted_downstream_stages: Sequence[str],
    receipt_refs: Mapping[str, str],
) -> LifecycleReceiptAudit:
    refs = dict(sorted(receipt_refs.items()))
    specs = {item.stage: item for item in plan.stages}
    parents = {
        stage: tuple(refs[parent] for parent in specs[stage].depends_on)
        for stage in refs
    }
    material = {
        "version": RECEIPT_DAG_VERSION,
        "plan_fingerprint": plan.fingerprint,
        "passed": passed,
        "first_failed_stage": first_failed_stage,
        "failure_code": failure_code,
        "failure_detail": failure_detail,
        "passed_stages": list(passed_stages),
        "not_executed_stages": list(not_executed_stages),
        "untrusted_downstream_stages": list(untrusted_downstream_stages),
        "receipt_refs": refs,
        "parent_receipt_refs": {
            stage: list(parent_refs)
            for stage, parent_refs in sorted(parents.items())
        },
    }
    return LifecycleReceiptAudit(
        version=RECEIPT_DAG_VERSION,
        plan_fingerprint=plan.fingerprint,
        passed=passed,
        first_failed_stage=first_failed_stage,
        failure_code=failure_code,
        failure_detail=failure_detail,
        passed_stages=tuple(passed_stages),
        not_executed_stages=tuple(not_executed_stages),
        untrusted_downstream_stages=tuple(untrusted_downstream_stages),
        receipt_refs=refs,
        parent_receipt_refs=parents,
        fingerprint=model_data_fingerprint(material),
    )


def audit_lifecycle_receipts(
    *, plan: LifecycleReceiptPlan, session_binding: RuntimeSessionBinding,
    temporal_position: TemporalPosition, runtime_ordinal: int,
    scope_ref: str | None, subject_ref: str | None, event_ref: str | None,
    action_ref: str | None,
    evidence: Mapping[
        str, LifecycleStageEvidence | LifecycleArtifactEvidence
        | LifecycleFailureEvidence],
) -> LifecycleReceiptAudit:
    """Validate one declared lifecycle and return its first broken seam."""
    if not isinstance(plan, LifecycleReceiptPlan):
        raise TypeError("receipt audit requires LifecycleReceiptPlan")
    plan.verify_integrity()
    if not isinstance(session_binding, RuntimeSessionBinding):
        raise TypeError("receipt audit requires RuntimeSessionBinding")
    session_binding.verify_integrity()
    if not isinstance(temporal_position, TemporalPosition):
        raise TypeError("receipt audit requires TemporalPosition")
    temporal_position.verify_integrity()
    if temporal_position.binding != session_binding.temporal_binding:
        raise ValueError("audit position uses a different progress domain")
    if (isinstance(runtime_ordinal, bool)
            or not isinstance(runtime_ordinal, int)
            or runtime_ordinal < 0):
        raise ValueError("audit runtime ordinal must be non-negative")
    if not isinstance(evidence, Mapping):
        raise TypeError("receipt evidence must be a mapping")

    plan_names = tuple(item.stage for item in plan.stages)
    unexpected = sorted(set(evidence) - set(plan_names))
    if unexpected:
        return _audit_result(
            plan=plan, passed=False, first_failed_stage=unexpected[0],
            failure_code="unexpected_receipt",
            failure_detail="receipt stage is absent from the declared plan",
            passed_stages=(), not_executed_stages=(),
            untrusted_downstream_stages=tuple(unexpected), receipt_refs={},
        )

    binding = session_binding.model_binding
    passed_stages: list[str] = []
    receipt_refs: dict[str, str] = {}
    for index, spec in enumerate(plan.stages):
        item = evidence.get(spec.stage)
        if not spec.enabled:
            if item is None:
                continue
            failure_code = "disabled_stage_executed"
            failure_detail = "disabled stage produced receipt evidence"
        elif item is None:
            failure_code = "missing_receipt"
            failure_detail = "enabled stage produced no receipt evidence"
        elif not isinstance(
            item, (
                LifecycleStageEvidence,
                LifecycleArtifactEvidence,
                LifecycleFailureEvidence,
            )
        ):
            failure_code = "invalid_evidence_type"
            failure_detail = "stage evidence has an unsupported type"
        else:
            try:
                item.verify_integrity()
            except (TypeError, ValueError) as exc:
                failure_code = "evidence_integrity_mismatch"
                failure_detail = str(exc)
            else:
                expected_parents = tuple(
                    receipt_refs[parent] for parent in spec.depends_on)
                expected_ordinal = (
                    runtime_ordinal + spec.runtime_ordinal_offset)
                if isinstance(item, LifecycleFailureEvidence):
                    checks = (
                        (item.stage == spec.stage,
                         "stage_mismatch", "failure stage differs from plan"),
                        (
                            item.session_binding_fingerprint
                            == session_binding.fingerprint,
                            "session_binding_mismatch",
                            "failure session differs from runtime binding",
                        ),
                        (
                            item.temporal_position_fingerprint
                            == temporal_position.fingerprint,
                            "temporal_position_mismatch",
                            "failure position differs from audited position",
                        ),
                        (item.runtime_ordinal == expected_ordinal,
                         "runtime_ordinal_mismatch",
                         "failure ordinal differs from audited stage ordinal"),
                        (item.parent_receipt_refs == expected_parents,
                         "parent_receipt_mismatch",
                         "failure parents differ from the declared DAG"),
                    )
                    failure_code = None
                    failure_detail = None
                    for ok, code, detail in checks:
                        if not ok:
                            failure_code = code
                            failure_detail = detail
                            break
                    if failure_code is None:
                        receipt_refs[spec.stage] = item.failure_ref
                        remaining = plan.stages[index + 1:]
                        not_executed = tuple(
                            stage.stage for stage in remaining
                            if stage.enabled and stage.stage not in evidence)
                        untrusted = tuple(
                            stage.stage for stage in remaining
                            if stage.stage in evidence)
                        return _audit_result(
                            plan=plan,
                            passed=False,
                            first_failed_stage=spec.stage,
                            failure_code=item.failure_kind,
                            failure_detail="verified stage failure",
                            passed_stages=passed_stages,
                            not_executed_stages=not_executed,
                            untrusted_downstream_stages=untrusted,
                            receipt_refs=receipt_refs,
                        )
                elif isinstance(item, LifecycleStageEvidence):
                    receipt = item.execution.receipt
                    checks = (
                        (item.stage == spec.stage and receipt.stage == spec.stage,
                         "stage_mismatch", "receipt stage differs from plan"),
                        (
                            receipt.package_id == binding.package_id
                            and receipt.package_version == binding.package_version
                            and receipt.schema_fingerprint
                            == binding.schema_fingerprint
                            and receipt.parameter_fingerprint
                            == binding.parameter_fingerprint
                            and receipt.operator_version
                            == binding.operator_versions.get(spec.stage),
                            "model_binding_mismatch",
                            "receipt does not match the bound model package",
                        ),
                        (receipt.session_id == session_binding.session_id,
                         "session_binding_mismatch",
                         "receipt session differs from runtime binding"),
                        (
                            receipt.scope_ref == scope_ref
                            and receipt.subject_ref == subject_ref
                            and receipt.event_ref == event_ref
                            and receipt.action_ref == action_ref,
                            "fact_identity_mismatch",
                            "receipt fact identity differs from the audited fact",
                        ),
                        (receipt.temporal_position == temporal_position,
                         "temporal_position_mismatch",
                         "receipt position differs from the audited position"),
                        (receipt.runtime_ordinal == expected_ordinal,
                         "runtime_ordinal_mismatch",
                         "receipt ordinal differs from the audited stage ordinal"),
                        (
                            receipt.input_fingerprint
                            == model_data_fingerprint(item.invocation_payload),
                            "input_fingerprint_mismatch",
                            "receipt input fingerprint differs from invocation input",
                        ),
                        (
                            receipt.output_fingerprint
                            == model_data_fingerprint(item.execution.payload),
                            "output_fingerprint_mismatch",
                            "receipt output fingerprint differs from stage output",
                        ),
                        (item.parent_receipt_refs == expected_parents,
                         "parent_receipt_mismatch",
                         "stage parent receipts differ from the declared DAG"),
                    )
                else:
                    checks = (
                        (item.stage == spec.stage,
                         "stage_mismatch", "artifact stage differs from plan"),
                        (
                            item.session_binding_fingerprint
                            == session_binding.fingerprint,
                            "session_binding_mismatch",
                            "artifact session binding differs from runtime binding",
                        ),
                        (
                            item.temporal_position_fingerprint
                            == temporal_position.fingerprint,
                            "temporal_position_mismatch",
                            "artifact position differs from audited position",
                        ),
                        (item.runtime_ordinal == expected_ordinal,
                         "runtime_ordinal_mismatch",
                         "artifact ordinal differs from audited stage ordinal"),
                        (item.parent_receipt_refs == expected_parents,
                         "parent_receipt_mismatch",
                         "artifact parents differ from the declared DAG"),
                    )
                failure_code = None
                failure_detail = None
                for ok, code, detail in checks:
                    if not ok:
                        failure_code = code
                        failure_detail = detail
                        break
                if failure_code is None:
                    passed_stages.append(spec.stage)
                    receipt_refs[spec.stage] = item.receipt_ref
                    continue

        remaining = plan.stages[index:]
        not_executed = tuple(
            stage.stage for stage in remaining
            if stage.enabled and stage.stage not in evidence)
        untrusted = tuple(
            stage.stage for stage in remaining
            if stage.stage in evidence and stage.stage != spec.stage)
        if spec.enabled and spec.stage not in evidence:
            not_executed = (spec.stage,) + tuple(
                name for name in not_executed if name != spec.stage)
        return _audit_result(
            plan=plan, passed=False, first_failed_stage=spec.stage,
            failure_code=failure_code, failure_detail=failure_detail,
            passed_stages=passed_stages,
            not_executed_stages=not_executed,
            untrusted_downstream_stages=untrusted,
            receipt_refs=receipt_refs,
        )

    return _audit_result(
        plan=plan, passed=True, first_failed_stage=None,
        failure_code=None, failure_detail=None,
        passed_stages=passed_stages, not_executed_stages=(),
        untrusted_downstream_stages=(), receipt_refs=receipt_refs,
    )


__all__ = (
    "RECEIPT_DAG_VERSION",
    "LifecycleReceiptAudit",
    "LifecycleReceiptPlan",
    "LifecycleArtifactEvidence",
    "LifecycleFailureEvidence",
    "LifecycleStageEvidence",
    "LifecycleStageSpec",
    "audit_lifecycle_receipts",
)
