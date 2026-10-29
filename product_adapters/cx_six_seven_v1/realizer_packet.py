"""Product-host view of the CX67 package-owned realization packet.

This adapter introduces no second schema and no realization authority.  It
only gives product/interaction hosts an explicit dependency edge to the chosen
model package while preserving exact Python object identity.
"""
from model_packages.cx_six_seven_v1.realizer_packet import (
    DEFAULT_WRITER_PROMPT_BUDGET_CHARS,
    WRITER_LANE_ORDER,
    WRITER_PACKET_VERSION,
    SUBCARD_WRITER_PACKET_VERSION,
    PROVENANCE_WRITER_PACKET_VERSION,
    WriterCard,
    WriterSubcard,
    WriterOutputIdentity,
    WriterPacket,
    assemble_writer_packet,
    restore_writer_packet,
    verify_writer_packet,
)


__all__ = (
    "DEFAULT_WRITER_PROMPT_BUDGET_CHARS",
    "WRITER_LANE_ORDER",
    "WRITER_PACKET_VERSION",
    "SUBCARD_WRITER_PACKET_VERSION",
    "PROVENANCE_WRITER_PACKET_VERSION",
    "WriterCard",
    "WriterSubcard",
    "WriterOutputIdentity",
    "WriterPacket",
    "assemble_writer_packet",
    "restore_writer_packet",
    "verify_writer_packet",
)
