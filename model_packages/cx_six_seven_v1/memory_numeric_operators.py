"""F2 parity adapter for the already-authorized actual-recall P4 branch.

This is not a Memory product producer.  The actual-recalled exact IDs and
time-live vectors are supplied as settled read-only facts.  The adapter only
wraps the existing state-input, once-only fusion, and root-transition pure
operators; content attention, hydration, and transaction commit remain
separate stages and authorities.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import ModelInvocationContext, thaw_model_data
from .memory_actual_state import (
    derive_actual_recall_state_impact,
    prepare_actual_recall_state_inputs,
)
from .memory_numeric_contracts import NeutralMemoryReadout
from .memory_root_transition import derive_actual_recall_root_transition
from .temporal_position import reference_position_ms

from .core_operators import (
    _context,
    _identifier,
    _plain_mapping,
    _require_payload_keys,
)


class CxSixSevenMemoryNumericAdapter:
    """Actual-recall ID → unscaled P4 → one root transition, without writes."""

    def state_impact(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        context = _context(context)
        _require_payload_keys(
            payload,
            {
                "actual_recall_receipt", "historical_readouts",
                "current_readout", "x_before", "fusion_radius",
                "parameter_version",
            },
            stage="memory_state_impact",
        )
        actual = _plain_mapping(
            payload["actual_recall_receipt"], "actual_recall_receipt")
        if (context.event_id is None
                or actual.get("trigger_event_id") != context.event_id
                or actual.get("evaluation_time_ms") != reference_position_ms(context)):
            raise ValueError("actual recall does not match invocation context")
        raw_history = payload["historical_readouts"]
        if not isinstance(raw_history, tuple):
            raise ValueError("historical_readouts must be an immutable sequence")
        history = tuple(
            NeutralMemoryReadout.from_record(
                _plain_mapping(record, "historical_readout"))
            for record in raw_history
        )
        current = NeutralMemoryReadout.from_record(
            _plain_mapping(payload["current_readout"], "current_readout"))
        before = _plain_mapping(payload["x_before"], "x_before")
        parameter_version = _identifier(
            payload["parameter_version"], "parameter_version")

        state_inputs = prepare_actual_recall_state_inputs(
            actual_recall_receipt=actual,
            historical_readouts=history,
            current_readout=current,
        )
        impact = derive_actual_recall_state_impact(
            state_inputs=state_inputs,
            fusion_radius=payload["fusion_radius"],
            parameter_version=parameter_version,
        )
        root_transition = derive_actual_recall_root_transition(
            impact_batch=impact,
            x_before=before,
        )
        return {
            "state_inputs": thaw_model_data(state_inputs.to_record()),
            "impact": thaw_model_data(impact.to_record()),
            "root_transition": thaw_model_data(root_transition.to_record()),
        }
