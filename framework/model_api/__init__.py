"""Dimension-agnostic contracts for versioned TIDE interaction-model packages."""
from __future__ import annotations

from collections.abc import Mapping
RuntimeMapping = Mapping
from collections import OrderedDict
from threading import RLock
from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Protocol
from .immutable_record import ImmutableModelRecord

# Only proxies whose entire graph was copied and validated by our factory.
# Strong references prevent identity reuse; eviction only loses the fast path.
_OWNED_FROZEN_PAYLOADS = OrderedDict()
_OWNED_FROZEN_LOCK = RLock()
_OWNED_FROZEN_LIMIT = 64

def _is_owned_frozen(value):
    if type(value) is ImmutableModelRecord:
        return True
    if type(value) is not MappingProxyType:
        return False
    with _OWNED_FROZEN_LOCK:
        return _OWNED_FROZEN_PAYLOADS.get(id(value)) is value

def _encode_frozen_mapping(value):
    if type(value) is MappingProxyType or type(value) is ImmutableModelRecord:
        return dict(value)
    raise TypeError("canonical JSON requires a validated mapping")


MODEL_API_VERSION_V1 = "interaction-model-api-v1"
MODEL_API_VERSION_V2 = "interaction-model-api-v2"
SUPPORTED_MODEL_API_VERSIONS = frozenset({
    MODEL_API_VERSION_V1,
    MODEL_API_VERSION_V2,
})

COMPAT_MODEL_CAPABILITIES_V1 = (
    "initialize_constants",
    "initialize_state",
    "resolve_constants",
    "semantic_measurement",
    "event_transition",
    "time_transition",
    "action_assessment",
    "memory_product",
    "expression_packet",
    "diagnostic_descriptor",
)
# Kept as an explicit v1 alias for existing F1/F2 golden tests.  New code must
# select an API version and must not treat this tuple as the final SDK.
REQUIRED_MODEL_CAPABILITIES = COMPAT_MODEL_CAPABILITIES_V1

BASE_MODEL_CAPABILITIES_V2 = (
    "initialize_parameters",
    "initialize_state",
    "measure_event",
    "derive_event_contribution",
    "settle_state",
    "project_shared_semantics",
    "assess_action",
)

OPTIONAL_MODEL_CAPABILITIES_V2 = (
    "plan_observation",
    "advance_progress",
    "prepare_memory_inputs",
    "materialize_memory_event",
    "recall_history",
    "read_historical_influence",
    "aggregate_historical_influence",
    "project_semantic_resources",
    "diagnostic_descriptor",
    "migrate_state",
)

ALLOWED_MODEL_CAPABILITIES_V2 = frozenset({
    *BASE_MODEL_CAPABILITIES_V2,
    *OPTIONAL_MODEL_CAPABILITIES_V2,
})
FORBIDDEN_CONTEXT_AUTHORITY_KEYS = frozenset({
    "store", "actor", "ui", "provider", "timer", "send",
})


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty identifier")
    return normalized


def _validate_capabilities(
    *, api_version: str, capability_profile: str,
    capabilities: tuple[str, ...],
) -> None:
    if api_version == MODEL_API_VERSION_V1:
        if capability_profile != "compat-full-v1":
            raise ValueError("model api v1 requires compat-full-v1 capability profile")
        return
    if api_version != MODEL_API_VERSION_V2:
        raise ValueError(f"unsupported model api version: {api_version}")


def _freeze(value: Any, field_name: str = "value") -> Any:
    if type(value) is ImmutableModelRecord:
        return value
    if type(value) in (tuple, list) and all(type(item) is str for item in value):
        return tuple(value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain only finite numbers")
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field_name}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, field_name) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain only finite numbers")
        return value
    raise ValueError(f"{field_name} must be serializable model data")


def thaw_model_data(value: Any) -> Any:
    """Return a JSON-compatible copy of an immutable model value."""
    if type(value) is tuple and all(type(item) is str for item in value):
        return list(value)
    if value is None or type(value) in (str, bool, int, float):
        return value
    if isinstance(value, Mapping):
        return {str(key): thaw_model_data(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [thaw_model_data(item) for item in value]
    return value


def _canonical_model_data(value: Any, field_name: str = "value") -> Any:
    """Validate and copy straight to JSON data, without a freeze/thaw round trip.

    Keep the same visitation and key normalization as _freeze: even a value
    overwritten by a later normalized key must first pass validation.
    This is deliberately not a cache; mutable inputs are read on every call.
    """
    if type(value) in (tuple, list) and all(type(item) is str for item in value):
        return list(value)
    if value is None or type(value) in (str, bool, int):
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain only finite numbers")
        return value
    if isinstance(value, RuntimeMapping):
        return {
            str(key): _canonical_model_data(item, f"{field_name}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, (tuple, list)):
        return [_canonical_model_data(item, field_name) for item in value]
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field_name} must contain only finite numbers")
        return value
    raise ValueError(f"{field_name} must be serializable model data")


def model_data_fingerprint(value: Any) -> str:
    """Hash model-owned data using one canonical, dimension-agnostic encoding."""
    if _is_owned_frozen(value):
        return _fingerprint_canonical_model_data(value)
    return _fingerprint_canonical_model_data(_canonical_model_data(value))


def _fingerprint_canonical_model_data(value: Any) -> str:
    """Internal encoder for data already copied and validated by this module."""
    encoder = json.JSONEncoder(
        ensure_ascii=False,
        default=_encode_frozen_mapping,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256()
    # Use the standard C encoder with identical canonical bytes. This trades
    # temporary encoded blocks for avoiding Python work on every tiny token.
    for chunk in encoder.iterencode(value, _one_shot=True):
        digest.update(chunk.encode("utf-8"))
    return digest.hexdigest()


@dataclass(frozen=True)
class InteractionModelManifest:
    """Immutable identity, schemas, capabilities, and parameter lineage for a package."""

    package_id: str
    package_version: str
    api_version: str
    capability_profile: str
    capabilities: tuple[str, ...]
    constants_schema: Mapping[str, Any]
    parameters_schema: Mapping[str, Any]
    state_schema: Mapping[str, Any]
    measurement_schema: Mapping[str, Any]
    shared_projection_schema: Mapping[str, Any]
    progress_delta_schema: Mapping[str, Any]
    parameter_package: Mapping[str, Any]
    operator_versions: Mapping[str, str]
    schema_fingerprint: str
    parameter_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        package_id: str,
        package_version: str,
        capabilities: tuple[str, ...] | list[str],
        state_schema: Mapping[str, Any],
        measurement_schema: Mapping[str, Any],
        parameter_package: Mapping[str, Any],
        operator_versions: Mapping[str, str],
        constants_schema: Mapping[str, Any] | None = None,
        parameters_schema: Mapping[str, Any] | None = None,
        shared_projection_schema: Mapping[str, Any] | None = None,
        progress_delta_schema: Mapping[str, Any] | None = None,
        capability_profile: str | None = None,
        api_version: str,
    ) -> "InteractionModelManifest":
        normalized_api = _identifier(api_version, "api_version")
        if normalized_api not in SUPPORTED_MODEL_API_VERSIONS:
            raise ValueError(f"unsupported model api version: {normalized_api}")
        normalized_profile = _identifier(
            capability_profile or (
                "compat-full-v1"
                if normalized_api == MODEL_API_VERSION_V1
                else "declared-capabilities-v2"
            ),
            "capability_profile",
        )
        capability_tuple = tuple(_identifier(item, "capability") for item in capabilities)
        if len(set(capability_tuple)) != len(capability_tuple):
            raise ValueError("capabilities must be unique")
        _validate_capabilities(
            api_version=normalized_api,
            capability_profile=normalized_profile,
            capabilities=capability_tuple,
        )
        if normalized_api == MODEL_API_VERSION_V1:
            if not isinstance(constants_schema, Mapping):
                raise ValueError("model api v1 requires constants_schema")
            normalized_constants = constants_schema
            normalized_parameters = constants_schema
            normalized_shared_projection = {}
            normalized_progress_delta = {}
        else:
            if not isinstance(parameters_schema, Mapping):
                raise ValueError("model api v2 requires parameters_schema")
            if not isinstance(shared_projection_schema, Mapping):
                raise ValueError("model api v2 requires shared_projection_schema")
            if not isinstance(progress_delta_schema, Mapping):
                raise ValueError("model api v2 requires progress_delta_schema")
            normalized_constants = {}
            normalized_parameters = parameters_schema
            normalized_shared_projection = shared_projection_schema
            normalized_progress_delta = progress_delta_schema
        frozen_schemas = {
            "constants": _freeze(normalized_constants, "constants_schema"),
            "parameters": _freeze(normalized_parameters, "parameters_schema"),
            "state": _freeze(state_schema, "state_schema"),
            "measurement": _freeze(measurement_schema, "measurement_schema"),
            "shared_projection": _freeze(
                normalized_shared_projection, "shared_projection_schema"),
            "progress_delta": _freeze(
                normalized_progress_delta, "progress_delta_schema"),
        }
        frozen_parameters = _freeze(parameter_package, "parameter_package")
        frozen_operators = _freeze(operator_versions, "operator_versions")
        if set(frozen_operators) != set(capability_tuple):
            raise ValueError("operator_versions must exactly match capabilities")
        for capability, version in frozen_operators.items():
            _identifier(version, f"operator_versions.{capability}")
        schema_material = (
            {
                "constants": frozen_schemas["constants"],
                "state": frozen_schemas["state"],
                "measurement": frozen_schemas["measurement"],
            }
            if normalized_api == MODEL_API_VERSION_V1 else {
                "capability_profile": normalized_profile,
                "parameters": frozen_schemas["parameters"],
                "state": frozen_schemas["state"],
                "measurement": frozen_schemas["measurement"],
                "shared_projection": frozen_schemas["shared_projection"],
                "progress_delta": frozen_schemas["progress_delta"],
            }
        )
        return cls(
            package_id=_identifier(package_id, "package_id"),
            package_version=_identifier(package_version, "package_version"),
            api_version=normalized_api,
            capability_profile=normalized_profile,
            capabilities=capability_tuple,
            constants_schema=frozen_schemas["constants"],
            parameters_schema=frozen_schemas["parameters"],
            state_schema=frozen_schemas["state"],
            measurement_schema=frozen_schemas["measurement"],
            shared_projection_schema=frozen_schemas["shared_projection"],
            progress_delta_schema=frozen_schemas["progress_delta"],
            parameter_package=frozen_parameters,
            operator_versions=frozen_operators,
            schema_fingerprint=model_data_fingerprint(schema_material),
            parameter_fingerprint=model_data_fingerprint(frozen_parameters),
        )

    def verify_integrity(self) -> None:
        if self.api_version not in SUPPORTED_MODEL_API_VERSIONS:
            raise ValueError(f"unsupported model api version: {self.api_version}")
        _validate_capabilities(
            api_version=self.api_version,
            capability_profile=self.capability_profile,
            capabilities=self.capabilities,
        )
        schemas = (
            {
                "constants": self.constants_schema,
                "state": self.state_schema,
                "measurement": self.measurement_schema,
            }
            if self.api_version == MODEL_API_VERSION_V1 else {
                "capability_profile": self.capability_profile,
                "parameters": self.parameters_schema,
                "state": self.state_schema,
                "measurement": self.measurement_schema,
                "shared_projection": self.shared_projection_schema,
                "progress_delta": self.progress_delta_schema,
            }
        )
        if model_data_fingerprint(schemas) != self.schema_fingerprint:
            raise ValueError("model schema fingerprint does not match manifest content")
        if model_data_fingerprint(self.parameter_package) != self.parameter_fingerprint:
            raise ValueError("model parameter fingerprint does not match manifest content")
        if set(self.operator_versions) != set(self.capabilities):
            raise ValueError("operator_versions must exactly match capabilities")

    def to_record(self) -> dict[str, Any]:
        return {
            "api_version": self.api_version,
            "package_id": self.package_id,
            "package_version": self.package_version,
            "capability_profile": self.capability_profile,
            "capabilities": list(self.capabilities),
            "constants_schema": thaw_model_data(self.constants_schema),
            "parameters_schema": thaw_model_data(self.parameters_schema),
            "state_schema": thaw_model_data(self.state_schema),
            "measurement_schema": thaw_model_data(self.measurement_schema),
            "shared_projection_schema": thaw_model_data(
                self.shared_projection_schema),
            "progress_delta_schema": thaw_model_data(self.progress_delta_schema),
            "parameter_package": thaw_model_data(self.parameter_package),
            "operator_versions": thaw_model_data(self.operator_versions),
            "schema_fingerprint": self.schema_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
        }


@dataclass(frozen=True)
class InteractionModelBinding:
    """The exact model identity pinned by one session or replay."""

    package_id: str
    package_version: str
    api_version: str
    capability_profile: str
    schema_fingerprint: str
    parameter_fingerprint: str
    operator_versions: Mapping[str, str]

    @classmethod
    def from_manifest(cls, manifest: InteractionModelManifest) -> "InteractionModelBinding":
        if not isinstance(manifest, InteractionModelManifest):
            raise ValueError("binding source must be InteractionModelManifest")
        manifest.verify_integrity()
        return cls(
            package_id=manifest.package_id,
            package_version=manifest.package_version,
            api_version=manifest.api_version,
            capability_profile=manifest.capability_profile,
            schema_fingerprint=manifest.schema_fingerprint,
            parameter_fingerprint=manifest.parameter_fingerprint,
            operator_versions=_freeze(
                manifest.operator_versions, "binding.operator_versions"),
        )

    def __post_init__(self) -> None:
        for name in (
            "package_id", "package_version", "api_version", "capability_profile",
            "schema_fingerprint", "parameter_fingerprint",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        object.__setattr__(self, "operator_versions", _freeze(
            self.operator_versions, "operator_versions"))

    def matches(self, manifest: InteractionModelManifest) -> bool:
        return (
            self.package_id == manifest.package_id
            and self.package_version == manifest.package_version
            and self.api_version == manifest.api_version
            and self.capability_profile == manifest.capability_profile
            and self.schema_fingerprint == manifest.schema_fingerprint
            and self.parameter_fingerprint == manifest.parameter_fingerprint
            and dict(self.operator_versions) == dict(manifest.operator_versions)
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "package_version": self.package_version,
            "api_version": self.api_version,
            "capability_profile": self.capability_profile,
            "schema_fingerprint": self.schema_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "operator_versions": thaw_model_data(self.operator_versions),
        }


@dataclass(frozen=True)
class ModelStageExecution:
    payload: Mapping[str, Any]
    receipt: Any


class InteractionModelPackage(Protocol):
    """Structural package protocol; concrete stage methods are capability named."""

    manifest: InteractionModelManifest


def freeze_model_payload(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping):
        raise ValueError("model stage payload must be a mapping")
    if _is_owned_frozen(payload):
        return payload
    frozen = _freeze(payload, "payload")
    with _OWNED_FROZEN_LOCK:
        _OWNED_FROZEN_PAYLOADS[id(frozen)] = frozen
        while len(_OWNED_FROZEN_PAYLOADS) > _OWNED_FROZEN_LIMIT:
            _OWNED_FROZEN_PAYLOADS.popitem(last=False)
    return frozen


def __getattr__(name: str) -> Any:
    """Resolve explicit v1 invocation identities only for legacy imports."""
    if name in {"ModelInvocationContext", "ModelStageReceipt"}:
        from framework.model_api.compat_v1.invocation import (
            ModelInvocationContext,
            ModelStageReceipt,
        )

        value = {
            "ModelInvocationContext": ModelInvocationContext,
            "ModelStageReceipt": ModelStageReceipt,
        }[name]
        globals()[name] = value
        return value
    raise AttributeError(name)
