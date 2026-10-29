"""F2 parity seam for C5 MemoryCard assembly after kernel exact-ID loading.

Only already occupied, committed event records can enter this adapter.  The
package receives no Store or loader authority; its local callback merely hands
the existing formatter the immutable records supplied by the kernel boundary.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import ModelInvocationContext, thaw_model_data
from ops.conversation_events import CommittedConversationEvent
from ops.memory_actual_join import ActualRecallCanonicalBatch
from ops.memory_card_attention import MemoryCardAttentionOccupation
from ops.memory_card_hydration import hydrate_memory_cards

from model_packages.cx_six_seven_v1.core_operators import (
    _context,
    _plain_mapping,
    _require_payload_keys,
)


class CxSixSevenMemoryCardAdapter:
    """Format selected committed content, without recall or storage authority."""

    def hydrate_cards(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        context = _context(context)
        _require_payload_keys(
            payload,
            {
                "canonical_batch", "attention_plan", "occupied_events",
                "receipt_refs_by_event_id",
            },
            stage="memory_card_hydration",
        )
        canonical = ActualRecallCanonicalBatch.from_record(
            _plain_mapping(payload["canonical_batch"], "canonical_batch"))
        plan_record = _plain_mapping(payload["attention_plan"], "attention_plan")
        _require_payload_keys(
            plan_record,
            {
                "trigger_event_id", "evaluation_time_ms", "formula_version",
                "recalled_event_ids", "selected_for_hydration_ids", "receipt",
            },
            stage="memory_card_attention_plan",
        )
        attention = MemoryCardAttentionOccupation(**plan_record)
        if (context.event_id is None
                or canonical.trigger_event_id != context.event_id
                or attention.trigger_event_id != context.event_id
                or canonical.evaluation_time_ms != context.at_ms
                or attention.evaluation_time_ms != context.at_ms):
            raise ValueError("C5 stage identity does not match invocation context")

        occupied_rows = payload["occupied_events"]
        if not isinstance(occupied_rows, tuple):
            raise ValueError("occupied_events must be an immutable sequence")
        records = tuple(CommittedConversationEvent.from_record(
            _plain_mapping(row, "occupied_event")) for row in occupied_rows)
        expected = set(attention.selected_for_hydration_ids)
        actual_ids = tuple(record.event_id for record in records)
        if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected:
            raise ValueError("occupied event records must exactly match occupied IDs")
        by_id = {record.event_id: record for record in records}
        refs = _plain_mapping(
            payload["receipt_refs_by_event_id"], "receipt_refs_by_event_id")
        result = hydrate_memory_cards(
            canonical_batch=canonical,
            attention_plan=attention,
            event_loader=by_id.__getitem__,
            owner_participant_id=context.participant_id,
            receipt_refs_by_event_id=refs,
        )
        return thaw_model_data(result.to_record())
