"""Concrete product composition for one eligible autonomous tick action."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass

from framework.model_api import model_data_fingerprint
from product_adapters.cx_six_seven_v1.writer_attempts import WriterAttemptJournal
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.product_conversation_journal import ProductConversationSourceJournal
from ops.runtime_product_autonomous_work import (
    FormalProductAutonomousActionWork,
)
from ops.runtime_autonomous_writer_attempt import (
    execute_autonomous_product_writer_attempt,
    prepare_autonomous_product_writer_attempt,
    restore_candidate_ready_autonomous_writer_attempt,
    restore_prepared_autonomous_writer_attempt,
)
from ops.runtime_expression_state import restore_runtime_expression_state
from ops.runtime_product_action_commit import (
    commit_autonomous_product_writer_action,
)
from ops.runtime_product_destination_authorization import (
    authorize_autonomous_product_destination,
)
from ops.runtime_product_observation_tick import RuntimeProductObservationTick
from ops.writer_packet_facts import (
    conversation_fact_writer_card,
    persona_fact_writer_card,
)


@dataclass(frozen=True)
class ProductAutonomousActionResult:
    tick: RuntimeProductObservationTick
    destination: object
    executed_attempt: object
    committed_action: object
    output_lifecycle: object
    observation_plan: object | None = None


class ProductAutonomousActionHost:
    """Consume one grant through existing Writer, Commit, and W3 components."""

    def __init__(
        self,
        *,
        conversation_registry: ProductConversationSessionRegistry,
        writer_attempt_journal: WriterAttemptJournal,
        model_runtime,
        responder_invoke: Callable,
        output_lifecycle_host,
        observation_planning_payload,
        runtime_owner=None,
        runner=None,
        memory_cards_provider: Callable | None = None,
    ) -> None:
        if not isinstance(
            conversation_registry, ProductConversationSessionRegistry,
        ):
            raise TypeError(
                "autonomous action host requires conversation registry")
        if not isinstance(writer_attempt_journal, WriterAttemptJournal):
            raise TypeError(
                "autonomous action host requires WriterAttemptJournal")
        if not callable(responder_invoke):
            raise TypeError("autonomous responder invoke must be callable")
        if not isinstance(observation_planning_payload, Mapping):
            raise TypeError(
                "autonomous observation planning payload must be a mapping")
        if not callable(getattr(
            output_lifecycle_host, "settle_route_dispatch", None,
        )):
            raise TypeError(
                "autonomous action host requires output lifecycle host")
        if runtime_owner is not None and runner is not None:
            raise TypeError("provide runtime_owner or legacy runner, not both")
        owner = runtime_owner if runtime_owner is not None else runner
        if owner is None:
            raise TypeError("autonomous action host requires runtime_owner")
        self.runtime_owner = owner
        self.registry = conversation_registry
        self.writer_attempt_journal = writer_attempt_journal
        self.model_runtime = model_runtime
        self.responder_invoke = responder_invoke
        self.output_lifecycle_host = output_lifecycle_host
        self.observation_planning_payload = observation_planning_payload
        if memory_cards_provider is not None and not callable(memory_cards_provider):
            raise TypeError("autonomous memory provider must be explicit and callable")
        self.memory_cards_provider = memory_cards_provider

    def _tick_action_is_committed(self, tick: RuntimeProductObservationTick) -> bool:
        grant = tick.committed_grant
        consumed = tick.consumed_plan
        if grant is None or consumed is None:
            raise ValueError("autonomous recovery requires committed grant lineage")
        conversation_id = consumed.destination_conversation_id
        if not conversation_id:
            raise ValueError("autonomous recovery lacks destination conversation")
        tick_event_id = grant.authorization.source_event_id
        commits = tuple(
            row for row in self.writer_attempt_journal.read_receipts()
            if row.get("parent_event_id") == tick_event_id
            and row.get("stage") == "commit"
        )
        if not commits:
            return False
        if len(commits) != 1:
            raise ValueError("autonomous tick has multiple committed attempts")
        attempt = self.writer_attempt_journal.read_attempt(
            commits[0]["writer_request_id"])
        commit = attempt.get("commit")
        if not isinstance(commit, Mapping):
            raise ValueError("autonomous committed attempt lacks commit artifact")
        source_ref = commit.get("action_receipt_ref")
        prefix = f"conversation-source:{conversation_id}#"
        if not isinstance(source_ref, str) or not source_ref.startswith(prefix):
            raise ValueError("autonomous commit source reference is invalid")
        event_id = source_ref[len(prefix):]
        source = ProductConversationSourceJournal(
            registry=self.registry,
            conversation_id=conversation_id,
        )
        rows = tuple(
            row for row in source.read_source_receipts()
            if row.get("event_id") == event_id
        )
        if len(rows) != 1:
            raise ValueError("autonomous committed source is not unique")
        row = rows[0]
        event = row.get("source_event") or {}
        if (
            row.get("sender_action_id") != commit.get("action_id")
            or event.get("sender_participant_id")
            != grant.authorization.participant_id
            or commit.get("action_receipt_fingerprint")
            != model_data_fingerprint(row)
        ):
            raise ValueError("autonomous committed source lineage is invalid")
        return True

    def recover_pending(self, actor) -> int:
        """Queue exact current-position grants not already canonically committed."""
        post = getattr(actor, "post_formal_product_autonomous_action", None)
        recover = getattr(
            self.runtime_owner,
            "recover_pending_formal_product_autonomous_ticks",
            None,
        )
        if not callable(post) or not callable(recover):
            raise TypeError(
                "autonomous recovery requires typed Actor and runtime_owner")
        queued = 0
        for tick in recover():
            if self._tick_action_is_committed(tick):
                continue
            post(FormalProductAutonomousActionWork(tick), priority=0)
            queued += 1
        return queued

    def _cards(self, *, destination):
        session = self.registry.load_session(destination.conversation_id)
        if session is None:
            raise ValueError(
                "autonomous cards require registered destination session")
        profile = self.runtime_owner.profile
        profile_fingerprint = model_data_fingerprint(profile)
        profile_version = (
            f"profile:{destination.participant_id}:{profile_fingerprint}")
        return {
            "persona": (
                persona_fact_writer_card(
                    profile_version=profile_version,
                    facts={
                        "角色标识": destination.participant_id,
                        "角色背景": str(profile.get("background") or ""),
                    },
                    source_receipt_refs=(
                        f"profile-receipt:{profile_fingerprint}",),
                ),
            ),
            "conversation": (
                conversation_fact_writer_card(
                    conversation_id=destination.conversation_id,
                    membership_version=str(destination.membership_version),
                    active_member_ids=session.conversation.active_member_ids,
                    source_receipt_refs=(
                        "destination-receipt:"
                        f"{model_data_fingerprint(destination.receipt)}",
                    ),
                ),
            ),
        }

    def execute(self, tick: RuntimeProductObservationTick):
        if not isinstance(tick, RuntimeProductObservationTick):
            raise TypeError("autonomous action host requires product tick")
        observation = tick.committed_grant
        if (
            observation is None
            or observation.authorization is None
            or not observation.assessment.eligible
        ):
            raise ValueError(
                "autonomous action host requires eligible observation")
        consumed = tick.consumed_plan
        conversation_id = (
            None if consumed is None
            else consumed.destination_conversation_id)
        if not conversation_id:
            raise ValueError(
                "autonomous tick lacks destination conversation lineage")
        if self._tick_action_is_committed(tick):
            raise ValueError("autonomous tick already has a committed action")
        session = observation.session
        destination = authorize_autonomous_product_destination(
            authorization=observation.authorization,
            current_position=session.position,
            session_binding=session.session_binding,
            conversation_registry=self.registry,
            conversation_id=conversation_id,
        )
        expression = restore_runtime_expression_state(
            session.profile, session_binding=session.session_binding)
        prepared = restore_prepared_autonomous_writer_attempt(
            observation=observation,
            destination=destination,
            journal=self.writer_attempt_journal,
        )
        executed = (None if prepared is not None else
            restore_candidate_ready_autonomous_writer_attempt(
                observation=observation, destination=destination,
                journal=self.writer_attempt_journal))
        if executed is None:
            if prepared is None:
                cards = self._cards(destination=destination)
                source_evidence = None
                if self.memory_cards_provider is not None:
                    _occupation, memory_cards, source_evidence = self.memory_cards_provider(observation)
                    cards["memory"] = tuple(memory_cards)
                prepared = prepare_autonomous_product_writer_attempt(
                    runtime=self.model_runtime, observation=observation,
                    destination=destination, journal=self.writer_attempt_journal,
                    cards_by_lane=cards,
                    committed_remainders=expression.committed_remainders,
                    source_evidence=source_evidence,
                    provider_dispatch_tracked=self.memory_cards_provider is not None,
                )
            executed = execute_autonomous_product_writer_attempt(
                prepared,
                journal=self.writer_attempt_journal,
                invoke=self.responder_invoke,
            )
        committed = commit_autonomous_product_writer_action(
            executed=executed,
            destination=destination,
            current_position=session.position,
            session_binding=session.session_binding,
            conversation_registry=self.registry,
            writer_attempt_journal=self.writer_attempt_journal,
        )
        output = self.output_lifecycle_host.settle_route_dispatch(committed)
        plan_observation = getattr(
            self.runtime_owner, "plan_formal_product_output_observation", None)
        if not callable(plan_observation):
            raise TypeError(
                "autonomous owner lacks output observation planning")
        observation_plan = plan_observation(
            lifecycle=output.lifecycle,
            action_id=committed.event.sender_action_id,
            planning_payload=self.observation_planning_payload,
        )
        return ProductAutonomousActionResult(
            tick=tick,
            destination=destination,
            executed_attempt=executed,
            committed_action=committed,
            output_lifecycle=output,
            observation_plan=observation_plan,
        )


__all__ = (
    "ProductAutonomousActionHost",
    "ProductAutonomousActionResult",
)
