"""Operational projection required by the CX six/seven conversation feature.

This module does not define an interaction object.  Concrete objects belong to
the selected model package.  It only validates the small read-only projection
needed by conversation storage and delivery hosts.
"""
from collections.abc import Mapping


class ParticipantProjectionError(ValueError):
    pass


def require_participant_projection(value, *, label="participant"):
    participant_id = str(getattr(value, "participant_id", "") or "").strip()
    capabilities = getattr(value, "capabilities", None)
    serializer = getattr(value, "as_record", None)
    if not participant_id or capabilities is None or not callable(serializer):
        raise ParticipantProjectionError(
            f"{label} must expose participant_id, capabilities and as_record")
    required = (
        "receive_projection",
        "has_local_store",
        "has_symbolic_scheduler",
        "has_writer",
    )
    if any(not isinstance(getattr(capabilities, name, None), bool)
           for name in required):
        raise ParticipantProjectionError(
            f"{label} capabilities projection is invalid")
    record = serializer()
    if not isinstance(record, Mapping):
        raise ParticipantProjectionError(f"{label} record must be mapping data")
    if str(record.get("participant_id") or "").strip() != participant_id:
        raise ParticipantProjectionError(
            f"{label} record identity differs from projection")
    return value


__all__ = ("ParticipantProjectionError", "require_participant_projection")
