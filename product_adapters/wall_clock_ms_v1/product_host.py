"""Product composition handle for the wall-clock reference wake path.

This optional module is outside the pure TemporalDomain operators.  It binds
one already-active formal Runner, that Runner's immutable owner Actor, and one
caller-supplied coordinate reader.  Construction does not read a clock, start
a loop, activate a runtime, or create an observation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from ops.runtime_product_observation_wake_gateway import (
    RuntimeProductObservationWakeGateway,
)
from ops.runtime_product_observation_work import (
    FormalProductObservationWakeWork,
)
from product_adapters.wall_clock_ms_v1.host_source import (
    WallClockMillisecondsWakeSource,
)
from product_adapters.wall_clock_ms_v1.wake_schedule import (
    WallClockObservationWakeSchedule,
    build_wall_clock_observation_wake_schedule,
)


@dataclass(frozen=True)
class WallClockProductObservationHost:
    """Explicit single-sample handle bound to one canonical owner."""

    owner_participant_id: str
    wake_source: WallClockMillisecondsWakeSource
    active_plan_reader: Callable[[], object]

    def next_wake_schedule(self) -> WallClockObservationWakeSchedule:
        return build_wall_clock_observation_wake_schedule(
            plan=self.active_plan_reader(),
            owner_participant_id=self.owner_participant_id,
        )

    def sample_scheduled(
        self, schedule: WallClockObservationWakeSchedule,
    ) -> FormalProductObservationWakeWork:
        if not isinstance(schedule, WallClockObservationWakeSchedule):
            raise TypeError("product observation host requires a wake schedule")
        current = self.next_wake_schedule()
        if current != schedule:
            raise ValueError("wall-clock observation wake schedule is stale")
        return self.wake_source.sample_once(
            tick_event_id=schedule.tick_event_id,
            expected_plan_id=schedule.plan_id,
        )


def compose_wall_clock_product_observation_host(
    *,
    runner: object,
    actor: object,
    coordinate_reader: Callable[[], object],
) -> WallClockProductObservationHost:
    """Validate one exact owner chain and return an explicit sample handle."""
    identity_reader = getattr(
        runner, "formal_owner_participant_identity", None)
    if not callable(identity_reader):
        raise TypeError(
            "formal Runner lacks product owner identity capability")
    participant = identity_reader()
    active_plan_reader = getattr(
        runner, "read_formal_active_observation_plan", None)
    if not callable(active_plan_reader):
        raise TypeError(
            "formal Runner lacks active observation plan capability")
    owner_check = getattr(actor, "owns_formal_runner", None)
    if not callable(owner_check) or not owner_check(runner):
        raise ValueError(
            "formal observation host requires the Runner's owner Actor")
    owner_id = str(getattr(participant, "participant_id", "") or "").strip()
    if not owner_id:
        raise ValueError(
            "formal owner identity has no participant identifier")
    gateway = RuntimeProductObservationWakeGateway(
        owner_participant_id=owner_id,
        wake_sink=actor,
    )
    source = WallClockMillisecondsWakeSource(
        coordinate_reader=coordinate_reader,
        wake_gateway=gateway,
    )
    return WallClockProductObservationHost(
        owner_participant_id=owner_id,
        wake_source=source,
        active_plan_reader=active_plan_reader,
    )


__all__ = (
    "WallClockProductObservationHost",
    "compose_wall_clock_product_observation_host",
)
