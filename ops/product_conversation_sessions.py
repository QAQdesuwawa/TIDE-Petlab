"""Compatibility exports for optional multi-participant session registry."""
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSession,
    ProductConversationSessionError,
    ProductConversationSessionRegistry,
    ProductDirectConversationSession,
)


__all__ = (
    "ProductConversationSession",
    "ProductConversationSessionError",
    "ProductConversationSessionRegistry",
    "ProductDirectConversationSession",
)
