"""Optional multi-participant feature derived from the CX six/seven package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass(frozen=True)
class CxSixSevenDerivedFeatureManifest:
    """Package-owned feature declaration; it is not a framework extension."""

    package_id: str
    feature_id: str
    version: str
    capabilities: Tuple[str, ...]
    required: bool = False

    def __post_init__(self) -> None:
        if self.package_id != "tide.cx-six-seven":
            raise ValueError("multi-participant feature must belong to the CX six/seven package")
        if not self.feature_id or not self.version or not self.capabilities:
            raise ValueError("derived feature manifest fields must be non-empty")


FEATURE_MANIFEST = CxSixSevenDerivedFeatureManifest(
    package_id="tide.cx-six-seven",
    feature_id="multi-participant",
    version="1.0.0-alpha04",
    capabilities=(
        "model_participant_composition",
        "conversation_membership",
        "local_projection",
        "received_event",
        "recipient_directory",
        "recipient_dispatch",
        "visibility_memory",
    ),
    required=False,
)


__all__ = (
    "CxSixSevenDerivedFeatureManifest",
    "FEATURE_MANIFEST",
)
