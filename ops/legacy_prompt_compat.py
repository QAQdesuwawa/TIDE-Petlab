"""Explicit Alpha 0.3/family-off responder prompt compatibility surface.

Formal W2/W7 provider input is assembled from WriterPacket material.  These
names expose the unchanged historical builders only to explicit compatibility
flows; they are not a fallback for the default product lifecycle.
"""

from ops.turn_prompt import (
    assemble_compat_autonomous_prompt,
    assemble_compat_received_prompt,
)


__all__ = (
    "assemble_compat_autonomous_prompt",
    "assemble_compat_received_prompt",
)
