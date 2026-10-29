"""Concrete W2 composition root for the reference product required path."""
from __future__ import annotations

import hashlib
import copy
import os
import threading
import time
import uuid
from collections.abc import Mapping

from core.params import DEFAULTS
from ops.agent_usage import bind_usage_sink
from interaction_extensions.content_attention import ContentOccupationPolicy
from product_adapters.cx_six_seven_v1.writer_attempts import WriterAttemptJournal
from ops.cx_six_seven_product_direct_measurement import (
    CxSixSevenProductDirectMeasurementProducer,
)
from ops.cx_six_seven_product_output_measurement import (
    CxSixSevenProductOutputMeasurementProducer,
)
from ops.cx_six_seven_product_recipient_measurement import (
    CxSixSevenProductRecipientMeasurementProducer,
)
from ops.product_output_lifecycle_host import ProductOutputLifecycleHost
from ops.product_autonomous_action_host import ProductAutonomousActionHost
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.product_full_memory_cards import ProductFullMemoryCardsProvider
from interaction_extensions.multi_participant.recipient_directory import (
    ProductFormalRecipientDirectory,
)
from ops.product_required_reply_host import ProductRequiredReplyHost
from product_adapters.wall_clock_ms_v1.product_driver import (
    WallClockProductObservationDriver,
)
from product_adapters.wall_clock_ms_v1.product_host import (
    compose_wall_clock_product_observation_host,
)


def product_reference_content_occupation_policy():
    return ContentOccupationPolicy(
        version="tide-product-memory-content-occupation-v1",
        leak=1.0,
        lateral_inhibition=0.5,
        solver_tolerance=1e-12,
    )


class ProductDefaultRequiredReplyHostFactory:
    """Bind configured providers to one already-activated formal participant."""

    def __init__(
        self,
        *,
        base_dir,
        semantic_projector,
        responder_agent,
        observation_coordinate_reader=None,
        memory_links_enabled=False,
        autonomous_memory_enabled=False,
        usage_sink_factory=None,
    ):
        self.base_dir = os.path.abspath(os.fspath(base_dir))
        self.semantic_projector = semantic_projector
        self.responder_agent = responder_agent
        if usage_sink_factory is not None and not callable(usage_sink_factory):
            raise TypeError("usage sink factory must be callable")
        self._usage_sink_factory = usage_sink_factory
        if not isinstance(memory_links_enabled, bool):
            raise ValueError("memory links require an explicit boolean switch")
        self.memory_links_enabled = memory_links_enabled
        if not isinstance(autonomous_memory_enabled, bool):
            raise ValueError("autonomous memory requires an explicit boolean switch")
        self.autonomous_memory_enabled = autonomous_memory_enabled
        if observation_coordinate_reader is None:
            observation_coordinate_reader = lambda: int(time.time() * 1000)
        if not callable(observation_coordinate_reader):
            raise TypeError("observation coordinate reader must be callable")
        self._observation_coordinate_reader = observation_coordinate_reader
        self.conversation_registry = ProductConversationSessionRegistry(
            base_dir=self.base_dir)
        self._recipient_directory = ProductFormalRecipientDirectory(
            conversation_registry=self.conversation_registry)
        self._recipient_lock = threading.RLock()
        self._output_recovery_lock = threading.RLock()
        self._recipient_evidence_factories = {}
        # Compatibility aliases only.  The package-neutral directory is the
        # single owner of these mappings; older focused tests still inspect
        # the historical private names.
        self._recipient_planning_payloads = (
            self._recipient_directory._planning_payloads)
        self._recipient_runners = self._recipient_directory._owners
        self._recipient_actors = self._recipient_directory._actors
        self._recipient_autonomous_hosts = {}
        self._recipient_output_hosts = {}
        self._recipient_observation_drivers = {}
        self._recipient_observer_bindings = {}

    @staticmethod
    def _participant_id(value):
        participant_id = str(value or "").strip()
        if not participant_id:
            raise ValueError("formal recipient participant_id is required")
        return participant_id

    def _register_formal_recipient_runtime(
        self, participant_id, runner, evidence_factory,
        observation_planning_payload,
        *, semantic_projector=None,
    ):
        participant_id = self._participant_id(participant_id)
        if evidence_factory is not None and not callable(evidence_factory):
            raise TypeError("formal recipient evidence authority must be callable")
        if not isinstance(observation_planning_payload, Mapping):
            raise TypeError(
                "formal recipient observation planning payload must be a mapping")
        if not callable(getattr(
            runner, "read_formal_active_observation_plan", None,
        )):
            raise TypeError("formal recipient Runner lacks active-plan reader")
        with self._recipient_lock:
            existing = self._recipient_evidence_factories.get(participant_id)
            if existing is not None and existing is not evidence_factory:
                raise ValueError("formal recipient runtime is already registered")

            def producer_factory(sender_kind, *, _evidence=evidence_factory):
                return CxSixSevenProductRecipientMeasurementProducer(
                    committed_measurement_evidence_factory=_evidence,
                    semantic_projector=(self.semantic_projector if semantic_projector is None
                                        else semantic_projector),
                    sender_participant_kind=sender_kind,
                )

            self._recipient_directory.register_runtime(
                participant_id=participant_id,
                runtime_owner=runner,
                measurement_producer_factory=producer_factory,
                observation_planning_payload=observation_planning_payload,
            )
            self._recipient_evidence_factories[participant_id] = evidence_factory

    def bind_formal_recipient_actor(self, participant_id, actor):
        """Bind one already-created owner Actor to its formal participant."""
        participant_id = self._participant_id(participant_id)
        if not callable(getattr(
            actor, "post_formal_product_recipient_ingress", None,
        )):
            raise TypeError("formal recipient owner Actor lacks typed ingress")
        with self._recipient_lock:
            if participant_id not in self._recipient_evidence_factories:
                raise ValueError("formal recipient runtime is not registered")
            current = self._recipient_actors.get(participant_id)
            if current is not None and current is not actor:
                stopped = getattr(current, "is_stopped", None)
                if not callable(stopped) or not stopped():
                    raise ValueError("formal recipient owner Actor differs")
                self.unbind_formal_recipient_actor(participant_id, current)
            if current is actor:
                stopped = getattr(actor, "is_stopped", None)
                if callable(stopped) and stopped():
                    raise RuntimeError("cannot bind a stopped owner Actor")
                return actor
            runner = self._recipient_runners[participant_id]
            autonomous_host = self._recipient_autonomous_hosts.get(
                participant_id)
            if autonomous_host is None:
                raise ValueError(
                    "formal recipient autonomous action host is unavailable")
            host = compose_wall_clock_product_observation_host(
                runner=runner,
                actor=actor,
                coordinate_reader=self._observation_coordinate_reader,
            )
            driver = WallClockProductObservationDriver(
                host=host,
                coordinate_reader=self._observation_coordinate_reader,
            )
            add_plan_observer = getattr(
                actor, "add_formal_observation_plan_observer", None)
            remove_plan_observer = getattr(
                actor, "remove_formal_observation_plan_observer", None)
            add_stop_observer = getattr(actor, "add_stop_observer", None)
            remove_stop_observer = getattr(actor, "remove_stop_observer", None)
            bind_autonomous = getattr(
                actor,
                "bind_formal_product_autonomous_action_handler",
                None,
            )
            unbind_autonomous = getattr(
                actor,
                "unbind_formal_product_autonomous_action_handler",
                None,
            )
            if not all(callable(item) for item in (
                add_plan_observer,
                remove_plan_observer,
                add_stop_observer,
                remove_stop_observer,
                bind_autonomous,
                unbind_autonomous,
            )):
                raise TypeError(
                    "formal recipient owner Actor lacks driver lifecycle hooks")
            plan_observer = driver.notify_active_plan_changed
            stop_observer = driver.stop
            autonomous_handler = autonomous_host.execute
            add_plan_observer(plan_observer)
            directory_bound = False
            try:
                add_stop_observer(stop_observer)
                bind_autonomous(autonomous_handler)
                autonomous_host.recover_pending(actor)
                self._recipient_directory.bind_actor(participant_id, actor)
                directory_bound = True
                driver.start()
            except Exception:
                if directory_bound:
                    self._recipient_directory.unbind_actor(
                        participant_id, actor)
                remove_plan_observer(plan_observer)
                remove_stop_observer(stop_observer)
                unbind_autonomous(autonomous_handler)
                driver.stop()
                raise
            self._recipient_observation_drivers[participant_id] = driver
            self._recipient_observer_bindings[participant_id] = (
                plan_observer,
                stop_observer,
                autonomous_handler,
            )
        self._recover_ready_pending_outputs()
        return actor

    def unbind_formal_recipient_actor(self, participant_id, actor=None):
        participant_id = self._participant_id(participant_id)
        with self._recipient_lock:
            current = self._recipient_actors.get(participant_id)
            if actor is not None and current is not actor:
                return False
            removed = current
            if current is not None:
                self._recipient_directory.unbind_actor(
                    participant_id, current)
            driver = self._recipient_observation_drivers.pop(
                participant_id, None)
            binding = self._recipient_observer_bindings.pop(
                participant_id, None)
        if removed is None:
            return False
        if binding is not None:
            remove_plan = getattr(
                removed, "remove_formal_observation_plan_observer", None)
            remove_stop = getattr(removed, "remove_stop_observer", None)
            if callable(remove_plan):
                remove_plan(binding[0])
            if callable(remove_stop):
                remove_stop(binding[1])
            unbind_autonomous = getattr(
                removed,
                "unbind_formal_product_autonomous_action_handler",
                None,
            )
            if callable(unbind_autonomous):
                unbind_autonomous(binding[2])
        if driver is not None:
            driver.stop()
        return True

    def remove_formal_recipient(self, participant_id):
        participant_id = self._participant_id(participant_id)
        self.unbind_formal_recipient_actor(participant_id)
        with self._recipient_lock:
            self._recipient_evidence_factories.pop(participant_id, None)
            self._recipient_directory.remove_runtime(participant_id)
            self._recipient_autonomous_hosts.pop(participant_id, None)
            self._recipient_output_hosts.pop(participant_id, None)

    def _pending_output_ready(self, action):
        event = action.event
        session = self.conversation_registry.load_session(
            event.conversation_id)
        if session is None:
            raise ValueError("pending output conversation is not registered")
        if (
            session.conversation.membership_version != event.membership_version
            or session.conversation.active_member_ids
            != event.member_snapshot_ids
        ):
            raise ValueError(
                "pending output differs from registered conversation snapshot")
        participants = session.participants_by_id
        local_visible = {
            participant_id
            for participant_id in event.visible_recipient_ids
            if participants[participant_id].capabilities.has_local_store
            and participants[participant_id].capabilities.receive_projection
        }
        local_addressed = local_visible & set(
            event.addressed_participant_ids)
        with self._recipient_lock:
            return (
                local_visible <= set(self._recipient_actors)
                and local_addressed <= {
                    participant_id
                    for participant_id, factory in (
                        self._recipient_evidence_factories.items())
                    if callable(factory)
                }
                and local_addressed <= set(self._recipient_planning_payloads)
            )

    def _recover_ready_pending_outputs(self):
        with self._output_recovery_lock:
            with self._recipient_lock:
                hosts = tuple(self._recipient_output_hosts.values())
            results = []
            for host in hosts:
                results.extend(host.recover_registered_pending(
                    is_ready=self._pending_output_ready))
            return tuple(results)

    def _recover_prepared_recipient_dispatches(self, participant_id):
        """Restore only this newly bound owner's missing archived deliveries."""
        participant_id = self._participant_id(participant_id)
        with self._output_recovery_lock:
            return self._recipient_directory.recover_participant(participant_id)

    def dispatch_formal_output_route(self, route):
        """Resolve exact product bindings, then reuse the generic dispatcher."""
        return self._recipient_directory.dispatch(route)

    def __call__(self, *, runner, provision, plan):
        projector = self.semantic_projector
        responder = self.responder_agent
        if self._usage_sink_factory is not None:
            sink = self._usage_sink_factory(runner)
            if not callable(sink):
                raise TypeError("usage sink factory must return a callable")

            def bind(agent):
                if agent is None:
                    return None
                # Legacy stubs expose a mutable sink; never mutate a shared
                # configured instance. Real clients use call-local binding.
                target = agent if getattr(agent, "supports_call_usage_sink", False) else copy.copy(agent)
                return bind_usage_sink(target, sink)

            responder = bind(responder)
            if projector is not None and getattr(projector, "agent", None) is not None:
                projector = copy.copy(projector)
                projector.agent = bind(projector.agent)
        if self.autonomous_memory_enabled:
            from model_packages.cx_six_seven_v1.autonomous_recall_package import AUTONOMOUS_RECALL_OPERATOR_VERSION
            if plan.composition.model_runtime.manifest.operator_versions.get("recall_history") != AUTONOMOUS_RECALL_OPERATOR_VERSION:
                raise ValueError("autonomous memory requires the explicit shared-tick model profile")
        evidence_factory = plan.committed_measurement_evidence_factory
        if projector is None:
            def measurement(_event):
                raise RuntimeError(
                    "formal direct input requires semantic projection; "
                    "legacy codec fallback is forbidden")
            output_measurement = measurement
        else:
            measurement = CxSixSevenProductDirectMeasurementProducer(
                semantic_projector=projector,
                evidence_factory=evidence_factory,
            )
            output_measurement = CxSixSevenProductOutputMeasurementProducer(
                semantic_projector=projector,
                evidence_factory=evidence_factory,
                parameter_fingerprint=(
                    plan.composition.model_runtime.binding.parameter_fingerprint),
            )
        call = getattr(responder, "call", None)
        if not callable(call):
            raise TypeError("formal required reply requires responder provider")
        self.conversation_registry.register_direct_session(provision.session)
        digest = hashlib.sha256(
            provision.conversation_id.encode("utf-8")).hexdigest()
        # Windows still encounters legacy path limits in some Python/runtime
        # combinations.  This directory is only a storage shard, never the
        # conversation identity; the full conversation/event identities remain
        # inside every append-only artifact.
        attempts = WriterAttemptJournal(os.path.join(
            self.base_dir, "wa", digest[:20]))

        def invoke(system, user):
            return call(
                system,
                user,
                temperature=DEFAULTS["responder_temperature"],
                require_json=True,
            )

        output_host = ProductOutputLifecycleHost(
            runner=runner,
            conversation_registry=self.conversation_registry,
            model_runtime=plan.composition.model_runtime,
            temporal_runtime=plan.composition.temporal_runtime,
            runtime_variant=plan.composition.variant_binding,
            measurement_producer=output_measurement,
            dispatch_route=self.dispatch_formal_output_route,
        )
        memory_provider = ProductFullMemoryCardsProvider(
            provision=provision, conversation_registry=self.conversation_registry,
            temporal_runtime=plan.composition.temporal_runtime,
            content_occupation_policy=product_reference_content_occupation_policy(),
            memory_links_enabled=self.memory_links_enabled)
        autonomous_memory_provider = None
        if self.autonomous_memory_enabled:
            from ops.product_autonomous_memory_cards import ProductAutonomousMemoryCardsProvider
            autonomous_memory_provider = ProductAutonomousMemoryCardsProvider(
                model_runtime=plan.composition.model_runtime, content_provider=memory_provider)
        autonomous_host = ProductAutonomousActionHost(
            runner=runner,
            conversation_registry=self.conversation_registry,
            writer_attempt_journal=attempts,
            model_runtime=plan.composition.model_runtime,
            responder_invoke=invoke,
            output_lifecycle_host=output_host,
            observation_planning_payload=plan.observation_planning_payload,
            memory_cards_provider=autonomous_memory_provider,
        )

        host = ProductRequiredReplyHost(
            runner=runner,
            provision=provision,
            conversation_registry=self.conversation_registry,
            writer_attempt_journal=attempts,
            model_runtime=plan.composition.model_runtime,
            temporal_runtime=plan.composition.temporal_runtime,
            runtime_variant=plan.composition.variant_binding,
            measurement_producer=measurement,
            responder_invoke=invoke,
            memory_cards_provider=memory_provider,
            event_id_factory=lambda: "evt:human-input:" + uuid.uuid4().hex,
            output_lifecycle_host=output_host,
        )
        self._register_formal_recipient_runtime(
            provision.npc.participant_id,
            runner,
            plan.committed_measurement_evidence_factory,
            plan.observation_planning_payload,
            semantic_projector=projector,
        )
        with self._recipient_lock:
            existing_autonomous = self._recipient_autonomous_hosts.get(
                provision.npc.participant_id)
            if (
                existing_autonomous is not None
                and existing_autonomous.runner is not runner
            ):
                raise ValueError("formal recipient autonomous host differs")
            self._recipient_autonomous_hosts[
                provision.npc.participant_id] = autonomous_host
            existing_output = self._recipient_output_hosts.get(
                provision.npc.participant_id)
            if (
                existing_output is not None
                and existing_output.runner is not runner
            ):
                raise ValueError("formal recipient output host differs")
            self._recipient_output_hosts[
                provision.npc.participant_id] = output_host
        self._recover_ready_pending_outputs()
        return host


__all__ = (
    "ProductDefaultRequiredReplyHostFactory",
    "product_reference_content_occupation_policy",
)
