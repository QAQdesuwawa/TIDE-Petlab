"""Legacy wall-clock wake-schedule bridge outside the TemporalDomain package."""
from product_adapters.wall_clock_ms_v1.wake_schedule import (
    WallClockObservationWakeSchedule,
    build_wall_clock_observation_wake_schedule,
)

__all__ = (
    "WallClockObservationWakeSchedule",
    "build_wall_clock_observation_wake_schedule",
)
