"""Apply the CX67 package's frozen realization-lane delivery policy."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from framework.model_api import ModelInvocationContext, model_data_fingerprint
from framework.model_api.identity import same_model_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from .expression_resources import ExpressionResourceBatch
from framework.runtime_kernel import RuntimeSessionBinding
from .realizer_packet import (
    WRITER_LANE_ORDER,
    WriterCard,
    WriterOutputIdentity,
    WriterPacket,
    assemble_writer_packet,
)


VARIANT_WRITER_DELIVERY_VERSION = "variant-writer-delivery-v1"


@dataclass(frozen=True)
class VariantWriterPacket:
    packet: WriterPacket
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        if not isinstance(self.packet, WriterPacket):
            raise ValueError("variant writer result requires WriterPacket")
        object.__setattr__(self, "receipt", MappingProxyType(dict(self.receipt)))


def _same_context(
    batch: ExpressionResourceBatch,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> bool:
    return same_model_fact_identity(context, batch.stage_receipt)


def assemble_variant_writer_packet(
    *, session_binding: RuntimeSessionBinding,
    context: ModelInvocationContext | ModelInvocationContextV2,
    writer_request_id: str,
    output_identity: WriterOutputIdentity,
    expression_resources: ExpressionResourceBatch | None,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
) -> VariantWriterPacket:
    """Deliver enabled lanes and leave explicit evidence for disabled lanes."""
    if not isinstance(session_binding, RuntimeSessionBinding):
        raise ValueError("variant writer requires RuntimeSessionBinding")
    session_binding.verify_integrity()
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("variant writer requires a model invocation context")
    if (
        context.session_id != session_binding.session_id
        or context.temporal_position is None
        or context.temporal_position.binding != session_binding.temporal_binding
    ):
        raise ValueError("variant writer context differs from session binding")
    if not isinstance(cards_by_lane, Mapping):
        raise ValueError("cards_by_lane must be a mapping")
    unknown = set(cards_by_lane) - (set(WRITER_LANE_ORDER) - {"expression"})
    if unknown:
        raise ValueError("variant writer received unknown caller lanes")
    normalized: dict[str, tuple[WriterCard, ...]] = {}
    for lane, raw_cards in cards_by_lane.items():
        if isinstance(raw_cards, (str, bytes)) or not isinstance(
                raw_cards, Sequence):
            raise ValueError(f"writer lane {lane} must be a card sequence")
        cards = tuple(raw_cards)
        if any(not isinstance(card, WriterCard) or card.lane != lane
               for card in cards):
            raise ValueError(f"writer lane {lane} contains invalid cards")
        normalized[lane] = cards
    if expression_resources is not None:
        if not isinstance(expression_resources, ExpressionResourceBatch):
            raise ValueError("expression_resources must be ExpressionResourceBatch")
        if not _same_context(expression_resources, context):
            raise ValueError("expression resources differ from writer context")

    policy = dict(session_binding.runtime_variant.resource_policy)
    delivered_cards = {
        lane: cards
        for lane, cards in normalized.items()
        if policy[lane] == "enabled"
    }
    delivered_expression = (
        expression_resources if policy["expression"] == "enabled" else None)
    packet = assemble_writer_packet(
        context=context,
        writer_request_id=writer_request_id,
        output_identity=output_identity,
        expression_resources=delivered_expression,
        cards_by_lane=delivered_cards,
    )
    lane_records = {}
    for lane in WRITER_LANE_ORDER:
        if lane == "expression":
            supplied = (
                () if expression_resources is None
                else expression_resources.selected_resource_ids)
            delivered = (
                () if delivered_expression is None
                else delivered_expression.selected_resource_ids)
        else:
            supplied = tuple(
                card.card_id for card in normalized.get(lane, ()))
            delivered = tuple(
                card.card_id for card in delivered_cards.get(lane, ()))
        lane_records[lane] = {
            "policy": policy[lane],
            "status": (
                "delivered" if policy[lane] == "enabled"
                else "disabled_by_variant"),
            "supplied_ids": supplied,
            "delivered_ids": delivered,
        }
    material = {
        "version": VARIANT_WRITER_DELIVERY_VERSION,
        "runtime_variant_fingerprint": (
            session_binding.runtime_variant_fingerprint),
        "writer_delivery_profile_id": (
            session_binding.runtime_variant.realizer_profile_id),
        "writer_request_id": packet.writer_request_id,
        "lane_delivery": lane_records,
        "packet_fingerprint": packet.packet_fingerprint,
        "packet_bypassed": False,
        "state_mutated": False,
    }
    return VariantWriterPacket(
        packet=packet,
        receipt=dict(material, fingerprint=model_data_fingerprint(material)),
    )


__all__ = (
    "VARIANT_WRITER_DELIVERY_VERSION",
    "VariantWriterPacket",
    "assemble_variant_writer_packet",
)
