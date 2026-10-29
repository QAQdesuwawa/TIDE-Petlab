"""Package-neutral composition root for a configured product runtime.

Every model value and concrete package is supplied by the caller.  This module
only turns that explicit configuration into the same immutable product plan for
new and restored sessions, and rejects drift from a committed session anchor.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from framework.runtime_kernel import RuntimeSessionBinding
from framework.runtime_variant import RuntimeVariantDefinition
from ops.product_runtime_session import ProductRuntimeSessionPlan
from ops.runtime_package_composition import (
    RuntimeSessionInitialization,
    compose_runtime_packages,
)


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _anchored_binding(profile: Mapping[str, object]) -> Mapping | None:
    anchor = profile.get("runtime_session_binding")
    if anchor is None:
        return None
    if not isinstance(anchor, Mapping):
        raise ValueError("runtime_session_binding must be a mapping")
    binding = anchor.get("binding")
    if not isinstance(binding, Mapping):
        raise ValueError("runtime session anchor binding is missing")
    return binding


class ConfiguredProductRuntimePlanFactory:
    """Create or restore one product plan from caller-owned configuration.

    The injected initialization factory is creation-only.  A restored session
    gets its identity from the committed profile and must match the configured
    model package, temporal package, and runtime variant exactly.
    """

    def __init__(
        self,
        *,
        model_package_factory: Callable[[], object],
        temporal_package_factory: Callable[[], object],
        variant_definition: RuntimeVariantDefinition,
        initialization_factory: Callable[..., RuntimeSessionInitialization],
        evidence_factory_selector: Callable[[object], Callable],
        session_id_factory: Callable[[], str],
        observation_planning_payload_factory: Callable[..., Mapping] | None = None,
        required_model_capabilities: tuple[str, ...] = (),
    ) -> None:
        named_callables = {
            "model_package_factory": model_package_factory,
            "temporal_package_factory": temporal_package_factory,
            "initialization_factory": initialization_factory,
            "evidence_factory_selector": evidence_factory_selector,
            "session_id_factory": session_id_factory,
        }
        for name, value in named_callables.items():
            if not callable(value):
                raise TypeError(f"{name} must be callable")
        if not isinstance(variant_definition, RuntimeVariantDefinition):
            raise TypeError(
                "variant_definition must be RuntimeVariantDefinition")
        if (
            observation_planning_payload_factory is not None
            and not callable(observation_planning_payload_factory)
        ):
            raise TypeError(
                "observation_planning_payload_factory must be callable or None")
        if not isinstance(required_model_capabilities, tuple) or any(
            not isinstance(value, str) or not value.strip()
            for value in required_model_capabilities
        ):
            raise TypeError(
                "required_model_capabilities must be a tuple of identifiers")

        self._model_package_factory = model_package_factory
        self._temporal_package_factory = temporal_package_factory
        self._variant_definition = variant_definition
        self._initialization_factory = initialization_factory
        self._evidence_factory_selector = evidence_factory_selector
        self._session_id_factory = session_id_factory
        self._observation_planning_payload_factory = (
            observation_planning_payload_factory)
        self._required_model_capabilities = tuple(
            value.strip() for value in required_model_capabilities)

    def __call__(self, *, profile, provision) -> ProductRuntimeSessionPlan:
        if not isinstance(profile, Mapping):
            raise TypeError("profile must be a mapping")
        anchored_binding = _anchored_binding(profile)
        session_id = (
            _identifier(
                anchored_binding.get("session_id"),
                "runtime session anchor session_id",
            )
            if anchored_binding is not None
            else _identifier(self._session_id_factory(), "new runtime session_id")
        )

        package = self._model_package_factory()
        temporal_package = self._temporal_package_factory()
        composition = compose_runtime_packages(
            model_package=package,
            temporal_package=temporal_package,
            session_id=session_id,
            variant_definition=self._variant_definition,
            required_model_capabilities=self._required_model_capabilities,
        )
        evidence_factory = self._evidence_factory_selector(package)
        if not callable(evidence_factory):
            raise TypeError(
                "evidence_factory_selector must return a callable")

        if anchored_binding is not None:
            configured_binding = RuntimeSessionBinding.create(
                model_runtime=composition.model_runtime,
                temporal_runtime=composition.temporal_runtime,
                runtime_variant=composition.variant_binding,
            ).to_record()
            if dict(anchored_binding) != configured_binding:
                raise ValueError(
                    "configured runtime does not match committed session anchor binding")
            initialization = None
        else:
            initialization = self._initialization_factory(
                profile=dict(profile), provision=provision)
            if not isinstance(initialization, RuntimeSessionInitialization):
                raise TypeError(
                    "initialization_factory must return RuntimeSessionInitialization")

        observation_payload = None
        if self._observation_planning_payload_factory is not None:
            observation_payload = self._observation_planning_payload_factory(
                profile=dict(profile), provision=provision)

        return ProductRuntimeSessionPlan(
            composition=composition,
            initialization=initialization,
            committed_measurement_evidence_factory=evidence_factory,
            observation_planning_payload=observation_payload,
        )


__all__ = ("ConfiguredProductRuntimePlanFactory",)
