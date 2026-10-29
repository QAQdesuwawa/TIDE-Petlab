"""Read-only invocation boundary for one version-pinned TemporalDomain."""
from __future__ import annotations

from typing import Callable, Mapping

from framework.temporal_domain import (
    ObservationDueProof,
    ObservationTarget,
    ProgressDelta,
    TemporalDomainBinding,
    TemporalDomainContractError,
    TemporalDomainManifest,
    TemporalOrderProof,
    TemporalPosition,
)


class BoundTemporalDomain:
    """Validate pure domain evidence without owning scheduling or commit authority."""

    def __init__(
        self,
        package: object,
        manifest: TemporalDomainManifest,
        *,
        operators: Mapping[str, Callable],
    ) -> None:
        self._package = package
        self.manifest = manifest
        self._operators = operators

    @property
    def binding(self) -> TemporalDomainBinding:
        self._verify_registered_identity()
        return TemporalDomainBinding.from_manifest(self.manifest)

    def _verify_registered_identity(self) -> None:
        if getattr(self._package, "manifest", None) is not self.manifest:
            raise RuntimeError("temporal domain manifest changed after registration")
        for name, registered in self._operators.items():
            if getattr(self._package, name, None) != registered:
                raise RuntimeError(
                    f"temporal domain operator changed after registration: {name}")

    def _require_binding(self, value: object, field_name: str) -> None:
        actual = getattr(value, "binding", None)
        if actual != self.binding:
            raise TemporalDomainContractError(
                f"{field_name} binding does not match temporal runtime binding")

    def normalize_position(self, coordinate: object) -> TemporalPosition:
        self._verify_registered_identity()
        result = self._operators["normalize_position"](coordinate=coordinate)
        if not isinstance(result, TemporalPosition):
            raise TemporalDomainContractError(
                "normalize_position must return TemporalPosition")
        result.verify_integrity()
        self._require_binding(result, "position")
        return result

    def prove_order(
        self, from_position: TemporalPosition, to_position: TemporalPosition,
    ) -> TemporalOrderProof:
        if not isinstance(from_position, TemporalPosition):
            raise TemporalDomainContractError(
                "from_position must be TemporalPosition")
        if not isinstance(to_position, TemporalPosition):
            raise TemporalDomainContractError("to_position must be TemporalPosition")
        from_position.verify_integrity()
        to_position.verify_integrity()
        self._require_binding(from_position, "from_position")
        self._require_binding(to_position, "to_position")
        self._verify_registered_identity()
        result = self._operators["prove_order"](
            from_position=from_position, to_position=to_position)
        if not isinstance(result, TemporalOrderProof):
            raise TemporalDomainContractError(
                "prove_order must return TemporalOrderProof")
        result.verify_integrity()
        self._require_binding(result, "order proof")
        if (
            result.from_position.fingerprint != from_position.fingerprint
            or result.to_position.fingerprint != to_position.fingerprint
        ):
            raise TemporalDomainContractError(
                "order proof does not describe the requested positions")
        return result

    def derive_progress(self, order_proof: TemporalOrderProof) -> ProgressDelta:
        if not isinstance(order_proof, TemporalOrderProof):
            raise TemporalDomainContractError(
                "order_proof must be TemporalOrderProof")
        order_proof.verify_integrity()
        self._require_binding(order_proof, "order_proof")
        self._verify_registered_identity()
        result = self._operators["derive_progress"](order_proof=order_proof)
        if not isinstance(result, ProgressDelta):
            raise TemporalDomainContractError(
                "derive_progress must return ProgressDelta")
        result.verify_integrity()
        self._require_binding(result, "progress delta")
        if result.order_proof.fingerprint != order_proof.fingerprint:
            raise TemporalDomainContractError(
                "progress delta does not reference the requested order proof")
        return result

    def resolve_observation_target(
        self, origin_position: TemporalPosition, advice: object,
    ) -> ObservationTarget:
        if not isinstance(origin_position, TemporalPosition):
            raise TemporalDomainContractError(
                "origin_position must be TemporalPosition")
        origin_position.verify_integrity()
        self._require_binding(origin_position, "origin_position")
        self._verify_registered_identity()
        result = self._operators["resolve_observation_target"](
            origin_position=origin_position, advice=advice)
        if not isinstance(result, ObservationTarget):
            raise TemporalDomainContractError(
                "resolve_observation_target must return ObservationTarget")
        result.verify_integrity()
        self._require_binding(result, "observation target")
        if result.origin_position.fingerprint != origin_position.fingerprint:
            raise TemporalDomainContractError(
                "observation target does not reference the requested origin")
        return result

    def prove_observation_due(
        self, target: ObservationTarget, current_position: TemporalPosition,
    ) -> ObservationDueProof:
        if not isinstance(target, ObservationTarget):
            raise TemporalDomainContractError("target must be ObservationTarget")
        if not isinstance(current_position, TemporalPosition):
            raise TemporalDomainContractError(
                "current_position must be TemporalPosition")
        current_position.verify_integrity()
        self._require_binding(target, "target")
        self._require_binding(current_position, "current_position")
        self._verify_registered_identity()
        result = self._operators["prove_observation_due"](
            target=target, current_position=current_position)
        if not isinstance(result, ObservationDueProof):
            raise TemporalDomainContractError(
                "prove_observation_due must return ObservationDueProof")
        result.verify_integrity()
        self._require_binding(result, "due proof")
        if result.target.fingerprint != target.fingerprint:
            raise TemporalDomainContractError(
                "due proof does not reference the requested target")
        if (result.current_order.to_position.fingerprint
                != current_position.fingerprint):
            raise TemporalDomainContractError(
                "due proof does not reference the requested current position")
        return result


__all__ = ("BoundTemporalDomain",)
