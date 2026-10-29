"""F2 read-only effective-constants seam for the current reference model.

The host selects an already committed B0 version.  This adapter only applies
the existing model formula; it neither reads profile data nor grants a B0 write.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import ModelInvocationContext, thaw_model_data
from .preference_parameters import resolve_effective_constants


class CxSixSevenConstantsAdapter:
    def resolve_constants(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        if not isinstance(context, ModelInvocationContext):
            raise ValueError("context must be ModelInvocationContext")
        expected = {"base_constants", "b0_binding", "correction"}
        if not isinstance(payload, Mapping) or set(payload) != expected:
            raise ValueError("resolve_constants payload keys do not match contract")
        base = thaw_model_data(payload["base_constants"])
        binding = thaw_model_data(payload["b0_binding"])
        correction = thaw_model_data(payload["correction"])
        if not isinstance(base, dict):
            raise ValueError("base_constants must be a mapping")
        if not isinstance(binding, dict) or not {
            "version", "weights", "enabled", "source", "created_at_ms",
        }.issubset(binding):
            raise ValueError("b0_binding must be a selected version record")

        receipt = resolve_effective_constants(
            base,
            b0_version=str(binding["version"]),
            b0=binding["weights"],
            b0_enabled=bool(binding["enabled"]),
            correction=correction,
        )
        return {
            "effective_constants_receipt": dict(
                receipt,
                b0_binding_source=binding["source"],
                b0_binding_created_at_ms=binding["created_at_ms"],
            ),
        }
