"""Product composition DTO for one explicitly selected formal session.

This module deliberately knows neither the six/seven reference package nor a
concrete temporal domain.  A product composition root constructs the package
pair and hands Guild this immutable plan; Guild only owns activation order.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Callable, Mapping
from typing import Any

from framework.model_api import freeze_model_payload

from ops.runtime_package_composition import (
    RuntimePackageComposition,
    RuntimeSessionInitialization,
)


@dataclass(frozen=True)
class ProductRuntimeSessionPlan:
    """One create-or-restore plan produced from committed product identity."""

    composition: RuntimePackageComposition
    initialization: RuntimeSessionInitialization | None = None
    committed_measurement_evidence_factory: Callable | None = None
    observation_planning_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.composition, RuntimePackageComposition):
            raise TypeError("composition must be RuntimePackageComposition")
        if (
            self.initialization is not None
            and not isinstance(self.initialization, RuntimeSessionInitialization)
        ):
            raise TypeError(
                "initialization must be RuntimeSessionInitialization or None")
        if (
            self.committed_measurement_evidence_factory is not None
            and not callable(self.committed_measurement_evidence_factory)
        ):
            raise TypeError(
                "committed_measurement_evidence_factory must be callable or None")
        if self.observation_planning_payload is not None:
            if not isinstance(self.observation_planning_payload, Mapping):
                raise TypeError(
                    "observation_planning_payload must be a mapping or None")
            object.__setattr__(
                self,
                "observation_planning_payload",
                freeze_model_payload(self.observation_planning_payload),
            )


__all__ = ("ProductRuntimeSessionPlan",)
