"""Explicit host-coordinate source for the wall-clock reference domain.

The pure TemporalDomain in this package still never reads a host clock.  This
optional adapter receives a caller-owned reader and event identity, validates
one coordinate, then submits it through the owner-bound framework gateway.
It has no timer, polling loop, Actor, Runner, Store, or output authority.
"""
from __future__ import annotations

from collections.abc import Callable

from ops.runtime_product_observation_wake_gateway import (
    ExternalProductObservationWakeSignal,
    RuntimeProductObservationWakeGateway,
)
from ops.runtime_product_observation_work import (
    FormalProductObservationWakeWork,
)
from temporal_packages.wall_clock_ms_v1 import WallClockMillisecondsV1


class WallClockMillisecondsWakeSource:
    """Read exactly one injected wall-clock coordinate per explicit sample."""

    def __init__(
        self,
        *,
        coordinate_reader: Callable[[], object],
        wake_gateway: RuntimeProductObservationWakeGateway,
    ) -> None:
        if not callable(coordinate_reader):
            raise TypeError("wall-clock wake source requires a coordinate reader")
        if not isinstance(
            wake_gateway, RuntimeProductObservationWakeGateway
        ):
            raise TypeError(
                "wall-clock wake source requires an owner-bound wake gateway")
        self._coordinate_reader = coordinate_reader
        self._wake_gateway = wake_gateway

    def sample_once(
        self, *, tick_event_id: str, expected_plan_id: str | None = None,
    ) -> FormalProductObservationWakeWork:
        event_id = str(tick_event_id or "").strip()
        if not event_id:
            raise ValueError(
                "wall-clock wake sample requires event identity")
        coordinate = self._coordinate_reader()
        coordinate = WallClockMillisecondsV1._milliseconds(
            coordinate, "coordinate")
        return self._wake_gateway.submit(
            ExternalProductObservationWakeSignal(
                target_coordinate=coordinate,
                tick_event_id=event_id,
                expected_plan_id=expected_plan_id,
            )
        )


__all__ = ("WallClockMillisecondsWakeSource",)
