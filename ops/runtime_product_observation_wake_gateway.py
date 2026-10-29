"""Owner-bound ingress for host-supplied formal observation wake signals.

This module deliberately owns no clock, polling loop, TemporalDomain, Runner,
Store, model, or output authority.  A host-specific source decides when a
signal exists; this gateway only binds that signal to one immutable owner and
hands typed work to the owner's Actor lane.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any

from ops.runtime_product_observation_work import (
    FormalProductObservationWakeWork,
)


@dataclass(frozen=True)
class ExternalProductObservationWakeSignal:
    """One opaque progress sample with a source-owned stable event identity."""

    target_coordinate: Any
    tick_event_id: str
    expected_plan_id: str | None = None

    def __post_init__(self) -> None:
        if self.target_coordinate is None:
            raise ValueError(
                "external observation wake requires a coordinate")
        tick_event_id = str(self.tick_event_id or "").strip()
        if not tick_event_id:
            raise ValueError(
                "external observation wake requires event identity")
        object.__setattr__(
            self, "target_coordinate", copy.deepcopy(self.target_coordinate))
        object.__setattr__(self, "tick_event_id", tick_event_id)
        expected_plan_id = (
            None
            if self.expected_plan_id is None
            else str(self.expected_plan_id or "").strip()
        )
        if self.expected_plan_id is not None and not expected_plan_id:
            raise ValueError(
                "external observation wake expected plan identity is empty")
        object.__setattr__(self, "expected_plan_id", expected_plan_id)


class RuntimeProductObservationWakeGateway:
    """Bind external wake signals to exactly one owner Actor sink."""

    def __init__(self, *, owner_participant_id: str, wake_sink: object) -> None:
        owner_id = str(owner_participant_id or "").strip()
        if not owner_id:
            raise ValueError("observation wake gateway requires owner identity")
        post_wake = getattr(
            wake_sink, "post_formal_product_observation_wake", None)
        if not callable(post_wake):
            raise TypeError(
                "observation wake gateway requires a typed Actor wake sink")
        self._owner_participant_id = owner_id
        self._post_wake = post_wake

    @property
    def owner_participant_id(self) -> str:
        return self._owner_participant_id

    def submit(
        self, signal: ExternalProductObservationWakeSignal,
    ) -> FormalProductObservationWakeWork:
        if not isinstance(signal, ExternalProductObservationWakeSignal):
            raise TypeError(
                "observation wake gateway requires a typed external signal")
        work = FormalProductObservationWakeWork(
            target_coordinate=signal.target_coordinate,
            participant_id=self._owner_participant_id,
            tick_event_id=signal.tick_event_id,
            expected_plan_id=signal.expected_plan_id,
        )
        self._post_wake(work)
        return work


__all__ = (
    "ExternalProductObservationWakeSignal",
    "RuntimeProductObservationWakeGateway",
)
