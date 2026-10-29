"""Package-neutral checkpoint envelope for one formal runtime session.

The framework owns the envelope and integrity checks.  Interaction-model
packages remain opaque: the checkpoint binds only a selected runtime identity,
session identity, signed session profile, append-only History record, and the
terminal runtime ordinal needed for recovery.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    _canonical_model_data,
    _fingerprint_canonical_model_data,
    model_data_fingerprint,
    thaw_model_data,
)


FRAMEWORK_RUNTIME_CHECKPOINT_VERSION = "framework-runtime-checkpoint-v1"
_FIELDS = {
    "schema_version", "runtime_identity", "session_id",
    "session_binding_fingerprint", "terminal_runtime_ordinal", "profile",
    "history", "bootstrap_mode", "fingerprint",
}


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"runtime checkpoint {field} is invalid")
    return normalized


def create_runtime_checkpoint(
    *, runtime_identity: str, session_id: str,
    session_binding_fingerprint: str, terminal_runtime_ordinal: int,
    profile: Mapping[str, Any], history: Mapping[str, Any],
    bootstrap_mode: str,
) -> dict[str, Any]:
    """Create a content-opaque checkpoint without package-specific fields."""
    if (
        isinstance(terminal_runtime_ordinal, bool)
        or not isinstance(terminal_runtime_ordinal, int)
        or terminal_runtime_ordinal < 0
    ):
        raise ValueError("runtime checkpoint terminal ordinal is invalid")
    if not isinstance(profile, Mapping):
        raise TypeError("runtime checkpoint profile must be a mapping")
    if not isinstance(history, Mapping):
        raise TypeError("runtime checkpoint history must be a mapping")
    material = {
        "schema_version": FRAMEWORK_RUNTIME_CHECKPOINT_VERSION,
        "runtime_identity": _identifier(runtime_identity, "runtime identity"),
        "session_id": _identifier(session_id, "session identity"),
        "session_binding_fingerprint": _identifier(
            session_binding_fingerprint, "session binding fingerprint"),
        "terminal_runtime_ordinal": terminal_runtime_ordinal,
        "profile": _canonical_model_data(profile, "payload"),
        "history": _canonical_model_data(history, "payload"),
        "bootstrap_mode": _identifier(bootstrap_mode, "bootstrap mode"),
    }
    return {**material, "fingerprint": _fingerprint_canonical_model_data(material)}


def read_runtime_checkpoint(
    value: Mapping[str, Any], *, expected_runtime_identity: str,
    expected_session_id: str,
) -> dict[str, Any]:
    """Verify and read one checkpoint without interpreting package payloads."""
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ValueError("runtime checkpoint schema is invalid")
    record = thaw_model_data(value)
    material = {key: record[key] for key in _FIELDS if key != "fingerprint"}
    if record["fingerprint"] != model_data_fingerprint(material):
        raise ValueError("runtime checkpoint fingerprint is invalid")
    if record["schema_version"] != FRAMEWORK_RUNTIME_CHECKPOINT_VERSION:
        raise ValueError("runtime checkpoint version is unsupported")
    expected_runtime = _identifier(
        expected_runtime_identity, "expected runtime identity")
    expected_session = _identifier(
        expected_session_id, "expected session identity")
    if record["runtime_identity"] != expected_runtime:
        raise ValueError("runtime snapshot runtime identity is invalid")
    if record["session_id"] != expected_session:
        raise ValueError("runtime snapshot session identity is invalid")
    _identifier(
        record["session_binding_fingerprint"],
        "session binding fingerprint",
    )
    ordinal = record["terminal_runtime_ordinal"]
    if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
        raise ValueError("runtime checkpoint terminal ordinal is invalid")
    if not isinstance(record["profile"], dict):
        raise ValueError("runtime checkpoint profile is invalid")
    if not isinstance(record["history"], dict):
        raise ValueError("runtime checkpoint history is invalid")
    _identifier(record["bootstrap_mode"], "bootstrap mode")
    return record


__all__ = (
    "FRAMEWORK_RUNTIME_CHECKPOINT_VERSION",
    "create_runtime_checkpoint",
    "read_runtime_checkpoint",
)
