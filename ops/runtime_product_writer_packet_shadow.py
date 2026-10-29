"""Assemble a read-only WriterPacket shadow from one complete lifecycle.

The bridge only consumes already-verified products.  It neither executes the
interaction model nor allocates an output attempt, renders a prompt, writes
state, commits an action, or sends content.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import context_fact_identity
from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterCard,
    WriterOutputIdentity,
)
from ops.runtime_product_fact_lifecycle import RuntimeProductFactLifecycle
from ops.writer_packet_shadow import WriterPacketShadow, build_writer_packet_shadow


PRODUCT_WRITER_PACKET_SHADOW_VERSION = (
    "runtime-product-writer-packet-shadow-v1")


@dataclass(frozen=True)
class RuntimeProductWriterPacketShadow:
    """One lifecycle-bound, non-authoritative WriterPacket shadow."""

    lifecycle: RuntimeProductFactLifecycle
    packet_shadow: WriterPacketShadow
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _verify_lifecycle(lifecycle: RuntimeProductFactLifecycle) -> None:
    receipt = thaw_model_data(lifecycle.receipt)
    fingerprint = receipt.pop("fingerprint", None)
    if model_data_fingerprint(receipt) != fingerprint:
        raise ValueError("lifecycle receipt fingerprint is invalid")
    if (
        receipt.get("version") != "runtime-product-fact-lifecycle-v1"
        or receipt.get("status") != "committed_with_expression_projection"
        or receipt.get("event_id") != lifecycle.evidence.event_id
        or receipt.get("evidence_receipt_fingerprint")
        != model_data_fingerprint(lifecycle.evidence.receipt)
        or receipt.get("transaction_receipt_fingerprint")
        != model_data_fingerprint(lifecycle.transaction.receipt)
        or receipt.get("expression_receipt_fingerprint")
        != model_data_fingerprint(lifecycle.expression.receipt)
    ):
        raise ValueError("lifecycle receipt differs from its products")


def _current_input_card(
    lifecycle: RuntimeProductFactLifecycle,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
) -> WriterCard:
    if not isinstance(cards_by_lane, Mapping):
        raise ValueError("cards_by_lane must be a mapping")
    cards = cards_by_lane.get("current_input", ())
    if isinstance(cards, (str, bytes)) or not isinstance(cards, Sequence):
        raise ValueError("current input lane must be a card sequence")
    if len(cards) != 1 or not isinstance(cards[0], WriterCard):
        raise ValueError("lifecycle requires exactly one current input card")
    card = cards[0]
    if card.lane != "current_input" or card.source_id != lifecycle.evidence.event_id:
        raise ValueError("current input card differs from lifecycle event")
    evidence_ref = model_data_fingerprint(lifecycle.evidence.receipt)
    if evidence_ref not in card.source_receipt_refs:
        raise ValueError("current input card lacks lifecycle evidence receipt")
    return card


def build_product_writer_packet_shadow(
    *,
    lifecycle: RuntimeProductFactLifecycle,
    shadow_request_id: str,
    output_identity: WriterOutputIdentity,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
) -> RuntimeProductWriterPacketShadow:
    """Consume a complete lifecycle without reopening its stage arguments."""
    if not isinstance(lifecycle, RuntimeProductFactLifecycle):
        raise ValueError("product WriterPacket shadow requires a complete lifecycle")
    _verify_lifecycle(lifecycle)
    current = _current_input_card(lifecycle, cards_by_lane)
    expression = lifecycle.expression
    expression_identity = context_fact_identity(expression.context)
    packet_shadow = build_writer_packet_shadow(
        context=expression.context,
        shadow_request_id=shadow_request_id,
        shared_projection_execution=expression.shared_projection_execution,
        semantic_resource_execution=expression.semantic_resource_execution,
        output_identity=output_identity,
        cards_by_lane=cards_by_lane,
    )
    material = {
        "version": PRODUCT_WRITER_PACKET_SHADOW_VERSION,
        "mode": "read_only_shadow",
        "session_id": expression_identity.session_id,
        "conversation_id": expression_identity.scope_ref,
        "event_id": expression_identity.event_ref,
        "shadow_request_id": packet_shadow.packet.writer_request_id,
        "current_input_card_id": current.card_id,
        "lifecycle_receipt_fingerprint": model_data_fingerprint(
            lifecycle.receipt),
        "packet_shadow_receipt_fingerprint": model_data_fingerprint(
            packet_shadow.receipt),
        "packet_fingerprint": packet_shadow.packet.packet_fingerprint,
        "authority": {
            "model_execution": False,
            "writer_attempt": False,
            "external_call": False,
            "state_write": False,
            "commit": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeProductWriterPacketShadow(
        lifecycle=lifecycle,
        packet_shadow=packet_shadow,
        receipt=receipt,
    )


__all__ = (
    "PRODUCT_WRITER_PACKET_SHADOW_VERSION",
    "RuntimeProductWriterPacketShadow",
    "build_product_writer_packet_shadow",
)
