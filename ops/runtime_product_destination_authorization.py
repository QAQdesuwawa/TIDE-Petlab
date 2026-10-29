"""Pure product authorization for an autonomous action's destination.

The Kernel grant proves that one participant may open autonomously at the
current runtime position.  It deliberately does not choose a Conversation.
This module binds that grant to one explicitly selected, already registered
conversation snapshot and freezes the visible/addressed audience.  It creates
no Writer request, action, source event, route, recipient ingress, or send.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import freeze_model_payload, model_data_fingerprint
from interaction_extensions.conversation_action_protocol import (
    RuntimeActionAuthorization,
)
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimePositionState,
    RuntimeSessionBinding,
)
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)


PRODUCT_DESTINATION_AUTHORIZATION_VERSION = (
    "runtime-product-destination-authorization-v1"
)


@dataclass(frozen=True)
class RuntimeProductDestinationAuthorization:
    """One autonomous grant bound to one immutable conversation snapshot."""

    authorization: RuntimeActionAuthorization
    conversation_id: str
    membership_version: int
    participant_id: str
    participant_kind: str
    audience_participant_ids: tuple[str, ...]
    addressed_participant_ids: tuple[str, ...]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "audience_participant_ids", tuple(self.audience_participant_ids))
        object.__setattr__(
            self, "addressed_participant_ids", tuple(self.addressed_participant_ids))
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def authorize_autonomous_product_destination(
    *,
    authorization: RuntimeActionAuthorization,
    current_position: RuntimePositionState,
    session_binding: RuntimeSessionBinding,
    conversation_registry: ProductConversationSessionRegistry,
    conversation_id: str,
) -> RuntimeProductDestinationAuthorization:
    """Bind an exact autonomous grant to a caller-selected registered session."""
    if not isinstance(authorization, RuntimeActionAuthorization):
        raise RuntimeKernelContractError(
            "autonomous destination requires RuntimeActionAuthorization")
    # Check mode before any unrelated fixture/binding detail: a required reply
    # already owns its destination and must never enter this path.
    if authorization.mode != "autonomous_opening":
        raise RuntimeKernelContractError(
            "autonomous destination requires autonomous_opening authorization")
    if not isinstance(session_binding, RuntimeSessionBinding):
        raise RuntimeKernelContractError(
            "autonomous destination requires RuntimeSessionBinding")
    if not isinstance(current_position, RuntimePositionState):
        raise RuntimeKernelContractError(
            "autonomous destination requires RuntimePositionState")
    if not isinstance(conversation_registry, ProductConversationSessionRegistry):
        raise ValueError(
            "autonomous destination requires ProductConversationSessionRegistry")
    authorization.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
    )

    selected_conversation_id = str(conversation_id or "").strip()
    if not selected_conversation_id:
        raise ValueError("autonomous destination requires registered conversation")
    session = conversation_registry.load_session(selected_conversation_id)
    if session is None:
        raise ValueError("autonomous destination requires registered conversation")

    conversation = session.conversation
    participant_id = authorization.participant_id
    participant = session.participants_by_id.get(participant_id)
    if participant is None:
        raise RuntimeKernelContractError(
            "autonomous speaker must be an active conversation member")
    capabilities = participant.capabilities
    if not (
        capabilities.has_local_store
        and capabilities.has_symbolic_scheduler
        and capabilities.has_writer
    ):
        raise RuntimeKernelContractError(
            "autonomous speaker lacks local state, scheduler, or writer capability")

    audience = tuple(
        member_id
        for member_id in conversation.active_member_ids
        if member_id != participant_id
    )
    if not audience:
        raise RuntimeKernelContractError(
            "autonomous destination requires at least one other active member")
    if conversation.kind == "direct":
        if len(audience) != 1:
            raise RuntimeKernelContractError(
                "direct autonomous destination requires one counterparty")
        destination_mode = "direct_counterparty"
    elif conversation.kind == "group":
        destination_mode = "group_broadcast"
    else:  # ConversationDescriptor already rejects this; keep the boundary local.
        raise RuntimeKernelContractError("unknown conversation destination kind")

    addressed = audience
    material = {
        "version": PRODUCT_DESTINATION_AUTHORIZATION_VERSION,
        "status": "autonomous_destination_authorized",
        "session_binding_fingerprint": session_binding.fingerprint,
        "authorization_fingerprint": authorization.fingerprint,
        "source_event_id": authorization.source_event_id,
        "source_position_fingerprint": authorization.source_position_fingerprint,
        "source_runtime_ordinal": authorization.source_runtime_ordinal,
        "conversation_id": conversation.conversation_id,
        "membership_version": conversation.membership_version,
        "participant_id": participant_id,
        "participant_kind": participant.kind,
        "audience_participant_ids": list(audience),
        "addressed_participant_ids": list(addressed),
        "destination_mode": destination_mode,
        "authority": {
            "writer_attempt": False,
            "provider_call": False,
            "action_commit": False,
            "source_write": False,
            "route": False,
            "recipient_dispatch": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    receipt = dict(material)
    receipt["fingerprint"] = model_data_fingerprint(material)
    return RuntimeProductDestinationAuthorization(
        authorization=authorization,
        conversation_id=conversation.conversation_id,
        membership_version=conversation.membership_version,
        participant_id=participant_id,
        participant_kind=participant.kind,
        audience_participant_ids=audience,
        addressed_participant_ids=addressed,
        receipt=receipt,
    )


__all__ = (
    "PRODUCT_DESTINATION_AUTHORIZATION_VERSION",
    "RuntimeProductDestinationAuthorization",
    "authorize_autonomous_product_destination",
)
