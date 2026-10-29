"""Product compatibility adapter for the legacy final expression packet.

Mapping, numeric emission, and writer-material delivery retain their existing
separate receipts.  This stage never commits the emission remainder: that
requires an actual F16 delivery and remains outside the model package.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from core.tokens.expression import compile_expression_tokens
from framework.model_api import ModelInvocationContext, thaw_model_data
from ops.cx_six_seven_compat_expression_delivery import (
    compile_expression_delivery,
)

from model_packages.cx_six_seven_v1.core_operators import (
    _context,
    _identifier,
    _plain_mapping,
    _require_payload_keys,
)
from model_packages.cx_six_seven_v1.expression_emission import (
    compile_expression_emission_plan,
)


_ANCHOR_NAMES = frozenset({
    "output", "memory", "event", "time", "interaction_residual",
})


def _plain_anchors(value: object) -> dict[str, dict[str, Any] | None]:
    if not isinstance(value, Mapping) or set(value) != _ANCHOR_NAMES:
        raise ValueError("anchors must name exactly the five committed anchor lanes")
    return {
        name: (
            None if value[name] is None
            else _plain_mapping(value[name], f"anchors.{name}")
        )
        for name in _ANCHOR_NAMES
    }


class CxSixSevenExpressionAdapter:
    """F2 expression packet parity; no F16, Store, provider, or prompt authority."""

    def expression_packet(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        context = _context(context)
        _require_payload_keys(
            payload,
            {
                "constants", "constants_version", "state", "source",
                "anchors", "committed_remainders",
            },
            stage="expression_packet",
        )
        constants = _plain_mapping(payload["constants"], "constants")
        state = _plain_mapping(payload["state"], "state")
        anchors = _plain_anchors(payload["anchors"])
        remainders = _plain_mapping(
            payload["committed_remainders"], "committed_remainders")
        constants_version = _identifier(
            payload["constants_version"], "constants_version")
        source = _identifier(payload["source"], "source")

        mapping = compile_expression_tokens(
            current_constants=constants,
            current_constants_version=constants_version,
            current_variables=state,
            now_ms=context.at_ms,
            output_anchor=anchors["output"],
            memory_anchor=anchors["memory"],
            event_anchor=anchors["event"],
            time_anchor=anchors["time"],
            interaction_residual_anchor=anchors["interaction_residual"],
            source=source,
        )
        emission_plan = compile_expression_emission_plan(
            mapping["candidates"],
            committed_remainders=remainders,
        )
        delivery = compile_expression_delivery(
            cards=mapping["cards"], emission_plan=emission_plan,
        )
        return {
            "mapping": thaw_model_data(mapping),
            "emission_plan": thaw_model_data(emission_plan),
            "delivery": {
                "lines": list(delivery.lines),
                "current_root_state": list(delivery.current_root_state),
                "receipt": thaw_model_data(list(delivery.receipt)),
                "delivered_token_keys": list(delivery.delivered_token_keys),
            },
        }
