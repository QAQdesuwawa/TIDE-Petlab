"""Compatibility exports for multi-participant received-event contracts."""
from interaction_extensions.multi_participant.received_events import (
    ReceivedParticipantEvent,
    ReceivedParticipantEventError,
    legacy_external_input,
    legacy_peer_input,
    received_from_projection,
)


__all__ = (
    "ReceivedParticipantEvent",
    "ReceivedParticipantEventError",
    "legacy_external_input",
    "legacy_peer_input",
    "received_from_projection",
)
