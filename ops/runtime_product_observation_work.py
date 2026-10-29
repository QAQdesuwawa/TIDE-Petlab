"""Transient owner-Actor work for one formal observation wake.

The work carries only an external TemporalDomain coordinate and stable owner /
tick identities.  It is not an observation target, model payload, timer,
action, output, or send authority.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class FormalProductObservationWakeWork:
    """One coordinate sample ready for owner-local due probing."""

    target_coordinate: Any
    participant_id: str
    tick_event_id: str
    expected_plan_id: str | None = None

    def __post_init__(self) -> None:
        if self.target_coordinate is None:
            raise ValueError(
                "formal observation wake requires an external coordinate")
        participant_id = str(self.participant_id or "").strip()
        if not participant_id:
            raise ValueError(
                "formal observation wake requires participant identity")
        tick_event_id = str(self.tick_event_id or "").strip()
        if not tick_event_id:
            raise ValueError(
                "formal observation wake requires tick event identity")
        object.__setattr__(
            self, "target_coordinate", copy.deepcopy(self.target_coordinate))
        object.__setattr__(self, "participant_id", participant_id)
        object.__setattr__(self, "tick_event_id", tick_event_id)
        expected_plan_id = (
            None
            if self.expected_plan_id is None
            else str(self.expected_plan_id or "").strip()
        )
        if self.expected_plan_id is not None and not expected_plan_id:
            raise ValueError(
                "formal observation wake expected plan identity is empty")
        object.__setattr__(self, "expected_plan_id", expected_plan_id)


__all__ = ["FormalProductObservationWakeWork"]
