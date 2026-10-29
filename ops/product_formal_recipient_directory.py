"""Compatibility export for the optional multi-participant extension.

New composition code imports the extension namespace directly. This module
keeps existing product callers and frozen tests on the same class object while
the remaining ``ops`` call sites migrate in bounded slices.
"""
from interaction_extensions.multi_participant.recipient_directory import (
    ProductFormalRecipientDirectory,
)


__all__ = ("ProductFormalRecipientDirectory",)
