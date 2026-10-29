"""Immutable identity for one TIDE-owned runtime implementation.

The binding deliberately excludes experiment orchestration facts such as task,
provider, budget, seed, run, and pair identifiers.  Those belong to an outer
evaluation harness.  This module only signs the implementation facts that TIDE
can verify locally.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    InteractionModelBinding,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.temporal_domain import TemporalDomainBinding


RUNTIME_VARIANT_BINDING_VERSION = "runtime-variant-binding-v2"
_RESOURCE_STATES = frozenset({"enabled", "disabled"})
_EXPERIMENT_CONTROL_FIELDS = frozenset({
    "experiment", "experiment_id", "run", "run_id", "pair", "pair_id",
    "benchmark", "task", "task_id", "provider", "model", "budget", "seed",
    "schedule", "trial", "trial_id",
})


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty identifier")
    return normalized


def _identifier_mapping(
    value: Mapping[str, object],
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field_name} must be a mapping")
    normalized: dict[str, str] = {}
    for raw_key, raw_value in value.items():
        key = _identifier(raw_key, f"{field_name} key")
        if key.lower() in _EXPERIMENT_CONTROL_FIELDS:
            raise ValueError(
                f"{field_name} cannot contain experiment control field: {key}")
        normalized[key] = _identifier(
            raw_value, f"{field_name}.{key}")
    if len(normalized) != len(value):
        raise ValueError(f"{field_name} keys must be unique")
    return freeze_model_payload(normalized)


@dataclass(frozen=True)
class RuntimeVariantDefinition:
    """Caller-selected TIDE runtime mechanisms before packages are bound."""

    implementation_id: str
    kernel_implementation_id: str
    mechanism_implementations: Mapping[str, Any]
    realizer_profile_id: str
    resource_policy: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        implementation_id: str,
        kernel_implementation_id: str,
        mechanism_implementations: Mapping[str, object],
        realizer_profile_id: str,
        resource_policy: Mapping[str, object],
    ) -> "RuntimeVariantDefinition":
        mechanisms = _identifier_mapping(
            mechanism_implementations, "mechanism_implementations")
        if not mechanisms:
            raise ValueError(
                "mechanism_implementations must declare at least one mechanism")
        resources = _identifier_mapping(
            resource_policy, "resource_policy")
        if not resources:
            raise ValueError(
                "resource_policy must declare at least one package resource")
        invalid_states = {
            str(value) for value in resources.values()
            if value not in _RESOURCE_STATES
        }
        if invalid_states:
            raise ValueError(
                "resource_policy contains invalid states: "
                + ", ".join(sorted(invalid_states)))
        material = {
            "schema_version": RUNTIME_VARIANT_BINDING_VERSION,
            "implementation_id": _identifier(
                implementation_id, "implementation_id"),
            "kernel_implementation_id": _identifier(
                kernel_implementation_id, "kernel_implementation_id"),
            "mechanism_implementations": thaw_model_data(mechanisms),
            "realizer_profile_id": _identifier(
                realizer_profile_id, "realizer_profile_id"),
            "resource_policy": thaw_model_data(resources),
        }
        return cls(
            implementation_id=material["implementation_id"],
            kernel_implementation_id=material[
                "kernel_implementation_id"],
            mechanism_implementations=mechanisms,
            realizer_profile_id=material["realizer_profile_id"],
            resource_policy=resources,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        mechanisms = _identifier_mapping(
            self.mechanism_implementations, "mechanism_implementations")
        if not mechanisms:
            raise ValueError(
                "mechanism_implementations must declare at least one mechanism")
        resources = _identifier_mapping(
            self.resource_policy, "resource_policy")
        if not resources:
            raise ValueError(
                "resource_policy must declare at least one package resource")
        if any(
            value not in _RESOURCE_STATES for value in resources.values()
        ):
            raise ValueError("resource_policy contains invalid states")
        material = {
            "schema_version": RUNTIME_VARIANT_BINDING_VERSION,
            "implementation_id": _identifier(
                self.implementation_id, "implementation_id"),
            "kernel_implementation_id": _identifier(
                self.kernel_implementation_id, "kernel_implementation_id"),
            "mechanism_implementations": thaw_model_data(mechanisms),
            "realizer_profile_id": _identifier(
                self.realizer_profile_id,
                "realizer_profile_id"),
            "resource_policy": thaw_model_data(resources),
        }
        if model_data_fingerprint(material) != self.fingerprint:
            raise ValueError("runtime variant definition fingerprint is invalid")

    def bind(
        self,
        *,
        model_binding: InteractionModelBinding,
        temporal_binding: TemporalDomainBinding,
    ) -> "RuntimeVariantBinding":
        self.verify_integrity()
        if not isinstance(model_binding, InteractionModelBinding):
            raise ValueError(
                "runtime variant requires an InteractionModelBinding")
        if not isinstance(temporal_binding, TemporalDomainBinding):
            raise ValueError(
                "runtime variant requires a TemporalDomainBinding")
        model_record = model_binding.to_record()
        temporal_record = temporal_binding.to_record()
        kernel_material = {
            "kernel_implementation_id": self.kernel_implementation_id,
            "mechanism_implementations": thaw_model_data(
                self.mechanism_implementations),
        }
        material = {
            "schema_version": RUNTIME_VARIANT_BINDING_VERSION,
            "implementation_id": self.implementation_id,
            "definition_fingerprint": self.fingerprint,
            "runtime_kernel_fingerprint": model_data_fingerprint(
                kernel_material),
            "model_binding_fingerprint": model_data_fingerprint(model_record),
            "temporal_domain_fingerprint": model_data_fingerprint(
                temporal_record),
            "interaction_model_family": model_binding.package_id,
            "interaction_model_version": model_binding.package_version,
            "capability_profile": model_binding.capability_profile,
            "capability_ids": sorted(model_binding.operator_versions),
            "kernel_implementation_id": self.kernel_implementation_id,
            "mechanism_implementations": thaw_model_data(
                self.mechanism_implementations),
            "realizer_profile_id": self.realizer_profile_id,
            "resource_policy": thaw_model_data(self.resource_policy),
        }
        return RuntimeVariantBinding(
            implementation_id=self.implementation_id,
            definition_fingerprint=self.fingerprint,
            runtime_kernel_fingerprint=material[
                "runtime_kernel_fingerprint"],
            model_binding_fingerprint=material[
                "model_binding_fingerprint"],
            temporal_domain_fingerprint=material[
                "temporal_domain_fingerprint"],
            interaction_model_family=model_binding.package_id,
            interaction_model_version=model_binding.package_version,
            capability_profile=model_binding.capability_profile,
            capability_ids=tuple(material["capability_ids"]),
            kernel_implementation_id=self.kernel_implementation_id,
            mechanism_implementations=self.mechanism_implementations,
            realizer_profile_id=self.realizer_profile_id,
            resource_policy=self.resource_policy,
            fingerprint=model_data_fingerprint(material),
        )


@dataclass(frozen=True)
class RuntimeVariantBinding:
    """Exact TIDE runtime combination pinned by one formal session."""

    implementation_id: str
    definition_fingerprint: str
    runtime_kernel_fingerprint: str
    model_binding_fingerprint: str
    temporal_domain_fingerprint: str
    interaction_model_family: str
    interaction_model_version: str
    capability_profile: str
    capability_ids: tuple[str, ...]
    kernel_implementation_id: str
    mechanism_implementations: Mapping[str, Any]
    realizer_profile_id: str
    resource_policy: Mapping[str, Any]
    fingerprint: str

    def verify_for(
        self,
        *,
        model_binding: InteractionModelBinding,
        temporal_binding: TemporalDomainBinding,
    ) -> None:
        definition_material = {
            "schema_version": RUNTIME_VARIANT_BINDING_VERSION,
            "implementation_id": _identifier(
                self.implementation_id, "implementation_id"),
            "kernel_implementation_id": _identifier(
                self.kernel_implementation_id,
                "kernel_implementation_id"),
            "mechanism_implementations": thaw_model_data(
                _identifier_mapping(
                    self.mechanism_implementations,
                    "mechanism_implementations",
                )
            ),
            "realizer_profile_id": _identifier(
                self.realizer_profile_id,
                "realizer_profile_id"),
            "resource_policy": thaw_model_data(
                _identifier_mapping(
                    self.resource_policy, "resource_policy")
            ),
        }
        if not definition_material["resource_policy"]:
            raise ValueError(
                "resource_policy must declare at least one package resource")
        if any(
            value not in _RESOURCE_STATES
            for value in definition_material["resource_policy"].values()
        ):
            raise ValueError("resource_policy contains invalid states")
        if model_data_fingerprint(
                definition_material) != self.definition_fingerprint:
            raise ValueError("runtime variant definition fingerprint is invalid")
        kernel_material = {
            "kernel_implementation_id": self.kernel_implementation_id,
            "mechanism_implementations": definition_material[
                "mechanism_implementations"],
        }
        if model_data_fingerprint(
                kernel_material) != self.runtime_kernel_fingerprint:
            raise ValueError("runtime variant kernel fingerprint is invalid")
        if (
            self.interaction_model_family != model_binding.package_id
            or self.interaction_model_version != model_binding.package_version
            or self.capability_profile != model_binding.capability_profile
            or self.capability_ids
            != tuple(sorted(model_binding.operator_versions))
            or self.model_binding_fingerprint
            != model_data_fingerprint(model_binding.to_record())
            or self.temporal_domain_fingerprint
            != model_data_fingerprint(temporal_binding.to_record())
        ):
            raise ValueError(
                "runtime variant does not match model/temporal binding")
        record = self.to_record()
        material = dict(record)
        fingerprint = material.pop("combination_fingerprint")
        if model_data_fingerprint(material) != fingerprint:
            raise ValueError("runtime variant fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": RUNTIME_VARIANT_BINDING_VERSION,
            "implementation_id": self.implementation_id,
            "definition_fingerprint": self.definition_fingerprint,
            "runtime_kernel_fingerprint": self.runtime_kernel_fingerprint,
            "model_binding_fingerprint": self.model_binding_fingerprint,
            "temporal_domain_fingerprint": self.temporal_domain_fingerprint,
            "interaction_model_family": self.interaction_model_family,
            "interaction_model_version": self.interaction_model_version,
            "capability_profile": self.capability_profile,
            "capability_ids": list(self.capability_ids),
            "kernel_implementation_id": self.kernel_implementation_id,
            "mechanism_implementations": thaw_model_data(
                self.mechanism_implementations),
            "realizer_profile_id": self.realizer_profile_id,
            "resource_policy": thaw_model_data(self.resource_policy),
            "combination_fingerprint": self.fingerprint,
        }


__all__ = (
    "RUNTIME_VARIANT_BINDING_VERSION",
    "RuntimeVariantBinding",
    "RuntimeVariantDefinition",
)
