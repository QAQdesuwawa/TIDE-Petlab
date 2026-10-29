"""Compatibility exports for multi-participant local projections."""
from interaction_extensions.multi_participant.local_projection import (
    LocalProjection,
    LocalProjectionError,
    project_delivery,
)


__all__ = ("LocalProjection", "LocalProjectionError", "project_delivery")
