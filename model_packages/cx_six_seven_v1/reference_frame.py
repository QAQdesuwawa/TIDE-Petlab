"""Package-owned semantic reference-frame contract.

The frame is a short-lived measurement product.  It has no Store, recall,
Writer, state, clock, or delivery authority.  Product-side delivery gates may
consume it, but they must not redefine its schema or parsing rules.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


_SCOPES = frozenset({"eligible", "third_party", "unresolved"})
_MIN_CONFIDENCE = 0.6


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value):
    return min(1.0, max(0.0, _number(value)))


def _integer(value, default=1):
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _text(value, limit=120):
    return str(value or "").strip()[:limit]


def _normalise_claims(values):
    if not isinstance(values, (list, tuple)):
        return ()
    claims = []
    for item in values:
        if not isinstance(item, dict):
            continue
        subject = _text(item.get("subject"), 40)
        target = _text(item.get("target", item.get("object")), 40)
        relation = _text(item.get("relation"), 80)
        evidence_span = item.get("evidence_span")
        if not (subject and target and relation):
            continue
        if (
            not isinstance(evidence_span, (list, tuple))
            or len(evidence_span) != 2
            or not all(isinstance(value, int) and value >= 0 for value in evidence_span)
            or evidence_span[1] < evidence_span[0]
        ):
            evidence_span = None
        else:
            evidence_span = [int(evidence_span[0]), int(evidence_span[1])]
        claims.append({
            "subject": subject,
            "target": target,
            "relation": relation,
            "evidence_span": evidence_span,
        })
    return tuple(claims)


@dataclass(frozen=True)
class ReferenceFrame:
    """One-event, auditable relation-attribution measurement."""

    turn_id: str
    delivery_scope: str
    relation_claims: tuple
    resolver_source: str
    confidence: float
    fallback_reason: Optional[str] = None
    schema_version: int = 1

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "turn_id": self.turn_id,
            "delivery_scope": self.delivery_scope,
            "relation_claims": [dict(claim) for claim in self.relation_claims],
            "resolver_source": self.resolver_source,
            "confidence": self.confidence,
            "fallback_reason": self.fallback_reason,
        }


def _unresolved(turn_id, reason):
    return ReferenceFrame(
        turn_id=str(turn_id or ""),
        delivery_scope="unresolved",
        relation_claims=(),
        resolver_source="invalid",
        confidence=0.0,
        fallback_reason=reason,
    )


def coerce_reference_frame(
    raw: Any, *, turn_id=None,
) -> Optional[ReferenceFrame]:
    """Narrow one untrusted provider value to the package schema."""
    if raw is None:
        return None
    if isinstance(raw, ReferenceFrame):
        raw = raw.as_record()
    if not isinstance(raw, dict):
        return _unresolved(turn_id, "invalid_structure")
    scope = _text(raw.get("delivery_scope"), 24).lower()
    if scope not in _SCOPES:
        return _unresolved(turn_id, "invalid_scope")
    claims = _normalise_claims(raw.get("relation_claims"))
    confidence = _clamp01(raw.get("confidence", 0.0))
    source = _text(
        raw.get("resolver_source", raw.get("source", "semantic_projector")), 80,
    )
    resolved_turn_id = str(
        turn_id if turn_id is not None else raw.get("turn_id") or ""
    )
    if scope != "unresolved" and not claims:
        return _unresolved(resolved_turn_id, "missing_relation_evidence")
    if scope != "unresolved" and confidence < _MIN_CONFIDENCE:
        return _unresolved(resolved_turn_id, "low_confidence")
    return ReferenceFrame(
        turn_id=resolved_turn_id,
        delivery_scope=scope,
        relation_claims=claims,
        resolver_source=source or "semantic_projector",
        confidence=confidence,
        fallback_reason=_text(raw.get("fallback_reason"), 120) or None,
        schema_version=_integer(raw.get("schema_version", 1), 1),
    )


__all__ = ("ReferenceFrame", "coerce_reference_frame")
