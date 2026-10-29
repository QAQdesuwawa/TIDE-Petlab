"""Compatibility exports for the six/seven model participant contract.

Participant semantics belong to the selected interaction-model package.  The
multi-participant extension owns conversation membership and routing only.
"""
from ..participants import (
    NpcActorProfile,
    Participant,
    ParticipantCapabilities,
    ParticipantRegistry,
    ParticipantSchemaError,
    make_human_participant,
    make_npc_profile,
    make_simulated_participant,
)


__all__ = (
    "NpcActorProfile",
    "Participant",
    "ParticipantCapabilities",
    "ParticipantRegistry",
    "ParticipantSchemaError",
    "make_human_participant",
    "make_npc_profile",
    "make_simulated_participant",
)
