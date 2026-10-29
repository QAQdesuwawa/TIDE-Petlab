"""Compatibility exports for optional preference-feedback interpretation."""

from interaction_extensions.preference_calibration.feedback_intent import (
    CallableFeedbackInterpreter,
    FeedbackIntent,
    coerce_feedback_intent,
)

__all__ = (
    "CallableFeedbackInterpreter",
    "FeedbackIntent",
    "coerce_feedback_intent",
)
