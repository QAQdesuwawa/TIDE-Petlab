"""Optional ID-only subcard for already-qualified, already-selected history.

The package assembler owns the heading, parent budget and actual delivery.
This formatter cannot recall memories, derive links, read text, settle state,
or verify source receipts. The caller supplies qualified chains in occurrence
order and keeps their source receipts for the existing audit mechanism.
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from interaction_extensions import InteractionExtensionManifest


VERSION = "memory-link-subcard-v1"
MANIFEST = InteractionExtensionManifest(
    extension_id="memory-links", version=VERSION,
    capabilities=("read-only-memory-link-subcard",),
    required_framework_contracts=(), required=False,
)


def _identifier(value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("link subcard identifiers must be non-empty strings")
    return value


def _identifiers(values: Sequence[str]) -> tuple[str, ...]:
    if isinstance(values, (str, bytes)) or not isinstance(values, Sequence):
        raise ValueError("link subcard identifiers must be a sequence")
    return tuple(_identifier(value) for value in values)


@dataclass(frozen=True)
class MemoryLinkSubcard:
    """Display-only child; IDs and receipt refs are not model-visible text."""

    parent_card_id: str
    selected_memory_ids: tuple[str, ...]
    related_chains: tuple[tuple[str, ...], ...]
    source_receipt_refs: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_card_id", _identifier(self.parent_card_id))
        selected = _identifiers(self.selected_memory_ids)
        if len(selected) != len(set(selected)):
            raise ValueError("selected memory IDs must be unique")
        refs = tuple(dict.fromkeys(_identifiers(self.source_receipt_refs)))
        if not refs:
            raise ValueError("qualified chains require source receipt references")
        if isinstance(self.related_chains, (str, bytes)) or not isinstance(
                self.related_chains, Sequence):
            raise ValueError("related chains must be a sequence")
        positions = {identifier: index for index, identifier in enumerate(selected)}
        chains = []
        for values in self.related_chains:
            chain = _identifiers(values)
            if len(chain) < 2:
                raise ValueError("a related chain requires at least two memories")
            if not set(chain).issubset(positions):
                raise ValueError("chain contains a memory outside the parent material")
            order = tuple(positions[value] for value in chain)
            if any(left >= right for left, right in zip(order, order[1:])):
                raise ValueError("chain must follow supplied memory occurrence order")
            if chain not in chains:
                chains.append(chain)
        if not chains:
            raise ValueError("empty links do not form a subcard")
        # Sort whole chains only. Never reorder a chain's nodes, bridge missing
        # memories or merge paths; doing so could invent a different relation.
        chains.sort(key=lambda chain: tuple(positions[value] for value in chain))
        object.__setattr__(self, "selected_memory_ids", selected)
        object.__setattr__(self, "related_chains", tuple(chains))
        object.__setattr__(self, "source_receipt_refs", refs)

    @property
    def text(self) -> str:
        positions = {identifier: index + 1
                     for index, identifier in enumerate(self.selected_memory_ids)}
        return "\n".join(" → ".join(str(positions[value]) for value in chain)
                         for chain in self.related_chains)

    @property
    def character_count(self) -> int:
        """Actual child text length, not tokens, money or a new budget."""
        return len(self.text)


def build_memory_link_subcard(
    *, parent_card_id: str, selected_memory_ids: Sequence[str],
    related_chains: Sequence[Sequence[str]], source_receipt_refs: Sequence[str],
    enabled: bool = False,
) -> MemoryLinkSubcard | None:
    """No-op by default; caller must include the result in its parent budget."""
    if not isinstance(enabled, bool):
        raise ValueError("memory link extension requires an explicit boolean switch")
    if not enabled:
        return None
    if isinstance(related_chains, (str, bytes)) or not isinstance(related_chains, Sequence):
        raise ValueError("related chains must be a sequence")
    if not related_chains:
        return None
    return MemoryLinkSubcard(parent_card_id, selected_memory_ids,
                             related_chains, source_receipt_refs)


__all__ = ("MANIFEST", "VERSION", "MemoryLinkSubcard", "build_memory_link_subcard")
