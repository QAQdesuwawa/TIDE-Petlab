"""Bind caller-selected model and progress packages for one runtime session.

This module is an application composition seam, not a product-default selector.
The caller constructs both packages, including any versioned parameter package,
and supplies the session identity.  The result can be injected into Runner or
another host without either consumer importing concrete packages.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from framework.model_api import freeze_model_payload, model_data_fingerprint
from framework.model_registry import InteractionModelRegistry
from framework.model_runtime import BoundInteractionModel
from framework.runtime_variant import (
    RuntimeVariantBinding,
    RuntimeVariantDefinition,
)
from framework.temporal_registry import TemporalDomainRegistry
from framework.temporal_runtime import BoundTemporalDomain


@dataclass(frozen=True)
class RuntimePackageComposition:
    model_runtime: BoundInteractionModel
    temporal_runtime: BoundTemporalDomain
    variant_binding: RuntimeVariantBinding
    receipt: Mapping[str, Any]


@dataclass(frozen=True)
class RuntimeSessionInitialization:
    """Explicit opaque inputs used only when creating a new formal session."""

    initial_coordinate: object
    parameter_payload: Mapping[str, Any]
    state_payload: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "parameter_payload",
            freeze_model_payload(self.parameter_payload),
        )
        object.__setattr__(
            self, "state_payload",
            freeze_model_payload(self.state_payload),
        )


def _session_id(value: object) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError("session_id must be a non-empty identifier")
    return normalized


def compose_runtime_packages(
    *,
    model_package: object,
    temporal_package: object,
    session_id: str,
    variant_definition: RuntimeVariantDefinition,
    required_model_capabilities: tuple[str, ...] = (),
) -> RuntimePackageComposition:
    """Register and bind two explicit package instances for one session.

    No package, parameter, coordinate, or initialization payload is inferred.
    Package registries remain fail-closed and validate their own manifests.
    """
    normalized_session_id = _session_id(session_id)
    required = tuple(required_model_capabilities)

    model_registry = InteractionModelRegistry()
    model_manifest = model_registry.register(model_package)
    model_binding = model_registry.binding_for(
        model_manifest.package_id, model_manifest.package_version)
    model_runtime = model_registry.bind(
        model_binding,
        session_id=normalized_session_id,
        required_capabilities=required,
    )

    temporal_registry = TemporalDomainRegistry()
    temporal_manifest = temporal_registry.register(temporal_package)
    temporal_binding = temporal_registry.binding_for(
        temporal_manifest.domain_id, temporal_manifest.domain_version)
    temporal_runtime = temporal_registry.bind(temporal_binding)
    if not isinstance(variant_definition, RuntimeVariantDefinition):
        raise TypeError(
            "variant_definition must be RuntimeVariantDefinition")
    variant_binding = variant_definition.bind(
        model_binding=model_binding,
        temporal_binding=temporal_binding,
    )
    variant_binding.verify_for(
        model_binding=model_binding,
        temporal_binding=temporal_binding,
    )

    material = {
        "version": "runtime-package-composition-v1",
        "session_id": normalized_session_id,
        "model_binding": model_binding.to_record(),
        "temporal_binding": temporal_binding.to_record(),
        "required_model_capabilities": list(required),
        "runtime_variant": variant_binding.to_record(),
        "runtime_variant_fingerprint": variant_binding.fingerprint,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimePackageComposition(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        variant_binding=variant_binding,
        receipt=receipt,
    )


__all__ = (
    "RuntimePackageComposition",
    "RuntimeSessionInitialization",
    "compose_runtime_packages",
)
