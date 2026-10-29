"""Fail-closed registry for versioned interaction-model packages."""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping

from framework.model_api import (
    ALLOWED_MODEL_CAPABILITIES_V2,
    BASE_MODEL_CAPABILITIES_V2,
    COMPAT_MODEL_CAPABILITIES_V1,
    MODEL_API_VERSION_V1,
    MODEL_API_VERSION_V2,
    SUPPORTED_MODEL_API_VERSIONS,
    InteractionModelBinding,
    InteractionModelManifest,
)
from framework.model_runtime import BoundInteractionModel


class ModelRegistrationError(ValueError):
    pass


@dataclass(frozen=True)
class _RegisteredPackage:
    package: object
    manifest: InteractionModelManifest
    operators: Mapping[str, Callable]

    def verify_unchanged(self) -> None:
        if getattr(self.package, "manifest", None) is not self.manifest:
            raise ModelRegistrationError("model manifest changed after registration")
        for stage, registered in self.operators.items():
            if getattr(self.package, stage, None) != registered:
                raise ModelRegistrationError(
                    f"model operator changed after registration: {stage}")


class InteractionModelRegistry:
    def __init__(self) -> None:
        self._packages: dict[tuple[str, str], _RegisteredPackage] = {}

    def register(self, package: object) -> InteractionModelManifest:
        manifest = getattr(package, "manifest", None)
        if not isinstance(manifest, InteractionModelManifest):
            raise ModelRegistrationError("model package must expose InteractionModelManifest")
        try:
            manifest.verify_integrity()
        except ValueError as exc:
            raise ModelRegistrationError(str(exc)) from exc
        if manifest.api_version not in SUPPORTED_MODEL_API_VERSIONS:
            raise ModelRegistrationError("unsupported model api version")

        if manifest.api_version == MODEL_API_VERSION_V1:
            allowed = frozenset(COMPAT_MODEL_CAPABILITIES_V1)
            required = frozenset(COMPAT_MODEL_CAPABILITIES_V1)
        elif manifest.api_version == MODEL_API_VERSION_V2:
            allowed = ALLOWED_MODEL_CAPABILITIES_V2
            required = frozenset(BASE_MODEL_CAPABILITIES_V2)
        else:  # guarded above; retained as a fail-closed branch
            raise ModelRegistrationError("unsupported model api version")

        unsupported = tuple(
            capability for capability in manifest.capabilities
            if capability not in allowed
        )
        if unsupported:
            raise ModelRegistrationError(
                "unsupported capabilities: " + ", ".join(unsupported))

        missing = tuple(
            capability for capability in required
            if capability not in manifest.capabilities
        )
        if missing:
            raise ModelRegistrationError(
                "missing required capabilities: " + ", ".join(missing))
        operators = {}
        for capability in manifest.capabilities:
            operator = getattr(package, capability, None)
            if not callable(operator):
                raise ModelRegistrationError(
                    f"required model operator is not callable: {capability}")
            operators[capability] = operator

        identity = (manifest.package_id, manifest.package_version)
        if identity in self._packages:
            raise ModelRegistrationError(
                f"model package already registered: {identity[0]}@{identity[1]}")
        self._packages[identity] = _RegisteredPackage(
            package=package,
            manifest=manifest,
            operators=MappingProxyType(operators),
        )
        return manifest

    def binding_for(
        self, package_id: str, package_version: str,
    ) -> InteractionModelBinding:
        identity = (str(package_id).strip(), str(package_version).strip())
        registered = self._packages.get(identity)
        if registered is None:
            raise ModelRegistrationError(
                f"model package is not registered: {identity[0]}@{identity[1]}")
        registered.verify_unchanged()
        return InteractionModelBinding.from_manifest(registered.manifest)

    def bind(
        self, binding: InteractionModelBinding, *, session_id: str | None = None,
        required_capabilities: tuple[str, ...] = (),
    ) -> BoundInteractionModel:
        if not isinstance(binding, InteractionModelBinding):
            raise ModelRegistrationError(
                "model runtime requires an InteractionModelBinding")
        if not isinstance(session_id, str) or not session_id.strip():
            raise ModelRegistrationError("model runtime requires a session_id")
        identity = (binding.package_id, binding.package_version)
        registered = self._packages.get(identity)
        if registered is None:
            raise ModelRegistrationError(
                f"model package is not registered: {identity[0]}@{identity[1]}")
        registered.verify_unchanged()
        if not binding.matches(registered.manifest):
            raise ModelRegistrationError(
                "model binding does not match registered manifest")
        normalized_required = tuple(
            str(capability or "").strip()
            for capability in required_capabilities
        )
        if any(not capability for capability in normalized_required):
            raise ModelRegistrationError(
                "task requires capabilities with non-empty identifiers")
        missing_for_task = tuple(
            capability for capability in normalized_required
            if capability not in registered.manifest.capabilities
        )
        if missing_for_task:
            raise ModelRegistrationError(
                "task requires capabilities not provided by model package: "
                + ", ".join(missing_for_task))
        return BoundInteractionModel(
            registered.package, registered.manifest,
            operators=registered.operators,
            session_id=session_id.strip(),
        )

    def manifests(self) -> tuple[InteractionModelManifest, ...]:
        registered = tuple(self._packages[key] for key in sorted(self._packages))
        for item in registered:
            item.verify_unchanged()
        return tuple(item.manifest for item in registered)
