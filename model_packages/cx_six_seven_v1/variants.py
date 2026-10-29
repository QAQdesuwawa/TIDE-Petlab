"""Versioned capability variants of the six/seven reference package.

Capability-removal variants only remove optional SDK capabilities from an
explicit base package.  The identity-progress variant is different: it keeps
the formal progress lifecycle intact while versioning the model contribution
operator to an auditable zero transformation.
"""
from __future__ import annotations

from framework.model_api import (
    BASE_MODEL_CAPABILITIES_V2,
    OPTIONAL_MODEL_CAPABILITIES_V2,
    InteractionModelManifest,
)
from model_packages.cx_six_seven_v1.v2_package import (
    ROOT_ORDER,
    _constant_binding,
    _context,
    _contribution_record,
    _exact_payload,
    _identifier as _model_identifier,
    _mapping,
    _plain,
    _root_vector,
)


CX_VARIANT_SPECS = {
    "no-expression": frozenset({"project_semantic_resources"}),
    "no-recall": frozenset({
        "prepare_memory_inputs", "materialize_memory_event",
        "recall_history", "read_historical_influence",
        "aggregate_historical_influence",
    }),
    "no-historical-influence": frozenset({
        "read_historical_influence", "aggregate_historical_influence",
    }),
}


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be a non-empty identifier")
    return result


class CxSixSevenVariantPackage:
    """One immutable capability-filtered view of an explicit base package."""

    def __init__(
        self, *, base_package: object, variant_id: str,
        disabled_capabilities: frozenset[str],
    ) -> None:
        base_manifest = getattr(base_package, "manifest", None)
        if not isinstance(base_manifest, InteractionModelManifest):
            raise ValueError("variant base must expose InteractionModelManifest")
        base_manifest.verify_integrity()
        normalized_id = _identifier(variant_id, "variant_id")
        disabled = frozenset(
            _identifier(item, "disabled capability")
            for item in disabled_capabilities)
        if not disabled:
            raise ValueError("variant must disable at least one capability")
        invalid = disabled - set(OPTIONAL_MODEL_CAPABILITIES_V2)
        if invalid:
            raise ValueError(
                "variant cannot disable base or unknown capabilities: "
                + ", ".join(sorted(invalid)))
        absent = disabled - set(base_manifest.capabilities)
        if absent:
            raise ValueError(
                "variant disabled capabilities are absent from base: "
                + ", ".join(sorted(absent)))
        capabilities = tuple(
            item for item in base_manifest.capabilities if item not in disabled)
        if not set(BASE_MODEL_CAPABILITIES_V2).issubset(capabilities):
            raise ValueError("variant cannot remove a base capability")
        self._base_package = base_package
        self.variant_id = normalized_id
        self.disabled_capabilities = disabled
        self.manifest = InteractionModelManifest.create(
            package_id=base_manifest.package_id,
            package_version=(
                f"{base_manifest.package_version}+variant.{normalized_id}"),
            api_version=base_manifest.api_version,
            capability_profile="standard",
            capabilities=capabilities,
            constants_schema=base_manifest.constants_schema,
            parameters_schema=base_manifest.parameters_schema,
            state_schema=base_manifest.state_schema,
            measurement_schema=base_manifest.measurement_schema,
            shared_projection_schema=base_manifest.shared_projection_schema,
            progress_delta_schema=base_manifest.progress_delta_schema,
            parameter_package=base_manifest.parameter_package,
            operator_versions={
                stage: version
                for stage, version in base_manifest.operator_versions.items()
                if stage in capabilities
            },
        )

    def __getattr__(self, name: str):
        if name in self.disabled_capabilities:
            raise AttributeError(name)
        return getattr(self._base_package, name)


class CxSixSevenIdentityProgressPackage:
    """Keep TemporalDomain progress while contributing zero model-state drift."""

    def __init__(self, *, base_package: object) -> None:
        base_manifest = getattr(base_package, "manifest", None)
        if not isinstance(base_manifest, InteractionModelManifest):
            raise ValueError("identity-progress base must expose InteractionModelManifest")
        base_manifest.verify_integrity()
        if "advance_progress" not in base_manifest.capabilities:
            raise ValueError("identity-progress base must expose advance_progress")
        self._base_package = base_package
        self.variant_id = "no-model-time"
        operator_versions = dict(base_manifest.operator_versions.items())
        operator_versions["advance_progress"] = "cx67-identity-progress-v1"
        self.manifest = InteractionModelManifest.create(
            package_id=base_manifest.package_id,
            package_version=(
                f"{base_manifest.package_version}+variant.no-model-time"),
            api_version=base_manifest.api_version,
            capability_profile="standard",
            capabilities=base_manifest.capabilities,
            constants_schema=base_manifest.constants_schema,
            parameters_schema=base_manifest.parameters_schema,
            state_schema=base_manifest.state_schema,
            measurement_schema=base_manifest.measurement_schema,
            shared_projection_schema=base_manifest.shared_projection_schema,
            progress_delta_schema=base_manifest.progress_delta_schema,
            parameter_package=base_manifest.parameter_package,
            operator_versions=operator_versions,
        )

    def advance_progress(self, *, payload, context) -> dict[str, object]:
        context = _context(context)
        _exact_payload(
            payload, {"parameters", "state", "progress_delta"},
            "advance_progress")
        binding = _constant_binding(payload["parameters"])
        state = _root_vector(payload["state"], "state")
        progress = _mapping(payload["progress_delta"], "progress_delta")
        progress_payload = _mapping(
            progress.get("payload"), "progress_delta.payload")
        if set(progress_payload) != {"elapsed_ms"}:
            raise ValueError(
                "reference progress delta must contain only elapsed_ms")
        if context.temporal_position is None:
            raise ValueError("advance_progress requires a temporal position")
        if _plain(progress.get("binding"), "progress_delta.binding") != (
                context.temporal_position.binding.to_record()):
            raise ValueError(
                "progress delta binding differs from invocation position")
        elapsed = progress_payload["elapsed_ms"]
        if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
            raise ValueError("elapsed_ms must be a non-negative integer")
        if bool(progress.get("advanced")) != (elapsed > 0):
            raise ValueError(
                "progress advanced flag disagrees with elapsed_ms")
        source_fingerprint = _model_identifier(
            progress.get("fingerprint"), "progress_delta.fingerprint")
        zero_delta = {axis: 0.0 for axis in ROOT_ORDER}
        operator_receipt = {
            "version": "cx67-identity-progress-v1",
            "effective_constants_version": binding[
                "effective_constants_version"],
            "source_progress_fingerprint": source_fingerprint,
            "elapsed_ms": elapsed,
            "model_time_contribution_zero": True,
        }
        return {"progress_contribution": _contribution_record(
            kind="progress",
            x_before=state,
            delta=zero_delta,
            x_after=state,
            operator_receipt=operator_receipt,
            source_field="source_progress_fingerprint",
            source_value=source_fingerprint,
        )}

    def __getattr__(self, name: str):
        return getattr(self._base_package, name)


def cx_six_seven_variant(*, base_package: object, variant_id: str):
    normalized = _identifier(variant_id, "variant_id")
    if normalized == "no-model-time":
        return CxSixSevenIdentityProgressPackage(base_package=base_package)
    try:
        disabled = CX_VARIANT_SPECS[normalized]
    except KeyError as exc:
        raise ValueError(f"unknown six/seven variant: {normalized}") from exc
    return CxSixSevenVariantPackage(
        base_package=base_package,
        variant_id=normalized,
        disabled_capabilities=disabled,
    )


__all__ = (
    "CX_VARIANT_SPECS",
    "CxSixSevenIdentityProgressPackage",
    "CxSixSevenVariantPackage",
    "cx_six_seven_variant",
)
