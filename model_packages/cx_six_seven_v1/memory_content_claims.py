"""CX six/seven text-free claims for optional content attention.

The claim schema is published by the reference model package.  A downstream
attention extension may consume it structurally, but neither the framework
History contract nor the extension owns this package-specific shape.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from typing import Any, Union

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


def _verified_recall_stage(
    execution: ModelStageExecution,
    *,
    expected_context: ModelContext,
) -> ModelReceipt:
    if not isinstance(execution, ModelStageExecution):
        raise ValueError("recall_history requires a model stage execution")
    if not isinstance(
        expected_context, (ModelInvocationContext, ModelInvocationContextV2),
    ):
        raise ValueError("expected_context must be a model invocation context")
    receipt = execution.receipt
    if receipt.stage != "recall_history":
        raise ValueError("memory content claims require recall_history stage")
    if not same_model_fact_identity(expected_context, receipt):
        raise ValueError("recall_history receipt context identity is invalid")
    if model_data_fingerprint(execution.payload) != receipt.output_fingerprint:
        raise ValueError("recall_history payload differs from stage receipt")
    return receipt


@dataclass(frozen=True)
class MemoryContentClaim:
    """One package-published, text-free attention claim."""

    event_id: str
    strength: float
    claim_version: str
    source_evidence_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(
            self.event_id, "content claim event ID"))
        if isinstance(self.strength, bool):
            raise ValueError("content claim strength must be finite")
        try:
            strength = float(self.strength)
        except (TypeError, ValueError) as exc:
            raise ValueError("content claim strength must be finite") from exc
        if not math.isfinite(strength) or not 0.0 <= strength <= 1.0:
            raise ValueError(
                "content claim strength must be between zero and one")
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "claim_version", _identifier(
            self.claim_version, "content claim version"))
        object.__setattr__(self, "source_evidence_fingerprint", _identifier(
            self.source_evidence_fingerprint,
            "content claim source evidence fingerprint",
        ))

    def to_record(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "strength": self.strength,
            "claim_version": self.claim_version,
            "source_evidence_fingerprint": self.source_evidence_fingerprint,
        }


@dataclass(frozen=True)
class MemoryContentClaims:
    """Exact recalled IDs carrying CX six/seven attention claims."""

    version: str
    claims: tuple[MemoryContentClaim, ...]
    stage_receipt: ModelReceipt


def read_memory_content_claims(
    execution: ModelStageExecution,
    *,
    expected_context: ModelContext,
    recalled_event_ids: Sequence[str],
) -> MemoryContentClaims:
    """Read package claims without interpreting private evidence fields."""
    receipt = _verified_recall_stage(
        execution, expected_context=expected_context)
    recalled = _event_ids(recalled_event_ids, "recalled_event_ids")
    package = execution.payload.get("memory_content_claims")
    if not isinstance(package, Mapping):
        raise ValueError("recall history requires memory content claims")
    version = _identifier(
        package.get("version"), "memory content claims version")
    raw_claims = package.get("claims")
    if isinstance(raw_claims, (str, bytes)) or not isinstance(
            raw_claims, Sequence):
        raise ValueError("memory content claims must be a sequence")
    decision = execution.payload.get("recall_decision")
    evidence = decision.get("candidate_evidence") if isinstance(
        decision, Mapping) else None
    if not isinstance(evidence, Mapping):
        raise ValueError("memory content claims require candidate evidence")
    by_id: dict[str, MemoryContentClaim] = {}
    for record in raw_claims:
        if not isinstance(record, Mapping) or set(record) != {
            "event_id", "strength", "claim_version",
            "source_evidence_fingerprint",
        }:
            raise ValueError("memory content claim schema is invalid")
        claim = MemoryContentClaim(**record)
        if claim.event_id in by_id:
            raise ValueError(
                "memory content claims contain duplicate event IDs")
        source = evidence.get(claim.event_id)
        if not isinstance(source, Mapping):
            raise ValueError("memory content claim has no source evidence")
        if model_data_fingerprint(source) != claim.source_evidence_fingerprint:
            raise ValueError(
                "memory content claim evidence fingerprint is invalid")
        by_id[claim.event_id] = claim
    if set(by_id) != set(recalled):
        raise ValueError("memory content claim IDs differ from recalled IDs")
    return MemoryContentClaims(
        version=version,
        claims=tuple(by_id[event_id] for event_id in recalled),
        stage_receipt=receipt,
    )


__all__ = (
    "MemoryContentClaim",
    "MemoryContentClaims",
    "read_memory_content_claims",
)
