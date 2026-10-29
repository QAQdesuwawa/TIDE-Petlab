"""CX six/seven ranked expression-resource products before packet assembly.

This is a package-owned resource schema, not a framework Realizer contract.
The generic Realizer sees only opaque control. This reader verifies the
reference package's published metadata and lineage without reading state,
hydrating canonical content, building a prompt, or authorizing an action.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    ModelStageReceipt,
    model_data_fingerprint,
)
from framework.model_api.identity import same_model_fact_identity
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)


_BATCH_FIELDS = {
    "version",
    "source_shared_projection_output_fingerprint",
    "resources",
    "selection",
}
_RESOURCE_FIELDS = {
    "resource_id",
    "lane",
    "descriptor",
    "score",
    "domain",
    "neutral",
    "deviation",
    "estimated_cost",
    "source_receipt_refs",
}
_SELECTION_FIELDS = {
    "version",
    "selected_resource_ids",
    "resource_evidence",
}


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be a finite number")
    return number


def _identifiers(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an identifier sequence")
    result = tuple(_identifier(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicate identifiers")
    return result


def _same_context(
    receipt: ModelStageReceipt | ModelStageReceiptV2,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> bool:
    return same_model_fact_identity(context, receipt)


def _verify_execution(
    execution: ModelStageExecution,
    *,
    stage: str,
    expected_context: ModelInvocationContext | ModelInvocationContextV2,
) -> ModelStageReceipt | ModelStageReceiptV2:
    if not isinstance(execution, ModelStageExecution):
        raise ValueError(f"{stage} requires a model stage execution")
    if not isinstance(
        expected_context, (ModelInvocationContext, ModelInvocationContextV2),
    ):
        raise ValueError("expected_context must be a model invocation context")
    receipt = execution.receipt
    if receipt.stage != stage:
        raise ValueError(f"expression resources require {stage} stage")
    if not _same_context(receipt, expected_context):
        raise ValueError(f"{stage} receipt context identity is invalid")
    if receipt.output_fingerprint != model_data_fingerprint(execution.payload):
        raise ValueError(f"{stage} payload differs from stage receipt")
    return receipt


def _normalized_deviation(
    *, score: float, low: float, high: float, neutral: float,
) -> float:
    if math.isclose(score, neutral, rel_tol=0.0, abs_tol=1e-12):
        return 0.0
    if score > neutral:
        return (score - neutral) / (high - neutral)
    return (score - neutral) / (neutral - low)


@dataclass(frozen=True)
class ExpressionResource:
    resource_id: str
    lane: str
    descriptor: Mapping[str, Any]
    score: float
    domain_low: float
    domain_high: float
    neutral: float
    deviation: float
    estimated_cost: float
    source_receipt_refs: tuple[str, ...]


@dataclass(frozen=True)
class ExpressionResourceBatch:
    version: str
    source_shared_projection_output_fingerprint: str
    resources: Mapping[str, ExpressionResource]
    selection_version: str
    selected_resource_ids: tuple[str, ...]
    resource_evidence: Mapping[str, Mapping[str, Any]]
    stage_receipt: ModelStageReceipt | ModelStageReceiptV2
    shared_projection_receipt: ModelStageReceipt | ModelStageReceiptV2


def read_expression_resource_batch(
    execution: ModelStageExecution,
    *,
    expected_context: ModelInvocationContext | ModelInvocationContextV2,
    shared_projection_execution: ModelStageExecution,
) -> ExpressionResourceBatch:
    """Validate a model-selected resource batch without assembling Writer input."""
    shared_receipt = _verify_execution(
        shared_projection_execution,
        stage="project_shared_semantics",
        expected_context=expected_context,
    )
    receipt = _verify_execution(
        execution,
        stage="project_semantic_resources",
        expected_context=expected_context,
    )
    batch = execution.payload.get("expression_resource_batch")
    if not isinstance(batch, Mapping) or set(batch) != _BATCH_FIELDS:
        raise ValueError("expression resource batch fields are invalid")
    version = _identifier(
        batch.get("version"), "expression resource batch version")
    source_fingerprint = _identifier(
        batch.get("source_shared_projection_output_fingerprint"),
        "source shared projection output fingerprint",
    )
    if source_fingerprint != shared_receipt.output_fingerprint:
        raise ValueError(
            "expression resource batch differs from shared projection")

    raw_resources = batch.get("resources")
    if (
        isinstance(raw_resources, (str, bytes))
        or not isinstance(raw_resources, Sequence)
    ):
        raise ValueError("expression resources must be a sequence")
    resources: dict[str, ExpressionResource] = {}
    for raw in raw_resources:
        if not isinstance(raw, Mapping) or set(raw) != _RESOURCE_FIELDS:
            raise ValueError("expression resource fields are invalid")
        resource_id = _identifier(raw.get("resource_id"), "resource_id")
        if resource_id in resources:
            raise ValueError("expression resource IDs cannot be duplicated")
        lane = _identifier(raw.get("lane"), "resource lane")
        descriptor = raw.get("descriptor")
        if not isinstance(descriptor, Mapping):
            raise ValueError("expression resource descriptor must be structured")
        domain = raw.get("domain")
        if not isinstance(domain, Mapping) or set(domain) != {"low", "high"}:
            raise ValueError("expression resource domain fields are invalid")
        low = _finite(domain.get("low"), "resource domain low")
        high = _finite(domain.get("high"), "resource domain high")
        score = _finite(raw.get("score"), "resource score")
        neutral = _finite(raw.get("neutral"), "resource neutral")
        deviation = _finite(raw.get("deviation"), "resource deviation")
        cost = _finite(raw.get("estimated_cost"), "resource estimated cost")
        if not low < high:
            raise ValueError("expression resource domain must be increasing")
        if not low <= score <= high or not low <= neutral <= high:
            raise ValueError(
                "expression resource score and neutral must be inside domain")
        expected_deviation = _normalized_deviation(
            score=score, low=low, high=high, neutral=neutral)
        if not math.isclose(
            deviation, expected_deviation, rel_tol=0.0, abs_tol=1e-9,
        ):
            raise ValueError(
                "expression resource deviation differs from its own mapping")
        if cost < 0.0:
            raise ValueError("expression resource estimated cost cannot be negative")
        refs = _identifiers(
            raw.get("source_receipt_refs"), "source_receipt_refs")
        if source_fingerprint not in refs:
            raise ValueError(
                "expression resource must reference the shared projection receipt")
        resources[resource_id] = ExpressionResource(
            resource_id=resource_id,
            lane=lane,
            descriptor=descriptor,
            score=score,
            domain_low=low,
            domain_high=high,
            neutral=neutral,
            deviation=deviation,
            estimated_cost=cost,
            source_receipt_refs=refs,
        )

    selection = batch.get("selection")
    if not isinstance(selection, Mapping) or set(selection) != _SELECTION_FIELDS:
        raise ValueError("expression resource selection fields are invalid")
    selection_version = _identifier(
        selection.get("version"), "expression resource selection version")
    selected = _identifiers(
        selection.get("selected_resource_ids"), "selected resource IDs")
    if not set(selected).issubset(set(resources)):
        raise ValueError("selected resource IDs contain an unknown resource")
    evidence = selection.get("resource_evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("expression resource selection evidence is invalid")
    normalized_evidence: dict[str, Mapping[str, Any]] = {}
    for raw_id, item in evidence.items():
        resource_id = _identifier(raw_id, "resource evidence ID")
        if not isinstance(item, Mapping):
            raise ValueError("expression resource evidence must be structured")
        normalized_evidence[resource_id] = item
    if set(normalized_evidence) != set(resources):
        raise ValueError("expression resource evidence set is incomplete")
    selected_set = set(selected)
    return ExpressionResourceBatch(
        version=version,
        source_shared_projection_output_fingerprint=source_fingerprint,
        resources=MappingProxyType(resources),
        selection_version=selection_version,
        selected_resource_ids=tuple(
            resource_id for resource_id in resources
            if resource_id in selected_set
        ),
        resource_evidence=MappingProxyType(normalized_evidence),
        stage_receipt=receipt,
        shared_projection_receipt=shared_receipt,
    )


__all__ = (
    "ExpressionResource",
    "ExpressionResourceBatch",
    "read_expression_resource_batch",
)
