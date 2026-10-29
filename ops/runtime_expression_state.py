"""Formal owner-local state for committed expression emission remainders.

This state is independent from the Interaction Model state.  It records only
remainders produced by an already committed expression delivery.  Creation is
explicit for a new formal session; missing state is never guessed on restore.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.runtime_kernel import RuntimeSessionBinding


RUNTIME_EXPRESSION_STATE_KEY = "runtime_expression_state"
RUNTIME_EXPRESSION_STATE_VERSION = "runtime-expression-state-v1"


@dataclass(frozen=True)
class RuntimeExpressionState:
    session_binding_fingerprint: str
    revision: int
    committed_remainders: Mapping[str, Any]
    last_committed_action_id: str | None
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "committed_remainders",
            freeze_model_payload(self.committed_remainders),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": RUNTIME_EXPRESSION_STATE_VERSION,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "revision": self.revision,
            "committed_remainders": thaw_model_data(
                self.committed_remainders),
            "last_committed_action_id": self.last_committed_action_id,
            "fingerprint": self.fingerprint,
        }


def _material(
    *, session_binding: RuntimeSessionBinding, revision: int,
    committed_remainders: Mapping[str, Any],
    last_committed_action_id: str | None,
) -> dict[str, Any]:
    return {
        "version": RUNTIME_EXPRESSION_STATE_VERSION,
        "session_binding_fingerprint": session_binding.fingerprint,
        "revision": revision,
        "committed_remainders": thaw_model_data(
            freeze_model_payload(committed_remainders)),
        "last_committed_action_id": last_committed_action_id,
    }


def initialize_runtime_expression_state(
    profile: Mapping[str, object], *, session_binding: RuntimeSessionBinding,
) -> dict[str, object]:
    if not isinstance(profile, Mapping):
        raise ValueError("runtime expression state requires a profile mapping")
    if RUNTIME_EXPRESSION_STATE_KEY in profile:
        raise ValueError("runtime expression state already exists")
    session_binding.verify_integrity()
    material = _material(
        session_binding=session_binding,
        revision=0,
        committed_remainders={},
        last_committed_action_id=None,
    )
    return dict(profile, **{
        RUNTIME_EXPRESSION_STATE_KEY: dict(
            material, fingerprint=model_data_fingerprint(material)),
    })


def restore_runtime_expression_state(
    profile: Mapping[str, object], *, session_binding: RuntimeSessionBinding,
) -> RuntimeExpressionState:
    if not isinstance(profile, Mapping):
        raise ValueError("runtime expression state requires a profile mapping")
    record = profile.get(RUNTIME_EXPRESSION_STATE_KEY)
    if not isinstance(record, Mapping):
        raise ValueError("runtime expression state is missing")
    required = {
        "version", "session_binding_fingerprint", "revision",
        "committed_remainders", "last_committed_action_id", "fingerprint",
    }
    if set(record) != required:
        raise ValueError("runtime expression state fields are invalid")
    revision = record.get("revision")
    remainders = record.get("committed_remainders")
    action_id = record.get("last_committed_action_id")
    if (
        record.get("version") != RUNTIME_EXPRESSION_STATE_VERSION
        or record.get("session_binding_fingerprint")
        != session_binding.fingerprint
        or isinstance(revision, bool)
        or not isinstance(revision, int)
        or revision < 0
        or not isinstance(remainders, Mapping)
        or (action_id is not None and not str(action_id).strip())
    ):
        raise ValueError("runtime expression state identity is invalid")
    material = _material(
        session_binding=session_binding,
        revision=revision,
        committed_remainders=remainders,
        last_committed_action_id=action_id,
    )
    if model_data_fingerprint(material) != record.get("fingerprint"):
        raise ValueError("runtime expression state fingerprint is invalid")
    return RuntimeExpressionState(
        session_binding_fingerprint=session_binding.fingerprint,
        revision=revision,
        committed_remainders=remainders,
        last_committed_action_id=action_id,
        fingerprint=record["fingerprint"],
    )


__all__ = (
    "RUNTIME_EXPRESSION_STATE_KEY",
    "RUNTIME_EXPRESSION_STATE_VERSION",
    "RuntimeExpressionState",
    "initialize_runtime_expression_state",
    "restore_runtime_expression_state",
)
