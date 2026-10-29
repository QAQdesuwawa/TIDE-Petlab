"""Optional occupation of text-free content claims.

This layer chooses which already-authorized claims may consume downstream
hydration attention.  It cannot change recall, read content, or deliver cards.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any, Mapping, Protocol

from framework.model_api import model_data_fingerprint


class ContentAttentionClaim(Protocol):
    """Structural claim consumed by the optional attention extension."""

    event_id: str
    strength: float
    claim_version: str
    source_evidence_fingerprint: str


class ContentAttentionClaimSet(Protocol):
    """Package-owned claim set; the extension owns no model schema."""

    version: str
    claims: tuple[ContentAttentionClaim, ...]


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


@dataclass(frozen=True)
class ContentOccupationPolicy:
    """Explicit extension policy; never an implicit model default."""

    version: str
    leak: float
    lateral_inhibition: float
    solver_tolerance: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _identifier(
            self.version, "content occupation policy version"))
        leak = _finite(self.leak, "leak")
        inhibition = _finite(self.lateral_inhibition, "lateral_inhibition")
        tolerance = _finite(self.solver_tolerance, "solver_tolerance")
        if leak <= 0.0 or inhibition < 0.0 or inhibition >= leak:
            raise ValueError("content occupation requires leak > inhibition >= 0")
        if tolerance <= 0.0:
            raise ValueError("solver_tolerance must be positive")
        object.__setattr__(self, "leak", leak)
        object.__setattr__(self, "lateral_inhibition", inhibition)
        object.__setattr__(self, "solver_tolerance", tolerance)


@dataclass(frozen=True)
class ContentOccupation:
    """Content-free exact-ID result for a later hydration boundary."""

    version: str
    claim_event_ids: tuple[str, ...]
    selected_for_hydration_ids: tuple[str, ...]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _identifier(
            self.version, "content occupation version"))
        claims = tuple(_identifier(item, "claim event ID")
                       for item in self.claim_event_ids)
        selected = tuple(_identifier(item, "selected hydration ID")
                         for item in self.selected_for_hydration_ids)
        if len(claims) != len(set(claims)):
            raise ValueError("content occupation claim IDs must be unique")
        if not set(selected).issubset(set(claims)):
            raise ValueError("selected hydration ID is outside content claims")
        object.__setattr__(self, "claim_event_ids", claims)
        object.__setattr__(self, "selected_for_hydration_ids", selected)
        if not isinstance(self.receipt, Mapping):
            raise ValueError("content occupation receipt must be a mapping")
        object.__setattr__(self, "receipt", MappingProxyType(dict(self.receipt)))
        self.verify_integrity()

    def verify_integrity(self) -> None:
        """Fail closed when selection fields and their receipt diverge."""
        receipt = self.receipt
        if receipt.get("operator_version") != self.version:
            raise ValueError("content occupation receipt version differs from result")
        if tuple(receipt.get("claim_event_ids", ())) != self.claim_event_ids:
            raise ValueError("content occupation receipt claim lineage is invalid")
        if tuple(receipt.get("selected_for_hydration_ids", ())) != (
                self.selected_for_hydration_ids):
            raise ValueError("content occupation receipt selection lineage is invalid")
        for field in (
            "content_hydrated", "actual_recall_mutated",
            "state_mutated", "writer_read",
        ):
            if receipt.get(field) is not False:
                raise ValueError(
                    f"content occupation receipt permission {field} is invalid")
        fingerprint = receipt.get("fingerprint")
        if not isinstance(fingerprint, str) or not fingerprint:
            raise ValueError("content occupation receipt fingerprint is missing")
        material = dict(receipt)
        material.pop("fingerprint", None)
        if model_data_fingerprint(material) != fingerprint:
            raise ValueError("content occupation receipt integrity is invalid")


def _active_set(rows, policy: ContentOccupationPolicy):
    active = [row for row in rows
              if row["strength"] > policy.solver_tolerance]
    passes = 0
    total = 0.0
    cutoff = 0.0
    while active:
        passes += 1
        denominator = policy.leak + policy.lateral_inhibition * (len(active) - 1)
        total = sum(row["strength"] for row in active) / denominator
        cutoff = policy.lateral_inhibition * total
        survivors = [row for row in active
                     if row["strength"] - cutoff > policy.solver_tolerance]
        if len(survivors) == len(active):
            break
        active = survivors
    return {row["event_id"] for row in active}, total, cutoff, passes


def settle_memory_content_occupation(
    *, claims: ContentAttentionClaimSet, policy: ContentOccupationPolicy,
) -> ContentOccupation:
    """Settle hydration attention without reading model-private state."""
    if not isinstance(policy, ContentOccupationPolicy):
        raise ValueError("policy must be ContentOccupationPolicy")
    claims_version = _identifier(
        getattr(claims, "version", None), "content claims version")
    claim_items = getattr(claims, "claims", None)
    if not isinstance(claim_items, tuple):
        raise ValueError("claims must publish an immutable claim tuple")
    rows = [
        {
            "event_id": _identifier(
                getattr(claim, "event_id", None), "claim event ID"),
            "strength": _finite(
                getattr(claim, "strength", None), "claim strength"),
            "claim_version": _identifier(
                getattr(claim, "claim_version", None), "claim version"),
            "source_evidence_fingerprint": _identifier(
                getattr(claim, "source_evidence_fingerprint", None),
                "claim source evidence fingerprint",
            ),
        }
        for claim in claim_items
    ]
    if any(not 0.0 <= row["strength"] <= 1.0 for row in rows):
        raise ValueError("claim strength must be between zero and one")
    if len({row["event_id"] for row in rows}) != len(rows):
        raise ValueError("content claims contain duplicate exact IDs")
    active, total, cutoff, passes = _active_set(rows, policy)
    claim_ids = tuple(row["event_id"] for row in rows)
    selected = tuple(event_id for event_id in claim_ids if event_id in active)
    claim_fingerprint = model_data_fingerprint({
        "claims_version": claims_version,
        "claims": rows,
    })
    receipt = {
        "operator_version": policy.version,
        "claims_version": claims_version,
        "claim_set_fingerprint": claim_fingerprint,
        "claim_event_ids": claim_ids,
        "selected_for_hydration_ids": selected,
        "equilibrium": {
            "active_count": len(active),
            "total_activation": total,
            "shared_cutoff": cutoff,
            "active_set_passes": passes,
        },
        "fixed_count_used": False,
        "content_hydrated": False,
        "actual_recall_mutated": False,
        "state_mutated": False,
        "writer_read": False,
    }
    receipt["fingerprint"] = model_data_fingerprint(receipt)
    return ContentOccupation(
        version=policy.version,
        claim_event_ids=claim_ids,
        selected_for_hydration_ids=selected,
        receipt=receipt,
    )


__all__ = (
    "ContentAttentionClaim",
    "ContentAttentionClaimSet",
    "ContentOccupation",
    "ContentOccupationPolicy",
    "settle_memory_content_occupation",
)
