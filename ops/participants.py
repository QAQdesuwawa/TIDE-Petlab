"""Compatibility exports for the six/seven model participant contract."""
from model_packages.cx_six_seven_v1.participants import (
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
