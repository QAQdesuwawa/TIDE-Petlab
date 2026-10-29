"""Compatibility exports for multi-participant visibility memory."""
from interaction_extensions.multi_participant.visibility_memory import (
    GroupVisibilityMemoryError,
    archive_visible_group_message,
)


__all__ = ("GroupVisibilityMemoryError", "archive_visible_group_message")
