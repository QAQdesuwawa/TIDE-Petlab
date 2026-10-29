"""Package-neutral owner of one formal product runtime session.

The legacy ``Runner`` remains a compatibility/product shell.  This owner keeps
only the formal v3 session, selected model/temporal runtimes, participant
identity and an injected profile persistence boundary.  It contains no legacy
interaction formulas, semantic codec, UI, Actor, timer, provider or send path.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping

from framework.model_api import model_data_fingerprint
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import RuntimeKernelContractError
from framework.runtime_variant import RuntimeVariantBinding
from framework.temporal_runtime import BoundTemporalDomain
from ops.model_expression_projection_shadow import produce_expression_projection_shadow
from interaction_extensions.multi_participant.object_projection import (
    ParticipantProjectionError,
    require_participant_projection,
)
from ops.runtime_committed_fact_transaction import (
    RuntimeCommittedFactTransaction,
    execute_runtime_committed_fact_transaction,
)
from ops.runtime_full_memory_committed_fact_transaction import (
    RuntimeFullMemoryCommittedFactTransaction,
    execute_runtime_full_memory_committed_fact_transaction,
    supports_full_memory_fact_transaction,
)
from ops.runtime_product_expression_continuation import (
    attach_product_expression_continuation,
    build_product_expression_continuation,
    commit_product_expression_completion,
    load_product_expression_continuation,
    restore_product_expression_projection,
    restore_product_fact_products,
)
from ops.runtime_product_fact_bridge import RuntimeCommittedFactEvidence
from ops.runtime_product_fact_lifecycle import (
    PostCommitExpressionProjectionError,
    complete_product_fact_lifecycle,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


_TRANSACTION_TYPES = (
    RuntimeCommittedFactTransaction,
    RuntimeFullMemoryCommittedFactTransaction,
)


class ProductFormalSessionOwner:
    """Persist and publish one package-bound formal session atomically."""

    def __init__(
        self,
        *,
        participant: object,
        session: BootstrappedRuntimeSession,
        model_runtime: BoundInteractionModel,
        temporal_runtime: BoundTemporalDomain,
        runtime_variant: RuntimeVariantBinding,
        persist_profile: Callable[[Mapping[str, object]], None] | None = None,
    ) -> None:
        try:
            require_participant_projection(participant, label="formal session owner")
        except ParticipantProjectionError as exc:
            raise TypeError("formal session owner requires participant projection") from exc
        if not isinstance(session, BootstrappedRuntimeSession):
            raise TypeError("formal session owner requires bootstrapped session")
        if not isinstance(model_runtime, BoundInteractionModel):
            raise TypeError("formal session owner requires bound model runtime")
        if not isinstance(temporal_runtime, BoundTemporalDomain):
            raise TypeError("formal session owner requires bound temporal runtime")
        if not isinstance(runtime_variant, RuntimeVariantBinding):
            raise TypeError("formal session owner requires runtime variant")
        session.session_binding.verify_runtimes(
            model_runtime=model_runtime,
            temporal_runtime=temporal_runtime,
        )
        if session.session_binding.runtime_variant != runtime_variant:
            raise ValueError("formal owner runtime variant differs from session")
        if persist_profile is not None and not callable(persist_profile):
            raise TypeError("persist_profile must be callable or None")
        self.participant = participant
        self.formal_runtime_session = session
        self.model_runtime = model_runtime
        self.temporal_runtime = temporal_runtime
        self.runtime_variant = runtime_variant
        self._persist_profile = persist_profile or (lambda _profile: None)
        self.profile = dict(session.profile)

    def formal_owner_participant_identity(self) -> object:
        return self.participant

    def _publish(self, session: BootstrappedRuntimeSession) -> None:
        profile = dict(session.profile)
        self._persist_profile(profile)
        self.profile = profile
        self.formal_runtime_session = session

    def _verify_evidence(self, evidence: RuntimeCommittedFactEvidence) -> None:
        if not isinstance(evidence, RuntimeCommittedFactEvidence):
            raise ValueError("formal owner requires verified product evidence")
        evidence.verify_for(
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
        )
        expected = evidence.expected_runtime_ordinal
        if (
            expected is not None
            and self.formal_runtime_session.position.runtime_ordinal != expected
        ):
            raise RuntimeKernelContractError(
                "formal owner evidence expected ordinal is stale")

    def _execute_fact(self, evidence: RuntimeCommittedFactEvidence):
        self._verify_evidence(evidence)
        transaction_host = (
            execute_runtime_full_memory_committed_fact_transaction
            if supports_full_memory_fact_transaction(self.model_runtime)
            else execute_runtime_committed_fact_transaction
        )
        return transaction_host(
            session=self.formal_runtime_session,
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
            target_coordinate=evidence.target_coordinate,
            scope_ref=evidence.conversation_id,
            subject_ref=evidence.participant_id,
            event_ref=evidence.event_id,
            action_ref=evidence.action_id,
            measurement_payload=evidence.measurement_payload,
            context_metadata=evidence.context_metadata,
        )

    def commit_formal_product_recipient_ingress(
        self,
        *,
        route,
        conversation_registry,
        recipient_participant_id,
        semantic_projection,
        measurement_evidence,
    ):
        """Commit one exact routed delivery into this participant session."""
        recipient_id = str(recipient_participant_id or "").strip()
        if recipient_id != self.participant.participant_id:
            raise ValueError(
                "recipient ingress must target this formal owner participant")
        from ops.runtime_expression_state import (
            restore_runtime_expression_state,
        )
        from ops.runtime_product_recipient_ingress import (
            execute_product_recipient_ingress,
        )

        expression_state = restore_runtime_expression_state(
            self.formal_runtime_session.profile,
            session_binding=self.formal_runtime_session.session_binding,
        )
        result = execute_product_recipient_ingress(
            route=route,
            conversation_registry=conversation_registry,
            recipient_participant_id=recipient_id,
            session=self.formal_runtime_session,
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
            runtime_variant=self.runtime_variant,
            semantic_projection=semantic_projection,
            measurement_evidence=measurement_evidence,
            committed_remainders=expression_state.committed_remainders,
        )
        if not result.already_committed:
            self._publish(result.session)
        return result

    def project_formal_runtime_fact_expression(
        self, transaction, *, committed_remainders,
    ):
        if not isinstance(transaction, _TRANSACTION_TYPES):
            raise ValueError("expression projection requires committed transaction")
        if (
            self.formal_runtime_session != transaction.session
            or self.profile != dict(transaction.session.profile)
        ):
            raise ValueError("expression projection requires current committed fact")
        event = transaction.event_transaction
        previous = transaction.event_input_session
        return produce_expression_projection_shadow(
            runtime=self.model_runtime,
            session_binding=transaction.session.session_binding,
            previous_state=previous.state,
            previous_position=transaction.settlement_input_position,
            model_parameters=previous.parameters,
            settlement_context=event.context,
            settlement_execution=event.settlement_execution,
            settlement_acceptance=event.acceptance,
            committed_remainders=committed_remainders,
        )

    def commit_verified_formal_runtime_lifecycle(
        self, evidence, *, committed_remainders,
    ):
        transaction = self._execute_fact(evidence)
        self._publish(transaction.session)
        try:
            expression = self.project_formal_runtime_fact_expression(
                transaction, committed_remainders=committed_remainders)
            return complete_product_fact_lifecycle(
                evidence=evidence,
                transaction=transaction,
                expression=expression,
            )
        except Exception as exc:
            raise PostCommitExpressionProjectionError(
                transaction=transaction, evidence=evidence, cause=exc) from exc

    def commit_recoverable_verified_formal_runtime_lifecycle(
        self,
        evidence,
        *,
        committed_remainders,
        continuation_id,
        anchor_receipt,
    ):
        transaction = self._execute_fact(evidence)
        continuation = build_product_expression_continuation(
            transaction=transaction,
            evidence=evidence,
            continuation_id=continuation_id,
            anchor_receipt=anchor_receipt,
            committed_remainders=committed_remainders,
        )
        next_session = attach_product_expression_continuation(
            transaction.session, continuation=continuation)
        self._publish(next_session)
        return self.recover_formal_product_recipient_lifecycle(
            delivery_id=continuation_id)

    def complete_formal_product_recipient_expression(self, *, delivery_id):
        continuation = load_product_expression_continuation(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        expression = restore_product_expression_projection(
            continuation=continuation,
            session=self.formal_runtime_session,
            runtime=self.model_runtime,
        )
        session, completion = commit_product_expression_completion(
            self.formal_runtime_session,
            continuation=continuation,
            expression=expression,
        )
        if not completion.already_completed:
            self._publish(session)
        return completion

    def recover_formal_product_recipient_lifecycle(self, *, delivery_id):
        completion = self.complete_formal_product_recipient_expression(
            delivery_id=delivery_id)
        continuation = load_product_expression_continuation(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        evidence, transaction = restore_product_fact_products(
            continuation=continuation,
            session=self.formal_runtime_session,
            runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
        )
        lifecycle = complete_product_fact_lifecycle(
            evidence=evidence,
            transaction=transaction,
            expression=completion.expression,
        )
        if completion.receipt.get("expression_receipt_fingerprint") != (
            model_data_fingerprint(lifecycle.expression.receipt)
        ):
            raise ValueError("recovered lifecycle differs from expression completion")
        return lifecycle

    def plan_formal_product_recipient_observation(
        self, *, delivery_id, planning_payload,
    ):
        """Persist the package-owned observation target for one delivery."""
        from ops.runtime_product_observation_plan import (
            build_product_observation_plan,
            commit_product_observation_plan,
            load_product_observation_plan,
        )

        existing = load_product_observation_plan(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self.temporal_runtime,
        )
        if existing is not None:
            return existing
        lifecycle = self.recover_formal_product_recipient_lifecycle(
            delivery_id=delivery_id)
        plan = build_product_observation_plan(
            delivery_id=delivery_id,
            lifecycle=lifecycle,
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
            planning_payload=planning_payload,
        )
        session, committed = commit_product_observation_plan(
            self.formal_runtime_session,
            plan=plan,
            temporal_runtime=self.temporal_runtime,
        )
        self._publish(session)
        return committed

    def is_formal_product_recipient_work_complete(
        self,
        *,
        delivery_id,
        delivery_scope,
        observation_planning_required,
    ) -> bool:
        """Derive work completion only from existing signed owner products."""
        scope = str(delivery_scope or "").strip().lower()
        if scope not in {"addressed", "observed"}:
            raise ValueError("recipient completion check scope is invalid")
        from ops.runtime_product_recipient_ingress import (
            committed_product_recipient_delivery_ids,
        )

        if delivery_id not in committed_product_recipient_delivery_ids(
            self.formal_runtime_session
        ):
            return False
        if scope == "observed":
            return True

        from ops.runtime_product_expression_continuation import (
            load_product_expression_completion_receipt,
            load_product_expression_continuation,
        )

        continuation = load_product_expression_continuation(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        completion = load_product_expression_completion_receipt(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        if completion is None:
            return False
        response_policy = continuation.fact_evidence.get("response_policy")
        if response_policy not in {"required", "eligible"}:
            raise ValueError("recipient completion response policy is invalid")
        if response_policy != "eligible" or not observation_planning_required:
            return True

        from ops.runtime_product_observation_plan import (
            load_product_observation_plan,
        )

        return load_product_observation_plan(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self.temporal_runtime,
        ) is not None

    def plan_formal_product_output_observation(
        self, *, lifecycle, action_id, planning_payload,
    ):
        """Replace the active target from one committed owner output state."""
        from ops.runtime_product_observation_plan import (
            build_product_observation_plan_from_output,
            commit_product_observation_plan,
            load_product_observation_plan_by_id,
        )

        plan_id = f"observation-plan:committed_output_action:{action_id}"
        existing = load_product_observation_plan_by_id(
            self.formal_runtime_session.profile,
            plan_id=plan_id,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self.temporal_runtime,
        )
        if existing is not None:
            return existing
        plan = build_product_observation_plan_from_output(
            action_id=action_id,
            lifecycle=lifecycle,
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
            planning_payload=planning_payload,
        )
        session, committed = commit_product_observation_plan(
            self.formal_runtime_session,
            plan=plan,
            temporal_runtime=self.temporal_runtime,
        )
        self._publish(session)
        return committed

    def read_formal_active_observation_plan(self):
        """Restore the exact durable active plan without advancing anything."""
        from ops.runtime_product_observation_plan import (
            load_active_product_observation_plan,
        )

        return load_active_product_observation_plan(
            self.formal_runtime_session.profile,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self.temporal_runtime,
        )

    def probe_formal_product_observation_due(self, *, target_coordinate):
        """Ask the bound TemporalDomain whether the active target is due."""
        from ops.runtime_product_observation_due import (
            probe_active_product_observation_due,
        )

        return probe_active_product_observation_due(
            self.formal_runtime_session,
            temporal_runtime=self.temporal_runtime,
            target_coordinate=target_coordinate,
        )

    def commit_due_formal_product_observation(
        self, *, target_coordinate, participant_id, tick_event_id,
    ):
        """Consume one due plan and persist the rotated successor exactly once."""
        from ops.runtime_product_observation_tick import (
            execute_product_observation_tick,
        )

        committed = execute_product_observation_tick(
            session=self.formal_runtime_session,
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
            target_coordinate=target_coordinate,
            participant_id=participant_id,
            tick_event_id=tick_event_id,
        )
        if not committed.already_committed:
            self._publish(committed.session)
        return committed

    def recover_pending_formal_product_autonomous_ticks(self):
        """Read exact committed grants without progressing or reassessing."""
        from ops.runtime_product_observation_tick import (
            recover_pending_product_observation_ticks,
        )

        return recover_pending_product_observation_ticks(
            session=self.formal_runtime_session,
            temporal_runtime=self.temporal_runtime,
        )


__all__ = ("ProductFormalSessionOwner",)
