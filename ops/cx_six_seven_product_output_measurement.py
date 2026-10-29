"""Reference-product adapter for one committed NPC output measurement."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from framework.model_api import freeze_model_payload
from model_packages.cx_six_seven_v1.semantic_projection import coerce_projection
from ops.runtime_product_fact_bridge import (
    build_committed_output_semantic_receipt,
)


@dataclass(frozen=True)
class PreparedCommittedOutputMeasurement:
    semantic_receipt: Mapping[str, object]
    measurement_evidence: Mapping[str, object]

    def __post_init__(self):
        if not isinstance(self.semantic_receipt, Mapping):
            raise TypeError("output measurement requires semantic receipt")
        if not isinstance(self.measurement_evidence, Mapping):
            raise TypeError("output measurement requires package evidence")
        object.__setattr__(
            self, "semantic_receipt", freeze_model_payload(self.semantic_receipt))
        object.__setattr__(
            self, "measurement_evidence",
            freeze_model_payload(self.measurement_evidence),
        )


class CxSixSevenProductOutputMeasurementProducer:
    """Measure an exact committed action without reading legacy Runner state."""

    def __init__(
        self, *, semantic_projector: object, evidence_factory: Callable,
        parameter_fingerprint: str,
    ) -> None:
        project = getattr(semantic_projector, "project", None)
        if not callable(project):
            raise TypeError("output measurement requires semantic projector")
        if not callable(evidence_factory):
            raise TypeError("output measurement requires package evidence factory")
        params = str(parameter_fingerprint or "").strip()
        if not params:
            raise ValueError("output measurement requires parameter fingerprint")
        self._project = project
        self._evidence_factory = evidence_factory
        self._parameter_fingerprint = params

    def __call__(self, committed_action) -> PreparedCommittedOutputMeasurement:
        event = committed_action.event
        raw = self._project(
            text=event.text,
            dialogue_tail="",
            recall_hint=None,
            state_vars={},
            event_kind="npc_output",
        )
        normalized = coerce_projection(
            raw, turn_id=event.event_id, event_kind="npc_output")
        if normalized is None:
            raise ValueError("output semantic measurement is unavailable")
        projection = normalized.as_record()
        semantic = build_committed_output_semantic_receipt(
            committed_action=committed_action,
            projection=projection,
            params_sha=self._parameter_fingerprint,
        )
        evidence = self._evidence_factory(
            projection=projection,
            source_event_id=event.event_id,
            local_event_id=event.event_id,
            event_kind="npc_output",
        )
        return PreparedCommittedOutputMeasurement(
            semantic_receipt=semantic,
            measurement_evidence=evidence,
        )


__all__ = (
    "CxSixSevenProductOutputMeasurementProducer",
    "PreparedCommittedOutputMeasurement",
)
