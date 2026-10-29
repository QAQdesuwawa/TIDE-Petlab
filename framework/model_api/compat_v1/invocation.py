"""Explicit chat/millisecond invocation identity for Interaction Model API v1."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from framework.temporal_domain import TemporalPosition


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field_name} must be a non-empty identifier")
    return normalized


def _freeze(value: Any, field_name: str = "value") -> Any:
    from framework.model_api import freeze_model_payload

    if isinstance(value, Mapping):
        return freeze_model_payload(value)
    raise ValueError(f"{field_name} must be a mapping")


@dataclass(frozen=True)
class ModelInvocationContext:
    """Frozen v1 context retained only for legacy chat-product records."""

    session_id: str
    conversation_id: str | None
    participant_id: str | None
    at_ms: int | None = None
    event_id: str | None = None
    action_id: str | None = None
    temporal_position: TemporalPosition | None = None
    runtime_ordinal: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "session_id", _identifier(self.session_id, "session_id"))
        for name in ("conversation_id", "participant_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identifier(value, name))
        if self.at_ms is not None and (
            isinstance(self.at_ms, bool)
            or not isinstance(self.at_ms, int)
            or self.at_ms < 0
        ):
            raise ValueError("at_ms must be a non-negative integer or None")
        has_position = self.temporal_position is not None
        has_ordinal = self.runtime_ordinal is not None
        if has_position != has_ordinal:
            raise ValueError(
                "temporal_position and runtime_ordinal must be provided together")
        if has_position:
            if not isinstance(self.temporal_position, TemporalPosition):
                raise ValueError("temporal_position must be TemporalPosition")
            self.temporal_position.verify_integrity()
            if (
                isinstance(self.runtime_ordinal, bool)
                or not isinstance(self.runtime_ordinal, int)
                or self.runtime_ordinal < 0
            ):
                raise ValueError("runtime_ordinal must be a non-negative integer")
        for name in ("event_id", "action_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _identifier(value, name))
        from framework.model_api import FORBIDDEN_CONTEXT_AUTHORITY_KEYS

        forbidden = FORBIDDEN_CONTEXT_AUTHORITY_KEYS.intersection(
            str(key).strip().lower() for key in self.metadata
        )
        if forbidden:
            raise ValueError(
                "model context metadata cannot carry an authority channel: "
                + ", ".join(sorted(forbidden))
            )
        object.__setattr__(self, "metadata", _freeze(self.metadata, "metadata"))

    def to_record(self) -> dict[str, Any]:
        from framework.model_api import thaw_model_data

        temporal_record: dict[str, Any]
        if self.temporal_position is not None:
            temporal_record = {
                "temporal_position": self.temporal_position.to_record(),
                "runtime_ordinal": self.runtime_ordinal,
            }
        else:
            temporal_record = {"at_ms": self.at_ms}
        return {
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "participant_id": self.participant_id,
            "event_id": self.event_id,
            "action_id": self.action_id,
            **temporal_record,
            "metadata": thaw_model_data(self.metadata),
        }


@dataclass(frozen=True)
class ModelStageReceipt:
    package_id: str
    package_version: str
    stage: str
    operator_version: str
    schema_fingerprint: str
    parameter_fingerprint: str
    session_id: str
    conversation_id: str | None
    participant_id: str | None
    event_id: str | None
    action_id: str | None
    at_ms: int | None
    input_fingerprint: str
    output_fingerprint: str
    temporal_position: TemporalPosition | None = None
    runtime_ordinal: int | None = None

    def to_record(self) -> dict[str, Any]:
        temporal_record: dict[str, Any]
        if self.temporal_position is not None:
            temporal_record = {
                "temporal_position": self.temporal_position.to_record(),
                "runtime_ordinal": self.runtime_ordinal,
            }
        else:
            temporal_record = {"at_ms": self.at_ms}
        return {
            "package_id": self.package_id,
            "package_version": self.package_version,
            "stage": self.stage,
            "operator_version": self.operator_version,
            "schema_fingerprint": self.schema_fingerprint,
            "parameter_fingerprint": self.parameter_fingerprint,
            "session_id": self.session_id,
            "conversation_id": self.conversation_id,
            "participant_id": self.participant_id,
            "event_id": self.event_id,
            "action_id": self.action_id,
            **temporal_record,
            "input_fingerprint": self.input_fingerprint,
            "output_fingerprint": self.output_fingerprint,
        }


__all__ = ("ModelInvocationContext", "ModelStageReceipt")
