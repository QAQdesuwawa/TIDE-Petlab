"""Reference-product adapter from one human source fact to package evidence."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from model_packages.cx_six_seven_v1.semantic_projection import coerce_projection
from ops.conversation_events import CommittedConversationEvent


@dataclass(frozen=True)
class PreparedDirectInputMeasurement:
    semantic_projection: Mapping[str, Any]
    measurement_evidence: Mapping[str, Any]


class CxSixSevenProductDirectMeasurementProducer:
    """Run the configured Agent-1 projector, then let the package sign it."""

    def __init__(self, *, semantic_projector, evidence_factory) -> None:
        project = getattr(semantic_projector, "project", None)
        if not callable(project):
            raise TypeError(
                "formal direct input requires an explicit semantic projector")
        if not callable(evidence_factory):
            raise TypeError(
                "formal direct input requires a package evidence factory")
        self._project = project
        self._evidence_factory = evidence_factory

    def __call__(self, event: CommittedConversationEvent):
        if not isinstance(event, CommittedConversationEvent):
            raise TypeError("direct measurement requires committed source event")
        if event.kind != "message" or not event.text:
            raise ValueError("direct measurement requires committed message text")
        local_event_id = f"ingress:{event.event_id}"
        raw = self._project(
            text=event.text,
            dialogue_tail="",
            recall_hint=None,
            state_vars={},
            event_kind="human_input",
        )
        normalized = coerce_projection(
            raw, turn_id=local_event_id, event_kind="human_input")
        if normalized is None:
            raise ValueError("formal direct semantic measurement is unavailable")
        projection = normalized.as_record()
        evidence = self._evidence_factory(
            projection=projection,
            source_event_id=event.event_id,
            local_event_id=local_event_id,
            event_kind="human_input",
        )
        if not isinstance(evidence, Mapping):
            raise TypeError("package evidence factory returned invalid evidence")
        return PreparedDirectInputMeasurement(
            semantic_projection=projection,
            measurement_evidence=evidence,
        )


__all__ = (
    "CxSixSevenProductDirectMeasurementProducer",
    "PreparedDirectInputMeasurement",
)
