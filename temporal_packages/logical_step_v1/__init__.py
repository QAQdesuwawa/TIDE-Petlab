"""Monotonic logical-step TemporalDomain for non-wall-clock runtimes."""
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


class LogicalStepV1:
    """Pure integer step ordering with no clock, timer, or send authority."""

    def __init__(
        self,
        *,
        domain_id: str = "tide.logical-step",
        domain_version: str = "1.0.0",
    ) -> None:
        self.manifest = TemporalDomainManifest.create(
            domain_id=domain_id,
            domain_version=domain_version,
            position_schema={"type": "integer", "minimum": 0, "unit": "step"},
            progress_schema={
                "type": "object",
                "required": ["elapsed_steps"],
                "properties": {
                    "elapsed_steps": {"type": "integer", "minimum": 0},
                },
            },
            observation_schema={
                "advice": {"required": ["after_steps"]},
                "target": {"required": ["at_step"]},
            },
            parameter_package={"version": "logical-step-params-v1"},
            operator_versions={
                name: f"logical-step-{name}-v1"
                for name in REQUIRED_TEMPORAL_OPERATORS
            },
        )
        self._binding = TemporalDomainBinding.from_manifest(self.manifest)

    @staticmethod
    def _step(value: object, field: str) -> int:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise TemporalDomainContractError(
                f"{field} must be a non-negative integer logical step")
        return value

    def normalize_position(self, *, coordinate: object) -> TemporalPosition:
        return TemporalPosition.create(
            binding=self._binding,
            coordinate=self._step(coordinate, "coordinate"),
        )

    def prove_order(
        self,
        *,
        from_position: TemporalPosition,
        to_position: TemporalPosition,
    ) -> TemporalOrderProof:
        start = self._step(from_position.coordinate, "from_position")
        end = self._step(to_position.coordinate, "to_position")
        if end < start:
            raise TemporalDomainContractError("logical step cannot move backward")
        relation = "same" if end == start else "forward"
        return TemporalOrderProof.create(
            binding=self._binding,
            from_position=from_position,
            to_position=to_position,
            relation=relation,
            evidence={"comparison": relation},
        )

    def derive_progress(self, *, order_proof: TemporalOrderProof) -> ProgressDelta:
        start = self._step(
            order_proof.from_position.coordinate, "from_position")
        end = self._step(order_proof.to_position.coordinate, "to_position")
        return ProgressDelta.create(
            binding=self._binding,
            order_proof=order_proof,
            payload={"elapsed_steps": end - start},
        )

    def resolve_observation_target(
        self, *, origin_position: TemporalPosition, advice: object,
    ) -> ObservationTarget:
        if not isinstance(advice, Mapping):
            raise TemporalDomainContractError(
                "logical-step observation advice must be a mapping")
        after_steps = self._step(advice.get("after_steps"), "after_steps")
        origin = self._step(origin_position.coordinate, "origin_position")
        return ObservationTarget.create(
            binding=self._binding,
            origin_position=origin_position,
            payload={"at_step": origin + after_steps},
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
        target_step = self._step(target.payload["at_step"], "target.at_step")
        current_step = self._step(
            current_position.coordinate, "current_position")
        return ObservationDueProof.create(
            binding=self._binding,
            target=target,
            current_order=order,
            due=current_step >= target_step,
            evidence={"comparison": "current_greater_or_equal_target"},
        )


__all__ = ("LogicalStepV1",)
