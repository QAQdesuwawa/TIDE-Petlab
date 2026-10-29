"""Model-package-neutral semantic measurement adapters for product facts.

The adapters bind committed text to package-owned evidence.  They do not
normalize any concrete axis names: the selected package validates and signs
the projector output.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from framework.model_api import freeze_model_payload
from interaction_extensions.multi_participant.events import (
    CommittedConversationEvent,
)
from interaction_extensions.multi_participant.local_projection import LocalProjection
from ops.runtime_product_fact_bridge import (
    build_committed_output_semantic_receipt,
)
from ops.runtime_product_recipient_dispatch import (
    PreparedFormalRecipientMeasurement,
)


_INPUT_KIND_BY_SENDER = {
    "human": "human_input",
    "npc": "peer_input",
    "simulation": "simulation_input",
}


@dataclass(frozen=True)
class PreparedPackageMeasurement:
    semantic_projection: Mapping[str, object]
    measurement_evidence: Mapping[str, object]

    def __post_init__(self):
        if not isinstance(self.semantic_projection, Mapping):
            raise TypeError("package measurement requires semantic projection")
        if not isinstance(self.measurement_evidence, Mapping):
            raise TypeError("package measurement requires package evidence")
        object.__setattr__(
            self, "semantic_projection",
            freeze_model_payload(self.semantic_projection),
        )
        object.__setattr__(
            self, "measurement_evidence",
            freeze_model_payload(self.measurement_evidence),
        )


@dataclass(frozen=True)
class PreparedPackageOutputMeasurement:
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


class ModelPackageDirectMeasurementProducer:
    """Measure one committed direct input with the selected package contract."""

    def __init__(self, *, semantic_projector, evidence_factory: Callable) -> None:
        project = getattr(semantic_projector, "project", None)
        if not callable(project):
            raise TypeError("direct input requires an explicit semantic projector")
        if not callable(evidence_factory):
            raise TypeError("direct input requires a package evidence factory")
        self._project = project
        self._evidence_factory = evidence_factory

    def __call__(self, event: CommittedConversationEvent):
        if not isinstance(event, CommittedConversationEvent):
            raise TypeError("direct measurement requires committed source event")
        if event.kind != "message" or not event.text:
            raise ValueError("direct measurement requires committed message text")
        local_event_id = f"ingress:{event.event_id}"
        projection = self._project(
            text=event.text,
            dialogue_tail="",
            recall_hint=None,
            state_vars={},
            event_kind="human_input",
        )
        if not isinstance(projection, Mapping):
            raise ValueError("direct semantic measurement is unavailable")
        evidence = self._evidence_factory(
            projection=projection,
            source_event_id=event.event_id,
            local_event_id=local_event_id,
            event_kind="human_input",
        )
        return PreparedPackageMeasurement(
            semantic_projection=projection,
            measurement_evidence=evidence,
        )


class ModelPackageOutputMeasurementProducer:
    """Measure one canonical output with the selected package contract."""

    def __init__(
        self, *, semantic_projector, evidence_factory: Callable,
        parameter_fingerprint: str,
    ) -> None:
        project = getattr(semantic_projector, "project", None)
        if not callable(project):
            raise TypeError("output requires an explicit semantic projector")
        if not callable(evidence_factory):
            raise TypeError("output requires a package evidence factory")
        parameters = str(parameter_fingerprint or "").strip()
        if not parameters:
            raise ValueError("output requires parameter fingerprint")
        self._project = project
        self._evidence_factory = evidence_factory
        self._parameter_fingerprint = parameters

    def __call__(self, committed_action):
        event = committed_action.event
        projection = self._project(
            text=event.text,
            dialogue_tail="",
            recall_hint=None,
            state_vars={},
            event_kind="npc_output",
        )
        if not isinstance(projection, Mapping):
            raise ValueError("output semantic measurement is unavailable")
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
        return PreparedPackageOutputMeasurement(
            semantic_receipt=semantic,
            measurement_evidence=evidence,
        )


class ModelPackageRecipientMeasurementProducer:
    """Measure one addressed route projection for its recipient package."""

    def __init__(
        self, *, semantic_projector, evidence_factory: Callable,
        sender_participant_kind: str,
    ) -> None:
        project = getattr(semantic_projector, "project", None)
        if not callable(project):
            raise TypeError("recipient input requires semantic projector")
        if not callable(evidence_factory):
            raise TypeError("recipient input requires package evidence factory")
        sender_kind = str(sender_participant_kind or "").strip()
        if sender_kind not in _INPUT_KIND_BY_SENDER:
            raise ValueError("recipient input sender kind is invalid")
        self._project = project
        self._evidence_factory = evidence_factory
        self._event_kind = _INPUT_KIND_BY_SENDER[sender_kind]

    def __call__(
        self, projection: LocalProjection,
    ) -> PreparedFormalRecipientMeasurement:
        if not isinstance(projection, LocalProjection):
            raise TypeError("recipient measurement requires LocalProjection")
        if projection.delivery_scope != "addressed":
            raise ValueError("recipient measurement requires addressed projection")
        if not isinstance(projection.text, str) or not projection.text:
            raise ValueError("recipient measurement requires committed source text")
        local_event_id = f"ingress:{projection.delivery_id}"
        semantic = self._project(
            text=projection.text,
            dialogue_tail="",
            recall_hint=None,
            state_vars={},
            event_kind=self._event_kind,
        )
        if not isinstance(semantic, Mapping):
            raise ValueError("recipient semantic measurement is unavailable")
        evidence = self._evidence_factory(
            projection=semantic,
            source_event_id=projection.source_event_id,
            local_event_id=local_event_id,
            event_kind=self._event_kind,
        )
        return PreparedFormalRecipientMeasurement(
            recipient_participant_id=projection.recipient_participant_id,
            delivery_id=projection.delivery_id,
            semantic_projection=semantic,
            measurement_evidence=evidence,
        )


__all__ = (
    "ModelPackageDirectMeasurementProducer",
    "ModelPackageOutputMeasurementProducer",
    "ModelPackageRecipientMeasurementProducer",
    "PreparedPackageMeasurement",
    "PreparedPackageOutputMeasurement",
)
