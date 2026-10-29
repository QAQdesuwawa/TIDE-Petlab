"""Explicit Alpha 0.3/family-off recall compatibility surface.

The default W2/W7 product lifecycle uses the bound InteractionModel Full
Memory stages and never imports this service as a fallback.  Historical tools
and explicitly family-off Runner flows may select these names; the functions
remain the exact legacy implementations exported by :mod:`ops.turn_recall`.
"""

from ops.turn_recall import (
    select_compat_autonomous_recall,
    select_compat_received_recall,
)


__all__ = (
    "select_compat_autonomous_recall",
    "select_compat_received_recall",
)
