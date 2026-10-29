"""Fail-closed registry for versioned monotonic progress domains."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from framework.temporal_domain import (
    REQUIRED_TEMPORAL_OPERATORS,
    TemporalDomainBinding,
    TemporalDomainManifest,
)
from framework.temporal_runtime import BoundTemporalDomain


class TemporalDomainRegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class _RegisteredDomain:
    package: object
    manifest: TemporalDomainManifest
    operators: Mapping[str, Callable]

    def verify_unchanged(self) -> None:
        if getattr(self.package, "manifest", None) is not self.manifest:
            raise TemporalDomainRegistrationError(
                "temporal domain manifest changed after registration")
        for name, registered in self.operators.items():
            if getattr(self.package, name, None) != registered:
                raise TemporalDomainRegistrationError(
                    f"temporal domain operator changed after registration: {name}")


class TemporalDomainRegistry:
    """Register immutable domain identities; never advances runtime position."""

    def __init__(self) -> None:
        self._domains: dict[tuple[str, str], _RegisteredDomain] = {}

    def register(self, package: object) -> TemporalDomainManifest:
        manifest = getattr(package, "manifest", None)
        if not isinstance(manifest, TemporalDomainManifest):
            raise TemporalDomainRegistrationError(
                "temporal package must expose TemporalDomainManifest")
        try:
            manifest.verify_integrity()
        except ValueError as exc:
            raise TemporalDomainRegistrationError(str(exc)) from exc
        operators: dict[str, Callable] = {}
        for name in REQUIRED_TEMPORAL_OPERATORS:
            operator = getattr(package, name, None)
            if not callable(operator):
                raise TemporalDomainRegistrationError(
                    f"required temporal operator is not callable: {name}")
            operators[name] = operator
        identity = (manifest.domain_id, manifest.domain_version)
        if identity in self._domains:
            raise TemporalDomainRegistrationError(
                f"temporal domain already registered: {identity[0]}@{identity[1]}")
        self._domains[identity] = _RegisteredDomain(
            package=package,
            manifest=manifest,
            operators=MappingProxyType(operators),
        )
        return manifest

    def binding_for(
        self, domain_id: str, domain_version: str,
    ) -> TemporalDomainBinding:
        identity = (str(domain_id).strip(), str(domain_version).strip())
        registered = self._domains.get(identity)
        if registered is None:
            raise TemporalDomainRegistrationError(
                f"temporal domain is not registered: {identity[0]}@{identity[1]}")
        registered.verify_unchanged()
        return TemporalDomainBinding.from_manifest(registered.manifest)

    def bind(self, binding: TemporalDomainBinding) -> BoundTemporalDomain:
        if not isinstance(binding, TemporalDomainBinding):
            raise TemporalDomainRegistrationError(
                "temporal runtime requires a TemporalDomainBinding")
        identity = (binding.domain_id, binding.domain_version)
        registered = self._domains.get(identity)
        if registered is None:
            raise TemporalDomainRegistrationError(
                f"temporal domain is not registered: {identity[0]}@{identity[1]}")
        registered.verify_unchanged()
        if not binding.matches(registered.manifest):
            raise TemporalDomainRegistrationError(
                "temporal binding does not match registered manifest")
        return BoundTemporalDomain(
            registered.package,
            registered.manifest,
            operators=registered.operators,
        )

    def manifests(self) -> tuple[TemporalDomainManifest, ...]:
        registered = tuple(self._domains[key] for key in sorted(self._domains))
        for item in registered:
            item.verify_unchanged()
        return tuple(item.manifest for item in registered)


__all__ = (
    "TemporalDomainRegistrationError",
    "TemporalDomainRegistry",
)
