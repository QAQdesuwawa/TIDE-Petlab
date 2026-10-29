"""F2 reference-model adapter for one committed C1 numeric memory projection.

The caller supplies an already settled root-response receipt and immutable
version binding. This stage neither measures semantics nor chooses, hydrates,
or commits recall. Exact event identity and wall-clock time come only from the
model invocation context.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .root_dynamics import RootEventResponse
from framework.model_api import ModelInvocationContext
from .memory_projection import project_canonical_memory_event
from .temporal_position import reference_position_ms

from .core_operators import (
    _context,
    _identifier,
    _plain_mapping,
    _require_payload_keys,
)


class CxSixSevenMemoryProjectionAdapter:
    """Committed C1 root transition → versioned canonical memory projection."""

    def canonical_projection(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        context = _context(context)
        if context.event_id is None:
            raise ValueError("canonical_projection requires context.event_id")
        _require_payload_keys(
            payload,
            {
                "constant_binding", "stimulus_receipt_ref",
                "root_response_receipt_ref", "root_response",
                "root_time_parameter_version",
            },
            stage="canonical_projection",
        )
        response_record = _plain_mapping(
            payload["root_response"], "root_response")
        if set(response_record) != {"delta_x_event", "x_after", "receipt"}:
            raise ValueError("root_response fields do not match contract")
        response = RootEventResponse(
            delta_x_event=_plain_mapping(
                response_record["delta_x_event"], "delta_x_event"),
            x_after=_plain_mapping(response_record["x_after"], "x_after"),
            receipt=_plain_mapping(response_record["receipt"], "receipt"),
        )
        projection = project_canonical_memory_event(
            event_id=context.event_id,
            committed_at_ms=reference_position_ms(context),
            constant_binding=_plain_mapping(
                payload["constant_binding"], "constant_binding"),
            stimulus_receipt_ref=_identifier(
                payload["stimulus_receipt_ref"], "stimulus_receipt_ref"),
            root_response_receipt_ref=_identifier(
                payload["root_response_receipt_ref"], "root_response_receipt_ref"),
            response=response,
            root_time_parameter_version=_identifier(
                payload["root_time_parameter_version"],
                "root_time_parameter_version",
            ),
        )
        return {"projection": projection.to_record()}
