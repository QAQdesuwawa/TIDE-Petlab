"""Typed owner-local work for one already committed eligible product tick."""
from __future__ import annotations

from dataclasses import dataclass

from ops.runtime_product_observation_tick import RuntimeProductObservationTick


@dataclass(frozen=True)
class FormalProductAutonomousActionWork:
    tick: RuntimeProductObservationTick

    def __post_init__(self) -> None:
        if not isinstance(self.tick, RuntimeProductObservationTick):
            raise TypeError("autonomous action work requires a committed tick")
        grant = self.tick.committed_grant
        if (
            grant is None
            or grant.authorization is None
            or not grant.assessment.eligible
        ):
            raise ValueError(
                "autonomous action work requires an eligible authorization")


__all__ = ("FormalProductAutonomousActionWork",)
