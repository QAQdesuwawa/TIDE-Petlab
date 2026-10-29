"""Legacy monolithic Memory-product reader for Interaction Model API v1."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from framework.model_api import (
    ModelStageExecution,
    ModelStageReceipt,
    model_data_fingerprint,
)
from framework.model_api.memory import _event_ids, _identifier


@dataclass(frozen=True)
class ActualRecallDecision:
    """One v1 package's settled, exact-ID recall set for one trigger."""

    decision_version: str
    candidate_event_ids: tuple[str, ...]
    recalled_event_ids: tuple[str, ...]
    candidate_evidence: Mapping[str, Mapping[str, Any]]
    stage_receipt: ModelStageReceipt


def read_actual_recall_decision(
    execution: ModelStageExecution,
    *,
    trigger_event_id: str,
    evaluation_time_ms: int,
    candidate_event_ids: Sequence[str],
) -> ActualRecallDecision:
    """Validate and read the frozen v1 monolithic actual-recall result."""
    if not isinstance(execution, ModelStageExecution):
        raise ValueError("actual recall requires a model stage execution")
    trigger = _identifier(trigger_event_id, "trigger_event_id")
    if type(evaluation_time_ms) is not int or evaluation_time_ms < 0:
        raise ValueError("actual recall evaluation time must be non-negative")
    expected_candidates = _event_ids(candidate_event_ids, "candidate_event_ids")

    receipt = execution.receipt
    if receipt.stage != "memory_product":
        raise ValueError("actual recall requires a memory product stage")
    if receipt.event_id != trigger:
        raise ValueError("actual recall differs from the committed trigger")
    if receipt.at_ms != evaluation_time_ms:
        raise ValueError("actual recall differs from the evaluation time")
    if model_data_fingerprint(execution.payload) != receipt.output_fingerprint:
        raise ValueError("actual recall payload differs from stage receipt")

    decision = execution.payload.get("actual_recall")
    if not isinstance(decision, Mapping):
        raise ValueError("actual recall requires a structured decision")
    decision_version = _identifier(
        decision.get("decision_version"), "actual recall decision version")
    published_candidates = _event_ids(
        decision.get("candidate_event_ids"),
        "actual_recall.candidate_event_ids",
    )
    if (len(published_candidates) != len(expected_candidates)
            or set(published_candidates) != set(expected_candidates)):
        raise ValueError("actual recall candidate set differs from settled candidates")

    recalled = _event_ids(
        decision.get("recalled_event_ids"),
        "actual_recall.recalled_event_ids",
    )
    if not set(recalled).issubset(set(expected_candidates)):
        raise ValueError("actual recall contains a non-candidate recalled event")

    evidence = decision.get("candidate_evidence")
    if not isinstance(evidence, Mapping):
        raise ValueError("actual recall requires evidence for every candidate")
    normalized_evidence: dict[str, Mapping[str, Any]] = {}
    for raw_event_id, raw_evidence in evidence.items():
        event_id = _identifier(raw_event_id, "actual_recall.candidate_evidence key")
        if not isinstance(raw_evidence, Mapping):
            raise ValueError("actual recall candidate evidence must be structured")
        normalized_evidence[event_id] = raw_evidence
    if set(normalized_evidence) != set(expected_candidates):
        raise ValueError("actual recall candidate evidence set is incomplete")

    recalled_set = set(recalled)
    canonical_recalled = tuple(
        event_id for event_id in expected_candidates if event_id in recalled_set)
    canonical_evidence = MappingProxyType({
        event_id: normalized_evidence[event_id]
        for event_id in expected_candidates
    })
    return ActualRecallDecision(
        decision_version=decision_version,
        candidate_event_ids=expected_candidates,
        recalled_event_ids=canonical_recalled,
        candidate_evidence=canonical_evidence,
        stage_receipt=receipt,
    )


__all__ = ("ActualRecallDecision", "read_actual_recall_decision")
