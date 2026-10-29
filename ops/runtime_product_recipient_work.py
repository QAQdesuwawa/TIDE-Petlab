"""Transient work handed to one owner Actor for formal recipient ingress.

The object carries already-prepared, exact product evidence.  It is neither a
new persisted truth nor a UI event, and it owns no provider, Writer, tick,
route, or send authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import freeze_model_payload
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.runtime_product_output_route import RuntimeProductOutputRoute


@dataclass(frozen=True)
class FormalProductRecipientIngressWork:
    """One exact delivery ready for owner-local formal settlement."""

    route: RuntimeProductOutputRoute
    conversation_registry: ProductConversationSessionRegistry
    recipient_participant_id: str
    semantic_projection: Mapping[str, Any] | None
    measurement_evidence: Mapping[str, Any] | None
    observation_planning_payload: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.route, RuntimeProductOutputRoute):
            raise TypeError("formal recipient work requires RuntimeProductOutputRoute")
        if not isinstance(
            self.conversation_registry, ProductConversationSessionRegistry
        ):
            raise TypeError(
                "formal recipient work requires ProductConversationSessionRegistry")
        recipient_id = str(self.recipient_participant_id or "").strip()
        if not recipient_id:
            raise ValueError("formal recipient work requires recipient identity")
        matches = tuple(
            projection
            for projection in self.route.projections
            if projection.recipient_participant_id == recipient_id
        )
        if len(matches) != 1:
            raise ValueError(
                "formal recipient work requires one exact projection")
        scope = matches[0].delivery_scope
        if scope == "addressed":
            if not isinstance(self.semantic_projection, Mapping):
                raise TypeError(
                    "addressed formal recipient work requires semantic projection")
            if not isinstance(self.measurement_evidence, Mapping):
                raise TypeError(
                    "addressed formal recipient work requires measurement evidence")
            if (
                self.observation_planning_payload is not None
                and not isinstance(self.observation_planning_payload, Mapping)
            ):
                raise TypeError(
                    "formal recipient observation planning payload "
                    "must be a mapping or None")
        elif scope == "observed":
            if (
                self.semantic_projection is not None
                or self.measurement_evidence is not None
                or self.observation_planning_payload is not None
            ):
                raise ValueError(
                    "observed formal recipient work cannot carry measurement "
                    "or observation planning")
        else:
            raise ValueError("formal recipient work projection scope is invalid")
        object.__setattr__(self, "recipient_participant_id", recipient_id)
        if self.semantic_projection is not None:
            object.__setattr__(
                self, "semantic_projection",
                freeze_model_payload(self.semantic_projection),
            )
        if self.measurement_evidence is not None:
            object.__setattr__(
                self, "measurement_evidence",
                freeze_model_payload(self.measurement_evidence),
            )
        if self.observation_planning_payload is not None:
            object.__setattr__(
                self, "observation_planning_payload",
                freeze_model_payload(self.observation_planning_payload),
            )


__all__ = ["FormalProductRecipientIngressWork"]
