"""Replaceable interpretation contract for explicit preference feedback."""

from dataclasses import dataclass
from typing import Any, Callable, Optional

from interaction_extensions.preference_calibration.feedback import normalize_feedback


def _confidence(value):
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class FeedbackIntent:
    """A feedback label already confirmed by the configured semantic provider."""

    label: str
    confidence: float
    source: str = "feedback_interpreter"
    model: str = "unknown"

    def as_record(self):
        return {
            "label": self.label,
            "confidence": self.confidence,
            "source": self.source,
            "model": self.model,
        }


def coerce_feedback_intent(raw: Any) -> Optional[FeedbackIntent]:
    """Coerce an untrusted provider result without guessing from source text."""
    if isinstance(raw, FeedbackIntent):
        return raw
    if not isinstance(raw, dict) or raw.get("label") in (None, "", "none"):
        return None
    try:
        label = normalize_feedback(raw.get("label"))
    except ValueError:
        return None
    return FeedbackIntent(
        label=label,
        confidence=_confidence(raw.get("confidence")),
        source=str(raw.get("source") or "feedback_interpreter")[:80],
        model=str(raw.get("model") or "unknown")[:120],
    )


class CallableFeedbackInterpreter:
    """Small adapter whose callable owns the decision to return a confirmed label."""

    def __init__(self, fn: Callable[..., Any]):
        self._fn = fn

    def interpret(self, *, text, dialogue_tail):
        return coerce_feedback_intent(
            self._fn(text=text, dialogue_tail=dialogue_tail)
        )


__all__ = (
    "CallableFeedbackInterpreter",
    "FeedbackIntent",
    "coerce_feedback_intent",
)
