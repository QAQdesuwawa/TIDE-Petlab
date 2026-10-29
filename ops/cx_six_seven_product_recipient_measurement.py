"""Product integration from a recipient projection to CX67 measurement.

This adapter owns product DTO knowledge.  The model package owns only semantic
normalization and evidence construction; no product type flows back into it.
"""
from __future__ import annotations

from collections.abc import Mapping

from model_packages.cx_six_seven_v1.multi_participant.local_projection import (
    LocalProjection,
)
from model_packages.cx_six_seven_v1.semantic_projection import coerce_projection
from ops.runtime_product_recipient_dispatch import (
    PreparedFormalRecipientMeasurement,
)


_INPUT_KIND_BY_SENDER = {
    "human": "human_input",
    "npc": "peer_input",
    "simulation": "simulation_input",
}


class CxSixSevenProductRecipientMeasurementProducer:
    """Prepare package-owned evidence for one addressed product delivery."""

    def __init__(
        self, *, package: object | None = None,
        committed_measurement_evidence_factory: object | None = None,
        semantic_projector: object,
        sender_participant_kind: str,
    ) -> None:
        if package is not None and committed_measurement_evidence_factory is not None:
            raise TypeError(
                "CX67 product measurement requires one evidence authority")
        evidence_factory = committed_measurement_evidence_factory
        if evidence_factory is None:
            evidence_factory = getattr(
                package, "committed_measurement_evidence", None)
        if not callable(evidence_factory):
            raise TypeError(
                "CX67 product measurement requires package evidence factory")
        project = getattr(semantic_projector, "project", None)
        if not callable(project):
            raise TypeError(
                "CX67 product measurement requires semantic projector")
        sender_kind = str(sender_participant_kind or "").strip()
        if sender_kind not in _INPUT_KIND_BY_SENDER:
            raise ValueError(
                "CX67 product measurement sender kind is invalid")
        self._evidence_factory = evidence_factory
        self._project = project
        self._event_kind = _INPUT_KIND_BY_SENDER[sender_kind]

    def __call__(
        self, projection: LocalProjection,
    ) -> PreparedFormalRecipientMeasurement:
        if not isinstance(projection, LocalProjection):
            raise TypeError(
                "CX67 product measurement requires LocalProjection")
        if projection.delivery_scope != "addressed":
            raise ValueError(
                "CX67 product measurement requires addressed projection")
        if not isinstance(projection.text, str) or not projection.text:
            raise ValueError(
                "CX67 product measurement requires committed source text")
        local_event_id = f"ingress:{projection.delivery_id}"
        raw = self._project(
            text=projection.text,
            dialogue_tail="",
            recall_hint=None,
            state_vars={},
            event_kind=self._event_kind,
        )
        normalized = coerce_projection(
            raw,
            turn_id=local_event_id,
            event_kind=self._event_kind,
        )
        if normalized is None:
            raise ValueError(
                "CX67 product semantic measurement is unavailable")
        semantic = normalized.as_record()
        evidence = self._evidence_factory(
            projection=semantic,
            source_event_id=projection.source_event_id,
            local_event_id=local_event_id,
            event_kind=self._event_kind,
        )
        if not isinstance(evidence, Mapping):
            raise TypeError(
                "CX67 package evidence factory returned invalid evidence")
        return PreparedFormalRecipientMeasurement(
            recipient_participant_id=projection.recipient_participant_id,
            delivery_id=projection.delivery_id,
            semantic_projection=semantic,
            measurement_evidence=evidence,
        )


__all__ = ["CxSixSevenProductRecipientMeasurementProducer"]
