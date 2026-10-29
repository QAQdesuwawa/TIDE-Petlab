"""Reference millisecond wall-clock TemporalDomain.

This package performs pure coordinate and due-proof calculations.  It never reads
the host clock, creates timers, commits observations, or sends messages.
"""
from __future__ import annotations

from collections.abc import Mapping

from framework.temporal_domain import (
    REQUIRED_TEMPORAL_OPERATORS,
    ObservationDueProof,
    ObservationTarget,
    ProgressDelta,
    TemporalDomainBinding,
    TemporalDomainContractError,
    TemporalDomainManifest,
    TemporalOrderProof,
    TemporalPosition,
)


class WallClockMillisecondsV1:
    def __init__(
        self,
        *,
        domain_id: str = "tide.wall-clock-ms",
        domain_version: str = "1.0.0",
    ) -> None:
        self.manifest = TemporalDomainManifest.create(
            domain_id=domain_id,
            domain_version=domain_version,
            position_schema={"type": "integer", "minimum": 0, "unit": "ms"},
            progress_schema={
                "type": "object",
                "required": ["elapsed_ms"],
                "properties": {"elapsed_ms": {"type": "integer", "minimum": 0}},
            },
            observation_schema={
                "advice": {"required": ["after_ms"]},
                "target": {"required": ["at_ms"]},
            },
            parameter_package={"version": "wall-clock-ms-params-v1"},
            operator_versions={
                name: f"wall-clock-ms-{name}-v1"
                for name in REQUIRED_TEMPORAL_OPERATORS
            },
        )
        self._binding = TemporalDomainBinding.from_manifest(self.manifest)

    @staticmethod
    def _milliseconds(value: object, field_name: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TemporalDomainContractError(
                f"{field_name} must be a non-negative integer millisecond value")
        return value

    def normalize_position(self, *, coordinate: object) -> TemporalPosition:
        return TemporalPosition.create(
            binding=self._binding,
            coordinate=self._milliseconds(coordinate, "coordinate"),
        )

    def prove_order(
        self,
        *,
        from_position: TemporalPosition,
        to_position: TemporalPosition,
    ) -> TemporalOrderProof:
        start = self._milliseconds(from_position.coordinate, "from_position")
        end = self._milliseconds(to_position.coordinate, "to_position")
        if end < start:
            raise TemporalDomainContractError("position cannot move backward")
        relation = "same" if end == start else "forward"
        return TemporalOrderProof.create(
            binding=self._binding,
            from_position=from_position,
            to_position=to_position,
            relation=relation,
            evidence={"comparison": relation},
        )

    def derive_progress(
        self, *, order_proof: TemporalOrderProof,
    ) -> ProgressDelta:
        start = self._milliseconds(
            order_proof.from_position.coordinate, "from_position")
        end = self._milliseconds(
            order_proof.to_position.coordinate, "to_position")
        return ProgressDelta.create(
            binding=self._binding,
            order_proof=order_proof,
            payload={"elapsed_ms": end - start},
        )

    def resolve_observation_target(
        self, *, origin_position: TemporalPosition, advice: object,
    ) -> ObservationTarget:
        if not isinstance(advice, Mapping):
            raise TemporalDomainContractError(
                "wall-clock observation advice must be a mapping")
        after_ms = self._milliseconds(advice.get("after_ms"), "after_ms")
        origin_ms = self._milliseconds(origin_position.coordinate, "origin_position")
        return ObservationTarget.create(
            binding=self._binding,
            origin_position=origin_position,
            payload={"at_ms": origin_ms + after_ms},
        )

    def prove_observation_due(
        self,
        *,
        target: ObservationTarget,
        current_position: TemporalPosition,
    ) -> ObservationDueProof:
        order = self.prove_order(
            from_position=target.origin_position,
            to_position=current_position,
        )
        target_ms = self._milliseconds(target.payload["at_ms"], "target.at_ms")
        current_ms = self._milliseconds(
            current_position.coordinate, "current_position")
        return ObservationDueProof.create(
            binding=self._binding,
            target=target,
            current_order=order,
            due=current_ms >= target_ms,
            evidence={"comparison": "current_greater_or_equal_target"},
        )


__all__ = ("WallClockMillisecondsV1",)
