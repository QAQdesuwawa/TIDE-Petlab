"""Read-only WriterPacket shadow assembly from formal model-stage evidence.

This seam does not execute an interaction model, allocate an attempt, render a
prompt, call a provider, or authorize/commit an output.  It only validates two
already completed model stages and assembles their published expression
resources with caller-supplied, already-authorized framework cards.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    freeze_model_payload,
)
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from product_adapters.cx_six_seven_v1.expression_resources import (
    read_expression_resource_batch,
)
from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterCard,
    WriterOutputIdentity,
    WriterPacket,
    assemble_writer_packet,
    verify_writer_packet,
)


WRITER_PACKET_SHADOW_VERSION = "writer-packet-shadow-v1"


@dataclass(frozen=True)
class WriterPacketShadow:
    """One non-authoritative packet and its content-free lineage receipt."""

    packet: WriterPacket
    receipt: Mapping[str, Any]


def _context_identity(
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> dict[str, Any]:
    identity = context_fact_identity(context)
    position = identity.temporal_position
    return {
        "session_id": identity.session_id,
        "conversation_id": identity.scope_ref,
        "participant_id": identity.subject_ref,
        "event_id": identity.event_ref,
        "action_id": identity.action_ref,
        "at_ms": identity.legacy_scalar_position,
        "temporal_position_fingerprint": (
            position.fingerprint if position is not None else None),
        "runtime_ordinal": context.runtime_ordinal,
    }


def build_writer_packet_shadow(
    *,
    context: ModelInvocationContext | ModelInvocationContextV2,
    shadow_request_id: str,
    shared_projection_execution: ModelStageExecution,
    semantic_resource_execution: ModelStageExecution,
    output_identity: WriterOutputIdentity,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
) -> WriterPacketShadow:
    """Assemble a packet from already-executed, same-context model evidence.

    ``shadow_request_id`` is a caller-provided, non-attempt identity.  Merely
    assembling this shadow is not a Writer/provider attempt and therefore does
    not append to ``WriterAttemptJournal``.  A later real provider attempt must
    allocate its own request identity before invocation.
    """
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("context must be a model invocation context")
    batch = read_expression_resource_batch(
        semantic_resource_execution,
        expected_context=context,
        shared_projection_execution=shared_projection_execution,
    )
    packet = assemble_writer_packet(
        context=context,
        writer_request_id=shadow_request_id,
        output_identity=output_identity,
        expression_resources=batch,
        cards_by_lane=cards_by_lane,
    )
    packet_receipt = verify_writer_packet(packet)
    expression_cards = tuple({
        "card_id": card.card_id,
        "source_id": card.source_id,
        "source_receipt_refs": list(card.source_receipt_refs),
    } for card in packet.sections["expression"])
    receipt = freeze_model_payload({
        "version": WRITER_PACKET_SHADOW_VERSION,
        "mode": "read_only_shadow",
        "shadow_request_id": packet.writer_request_id,
        "context_identity": _context_identity(context),
        "shared_projection_stage_receipt": (
            shared_projection_execution.receipt.to_record()),
        "semantic_resource_stage_receipt": (
            semantic_resource_execution.receipt.to_record()),
        "shared_projection_output_fingerprint": (
            shared_projection_execution.receipt.output_fingerprint),
        "semantic_resource_output_fingerprint": (
            semantic_resource_execution.receipt.output_fingerprint),
        "expression_batch_version": batch.version,
        "expression_selection_version": batch.selection_version,
        "expression_cards": expression_cards,
        "packet_receipt": packet_receipt,
        "packet_fingerprint": packet.packet_fingerprint,
        "authority": {
            "model_execution": False,
            "writer_attempt": False,
            "provider_call": False,
            "state_write": False,
            "commit": False,
            "send": False,
        },
    })
    return WriterPacketShadow(packet=packet, receipt=receipt)


__all__ = (
    "WRITER_PACKET_SHADOW_VERSION",
    "WriterPacketShadow",
    "build_writer_packet_shadow",
)
