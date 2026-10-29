"""Concrete product composition root for TIDE's reference runtime."""
from __future__ import annotations

from collections.abc import Callable, Mapping
import uuid

from framework.runtime_variant import RuntimeVariantDefinition
from product_adapters.cx_six_seven_v1.realizer_packet import WRITER_LANE_ORDER
from model_packages.cx_six_seven_v1.full_v2_package import (
    CxSixSevenFullV2Package,
)
from model_packages.cx_six_seven_v1.reference_parameters import (
    reference_full_parameter_package,
)
from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
    current_b0,
)
from ops.product_runtime_session import ProductRuntimeSessionPlan
from ops.runtime_package_composition import (
    RuntimeSessionInitialization,
    compose_runtime_packages,
)
from temporal_packages.wall_clock_ms_v1 import WallClockMillisecondsV1
from core.params import ACTOR


PRODUCT_REFERENCE_RUNTIME_VERSION = "tide-product-reference-runtime-v1"


def _observation_planning_payload() -> dict[str, int]:
    """Concrete reference-package input; generic hosts keep it opaque."""
    return {
        "min_interval_ms": int(ACTOR["tick_min_ms"]),
        "max_interval_ms": int(ACTOR["tick_max_ms"]),
    }


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _runtime_variant_definition() -> RuntimeVariantDefinition:
    return RuntimeVariantDefinition.create(
        implementation_id=PRODUCT_REFERENCE_RUNTIME_VERSION,
        kernel_implementation_id="tide.runtime-kernel.v1",
        mechanism_implementations={
            "persistent_state_authority": "runtime-state-commit-authority-v1",
            "commit_gate": "canonical-action-commit-gate-v1",
        },
        realizer_profile_id="writer-delivery.reference-full-v1",
        resource_policy={lane: "enabled" for lane in WRITER_LANE_ORDER},
    )


def _anchor_session_id(profile: Mapping[str, object]) -> str | None:
    anchor = profile.get("runtime_session_binding")
    if anchor is None:
        return None
    if not isinstance(anchor, Mapping):
        raise ValueError("runtime_session_binding must be a mapping")
    binding = anchor.get("binding")
    if not isinstance(binding, Mapping):
        raise ValueError("runtime session binding record is missing")
    return _identifier(binding.get("session_id"), "runtime session_id")


class ProductDefaultFormalRuntimePlanFactory:
    """Select the official package pair from committed product facts only."""

    def __init__(
        self,
        *,
        session_id_factory: Callable[[], str] | None = None,
        memory_recall_profile: str = "event-only-v1",
    ) -> None:
        self._session_id_factory = session_id_factory or (
            lambda: "session:" + uuid.uuid4().hex)
        if not callable(self._session_id_factory):
            raise TypeError("session_id_factory must be callable")
        if memory_recall_profile not in {"event-only-v1", "shared-tick-v1"}:
            raise ValueError("unknown reference memory recall profile")
        self.memory_recall_profile = memory_recall_profile

    def __call__(self, *, profile, provision) -> ProductRuntimeSessionPlan:
        if not isinstance(profile, Mapping):
            raise TypeError("profile must be a mapping")
        # The provision is an identity proof, not a source of model values.
        participant_id = _identifier(
            provision.npc.participant_id, "provision participant_id")
        if profile.get("product_participant_identity", {}).get(
                "participant_id") != participant_id:
            raise ValueError("formal runtime provision owner does not match profile")

        restored_session_id = _anchor_session_id(profile)
        session_id = restored_session_id or _identifier(
            self._session_id_factory(), "new formal session_id")
        if self.memory_recall_profile == "shared-tick-v1":
            from model_packages.cx_six_seven_v1.autonomous_recall_package import CxSixSevenAutonomousRecallPackage
            package = CxSixSevenAutonomousRecallPackage(parameter_package=reference_full_parameter_package())
        else:
            package = CxSixSevenFullV2Package(parameter_package=reference_full_parameter_package())
        if restored_session_id is not None:
            committed_model = profile["runtime_session_binding"]["binding"].get("model_binding")
            if not isinstance(committed_model, Mapping) or committed_model.get("package_version") != package.manifest.package_version:
                raise ValueError("configured reference package differs from committed session; explicit migration required")
        composition = compose_runtime_packages(
            model_package=package,
            temporal_package=WallClockMillisecondsV1(),
            session_id=session_id,
            variant_definition=_runtime_variant_definition(),
        )
        if restored_session_id is not None:
            return ProductRuntimeSessionPlan(
                composition=composition,
                committed_measurement_evidence_factory=(
                    package.committed_measurement_evidence),
                observation_planning_payload=_observation_planning_payload(),
            )

        b0 = current_b0(profile)
        if b0 is None:
            raise ValueError("new formal product session requires a committed B0 anchor")
        base_constants = profile.get("constants")
        initial_state = profile.get("initial_state")
        if not isinstance(base_constants, Mapping):
            raise ValueError("new formal product session requires committed constants")
        if not isinstance(initial_state, Mapping):
            raise ValueError("new formal product session requires committed initial_state")
        created_at = profile.get("created_at")
        if isinstance(created_at, bool) or not isinstance(created_at, int) or created_at < 0:
            raise ValueError("new formal product session requires created_at coordinate")

        initialization = RuntimeSessionInitialization(
            initial_coordinate=created_at,
            parameter_payload={
                "base_constants": dict(base_constants),
                "base_constants_version": _identifier(
                    provision.npc.c_base_ref,
                    "participant c_base_ref"),
                "b0_binding": {
                    "version": _identifier(b0.get("version"), "B0 version"),
                    "enabled": bool(b0.get("enabled")),
                    "weights": dict(b0.get("weights") or {}),
                },
                "correction": None,
            },
            state_payload={"initial_state": dict(initial_state)},
        )
        return ProductRuntimeSessionPlan(
            composition=composition,
            initialization=initialization,
            committed_measurement_evidence_factory=(
                package.committed_measurement_evidence),
            observation_planning_payload=_observation_planning_payload(),
        )


__all__ = (
    "PRODUCT_REFERENCE_RUNTIME_VERSION",
    "ProductDefaultFormalRuntimePlanFactory",
)
