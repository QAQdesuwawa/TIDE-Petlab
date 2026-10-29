"""Text-free content-attention claims for policy-transaction recall.

Structured exact matches carry full package confidence.  RAG-only matches use
their calibrated non-negative address similarity.  When both witnesses name
the same exact ID, the stronger witness is retained; strengths are never
added, and these claims have no state or selection authority.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import ModelStageExecution, model_data_fingerprint
from framework.model_api.identity import same_model_fact_identity
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)


MEMORY_CONTENT_CLAIMS_VERSION = (
    "policy-transaction-memory-content-claims-v1")
MEMORY_CONTENT_CLAIM_VERSION = (
    "policy-transaction-memory-content-claim-v1")


def _unit_score(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{field} must be in [0, 1]")
    return result


def fuse_memory_relevance(
    local_score: object,
    rag_score: object,
    *,
    agreement_gain: object,
) -> float:
    """Fuse two calibrated relevance witnesses without privileging either.

    A missing source is represented by zero and therefore leaves the other
    source unchanged.  Agreement adds only the remaining headroom, so the
    result is symmetric, monotone, and bounded by one.
    """
    local = _unit_score(local_score, "local memory relevance")
    rag = _unit_score(rag_score, "RAG memory relevance")
    gain = _unit_score(agreement_gain, "memory agreement gain")
    high = max(local, rag)
    low = min(local, rag)
    return high + gain * low * (1.0 - high)


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _ids(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an exact-ID sequence")
    result = tuple(_identifier(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicate exact IDs")
    return result


def build_memory_content_claims(
    *, recalled_event_ids: Sequence[str], candidate_evidence: Mapping[str, Any],
) -> dict[str, Any]:
    recalled = _ids(recalled_event_ids, "recalled_event_ids")
    if not isinstance(candidate_evidence, Mapping):
        raise ValueError("candidate_evidence must be a mapping")
    claims = []
    for event_id in recalled:
        evidence = candidate_evidence.get(event_id)
        if not isinstance(evidence, Mapping) or evidence.get("selected") is not True:
            raise ValueError("recalled event has no selected candidate evidence")
        witness = evidence.get("selection_witness")
        if not isinstance(witness, Mapping):
            raise ValueError("selected candidate has no selection witness")
        structured = bool(witness.get("matched_reference_slots")) or (
            witness.get("matched_exact_tool_id") is not None)
        if "fused_score" in witness:
            strength = _unit_score(
                witness["fused_score"], "fused memory relevance")
        else:
            strength = 1.0 if structured else 0.0
        address = witness.get("rag_address")
        if address is not None:
            if not isinstance(address, Mapping):
                raise ValueError("RAG address witness must be a mapping")
            value = address.get("similarity")
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError("RAG address strength must be numeric")
            similarity = float(value)
            if similarity < -1.0 or similarity > 1.0:
                raise ValueError("RAG address strength must be in [-1, 1]")
            strength = max(strength, max(0.0, similarity))
        source_fingerprint = model_data_fingerprint(evidence)
        claims.append({
            "event_id": event_id,
            "strength": strength,
            "claim_version": MEMORY_CONTENT_CLAIM_VERSION,
            "source_evidence_fingerprint": source_fingerprint,
        })
    return {
        "version": MEMORY_CONTENT_CLAIMS_VERSION,
        "claims": claims,
        "state_commit_authority": False,
        "selection_authority": False,
        "content_hydration_authority": False,
    }


@dataclass(frozen=True)
class MemoryContentClaim:
    event_id: str
    strength: float
    claim_version: str
    source_evidence_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "event_id", _identifier(
            self.event_id, "content claim event ID"))
        if isinstance(self.strength, bool) or not isinstance(
                self.strength, (int, float)):
            raise ValueError("content claim strength must be numeric")
        strength = float(self.strength)
        if strength < 0.0 or strength > 1.0:
            raise ValueError("content claim strength must be in [0, 1]")
        object.__setattr__(self, "strength", strength)
        object.__setattr__(self, "claim_version", _identifier(
            self.claim_version, "content claim version"))
        object.__setattr__(self, "source_evidence_fingerprint", _identifier(
            self.source_evidence_fingerprint,
            "content claim source evidence fingerprint",
        ))


@dataclass(frozen=True)
class MemoryContentClaims:
    version: str
    claims: tuple[MemoryContentClaim, ...]
    stage_receipt: ModelStageReceiptV2


def read_memory_content_claims(
    execution: ModelStageExecution,
    *, expected_context: ModelInvocationContextV2,
    recalled_event_ids: Sequence[str],
) -> MemoryContentClaims:
    if not isinstance(execution, ModelStageExecution):
        raise ValueError("content claims require a model stage execution")
    if not isinstance(expected_context, ModelInvocationContextV2):
        raise ValueError("expected_context must be ModelInvocationContextV2")
    receipt = execution.receipt
    if receipt.stage != "recall_history":
        raise ValueError("content claims require recall_history stage")
    if not same_model_fact_identity(expected_context, receipt):
        raise ValueError("content claims use another model fact identity")
    if model_data_fingerprint(execution.payload) != receipt.output_fingerprint:
        raise ValueError("content claims differ from their stage receipt")
    recalled = _ids(recalled_event_ids, "recalled_event_ids")
    decision = execution.payload.get("recall_decision")
    evidence = decision.get("candidate_evidence") if isinstance(
        decision, Mapping) else None
    package = execution.payload.get("memory_content_claims")
    if not isinstance(evidence, Mapping) or not isinstance(package, Mapping):
        raise ValueError("recall stage omits content claim evidence")
    if package.get("version") != MEMORY_CONTENT_CLAIMS_VERSION:
        raise ValueError("content claims version is invalid")
    for field in (
        "state_commit_authority", "selection_authority",
        "content_hydration_authority",
    ):
        if package.get(field) is not False:
            raise ValueError(f"content claims permission {field} is invalid")
    raw_claims = package.get("claims")
    if isinstance(raw_claims, (str, bytes)) or not isinstance(
            raw_claims, Sequence):
        raise ValueError("content claims must be a sequence")
    claims = []
    for raw in raw_claims:
        if not isinstance(raw, Mapping) or set(raw) != {
            "event_id", "strength", "claim_version",
            "source_evidence_fingerprint",
        }:
            raise ValueError("content claim schema is invalid")
        claim = MemoryContentClaim(**raw)
        source = evidence.get(claim.event_id)
        if not isinstance(source, Mapping) or model_data_fingerprint(
                source) != claim.source_evidence_fingerprint:
            raise ValueError("content claim source evidence is invalid")
        claims.append(claim)
    if tuple(claim.event_id for claim in claims) != recalled:
        raise ValueError("content claim IDs differ from recalled event IDs")
    return MemoryContentClaims(
        version=MEMORY_CONTENT_CLAIMS_VERSION,
        claims=tuple(claims),
        stage_receipt=receipt,
    )


__all__ = (
    "MEMORY_CONTENT_CLAIMS_VERSION",
    "MemoryContentClaim",
    "MemoryContentClaims",
    "build_memory_content_claims",
    "fuse_memory_relevance",
    "read_memory_content_claims",
)
