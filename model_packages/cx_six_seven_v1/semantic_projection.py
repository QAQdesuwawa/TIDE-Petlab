"""Package-owned normalization for the six/seven semantic measurement."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional

from .preference_parameters import PREFERENCE_ORDER
from .reference_frame import coerce_reference_frame


VARIABLE_KEYS = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")
BEHAVIOR_PROTOTYPES = PREFERENCE_ORDER
_NON_WRITABLE_BY_EVENT_KIND = {
    "human_input": ("L_self",),
    "peer_input": ("L_self",),
    "simulation_input": ("L_self",),
    "user_input": ("L_self",),
    "npc_output": ("L_opponent",),
}


def _non_writable_axes(event_kind):
    return _NON_WRITABLE_BY_EVENT_KIND.get(
        str(event_kind or "user_input"),
        _NON_WRITABLE_BY_EVENT_KIND["user_input"],
    )


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _clamp01(value):
    return min(1.0, max(0.0, _number(value)))


def _normalise_weights(values):
    if not isinstance(values, dict):
        return None
    raw = {
        key: max(0.0, _number(values.get(key, 0.0)))
        for key in BEHAVIOR_PROTOTYPES
    }
    total = sum(raw.values())
    if total <= 0.0:
        return None
    return {key: round(value / total, 4) for key, value in raw.items()}


def _normalise_tokens(values):
    out = []
    for item in values or []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        evidence = str(item.get("evidence") or "").strip()
        out.append({
            "text": text[:240],
            "layer": "表",
            "category": "other",
            "strength": _clamp01(item.get("strength", 0.0)),
            "evidence": evidence[:240],
        })
    return tuple(out)


@dataclass(frozen=True)
class SemanticProjection:
    """The bounded continuous measurement published by this model package."""

    variable_deltas: dict
    behavior_weights: Optional[dict]
    tokens: tuple
    confidence: float
    source: str = "semantic_projector"
    model: str = "unknown"
    reference_frame: object = None
    axis_presence: Optional[dict] = None

    def as_record(self):
        return {
            "variable_deltas": dict(self.variable_deltas),
            "behavior_weights": (
                dict(self.behavior_weights)
                if self.behavior_weights is not None else None
            ),
            "tokens": [dict(token) for token in self.tokens],
            "confidence": self.confidence,
            "source": self.source,
            "model": self.model,
            "reference_frame": (
                self.reference_frame.as_record()
                if self.reference_frame is not None else None
            ),
            "axis_presence": (
                dict(self.axis_presence)
                if self.axis_presence is not None else None
            ),
        }


def _axis_presence(raw_deltas, deltas, event_kind, *, prior=None):
    if (
        isinstance(prior, dict)
        and prior.get("version") == "semantic-axis-presence-v1"
    ):
        provided = prior.get("provided_axes")
        if (
            isinstance(provided, list)
            and all(key in VARIABLE_KEYS for key in provided)
        ):
            return {
                "version": "semantic-axis-presence-v1",
                "variable_deltas_object": bool(
                    prior.get("variable_deltas_object")
                ),
                "provided_axes": [key for key in VARIABLE_KEYS if key in provided],
                "missing_axes": [key for key in VARIABLE_KEYS if key not in provided],
                "forced_zero_axes": [
                    key for key in _non_writable_axes(event_kind)
                ],
                "normalised_nonzero_axes": [
                    key for key in VARIABLE_KEYS
                    if abs(deltas.get(key, 0.0)) > 0.0
                ],
            }
    deltas_raw = raw_deltas if isinstance(raw_deltas, dict) else {}
    provided = [key for key in VARIABLE_KEYS if key in deltas_raw]
    return {
        "version": "semantic-axis-presence-v1",
        "variable_deltas_object": isinstance(raw_deltas, dict),
        "provided_axes": provided,
        "missing_axes": [key for key in VARIABLE_KEYS if key not in provided],
        "forced_zero_axes": [key for key in _non_writable_axes(event_kind)],
        "normalised_nonzero_axes": [
            key for key in VARIABLE_KEYS if abs(deltas.get(key, 0.0)) > 0.0
        ],
    }


def coerce_projection(
    raw: Any, *, turn_id=None, event_kind="user_input",
) -> Optional[SemanticProjection]:
    """Narrow untrusted provider output to this package's measurement schema."""
    prior_axis_presence = None
    if isinstance(raw, SemanticProjection):
        prior_axis_presence = raw.axis_presence
        raw = raw.as_record()
    if not isinstance(raw, dict):
        return None
    raw_deltas = raw.get("variable_deltas")
    deltas_raw = raw_deltas if isinstance(raw_deltas, dict) else {}
    deltas = {
        key: max(-1.0, min(1.0, _number(deltas_raw.get(key, 0.0))))
        for key in VARIABLE_KEYS
    }
    for key in _non_writable_axes(event_kind):
        deltas[key] = 0.0
    weights = _normalise_weights(raw.get("behavior_weights"))
    tokens = _normalise_tokens(raw.get("tokens"))
    confidence = _clamp01(raw.get("confidence", 0.0))
    reference_frame = coerce_reference_frame(
        raw.get("reference_frame"), turn_id=turn_id,
    )
    if (
        not any(abs(value) > 0.0 for value in deltas.values())
        and not weights
        and not tokens
        and reference_frame is None
    ):
        return None
    return SemanticProjection(
        variable_deltas=deltas,
        behavior_weights=weights,
        tokens=tokens,
        confidence=confidence,
        source=str(raw.get("source") or "semantic_projector")[:80],
        model=str(raw.get("model") or "unknown")[:120],
        reference_frame=reference_frame,
        axis_presence=_axis_presence(
            raw_deltas, deltas, event_kind, prior=prior_axis_presence,
        ),
    )


class CallableSemanticProjector:
    """Compatibility provider adapter without Store or model authority."""

    def __init__(self, fn: Callable[..., Any]):
        self._fn = fn

    def project(
        self, *, text, dialogue_tail, recall_hint, state_vars,
        event_kind="user_input",
    ):
        return coerce_projection(
            self._fn(
                text=text,
                dialogue_tail=dialogue_tail,
                recall_hint=recall_hint,
                state_vars=dict(state_vars or {}),
            ),
            event_kind=event_kind,
        )


def blend_behavior_weights(fallback, projection):
    """Blend package behavior weights while preserving the frozen formula."""
    base = _normalise_weights(fallback) or {
        key: 1.0 / len(BEHAVIOR_PROTOTYPES)
        for key in BEHAVIOR_PROTOTYPES
    }
    if projection is None or projection.behavior_weights is None:
        return dict(base)
    alpha = projection.confidence
    mixed = {
        key: (1.0 - alpha) * base[key] + alpha * projection.behavior_weights[key]
        for key in BEHAVIOR_PROTOTYPES
    }
    return _normalise_weights(mixed) or dict(base)


__all__ = (
    "BEHAVIOR_PROTOTYPES",
    "CallableSemanticProjector",
    "SemanticProjection",
    "VARIABLE_KEYS",
    "blend_behavior_weights",
    "coerce_projection",
)
