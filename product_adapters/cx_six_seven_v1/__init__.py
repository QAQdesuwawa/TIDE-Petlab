"""Explicit product compatibility adapters for the six/seven reference model."""

from .full_memory_history_migration import (
    LEGACY_FULL_MEMORY_HISTORY_MIGRATION_VERSION,
    LegacyFullMemoryHistoryMigration,
    migrate_legacy_full_memory_history,
)

__all__ = (
    "LEGACY_FULL_MEMORY_HISTORY_MIGRATION_VERSION",
    "LegacyFullMemoryHistoryMigration",
    "migrate_legacy_full_memory_history",
)
