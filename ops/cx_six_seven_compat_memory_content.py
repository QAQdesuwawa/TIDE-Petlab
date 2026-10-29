"""F2 parity adapter for settled Memory content attention, without hydration.

The canonical exact-ID set and both evidence lanes are already settled inputs.
This adapter cannot select actual recall, affect P4, load text, or commit state.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import ModelInvocationContext, thaw_model_data
from ops.memory_actual_join import ActualRecallCanonicalBatch
from ops.memory_attention import MemoryAttentionWitness
from ops.memory_content_attention import (
    AddressContentStrengthReceipt,
    build_content_attention_witnesses,
    derive_address_content_strength,
    settle_content_memory_card_attention,
)
from ops.memory_contracts import AddressReceipt

from model_packages.cx_six_seven_v1.core_operators import (
    _context,
    _plain_mapping,
    _require_payload_keys,
)


def _settled_rows(value: object, field_name: str) -> tuple[dict[str, Any], ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{field_name} must be an immutable sequence")
    return tuple(_plain_mapping(item, field_name) for item in value)


def _numeric_witness(record: Mapping[str, Any]) -> MemoryAttentionWitness:
    required = {
        "target_event_id", "trigger_event_id", "evaluation_time_ms",
        "raw_drive", "formula_version", "receipt",
    }
    _require_payload_keys(record, required, stage="numeric_attention_witness")
    return MemoryAttentionWitness(**record)


def _address_strength(record: Mapping[str, Any]) -> AddressContentStrengthReceipt:
    required = {
        "event_id", "content_strength", "formula_version",
        "source_policy_version", "receipt",
    }
    _require_payload_keys(record, required, stage="address_strength_receipt")
    return AddressContentStrengthReceipt(**record)


class CxSixSevenMemoryContentAdapter:
    """Settled exact-ID evidence → expression-only content occupation."""

    def address_content_strength(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        """Wrap one qualified F09 address as expression-only strength.

        The address receipt names a target memory, while context.event_id names
        the current trigger.  F09's current DTO carries no trigger/time binding;
        this F2 parity seam therefore does not invent one or claim to verify it.
        """
        _context(context)
        _require_payload_keys(
            payload, {"address_receipt"}, stage="memory_address_content_strength")
        address = AddressReceipt.from_record(
            _plain_mapping(payload["address_receipt"], "address_receipt"))
        return thaw_model_data(derive_address_content_strength(address).to_record())

    def content_attention(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        context = _context(context)
        _require_payload_keys(
            payload,
            {
                "canonical_batch", "numeric_attention_witnesses",
                "address_strength_receipts", "params",
            },
            stage="memory_content_attention",
        )
        canonical = ActualRecallCanonicalBatch.from_record(
            _plain_mapping(payload["canonical_batch"], "canonical_batch"))
        if (context.event_id is None
                or canonical.trigger_event_id != context.event_id
                or canonical.evaluation_time_ms != context.at_ms):
            raise ValueError("canonical batch does not match invocation context")
        numeric = tuple(_numeric_witness(item) for item in _settled_rows(
            payload["numeric_attention_witnesses"],
            "numeric_attention_witnesses",
        ))
        address = tuple(_address_strength(item) for item in _settled_rows(
            payload["address_strength_receipts"],
            "address_strength_receipts",
        ))
        params = _plain_mapping(payload["params"], "params")
        witnesses = build_content_attention_witnesses(
            canonical_batch=canonical,
            numeric_attention_witnesses=numeric,
            address_strength_receipts=address,
            trigger_event_id=context.event_id,
            evaluation_time_ms=context.at_ms,
        )
        attention = settle_content_memory_card_attention(
            witnesses=witnesses, params=params,
        )
        return {
            "content_witnesses": [
                thaw_model_data(item.to_record()) for item in witnesses
            ],
            "attention": thaw_model_data(attention.to_record()),
        }
