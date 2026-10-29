"""Persist an explicit, non-authoritative F2 model shadow binding in a profile.

The model package never receives this profile.  Active model cutover and migration
are separate later transactions; this anchor grants no stage or send authority.
"""
from __future__ import annotations

from collections.abc import Mapping

from framework.model_runtime import BoundInteractionModel


ANCHOR_KEY = "interaction_model_binding"
ANCHOR_VERSION = 1


def _runtime_identity(runtime: object) -> tuple[str, dict[str, object]]:
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("model binding requires a registry-bound runtime")
    return runtime.session_id, runtime.binding.to_record()


def append_shadow_model_binding(
    profile: Mapping[str, object], *, runtime: BoundInteractionModel, at_ms: int,
) -> dict[str, object]:
    """Return a new profile containing one explicit F2 shadow identity."""
    if not isinstance(profile, Mapping) or ANCHOR_KEY in profile:
        raise ValueError("model binding cannot replace an existing anchor")
    if isinstance(at_ms, bool) or not isinstance(at_ms, int) or at_ms < 0:
        raise ValueError("model binding requires a non-negative absolute time")
    session_id, binding = _runtime_identity(runtime)
    return dict(profile, **{
        ANCHOR_KEY: {
            "schema_version": ANCHOR_VERSION,
            "mode": "shadow",
            "session_id": session_id,
            "binding": binding,
            "bound_at_ms": at_ms,
        },
    })


def validate_shadow_model_binding(
    profile: Mapping[str, object] | None, *,
    runtime: BoundInteractionModel | None,
) -> dict[str, object] | None:
    """Fail closed before replay if the selected runtime differs from the anchor."""
    if profile is None:
        return None
    if not isinstance(profile, Mapping):
        raise ValueError("model binding profile must be a mapping")
    if ANCHOR_KEY not in profile:
        if runtime is not None:
            raise ValueError("model binding is missing from existing profile")
        return None
    anchor = profile[ANCHOR_KEY]
    if runtime is None:
        raise ValueError("model binding requires the original runtime")
    session_id, binding = _runtime_identity(runtime)
    if (not isinstance(anchor, Mapping)
            or set(anchor) != {
                "schema_version", "mode", "session_id", "binding", "bound_at_ms",
            }
            or type(anchor.get("schema_version")) is not int
            or anchor.get("schema_version") != ANCHOR_VERSION
            or anchor.get("mode") != "shadow"
            or anchor.get("session_id") != session_id
            or anchor.get("binding") != binding
            or isinstance(anchor.get("bound_at_ms"), bool)
            or not isinstance(anchor.get("bound_at_ms"), int)
            or anchor["bound_at_ms"] < 0):
        raise ValueError("model binding does not match the persisted shadow anchor")
    return dict(anchor)
