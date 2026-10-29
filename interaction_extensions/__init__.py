"""Optional product interaction extensions built on TIDE framework contracts.

An extension can be enabled by a product composition root, but is never a
requirement of the model-agnostic framework itself.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class InteractionExtensionManifest:
    """Content-free identity and dependency declaration for one extension."""

    extension_id: str
    version: str
    capabilities: tuple[str, ...]
    required_framework_contracts: tuple[str, ...]
    required: bool = False

    def __post_init__(self) -> None:
        if not self.extension_id.strip():
            raise ValueError("interaction extension id is required")
        if not self.version.strip():
            raise ValueError("interaction extension version is required")
        if not self.capabilities or any(
            not str(item).strip() for item in self.capabilities
        ):
            raise ValueError("interaction extension capabilities are required")
        if len(set(self.capabilities)) != len(self.capabilities):
            raise ValueError("interaction extension capabilities must be unique")
        if any(not str(item).strip() for item in self.required_framework_contracts):
            raise ValueError("framework contract names must be non-empty")


__all__ = ("InteractionExtensionManifest",)
