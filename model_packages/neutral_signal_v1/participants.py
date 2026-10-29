"""Interaction objects owned by the neutral-signal sample package."""
from dataclasses import dataclass
from typing import Mapping


PACKAGE_ID = "tide.neutral-signal"


@dataclass(frozen=True)
class NeutralSignalCapabilities:
    receive_projection: bool = True
    has_local_store: bool = True
    has_symbolic_scheduler: bool = True
    has_writer: bool = True

    def as_record(self):
        return {
            "receive_projection": self.receive_projection,
            "has_local_store": self.has_local_store,
            "has_symbolic_scheduler": self.has_symbolic_scheduler,
            "has_writer": self.has_writer,
        }


@dataclass(frozen=True)
class NeutralSignalParticipant:
    participant_id: str
    capabilities: NeutralSignalCapabilities = NeutralSignalCapabilities()
    # Role label consumed only when this object is composed with TIDE's
    # optional conversation product adapter.  The framework does not inspect it.
    kind: str = "npc"
    schema_version: int = 1

    def __post_init__(self):
        if not str(self.participant_id or "").strip():
            raise ValueError("neutral-signal participant_id is required")
        if self.schema_version != 1 or self.kind != "npc":
            raise ValueError("neutral-signal participant contract is invalid")

    def as_record(self):
        return {
            "schema_version": self.schema_version,
            "model_package_id": PACKAGE_ID,
            "participant_id": self.participant_id,
            "kind": self.kind,
            "capabilities": self.capabilities.as_record(),
        }

    @classmethod
    def from_record(cls, raw):
        if not isinstance(raw, Mapping) or raw.get("model_package_id") != PACKAGE_ID:
            raise ValueError("not a neutral-signal participant record")
        capabilities = raw.get("capabilities")
        if not isinstance(capabilities, Mapping):
            raise ValueError("neutral-signal capabilities are required")
        return cls(
            participant_id=raw.get("participant_id"),
            kind=raw.get("kind"),
            schema_version=raw.get("schema_version"),
            capabilities=NeutralSignalCapabilities(**dict(capabilities)),
        )


def make_autonomous_participant(participant_id):
    return NeutralSignalParticipant(participant_id=participant_id)


__all__ = (
    "NeutralSignalCapabilities",
    "NeutralSignalParticipant",
    "make_autonomous_participant",
)
