"""F2 parity adapter for C2 forward-DAG time-live Memory readouts.

Inputs are already committed canonical numeric projections and strictly-prior
influence transactions. This read-only stage does not select actual recall,
hydrate text, advance wall clock, or write any state.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from framework.model_api import ModelInvocationContext, thaw_model_data
from .memory_numeric_contracts import MemoryInfluenceTransaction
from .memory_forward_readout import reconstruct_forward_memory_graph
from .memory_projection import CanonicalMemoryProjection
from .temporal_position import reference_position_ms

from .core_operators import _context, _plain_mapping, _require_payload_keys


CX_FORWARD_MEMORY_READOUT_VERSION = "cx67-forward-memory-readout-v1"
CX_FORWARD_MEMORY_READOUT_V1 = {
    "version": CX_FORWARD_MEMORY_READOUT_VERSION,
    # Natural Euclidean radius of the six signed-unit root axes.
    "bound_radius": math.sqrt(6.0),
}


def _records(value: object, field_name: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be an immutable sequence")
    return tuple(_plain_mapping(item, field_name) for item in value)


class CxSixSevenMemoryLiveAdapter:
    """Committed numeric graph → time-live readouts at one real timestamp."""

    def __init__(self, *, receipt_format_resolver=None):
        self._receipt_format_resolver = receipt_format_resolver

    def live_readouts(self, *, payload, context):
        readouts = self._validated_readouts(payload=payload, context=context)
        return {"readouts": {
            event_id: thaw_model_data(readout.to_record())
            for event_id, readout in sorted(readouts.items())
        }}

    def _validated_readouts(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        context = _context(context)
        _require_payload_keys(
            payload,
            {
                "projections", "transactions", "propagation_bindings_by_ref",
                "root_time_parameters_by_version", "params",
            } | ({"runtime_order"} if "runtime_order" in payload else set()),
            stage="memory_live_readouts",
        )
        projections = tuple(
            CanonicalMemoryProjection.from_record(record)
            for record in _records(payload["projections"], "projection")
        )
        transactions = tuple(
            MemoryInfluenceTransaction.from_record(record)
            for record in _records(payload["transactions"], "transaction")
        )
        bindings = _plain_mapping(
            payload["propagation_bindings_by_ref"],
            "propagation_bindings_by_ref",
        )
        time_parameters = _plain_mapping(
            payload["root_time_parameters_by_version"],
            "root_time_parameters_by_version",
        )
        params = _plain_mapping(payload["params"], "params")
        order = payload.get("runtime_order")
        if "runtime_order" in payload:
            from .memory_runtime_order import RUNTIME_MEMORY_ORDER_VERSION
            if (not isinstance(order, Mapping) or
                    set(order) != {"version", "canonical_node_ordinals"} or
                    order["version"] != RUNTIME_MEMORY_ORDER_VERSION or
                    isinstance(context.runtime_ordinal, bool) or
                    not isinstance(context.runtime_ordinal, int) or context.runtime_ordinal < 0):
                raise ValueError("memory runtime order contract is invalid")
        readouts = reconstruct_forward_memory_graph(
            projections=projections,
            transactions=transactions,
            as_of_ms=reference_position_ms(context),
            propagation_bindings_by_ref=bindings,
            root_time_parameters_by_version=time_parameters,
            params=params,
            canonical_node_ordinals=None if order is None else order["canonical_node_ordinals"],
            as_of_runtime_ordinal=None if order is None else context.runtime_ordinal,
            receipt_format=("full" if self._receipt_format_resolver is None
                            else self._receipt_format_resolver(context)),
        )
        return readouts



__all__ = (
    "CX_FORWARD_MEMORY_READOUT_V1",
    "CX_FORWARD_MEMORY_READOUT_VERSION",
    "CxSixSevenMemoryLiveAdapter",
)
