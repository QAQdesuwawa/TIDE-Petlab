"""Versioned, unit-agnostic contracts for monotonic progress domains.

This module contains data contracts only.  It does not read a host clock, schedule
callbacks, create observations, commit events, or send messages.  A later runtime
layer may validate and invoke a bound TemporalDomain package, while Kernel remains
the sole owner of runtime ordinals and formal observation authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any, Mapping


TEMPORAL_DOMAIN_API_VERSION = "temporal-domain-api-v1"

REQUIRED_TEMPORAL_OPERATORS = (
    "normalize_position",
    "prove_order",
    "derive_progress",
    "resolve_observation_target",
    "prove_observation_due",
)


class TemporalDomainContractError(ValueError):
    """A package attempted to publish invalid or cross-domain temporal evidence."""


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty identifier")
    return normalized


def _freeze(value: Any, field_name: str = "value") -> Any:
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
    raise ValueError(f"{field_name} must be serializable temporal data")


def thaw_temporal_data(value: Any) -> Any:
    """Return a JSON-compatible copy of immutable temporal data."""
    if isinstance(value, Mapping):
        return {
            str(key): thaw_temporal_data(item) for key, item in value.items()
        }
    if isinstance(value, tuple):
        return [thaw_temporal_data(item) for item in value]
    return value


def temporal_data_fingerprint(value: Any) -> str:
    """Hash one temporal value using a deterministic, unit-agnostic encoding."""
    encoded = json.dumps(
        thaw_temporal_data(_freeze(value)),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


@dataclass(frozen=True)
class TemporalDomainManifest:
    """Immutable identity, schemas and operator lineage for a progress domain."""

    domain_id: str
    domain_version: str
    api_version: str
    position_schema: Mapping[str, Any]
    progress_schema: Mapping[str, Any]
    observation_schema: Mapping[str, Any]
    parameter_package: Mapping[str, Any]
    operator_versions: Mapping[str, str]
    schema_fingerprint: str
    parameter_fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        domain_id: str,
        domain_version: str,
        position_schema: Mapping[str, Any],
        progress_schema: Mapping[str, Any],
        observation_schema: Mapping[str, Any],
        parameter_package: Mapping[str, Any],
        operator_versions: Mapping[str, str],
        api_version: str = TEMPORAL_DOMAIN_API_VERSION,
    ) -> "TemporalDomainManifest":
        frozen_schemas = _freeze({
            "position": position_schema,
            "progress": progress_schema,
            "observation": observation_schema,
        }, "schemas")
        frozen_parameters = _freeze(parameter_package, "parameter_package")
        frozen_operators = _freeze(operator_versions, "operator_versions")
        if set(frozen_operators) != set(REQUIRED_TEMPORAL_OPERATORS):
            raise ValueError(
                "operator_versions must exactly match required temporal operators")
        for operator, version in frozen_operators.items():
            _identifier(version, f"operator_versions.{operator}")
        return cls(
            domain_id=_identifier(domain_id, "domain_id"),
            domain_version=_identifier(domain_version, "domain_version"),
            api_version=_identifier(api_version, "api_version"),
            position_schema=frozen_schemas["position"],
            progress_schema=frozen_schemas["progress"],
            observation_schema=frozen_schemas["observation"],
            parameter_package=frozen_parameters,
            operator_versions=frozen_operators,
            schema_fingerprint=temporal_data_fingerprint(frozen_schemas),
            parameter_fingerprint=temporal_data_fingerprint(frozen_parameters),
        )

    def verify_integrity(self) -> None:
        if self.api_version != TEMPORAL_DOMAIN_API_VERSION:
            raise ValueError(
                f"unsupported temporal domain api version: {self.api_version}")
        schemas = {
            "position": self.position_schema,
            "progress": self.progress_schema,
            "observation": self.observation_schema,
        }
        if temporal_data_fingerprint(schemas) != self.schema_fingerprint:
            raise ValueError(
                "temporal domain schema fingerprint does not match manifest content")
        if (temporal_data_fingerprint(self.parameter_package)
                != self.parameter_fingerprint):
            raise ValueError(
                "temporal domain parameter fingerprint does not match manifest content")
        if set(self.operator_versions) != set(REQUIRED_TEMPORAL_OPERATORS):
            raise ValueError(
                "operator_versions must exactly match required temporal operators")

    def to_record(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "domain_version": self.domain_version,
            "api_version": self.api_version,
            "position_schema": thaw_temporal_data(self.position_schema),
            "progress_schema": thaw_temporal_data(self.progress_schema),
            "observation_schema": thaw_temporal_data(self.observation_schema),
            "parameter_package": thaw_temporal_data(self.parameter_package),
            "operator_versions": thaw_temporal_data(self.operator_versions),
            "schema_fingerprint": self.schema_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
        }


@dataclass(frozen=True)
class TemporalDomainBinding:
    """The exact progress-domain identity pinned by one session or replay."""

    domain_id: str
    domain_version: str
    api_version: str
    schema_fingerprint: str
    parameter_fingerprint: str
    operator_versions: Mapping[str, str]

    @classmethod
    def from_manifest(
        cls, manifest: TemporalDomainManifest,
    ) -> "TemporalDomainBinding":
        if not isinstance(manifest, TemporalDomainManifest):
            raise ValueError("binding source must be TemporalDomainManifest")
        manifest.verify_integrity()
        return cls(
            domain_id=manifest.domain_id,
            domain_version=manifest.domain_version,
            api_version=manifest.api_version,
            schema_fingerprint=manifest.schema_fingerprint,
            parameter_fingerprint=manifest.parameter_fingerprint,
            operator_versions=_freeze(
                manifest.operator_versions, "binding.operator_versions"),
        )

    def __post_init__(self) -> None:
        for name in (
            "domain_id", "domain_version", "api_version",
            "schema_fingerprint", "parameter_fingerprint",
        ):
            object.__setattr__(self, name, _identifier(getattr(self, name), name))
        object.__setattr__(self, "operator_versions", _freeze(
            self.operator_versions, "operator_versions"))
        if set(self.operator_versions) != set(REQUIRED_TEMPORAL_OPERATORS):
            raise ValueError(
                "operator_versions must exactly match required temporal operators")

    def matches(self, manifest: TemporalDomainManifest) -> bool:
        return (
            self.domain_id == manifest.domain_id
            and self.domain_version == manifest.domain_version
            and self.api_version == manifest.api_version
            and self.schema_fingerprint == manifest.schema_fingerprint
            and self.parameter_fingerprint == manifest.parameter_fingerprint
            and dict(self.operator_versions) == dict(manifest.operator_versions)
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "domain_id": self.domain_id,
            "domain_version": self.domain_version,
            "api_version": self.api_version,
            "schema_fingerprint": self.schema_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "operator_versions": thaw_temporal_data(self.operator_versions),
        }


def _require_binding(
    expected: TemporalDomainBinding,
    actual: TemporalDomainBinding,
    field_name: str,
) -> None:
    if not isinstance(actual, TemporalDomainBinding) or actual != expected:
        raise TemporalDomainContractError(
            f"{field_name} binding does not match temporal domain binding")


def _fingerprinted_record(record: Mapping[str, Any]) -> str:
    return temporal_data_fingerprint(record)


@dataclass(frozen=True)
class TemporalPosition:
    """One canonical position in a bound monotonic progress domain."""

    binding: TemporalDomainBinding
    coordinate: Any
    fingerprint: str

    @classmethod
    def create(
        cls, *, binding: TemporalDomainBinding, coordinate: Any,
    ) -> "TemporalPosition":
        if not isinstance(binding, TemporalDomainBinding):
            raise ValueError("position binding must be TemporalDomainBinding")
        frozen_coordinate = _freeze(coordinate, "coordinate")
        fingerprint = _fingerprinted_record({
            "binding": binding.to_record(),
            "coordinate": thaw_temporal_data(frozen_coordinate),
        })
        return cls(
            binding=binding,
            coordinate=frozen_coordinate,
            fingerprint=fingerprint,
        )

    def verify_integrity(self) -> None:
        expected = _fingerprinted_record({
            "binding": self.binding.to_record(),
            "coordinate": thaw_temporal_data(self.coordinate),
        })
        if expected != self.fingerprint:
            raise TemporalDomainContractError("position fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "binding": self.binding.to_record(),
            "coordinate": thaw_temporal_data(self.coordinate),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class TemporalOrderProof:
    """A domain-issued proof that one canonical position did not move backward."""

    binding: TemporalDomainBinding
    from_position: TemporalPosition
    to_position: TemporalPosition
    relation: str
    operator_version: str
    evidence: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        binding: TemporalDomainBinding,
        from_position: TemporalPosition,
        to_position: TemporalPosition,
        relation: str,
        evidence: Mapping[str, Any],
    ) -> "TemporalOrderProof":
        if not isinstance(binding, TemporalDomainBinding):
            raise ValueError("order proof binding must be TemporalDomainBinding")
        if not isinstance(from_position, TemporalPosition):
            raise ValueError("from_position must be TemporalPosition")
        if not isinstance(to_position, TemporalPosition):
            raise ValueError("to_position must be TemporalPosition")
        from_position.verify_integrity()
        to_position.verify_integrity()
        _require_binding(binding, from_position.binding, "from_position")
        _require_binding(binding, to_position.binding, "to_position")
        normalized_relation = str(relation or "").strip()
        if normalized_relation not in {"same", "forward"}:
            raise TemporalDomainContractError(
                "order relation must be same or forward")
        identical = from_position.fingerprint == to_position.fingerprint
        if identical and normalized_relation != "same":
            raise TemporalDomainContractError(
                "identical positions cannot publish positive forward progress")
        if not identical and normalized_relation == "same":
            raise TemporalDomainContractError(
                "same relation requires identical canonical positions")
        frozen_evidence = _freeze(evidence, "order_evidence")
        operator_version = binding.operator_versions["prove_order"]
        record = {
            "binding": binding.to_record(),
            "from_position": from_position.to_record(),
            "to_position": to_position.to_record(),
            "relation": normalized_relation,
            "operator_version": operator_version,
            "evidence": thaw_temporal_data(frozen_evidence),
        }
        return cls(
            binding=binding,
            from_position=from_position,
            to_position=to_position,
            relation=normalized_relation,
            operator_version=operator_version,
            evidence=frozen_evidence,
            fingerprint=_fingerprinted_record(record),
        )

    def verify_integrity(self) -> None:
        expected = _fingerprinted_record({
            "binding": self.binding.to_record(),
            "from_position": self.from_position.to_record(),
            "to_position": self.to_position.to_record(),
            "relation": self.relation,
            "operator_version": self.operator_version,
            "evidence": thaw_temporal_data(self.evidence),
        })
        if expected != self.fingerprint:
            raise TemporalDomainContractError("order proof fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "binding": self.binding.to_record(),
            "from_position": self.from_position.to_record(),
            "to_position": self.to_position.to_record(),
            "relation": self.relation,
            "operator_version": self.operator_version,
            "evidence": thaw_temporal_data(self.evidence),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class ProgressDelta:
    """Opaque model-consumable progress evidence between two legal positions."""

    binding: TemporalDomainBinding
    order_proof: TemporalOrderProof
    advanced: bool
    operator_version: str
    payload: Any
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        binding: TemporalDomainBinding,
        order_proof: TemporalOrderProof,
        payload: Any,
    ) -> "ProgressDelta":
        if not isinstance(binding, TemporalDomainBinding):
            raise ValueError("progress binding must be TemporalDomainBinding")
        if not isinstance(order_proof, TemporalOrderProof):
            raise ValueError("order_proof must be TemporalOrderProof")
        order_proof.verify_integrity()
        _require_binding(binding, order_proof.binding, "order_proof")
        frozen_payload = _freeze(payload, "progress_payload")
        operator_version = binding.operator_versions["derive_progress"]
        advanced = order_proof.relation == "forward"
        record = {
            "binding": binding.to_record(),
            "order_proof_fingerprint": order_proof.fingerprint,
            "advanced": advanced,
            "operator_version": operator_version,
            "payload": thaw_temporal_data(frozen_payload),
        }
        return cls(
            binding=binding,
            order_proof=order_proof,
            advanced=advanced,
            operator_version=operator_version,
            payload=frozen_payload,
            fingerprint=_fingerprinted_record(record),
        )

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "binding": self.binding.to_record(),
            "order_proof_fingerprint": self.order_proof.fingerprint,
            "advanced": self.advanced,
            "operator_version": self.operator_version,
            "payload": thaw_temporal_data(self.payload),
            "fingerprint": self.fingerprint,
        }

    def verify_integrity(self) -> None:
        self.order_proof.verify_integrity()
        _require_binding(self.binding, self.order_proof.binding, "order_proof")
        expected = _fingerprinted_record({
            "binding": self.binding.to_record(),
            "order_proof_fingerprint": self.order_proof.fingerprint,
            "advanced": self.advanced,
            "operator_version": self.operator_version,
            "payload": thaw_temporal_data(self.payload),
        })
        if expected != self.fingerprint:
            raise TemporalDomainContractError(
                "progress delta fingerprint is invalid")
        if self.advanced != (self.order_proof.relation == "forward"):
            raise TemporalDomainContractError(
                "progress delta advanced flag contradicts order proof")


@dataclass(frozen=True)
class ObservationTarget:
    """An opaque domain target derived from one already committed origin position."""

    binding: TemporalDomainBinding
    origin_position: TemporalPosition
    operator_version: str
    payload: Any
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        binding: TemporalDomainBinding,
        origin_position: TemporalPosition,
        payload: Any,
    ) -> "ObservationTarget":
        if not isinstance(binding, TemporalDomainBinding):
            raise ValueError("observation target binding must be TemporalDomainBinding")
        if not isinstance(origin_position, TemporalPosition):
            raise ValueError("origin_position must be TemporalPosition")
        origin_position.verify_integrity()
        _require_binding(binding, origin_position.binding, "origin_position")
        frozen_payload = _freeze(payload, "observation_target")
        operator_version = binding.operator_versions["resolve_observation_target"]
        record = {
            "binding": binding.to_record(),
            "origin_position_fingerprint": origin_position.fingerprint,
            "operator_version": operator_version,
            "payload": thaw_temporal_data(frozen_payload),
        }
        return cls(
            binding=binding,
            origin_position=origin_position,
            operator_version=operator_version,
            payload=frozen_payload,
            fingerprint=_fingerprinted_record(record),
        )

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "binding": self.binding.to_record(),
            "origin_position_fingerprint": self.origin_position.fingerprint,
            "operator_version": self.operator_version,
            "payload": thaw_temporal_data(self.payload),
            "fingerprint": self.fingerprint,
        }

    def verify_integrity(self) -> None:
        self.origin_position.verify_integrity()
        _require_binding(
            self.binding, self.origin_position.binding, "origin_position")
        expected = _fingerprinted_record({
            "binding": self.binding.to_record(),
            "origin_position_fingerprint": self.origin_position.fingerprint,
            "operator_version": self.operator_version,
            "payload": thaw_temporal_data(self.payload),
        })
        if expected != self.fingerprint:
            raise TemporalDomainContractError(
                "observation target fingerprint is invalid")


@dataclass(frozen=True)
class ObservationDueProof:
    """Domain evidence about whether a target is due at a validated position."""

    binding: TemporalDomainBinding
    target: ObservationTarget
    current_order: TemporalOrderProof
    due: bool
    operator_version: str
    evidence: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        binding: TemporalDomainBinding,
        target: ObservationTarget,
        current_order: TemporalOrderProof,
        due: bool,
        evidence: Mapping[str, Any],
    ) -> "ObservationDueProof":
        if not isinstance(binding, TemporalDomainBinding):
            raise ValueError("due proof binding must be TemporalDomainBinding")
        if not isinstance(target, ObservationTarget):
            raise ValueError("target must be ObservationTarget")
        if not isinstance(current_order, TemporalOrderProof):
            raise ValueError("current_order must be TemporalOrderProof")
        if not isinstance(due, bool):
            raise ValueError("due must be a boolean")
        current_order.verify_integrity()
        _require_binding(binding, target.binding, "target")
        _require_binding(binding, current_order.binding, "current_order")
        if (current_order.from_position.fingerprint
                != target.origin_position.fingerprint):
            raise TemporalDomainContractError(
                "due proof order must start at target origin position")
        frozen_evidence = _freeze(evidence, "due_evidence")
        operator_version = binding.operator_versions["prove_observation_due"]
        record = {
            "binding": binding.to_record(),
            "target_fingerprint": target.fingerprint,
            "current_order_fingerprint": current_order.fingerprint,
            "due": due,
            "operator_version": operator_version,
            "evidence": thaw_temporal_data(frozen_evidence),
        }
        return cls(
            binding=binding,
            target=target,
            current_order=current_order,
            due=due,
            operator_version=operator_version,
            evidence=frozen_evidence,
            fingerprint=_fingerprinted_record(record),
        )

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "binding": self.binding.to_record(),
            "target_fingerprint": self.target.fingerprint,
            "current_order_fingerprint": self.current_order.fingerprint,
            "due": self.due,
            "operator_version": self.operator_version,
            "evidence": thaw_temporal_data(self.evidence),
            "fingerprint": self.fingerprint,
        }

    def verify_integrity(self) -> None:
        self.target.verify_integrity()
        self.current_order.verify_integrity()
        _require_binding(self.binding, self.target.binding, "target")
        _require_binding(
            self.binding, self.current_order.binding, "current_order")
        if (self.current_order.from_position.fingerprint
                != self.target.origin_position.fingerprint):
            raise TemporalDomainContractError(
                "due proof order must start at target origin position")
        expected = _fingerprinted_record({
            "binding": self.binding.to_record(),
            "target_fingerprint": self.target.fingerprint,
            "current_order_fingerprint": self.current_order.fingerprint,
            "due": self.due,
            "operator_version": self.operator_version,
            "evidence": thaw_temporal_data(self.evidence),
        })
        if expected != self.fingerprint:
            raise TemporalDomainContractError("due proof fingerprint is invalid")


__all__ = (
    "TEMPORAL_DOMAIN_API_VERSION",
    "REQUIRED_TEMPORAL_OPERATORS",
    "ObservationDueProof",
    "ObservationTarget",
    "ProgressDelta",
    "TemporalDomainBinding",
    "TemporalDomainContractError",
    "TemporalDomainManifest",
    "TemporalOrderProof",
    "TemporalPosition",
    "temporal_data_fingerprint",
    "thaw_temporal_data",
)
