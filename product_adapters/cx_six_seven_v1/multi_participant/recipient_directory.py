"""Optional multi-participant owner directory for formal recipient dispatch.

The directory binds participant identity to already activated owner/Actor
objects and caller-supplied measurement producer factories. It is package
neutral but deliberately not framework-core. It owns no model formula,
provider, Writer, clock, tick, send, UI, or conversation scope.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
import threading

from .sessions import (
    ProductConversationSessionRegistry,
)
from ops.runtime_product_output_route import RuntimeProductOutputRoute
from ops.runtime_product_recipient_dispatch import (
    dispatch_formal_product_output_route,
    recover_prepared_formal_product_recipient_dispatches,
)


class ProductFormalRecipientDirectory:
    """Resolve exact recipient owners for one canonical output route."""

    def __init__(
        self, *, conversation_registry: ProductConversationSessionRegistry,
    ) -> None:
        if not isinstance(
            conversation_registry, ProductConversationSessionRegistry
        ):
            raise TypeError("recipient directory requires conversation registry")
        self.conversation_registry = conversation_registry
        self._lock = threading.RLock()
        self._owners: dict[str, object] = {}
        self._actors: dict[str, object] = {}
        self._measurement_factories: dict[str, Callable] = {}
        self._planning_payloads: dict[str, Mapping] = {}

    @staticmethod
    def _participant_id(value) -> str:
        participant_id = str(value or "").strip()
        if not participant_id:
            raise ValueError("recipient directory participant_id is required")
        return participant_id

    def register_runtime(
        self,
        *,
        participant_id,
        runtime_owner,
        measurement_producer_factory,
        observation_planning_payload,
    ):
        """Register package-owned recipient products for one formal owner."""
        participant_id = self._participant_id(participant_id)
        identity_reader = getattr(
            runtime_owner, "formal_owner_participant_identity", None)
        if not callable(identity_reader):
            raise TypeError("recipient runtime lacks formal owner identity")
        identity = identity_reader()
        if getattr(identity, "participant_id", None) != participant_id:
            raise ValueError("recipient runtime owner identity differs")
        if not callable(getattr(
            runtime_owner, "commit_formal_product_recipient_ingress", None,
        )):
            raise TypeError("recipient runtime owner lacks formal ingress")
        if not callable(getattr(
            runtime_owner, "is_formal_product_recipient_work_complete", None,
        )):
            raise TypeError("recipient runtime owner lacks completion proof")
        if not callable(measurement_producer_factory):
            raise TypeError(
                "recipient measurement producer factory must be callable")
        if not isinstance(observation_planning_payload, Mapping):
            raise TypeError(
                "recipient observation planning payload must be a mapping")
        with self._lock:
            existing = self._owners.get(participant_id)
            if existing is not None and existing is not runtime_owner:
                raise ValueError("recipient runtime owner already differs")
            existing_factory = self._measurement_factories.get(participant_id)
            if (
                existing_factory is not None
                and existing_factory is not measurement_producer_factory
            ):
                raise ValueError("recipient measurement authority already differs")
            self._owners[participant_id] = runtime_owner
            self._measurement_factories[
                participant_id] = measurement_producer_factory
            self._planning_payloads[
                participant_id] = dict(observation_planning_payload)
        return runtime_owner

    def bind_actor(self, participant_id, actor):
        """Bind the exact owner Actor, then recover its missing durable work."""
        participant_id = self._participant_id(participant_id)
        if not callable(getattr(
            actor, "post_formal_product_recipient_ingress", None,
        )):
            raise TypeError("recipient Actor lacks typed formal ingress")
        with self._lock:
            owner = self._owners.get(participant_id)
            if owner is None:
                raise ValueError("recipient runtime is not registered")
            owns = getattr(actor, "owns_formal_runner", None)
            if callable(owns) and not owns(owner):
                raise ValueError("recipient Actor owner differs")
            current = self._actors.get(participant_id)
            if current is not None and current is not actor:
                raise ValueError("recipient Actor already differs")
            self._actors[participant_id] = actor
        try:
            self.recover_participant(participant_id)
        except Exception:
            with self._lock:
                if self._actors.get(participant_id) is actor:
                    self._actors.pop(participant_id, None)
            raise
        return actor

    def unbind_actor(self, participant_id, actor=None) -> bool:
        participant_id = self._participant_id(participant_id)
        with self._lock:
            current = self._actors.get(participant_id)
            if actor is not None and current is not actor:
                return False
            return self._actors.pop(participant_id, None) is not None

    def remove_runtime(self, participant_id) -> bool:
        """Remove one unbound owner and its package-supplied authorities."""
        participant_id = self._participant_id(participant_id)
        with self._lock:
            if participant_id in self._actors:
                raise RuntimeError("recipient Actor must be unbound first")
            existed = participant_id in self._owners
            self._owners.pop(participant_id, None)
            self._measurement_factories.pop(participant_id, None)
            self._planning_payloads.pop(participant_id, None)
            return existed

    @staticmethod
    def _work_projection(work):
        projections = tuple(
            projection for projection in work.route.projections
            if projection.recipient_participant_id
            == work.recipient_participant_id
        )
        if len(projections) != 1:
            raise ValueError("recipient work projection is not unique")
        return projections[0]

    def recover_participant(self, participant_id):
        """Requeue only this registered participant's incomplete work."""
        participant_id = self._participant_id(participant_id)
        with self._lock:
            owner = self._owners.get(participant_id)
            actor = self._actors.get(participant_id)
        if owner is None or actor is None:
            raise ValueError("recipient owner and Actor must both be bound")

        def is_complete(work):
            projection = self._work_projection(work)
            return owner.is_formal_product_recipient_work_complete(
                delivery_id=projection.delivery_id,
                delivery_scope=projection.delivery_scope,
                observation_planning_required=(
                    work.observation_planning_payload is not None),
            )

        return recover_prepared_formal_product_recipient_dispatches(
            conversation_registry=self.conversation_registry,
            recipient_actors={participant_id: actor},
            recipient_work_completion_checks={participant_id: is_complete},
        )

    def dispatch(self, route: RuntimeProductOutputRoute):
        """Dispatch one canonical route using exact registered recipients."""
        if not isinstance(route, RuntimeProductOutputRoute):
            raise TypeError("recipient directory requires product output route")
        conversation_ids = {
            projection.conversation_id for projection in route.projections
        }
        if len(conversation_ids) != 1:
            raise ValueError("recipient route requires one conversation")
        session = self.conversation_registry.load_session(
            next(iter(conversation_ids)))
        if session is None:
            raise ValueError("recipient route conversation is not registered")
        participants = session.participants_by_id
        local = tuple(
            projection for projection in route.projections
            if participants[projection.recipient_participant_id]
            .capabilities.has_local_store
            and participants[projection.recipient_participant_id]
            .capabilities.receive_projection
        )
        local_ids = {
            projection.recipient_participant_id for projection in local
        }
        addressed_ids = {
            projection.recipient_participant_id for projection in local
            if projection.delivery_scope == "addressed"
        }
        sender_ids = {
            projection.sender_participant_id for projection in route.projections
        }
        if len(sender_ids) != 1:
            raise ValueError("recipient route requires one sender")
        sender = participants.get(next(iter(sender_ids)))
        if sender is None:
            raise ValueError("recipient route sender is not registered")
        with self._lock:
            missing_actors = local_ids - set(self._actors)
            missing_factories = addressed_ids - set(self._measurement_factories)
            missing_planning = addressed_ids - set(self._planning_payloads)
            if missing_actors:
                raise RuntimeError(
                    "recipient owner Actor is unavailable: "
                    + ", ".join(sorted(missing_actors)))
            if missing_factories:
                raise RuntimeError(
                    "recipient measurement authority is unavailable: "
                    + ", ".join(sorted(missing_factories)))
            if missing_planning:
                raise RuntimeError(
                    "recipient planning authority is unavailable: "
                    + ", ".join(sorted(missing_planning)))
            actors = {
                participant_id: self._actors[participant_id]
                for participant_id in local_ids
            }
            factories = {
                participant_id: self._measurement_factories[participant_id]
                for participant_id in addressed_ids
            }
            planning_payloads = {
                participant_id: self._planning_payloads[participant_id]
                for participant_id in addressed_ids
            }
        producers = {
            participant_id: factory(sender.kind)
            for participant_id, factory in factories.items()
        }
        if any(not callable(producer) for producer in producers.values()):
            raise TypeError(
                "recipient measurement factory returned non-callable producer")
        return dispatch_formal_product_output_route(
            route=route,
            conversation_registry=self.conversation_registry,
            recipient_actors=actors,
            addressed_measurement_producers=producers,
            addressed_observation_planning_payloads=planning_payloads,
        )


__all__ = ("ProductFormalRecipientDirectory",)
