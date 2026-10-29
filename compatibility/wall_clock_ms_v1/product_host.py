"""Legacy wall-clock observation-host bridge outside the TemporalDomain package."""
from product_adapters.wall_clock_ms_v1.product_host import (
    WallClockProductObservationHost,
    compose_wall_clock_product_observation_host,
)

__all__ = (
    "WallClockProductObservationHost",
    "compose_wall_clock_product_observation_host",
)
