"""Kernel-owned, model-agnostic exact-ID read after content occupation.

The caller settles memory selection and passes only occupied committed event
IDs. This module can read canonical text but cannot choose memories, inspect
model evidence, assemble writer cards, or change state.
"""
from __future__ import annotations

from collections.abc import Callable, Sequence

from ops.conversation_events import CommittedConversationEvent


def _ids(values: Sequence[str], field: str) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{field} must be a sequence of exact IDs")
    if any(not isinstance(item, str) or not item or item != item.strip()
           for item in values):
        raise ValueError(f"{field} must contain canonical string exact IDs")
    result = tuple(values)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} exact IDs must be unique")
    return result


def load_occupied_committed_events(
    *, canonical_event_ids: Sequence[str],
    selected_for_hydration_ids: Sequence[str],
    evaluation_time_ms: int,
    owner_participant_id: str,
    event_loader: Callable[[str], CommittedConversationEvent],
) -> tuple[CommittedConversationEvent, ...]:
    """Read only selected, owner-visible committed text from the canonical set."""
    canonical = _ids(canonical_event_ids, "canonical_event_ids")
    selected = _ids(selected_for_hydration_ids, "selected_for_hydration_ids")
    if not set(selected).issubset(set(canonical)):
        raise ValueError("occupied exact ID is outside the canonical recall batch")
    if (isinstance(evaluation_time_ms, bool)
            or not isinstance(evaluation_time_ms, int)
            or evaluation_time_ms < 0):
        raise ValueError("evaluation_time_ms must be a non-negative integer")
    owner_id = str(owner_participant_id or "").strip()
    if not owner_id:
        raise ValueError("owner_participant_id cannot be empty")
    if not callable(event_loader):
        raise ValueError("event_loader must be callable")

    records = []
    for event_id in selected:
        record = event_loader(event_id)
        if not isinstance(record, CommittedConversationEvent):
            raise ValueError("event_loader must return CommittedConversationEvent")
        if record.event_id != event_id:
            raise ValueError(
                "loaded canonical event exact ID does not match requested exact ID")
        if record.kind != "message" or not str(record.text or "").strip():
            raise ValueError("MemoryCard hydration requires committed message text")
        if record.committed_at_ms > evaluation_time_ms:
            raise ValueError("future canonical event cannot be hydrated")
        if owner_id not in record.member_snapshot_ids:
            raise ValueError("canonical event is outside the owner-local conversation scope")
        if (record.sender_participant_id != owner_id
                and owner_id not in record.visible_recipient_ids):
            raise ValueError("canonical event was not visible in the owner-local scope")
        records.append(record)
    return tuple(records)
