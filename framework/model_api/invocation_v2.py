"""Domain-neutral identity and receipt records for model API v2 stages."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from framework.model_api import (
    FORBIDDEN_CONTEXT_AUTHORITY_KEYS,
    freeze_model_payload,
    thaw_model_data,
)
from framework.temporal_domain import TemporalPosition


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty identifier")
    return normalized


def _optional_identifier(value: object, field_name: str) -> str | None:
    return None if value is None else _identifier(value, field_name)


@dataclass(frozen=True)
class ModelInvocationContextV2:
    session_id: str
    scope_ref: str | None
    subject_ref: str | None
    event_ref: str | None
    action_ref: str | None
    temporal_position: TemporalPosition
    runtime_ordinal: int
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _identifier(
            self.session_id, "session_id"))
        for name in ("scope_ref", "subject_ref", "event_ref", "action_ref"):
            object.__setattr__(self, name, _optional_identifier(
                getattr(self, name), name))
        if not isinstance(self.temporal_position, TemporalPosition):
            raise ValueError("temporal_position must be TemporalPosition")
        self.temporal_position.verify_integrity()
        if (isinstance(self.runtime_ordinal, bool)
                or not isinstance(self.runtime_ordinal, int)
                or self.runtime_ordinal < 0):
            raise ValueError("runtime_ordinal must be a non-negative integer")
        forbidden = FORBIDDEN_CONTEXT_AUTHORITY_KEYS.intersection(
            str(key).strip().lower() for key in self.metadata)
        if forbidden:
            raise ValueError(
                "model context metadata cannot carry an authority channel: "
                + ", ".join(sorted(forbidden)))
        object.__setattr__(self, "metadata", freeze_model_payload(self.metadata))

    def to_record(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "scope_ref": self.scope_ref,
            "subject_ref": self.subject_ref,
            "event_ref": self.event_ref,
            "action_ref": self.action_ref,
            "temporal_position": self.temporal_position.to_record(),
            "runtime_ordinal": self.runtime_ordinal,
            "metadata": thaw_model_data(self.metadata),
        }


@dataclass(frozen=True)
class ModelStageReceiptV2:
    package_id: str
    package_version: str
    stage: str
    operator_version: str
    schema_fingerprint: str
    parameter_fingerprint: str
    session_id: str
    scope_ref: str | None
    subject_ref: str | None
    event_ref: str | None
    action_ref: str | None
    input_fingerprint: str
    output_fingerprint: str
    temporal_position: TemporalPosition
    runtime_ordinal: int

    def to_record(self) -> dict[str, Any]:
        return {
            "package_id": self.package_id,
            "package_version": self.package_version,
            "stage": self.stage,
            "operator_version": self.operator_version,
            "schema_fingerprint": self.schema_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "session_id": self.session_id,
            "scope_ref": self.scope_ref,
            "subject_ref": self.subject_ref,
            "event_ref": self.event_ref,
            "action_ref": self.action_ref,
            "temporal_position": self.temporal_position.to_record(),
            "runtime_ordinal": self.runtime_ordinal,
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
        }


__all__ = ("ModelInvocationContextV2", "ModelStageReceiptV2")
