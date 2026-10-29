"""Model-neutral composition of output settlement, route, and dispatch."""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import os

from ops.runtime_expression_state import restore_runtime_expression_state
from ops.runtime_product_fact_bridge import build_committed_output_fact_evidence
from ops.runtime_product_output_route import commit_product_output_route
from ops.product_output_lifecycle_recovery import (
    ProductOutputLifecycleRecoveryJournal,
)


@dataclass(frozen=True)
class ProductOutputLifecycleResult:
    lifecycle: object
    route: object
    dispatch: object


class ProductOutputLifecycleHost:
    def __init__(
        self, *, conversation_registry, model_runtime,
        temporal_runtime, runtime_variant, measurement_producer: Callable,
        dispatch_route: Callable, runtime_owner=None, runner=None,
    ) -> None:
        if not callable(measurement_producer):
            raise TypeError("output lifecycle requires measurement producer")
        if not callable(dispatch_route):
            raise TypeError("output lifecycle requires route dispatcher")
        if runtime_owner is not None and runner is not None:
            raise TypeError("provide runtime_owner or legacy runner, not both")
        owner = runtime_owner if runtime_owner is not None else runner
        if owner is None:
            raise TypeError("output lifecycle requires runtime_owner")
        self.runtime_owner = owner
        self.conversation_registry = conversation_registry
        self.model_runtime = model_runtime
        self.temporal_runtime = temporal_runtime
        self.runtime_variant = runtime_variant
        self.measurement_producer = measurement_producer
        self.dispatch_route = dispatch_route

    def _recovery_journal(self, conversation_id):
        session = self.runtime_owner.formal_runtime_session
        if session is None:
            raise RuntimeError("output lifecycle recovery requires formal session")
        return ProductOutputLifecycleRecoveryJournal(
            registry=self.conversation_registry,
            conversation_id=conversation_id,
            session_binding=session.session_binding,
        )

    def settle_route_dispatch(self, committed_action) -> ProductOutputLifecycleResult:
        recovery = self._recovery_journal(
            committed_action.event.conversation_id)
        pending = recovery.begin(committed_action)
        measurement = self.measurement_producer(committed_action)
        evidence = build_committed_output_fact_evidence(
            committed_action=committed_action,
            semantic_receipt=measurement.semantic_receipt,
            measurement_evidence=measurement.measurement_evidence,
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
            runtime_variant=self.runtime_variant,
        )
        session = self.runtime_owner.formal_runtime_session
        expression = restore_runtime_expression_state(
            session.profile, session_binding=session.session_binding)
        continuation_id = "output-lifecycle:" + committed_action.event.event_id
        lifecycle = self.runtime_owner.commit_recoverable_verified_formal_runtime_lifecycle(
            evidence,
            committed_remainders=expression.committed_remainders,
            continuation_id=continuation_id,
            anchor_receipt=pending,
        )
        return self._route_dispatch_complete(
            committed_action, pending=pending, lifecycle=lifecycle,
            recovery=recovery)

    def _route_dispatch_complete(
        self, committed_action, *, pending, lifecycle, recovery,
    ) -> ProductOutputLifecycleResult:
        route = commit_product_output_route(
            committed_action=committed_action,
            lifecycle=lifecycle,
            conversation_registry=self.conversation_registry,
        )
        dispatch = self.dispatch_route(route)
        recovery.complete(
            committed_action, pending=pending, lifecycle=lifecycle, route=route)
        return ProductOutputLifecycleResult(
            lifecycle=lifecycle, route=route, dispatch=dispatch)

    def recover_pending(self, conversation_id, *, is_ready=None):
        """Resume pre-settlement actions; never invokes Writer or source Commit."""
        if is_ready is not None and not callable(is_ready):
            raise TypeError("output lifecycle readiness predicate must be callable")
        recovery = self._recovery_journal(conversation_id)
        results = []
        for action in recovery.pending_actions():
            if is_ready is not None and not is_ready(action):
                continue
            current = self.runtime_owner.formal_runtime_session.position.runtime_ordinal
            source = action.action_plan.source_runtime_ordinal
            if current == source:
                results.append(self.settle_route_dispatch(action))
                continue
            if current == source + 1:
                lifecycle = self.runtime_owner.recover_formal_product_recipient_lifecycle(
                    delivery_id="output-lifecycle:" + action.event.event_id)
                pending = next(
                    row for row in recovery.read_rows()
                    if row.get("stage") == "pending"
                    and row.get("event_id") == action.event.event_id
                    and row.get("session_binding_fingerprint")
                    == recovery.session_binding.fingerprint)
                results.append(self._route_dispatch_complete(
                    action, pending=pending, lifecycle=lifecycle,
                    recovery=recovery))
                continue
            if current != source:
                raise RuntimeError(
                    "pending output crossed a deeper post-settlement crash cut")
        return tuple(results)

    def recover_registered_pending(self, *, is_ready=None):
        """Resume this Actor's exact pending outputs in every registered session."""
        if is_ready is not None and not callable(is_ready):
            raise TypeError("output lifecycle readiness predicate must be callable")
        results = []
        for registered in self.conversation_registry.registered_sessions():
            conversation_id = registered.conversation.conversation_id
            recovery = self._recovery_journal(conversation_id)
            if not os.path.isfile(recovery.path):
                continue
            if not recovery.pending_actions():
                continue
            results.extend(self.recover_pending(
                conversation_id, is_ready=is_ready))
        return tuple(results)


__all__ = ("ProductOutputLifecycleHost", "ProductOutputLifecycleResult")
