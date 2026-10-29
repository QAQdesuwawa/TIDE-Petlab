"""CX67 adapter for a qualified, ID-only optional memory child.

It preserves the supplied parent and source map; it neither verifies history
receipts nor discovers relations. The upstream source authority must qualify
the links and their correspondence with the parent's numbered material.
"""
from __future__ import annotations

from dataclasses import replace

from interaction_extensions.memory_links import MemoryLinkSubcard, VERSION
from framework.model_api import model_data_fingerprint
from .realizer_packet import WriterCard, WriterSubcard


def with_memory_links(parent: WriterCard, links: MemoryLinkSubcard | None) -> WriterCard:
    """Attach an optional child, without changing parent selection or fields."""
    if not isinstance(parent, WriterCard):
        raise ValueError("memory link delivery requires a WriterCard parent")
    if links is None:
        return parent
    if not isinstance(links, MemoryLinkSubcard):
        raise ValueError("memory links require a prebuilt display subcard")
    if parent.lane != "memory":
        raise ValueError("memory links must be attached to the memory lane")
    if parent.card_id != links.parent_card_id:
        raise ValueError("memory link parent differs from the supplied parent")
    if parent.provenance.get("presentation_version") == "cx-numbered-memory-v1":
        if tuple(parent.provenance.get("selected_memory_ids", ())) != links.selected_memory_ids:
            raise ValueError("memory link numbering differs from the parent's source map")
        if parent.provenance.get("fields_fingerprint") != model_data_fingerprint(parent.fields):
            raise ValueError("numbered memory body differs from its source map")
    child = WriterSubcard(
        subcard_id=parent.card_id + ":memory-links",
        kind="memory_link_subcard",
        source_id=VERSION,
        fields={"相关记忆编号链": links.text},
        source_receipt_refs=links.source_receipt_refs,
        provenance={
            "extension_version": VERSION,
            "parent_card_id": links.parent_card_id,
            "selected_memory_ids": links.selected_memory_ids,
            "related_chains": links.related_chains,
        },
    )
    return replace(parent, subcards=(*parent.subcards, child))


__all__ = ("with_memory_links",)
