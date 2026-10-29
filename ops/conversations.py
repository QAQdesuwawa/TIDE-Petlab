"""Compatibility exports for optional multi-participant conversations."""
from interaction_extensions.multi_participant.conversations import (
    ConversationDescriptor,
    ConversationFrame,
    ConversationSchemaError,
    ReadOnlyConversationObserverView,
    canonical_member_ids,
    frame_for_member,
    observer_view_for,
)


__all__ = (
    "ConversationDescriptor",
    "ConversationFrame",
    "ConversationSchemaError",
    "ReadOnlyConversationObserverView",
    "canonical_member_ids",
    "frame_for_member",
    "observer_view_for",
)
