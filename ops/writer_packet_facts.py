"""Pure product-fact adapters for the framework WriterPacket contract.

The functions in this module only preserve already-authorized structural
facts.  They do not select facts, infer addressees, read model roots, compose
prompts, or authorize an output action.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterCard,
    WriterOutputIdentity,
)


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _identifiers(
    values: Sequence[object],
    field: str,
    *,
    allow_empty: bool,
) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError(f"{field} must be an identifier sequence")
    normalized = tuple(_identifier(value, field) for value in values)
    if not allow_empty and not normalized:
        raise ValueError(f"{field} cannot be empty")
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} cannot contain duplicate identifiers")
    return normalized


def _receipt_refs(values: Sequence[object]) -> tuple[str, ...]:
    return _identifiers(values, "source receipt refs", allow_empty=False)


def _fact_fields(facts: Mapping[str, Any], field: str) -> dict[str, Any]:
    if not isinstance(facts, Mapping) or not facts:
        raise ValueError(f"{field} must be a non-empty fact mapping")
    normalized: dict[str, Any] = {}
    for raw_label, value in facts.items():
        label = _identifier(raw_label, f"{field} label")
        if label in normalized:
            raise ValueError(f"{field} cannot contain duplicate labels")
        normalized[label] = value
    return normalized


def writer_output_identity(
    *,
    speaker_participant_id: str,
    speaker_kind: str,
    active_member_ids: Sequence[str],
    addressed_participant_ids: Sequence[str],
) -> WriterOutputIdentity:
    """Build explicit output identity without deriving targets from prose."""
    speaker = _identifier(speaker_participant_id, "speaker participant ID")
    active = _identifiers(
        active_member_ids, "active member IDs", allow_empty=False)
    if speaker not in active:
        raise ValueError("speaker participant ID must belong to active members")
    addressed = _identifiers(
        addressed_participant_ids,
        "addressed participant IDs",
        allow_empty=True,
    )
    audience = tuple(member for member in active if member != speaker)
    return WriterOutputIdentity(
        speaker_participant_id=speaker,
        speaker_kind=_identifier(speaker_kind, "speaker kind"),
        audience_participant_ids=audience,
        addressed_participant_ids=addressed,
    )


def current_input_writer_card(
    *,
    event_id: str,
    sender_participant_id: str,
    committed_position_label: str,
    text: str,
    delivery_id: str | None,
    source_receipt_refs: Sequence[str],
) -> WriterCard:
    """Represent one committed input event; previews are not accepted here."""
    event = _identifier(event_id, "current input event ID")
    sender = _identifier(
        sender_participant_id, "current input sender participant ID")
    position = _identifier(
        committed_position_label, "committed temporal position label")
    committed_text = str(text or "").strip()
    if not committed_text:
        raise ValueError("current input text cannot be empty")
    fields: dict[str, Any] = {
        "说话者": sender,
        "提交位置": position,
        "原话": committed_text,
        "事件标识": event,
    }
    if delivery_id is not None:
        fields["投递标识"] = _identifier(
            delivery_id, "current input delivery ID")
    return WriterCard(
        card_id=f"current:{event}",
        lane="current_input",
        kind="committed_event",
        source_id=event,
        fields=fields,
        source_receipt_refs=_receipt_refs(source_receipt_refs),
    )


def persona_fact_writer_card(
    *,
    profile_version: str,
    facts: Mapping[str, Any],
    source_receipt_refs: Sequence[str],
) -> WriterCard:
    """Carry already-selected persona facts without ranking or rewriting."""
    version = _identifier(profile_version, "persona profile version")
    return WriterCard(
        card_id=f"persona:{version}",
        lane="persona",
        kind="persona_fact",
        source_id=version,
        fields=_fact_fields(facts, "persona facts"),
        source_receipt_refs=_receipt_refs(source_receipt_refs),
    )


def conversation_fact_writer_card(
    *,
    conversation_id: str,
    membership_version: str,
    active_member_ids: Sequence[str],
    source_receipt_refs: Sequence[str],
) -> WriterCard:
    """Carry the explicit conversation frame and committed membership view."""
    conversation = _identifier(conversation_id, "conversation ID")
    membership = _identifier(membership_version, "membership version")
    members = _identifiers(
        active_member_ids, "active member IDs", allow_empty=False)
    return WriterCard(
        card_id=f"conversation:{conversation}:{membership}",
        lane="conversation",
        kind="conversation_fact",
        source_id=membership,
        fields={
            "会话标识": conversation,
            "成员版本": membership,
            "当前成员": members,
        },
        source_receipt_refs=_receipt_refs(source_receipt_refs),
    )


__all__ = (
    "conversation_fact_writer_card",
    "current_input_writer_card",
    "persona_fact_writer_card",
    "writer_output_identity",
)
