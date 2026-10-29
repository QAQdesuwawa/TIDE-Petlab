"""Read-only initialization seam for the current seven constants and six roots."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import ModelInvocationContext, thaw_model_data

from .initialization_schema import generate_base_constants, normalize_initial_state


def _require_payload(payload: object, field: str) -> Mapping[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != {field}:
        raise ValueError(f"initialization payload keys must be exactly {field}")
    return payload


def _require_context(context: object) -> None:
    if not isinstance(context, ModelInvocationContext):
        raise ValueError("context must be ModelInvocationContext")


class CxSixSevenInitializationAdapter:
    """Match existing creation formulas without storage or lifecycle authority."""

    def initialize_constants(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        _require_context(context)
        payload = _require_payload(payload, "weights")
        weights = thaw_model_data(payload["weights"])
        if not isinstance(weights, dict):
            raise ValueError("weights must be a mapping")
        return {"constants": generate_base_constants(weights)}

    def initialize_state(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        _require_context(context)
        payload = _require_payload(payload, "initial_vars")
        initial_vars = thaw_model_data(payload["initial_vars"])
        if initial_vars is not None and not isinstance(initial_vars, dict):
            raise ValueError("initial_vars must be a mapping or None")
        return {"state_vars": normalize_initial_state(initial_vars)}
