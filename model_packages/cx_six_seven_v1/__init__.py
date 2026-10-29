"""Versioned six-state/seven-constant reference model.

Top-level adapter names stay compatible, but are loaded lazily so importing a
package-owned schema does not initialize the whole legacy adapter graph.
"""
from __future__ import annotations

from typing import Any


__all__ = [
    "CxSixSevenCoreOperatorsAdapter",
    "CxSixSevenFullV2Package",
    "CxSixSevenV2Package",
]


def __getattr__(name: str) -> Any:
    if name == "CxSixSevenCoreOperatorsAdapter":
        from .core_operators import CxSixSevenCoreOperatorsAdapter
        return CxSixSevenCoreOperatorsAdapter
    if name == "CxSixSevenV2Package":
        from .v2_package import CxSixSevenV2Package
        return CxSixSevenV2Package
    if name == "CxSixSevenFullV2Package":
        from .full_v2_package import CxSixSevenFullV2Package
        return CxSixSevenFullV2Package
    raise AttributeError(name)
