"""Dimension-agnostic, read-only actual-recall result contract.

The interaction model owns the recall mathematics.  The kernel only verifies
that one signed ``memory_product`` stage settled every independently supplied
candidate for the same committed trigger and evaluation time.  This module
does not retrieve candidates, derive a threshold, hydrate text, apply memory
state influence, schedule a tick, or authorize a send.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Union

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    ModelStageReceipt,
    model_data_fingerprint,
)
from framework.model_api.identity import (
    context_fact_identity,
    same_model_fact_identity,
)
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)


ModelContext = Union[ModelInvocationContext, ModelInvocationContextV2]
ModelReceipt = Union[ModelStageReceipt, ModelStageReceiptV2]


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _event_ids(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an event-id sequence")
    normalized = tuple(_identifier(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} cannot contain duplicate event IDs")
    return normalized


@dataclass(frozen=True)
class RecallDecision:
    """One v2 model-owned exact-ID recall decision."""

    version: str
    candidate_event_ids: tuple[str, ...]
    recalled_event_ids: tuple[str, ...]
    candidate_evidence: Mapping[str, Mapping[str, Any]]
    stage_receipt: ModelReceipt


@dataclass(frozen=True)
class HistoricalInfluenceProduct:
    """Opaque per-event influences and later edges for one recall decision."""

    version: str
    recalled_event_ids: tuple[str, ...]
    event_influences: Mapping[str, Mapping[str, Any]]
    later_edges: tuple[Mapping[str, Any], ...]
    stage_receipt: ModelReceipt


@dataclass(frozen=True)
class HistoricalAggregate:
    """Opaque aggregate derived from exactly one influence-stage output."""

    version: str
    recalled_event_ids: tuple[str, ...]
    source_influence_output_fingerprint: str
    aggregate: Mapping[str, Any]
    stage_receipt: ModelReceipt


def _same_context_identity(
    receipt: ModelReceipt,
    context: ModelContext,
) -> bool:
    return same_model_fact_identity(context, receipt)


def _verified_stage(
    execution: ModelStageExecution,
    *,
    stage: str,
    expected_context: ModelContext,
) -> ModelReceipt:
    if not isinstance(execution, ModelStageExecution):
        raise ValueError(f"{stage} requires a model stage execution")
    if not isinstance(
        expected_context, (ModelInvocationContext, ModelInvocationContextV2),
    ):
        raise ValueError("expected_context must be a model invocation context")
    receipt = execution.receipt
    if receipt.stage != stage:
        raise ValueError(f"memory product requires {stage} stage")
    if not _same_context_identity(receipt, expected_context):
        raise ValueError(f"{stage} receipt context identity is invalid")
    if model_data_fingerprint(execution.payload) != receipt.output_fingerprint:
        raise ValueError(f"{stage} payload differs from stage receipt")
    return receipt


def read_recall_decision(
    execution: ModelStageExecution,
    *,
    expected_context: ModelContext,
    candidate_event_ids: Sequence[str],
) -> RecallDecision:
    """Read a v2 recall product without interpreting model-owned evidence."""
    receipt = _verified_stage(
        execution, stage="recall_history", expected_context=expected_context)
    expected_candidates = _event_ids(
        candidate_event_ids, "candidate_event_ids")
    decision = execution.payload.get("recall_decision")
    if not isinstance(decision, Mapping):
        raise ValueError("recall history requires a structured recall decision")
    version = _identifier(decision.get("version"), "recall decision version")
    published_candidates = _event_ids(
        decision.get("candidate_event_ids"),
        "recall_decision.candidate_event_ids",
    )
    if (
        len(published_candidates) != len(expected_candidates)
        or set(published_candidates) != set(expected_candidates)
    ):
        raise ValueError("recall candidate set differs from kernel candidates")
    recalled = _event_ids(
        decision.get("recalled_event_ids"),
        "recall_decision.recalled_event_ids",
    )
    if not set(recalled).issubset(set(expected_candidates)):
        raise ValueError("recall decision contains a non-candidate event")
    evidence = decision.get("candidate_evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("recall decision requires candidate evidence")
    normalized_evidence: dict[str, Mapping[str, Any]] = {}
    for raw_event_id, record in evidence.items():
        event_id = _identifier(raw_event_id, "candidate evidence event ID")
        if not isinstance(record, Mapping):
            raise ValueError("recall candidate evidence must be structured")
        normalized_evidence[event_id] = record
    if set(normalized_evidence) != set(expected_candidates):
        raise ValueError("recall candidate evidence set is incomplete")
    recalled_set = set(recalled)
    return RecallDecision(
        version=version,
        candidate_event_ids=expected_candidates,
        recalled_event_ids=tuple(
            event_id for event_id in expected_candidates
            if event_id in recalled_set
        ),
        candidate_evidence=MappingProxyType({
            event_id: normalized_evidence[event_id]
            for event_id in expected_candidates
        }),
        stage_receipt=receipt,
    )

def read_historical_influence(
    execution: ModelStageExecution,
    *,
    expected_context: ModelContext,
    recalled_event_ids: Sequence[str],
) -> HistoricalInfluenceProduct:
    """Read per-event influence and exact later-edge products."""
    receipt = _verified_stage(
        execution,
        stage="read_historical_influence",
        expected_context=expected_context,
    )
    expected_recalled = _event_ids(
        recalled_event_ids, "recalled_event_ids")
    product = execution.payload.get("historical_influence")
    if not isinstance(product, Mapping):
        raise ValueError(
            "historical influence requires a structured product")
    version = _identifier(
        product.get("version"), "historical influence version")
    published_recalled = _event_ids(
        product.get("recalled_event_ids"),
        "historical_influence.recalled_event_ids",
    )
    if (
        len(published_recalled) != len(expected_recalled)
        or set(published_recalled) != set(expected_recalled)
    ):
        raise ValueError(
            "historical influence recalled event set differs from recall decision")
    influences = product.get("event_influences")
    if not isinstance(influences, Mapping):
        raise ValueError("historical influence requires event influences")
    normalized_influences: dict[str, Mapping[str, Any]] = {}
    for raw_event_id, record in influences.items():
        event_id = _identifier(raw_event_id, "event influence event ID")
        if not isinstance(record, Mapping):
            raise ValueError("historical event influence must be structured")
        normalized_influences[event_id] = record
    if set(normalized_influences) != set(expected_recalled):
        raise ValueError(
            "historical influence event set differs from recalled event set")
    raw_edges = product.get("later_edges")
    if (
        isinstance(raw_edges, (str, bytes))
        or not isinstance(raw_edges, Sequence)
    ):
        raise ValueError("historical influence later_edges must be a sequence")
    child_event_id = _identifier(
        context_fact_identity(expected_context).event_ref,
        "expected context event ID",
    )
    normalized_edges = []
    parent_ids = []
    for edge in raw_edges:
        if not isinstance(edge, Mapping):
            raise ValueError("historical influence later edge must be structured")
        if set(edge) != {
            "parent_event_id", "child_event_id", "edge_evidence",
        }:
            raise ValueError("historical influence later edge fields are invalid")
        parent_id = _identifier(
            edge.get("parent_event_id"), "later edge parent event ID")
        child_id = _identifier(
            edge.get("child_event_id"), "later edge child event ID")
        if child_id != child_event_id:
            raise ValueError(
                "historical influence later edge child differs from trigger")
        if not isinstance(edge.get("edge_evidence"), Mapping):
            raise ValueError("historical influence edge evidence must be structured")
        parent_ids.append(parent_id)
        normalized_edges.append(edge)
    if len(parent_ids) != len(set(parent_ids)):
        raise ValueError("historical influence later edges contain duplicates")
    if set(parent_ids) != set(expected_recalled):
        raise ValueError(
            "historical influence later edge parents differ from recalled events")
    return HistoricalInfluenceProduct(
        version=version,
        recalled_event_ids=expected_recalled,
        event_influences=MappingProxyType({
            event_id: normalized_influences[event_id]
            for event_id in expected_recalled
        }),
        later_edges=tuple(normalized_edges),
        stage_receipt=receipt,
    )


def read_historical_aggregate(
    execution: ModelStageExecution,
    *,
    expected_context: ModelContext,
    influence: HistoricalInfluenceProduct,
) -> HistoricalAggregate:
    """Read one aggregate bound to an exact influence-stage output."""
    receipt = _verified_stage(
        execution,
        stage="aggregate_historical_influence",
        expected_context=expected_context,
    )
    if not isinstance(influence, HistoricalInfluenceProduct):
        raise ValueError(
            "historical aggregate requires HistoricalInfluenceProduct")
    product = execution.payload.get("historical_aggregate")
    if not isinstance(product, Mapping):
        raise ValueError("historical aggregate requires a structured product")
    version = _identifier(
        product.get("version"), "historical aggregate version")
    recalled = _event_ids(
        product.get("recalled_event_ids"),
        "historical_aggregate.recalled_event_ids",
    )
    if (
        len(recalled) != len(influence.recalled_event_ids)
        or set(recalled) != set(influence.recalled_event_ids)
    ):
        raise ValueError(
            "historical aggregate recalled event set differs from influence")
    source_fingerprint = _identifier(
        product.get("source_influence_output_fingerprint"),
        "historical aggregate source influence fingerprint",
    )
    if source_fingerprint != influence.stage_receipt.output_fingerprint:
        raise ValueError(
            "historical aggregate source influence is not the accepted product")
    aggregate = product.get("aggregate")
    if not isinstance(aggregate, Mapping):
        raise ValueError("historical aggregate payload must be structured")
    return HistoricalAggregate(
        version=version,
        recalled_event_ids=influence.recalled_event_ids,
        source_influence_output_fingerprint=source_fingerprint,
        aggregate=aggregate,
        stage_receipt=receipt,
    )


__all__ = (
    "HistoricalAggregate",
    "HistoricalInfluenceProduct",
    "RecallDecision",
    "read_historical_aggregate",
    "read_historical_influence",
    "read_recall_decision",
)
