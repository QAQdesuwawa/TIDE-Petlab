"""Legacy wall-clock opening result reader for Interaction Model API v1.

This compatibility module preserves the frozen millisecond contract without
presenting it as the current opaque-temporal action-assessment interface.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from framework.model_api import (
    ModelStageExecution,
    ModelStageReceipt,
    model_data_fingerprint,
)


@dataclass(frozen=True)
class AutonomousOpeningAssessment:
    eligible: bool
    next_observation_at_ms: int
    stage_receipt: ModelStageReceipt


def read_autonomous_opening_assessment(
    execution: ModelStageExecution, *, tick_event_id: str, tick_at_ms: int,
) -> AutonomousOpeningAssessment:
    """Read the frozen v1 wall-clock opening assessment contract."""
    if not isinstance(execution, ModelStageExecution):
        raise ValueError("autonomous opening requires a model stage execution")
    receipt = execution.receipt
    if receipt.stage != "action_assessment":
        raise ValueError("autonomous opening requires action assessment")
    if (not isinstance(tick_event_id, str) or not tick_event_id.strip()
            or type(tick_at_ms) is not int or tick_at_ms < 0
            or receipt.event_id != tick_event_id or receipt.at_ms != tick_at_ms):
        raise ValueError("autonomous opening assessment differs from the formal tick")
    if model_data_fingerprint(execution.payload) != receipt.output_fingerprint:
        raise ValueError("autonomous opening payload differs from stage receipt")
    decision = execution.payload.get("autonomous_opening")
    if not isinstance(decision, Mapping) or type(decision.get("eligible")) is not bool:
        raise ValueError("autonomous opening requires a boolean eligible result")
    next_at = decision.get("next_observation_at_ms")
    if type(next_at) is not int or next_at <= tick_at_ms:
        raise ValueError("next observation must be after the assessed tick")
    return AutonomousOpeningAssessment(
        eligible=decision["eligible"], next_observation_at_ms=next_at,
        stage_receipt=receipt,
    )


__all__ = (
    "AutonomousOpeningAssessment",
    "read_autonomous_opening_assessment",
)
