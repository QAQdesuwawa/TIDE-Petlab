"""Model-neutral product host for one required direct reply lifecycle.

The host composes already-frozen authorities.  It owns no interaction math,
prompt prose, routing, dynamic tick, UI callback, or autonomous scheduling.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Mapping

from framework.model_api import model_data_fingerprint
from product_adapters.cx_six_seven_v1.writer_attempts import WriterAttemptJournal
from interaction_extensions.multi_participant.events import commit_message_event
from ops.product_conversation_journal import ProductConversationSourceJournal
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.product_default_direct_provision import ProductDefaultDirectProvision
from ops.runtime_expression_state import restore_runtime_expression_state
from ops.runtime_product_action_authorization import (
    authorize_required_product_reply,
)
from ops.runtime_product_action_commit import (
    commit_required_product_writer_action,
)
from ops.runtime_product_fact_bridge import (
    build_committed_direct_input_fact_evidence,
)
from ops.runtime_product_writer_attempt import (
    execute_product_writer_attempt,
    prepare_product_writer_attempt,
)
from ops.writer_packet_facts import (
    conversation_fact_writer_card,
    current_input_writer_card,
    persona_fact_writer_card,
    writer_output_identity,
)


@dataclass(frozen=True)
class ProductRequiredReplyResult:
    source_event: object
    lifecycle: object
    occupation: object
    executed_attempt: object
    committed_action: object
    output_lifecycle: object

    def ui_envelope(self) -> dict[str, Any]:
        event = self.committed_action.event
        return {
            "sent": True,
            "reply": event.text,
            "send_at": event.committed_at_ms,
            "action_id": event.sender_action_id,
            "event_id": event.event_id,
            "source_event_id": self.source_event.event_id,
            "formal": True,
            "clock_plan": None,
        }


class ProductRequiredReplyHost:
    """Commit one human source fact through the formal required action path."""

    def __init__(
        self,
        *,
        provision: ProductDefaultDirectProvision,
        conversation_registry: ProductConversationSessionRegistry,
        writer_attempt_journal: WriterAttemptJournal,
        model_runtime,
        temporal_runtime,
        runtime_variant,
        measurement_producer: Callable,
        responder_invoke: Callable,
        memory_cards_provider: Callable | None,
        event_id_factory: Callable[[], str],
        output_lifecycle_host,
        runtime_owner=None,
        runner=None,
    ) -> None:
        if not isinstance(provision, ProductDefaultDirectProvision):
            raise TypeError("required reply host requires direct provision")
        if not isinstance(conversation_registry, ProductConversationSessionRegistry):
            raise TypeError("required reply host requires conversation registry")
        if not isinstance(writer_attempt_journal, WriterAttemptJournal):
            raise TypeError("required reply host requires WriterAttemptJournal")
        for value, label in (
            (measurement_producer, "measurement_producer"),
            (responder_invoke, "responder_invoke"),
            (event_id_factory, "event_id_factory"),
        ):
            if not callable(value):
                raise TypeError(f"{label} must be callable")
        memory_lane = runtime_variant.resource_policy["memory"]
        if memory_lane == "enabled" and not callable(memory_cards_provider):
            raise TypeError(
                "enabled memory lane requires an explicit memory cards provider")
        if memory_cards_provider is not None and not callable(
            memory_cards_provider
        ):
            raise TypeError("memory cards provider must be callable")
        if runtime_owner is not None and runner is not None:
            raise TypeError("provide runtime_owner or legacy runner, not both")
        owner = runtime_owner if runtime_owner is not None else runner
        if owner is None:
            raise TypeError("required reply host requires runtime_owner")
        for method in (
            "formal_owner_participant_identity",
            "commit_verified_formal_runtime_lifecycle",
        ):
            if not callable(getattr(owner, method, None)):
                raise TypeError(
                    f"required reply runtime_owner lacks {method}")
        self.runtime_owner = owner
        self.provision = provision
        self.registry = conversation_registry
        self.writer_attempt_journal = writer_attempt_journal
        self.model_runtime = model_runtime
        self.temporal_runtime = temporal_runtime
        self.runtime_variant = runtime_variant
        self.measurement_producer = measurement_producer
        self.responder_invoke = responder_invoke
        self.memory_cards_provider = memory_cards_provider
        self.event_id_factory = event_id_factory
        settle_output = getattr(
            output_lifecycle_host, "settle_route_dispatch", None)
        if not callable(settle_output):
            raise TypeError(
                "required reply host requires output lifecycle host")
        self.output_lifecycle_host = output_lifecycle_host

    def _memory_cards(self, lifecycle):
        if self.runtime_variant.resource_policy["memory"] == "disabled":
            return None, ()
        return self.memory_cards_provider(lifecycle)

    def submit(self, text: str, *, now_ms: int) -> ProductRequiredReplyResult:
        body = str(text or "").strip()
        if not body:
            raise ValueError("formal direct input text cannot be empty")
        if isinstance(now_ms, bool) or not isinstance(now_ms, int) or now_ms < 0:
            raise ValueError("formal direct input time must be non-negative")
        session = self.runtime_owner.formal_runtime_session
        if session is None:
            raise RuntimeError("required reply host needs an active formal session")
        owner = self.runtime_owner.formal_owner_participant_identity()
        if owner != self.provision.npc:
            raise ValueError("required reply owner differs from direct provision")
        target_position = self.temporal_runtime.normalize_position(now_ms)
        self.temporal_runtime.prove_order(
            session.position.position, target_position)

        event_id = str(self.event_id_factory() or "").strip()
        if not event_id:
            raise ValueError("direct input event ID factory returned empty identity")
        event = commit_message_event(
            self.provision.session.conversation,
            event_id=event_id,
            sender_participant_id=self.provision.human.participant_id,
            text=body,
            committed_at_ms=now_ms,
            addressed_participant_ids=(self.provision.npc.participant_id,),
        )
        source_journal = ProductConversationSourceJournal(
            registry=self.registry,
            conversation_id=self.provision.conversation_id,
        )
        source_receipt = source_journal.append_source_event(event)
        measurement = self.measurement_producer(event)
        evidence = build_committed_direct_input_fact_evidence(
            source_event=event,
            source_receipt=source_receipt,
            sender_participant_kind=self.provision.human.kind,
            recipient_participant_id=self.provision.npc.participant_id,
            semantic_projection=measurement.semantic_projection,
            measurement_evidence=measurement.measurement_evidence,
            expected_runtime_ordinal=session.position.runtime_ordinal,
            model_runtime=self.model_runtime,
            temporal_runtime=self.temporal_runtime,
            runtime_variant=self.runtime_variant,
        )
        expression_state = restore_runtime_expression_state(
            session.profile, session_binding=session.session_binding)
        lifecycle = self.runtime_owner.commit_verified_formal_runtime_lifecycle(
            evidence,
            committed_remainders=expression_state.committed_remainders,
        )
        occupation, memory_cards = self._memory_cards(lifecycle)

        source_ref = "conversation-source:" + (
            f"{event.conversation_id}#{event.event_id}")
        evidence_ref = model_data_fingerprint(lifecycle.evidence.receipt)
        profile = self.runtime_owner.profile
        profile_fingerprint = model_data_fingerprint(profile)
        profile_version = (
            f"profile:{self.provision.npc.participant_id}:"
            f"{profile_fingerprint}")
        cards_by_lane = {
            "current_input": (
                current_input_writer_card(
                    event_id=event.event_id,
                    sender_participant_id=event.sender_participant_id,
                    committed_position_label=(
                        f"{target_position.binding.domain_id}:"
                        f"{target_position.coordinate}"),
                    text=event.text,
                    delivery_id=None,
                    source_receipt_refs=(source_ref, evidence_ref),
                ),
            ),
            "persona": (
                persona_fact_writer_card(
                    profile_version=profile_version,
                    facts={
                        "角色标识": self.provision.npc.participant_id,
                        "角色背景": str(profile.get("background") or ""),
                    },
                    source_receipt_refs=(
                        f"profile-receipt:{profile_fingerprint}",),
                ),
            ),
            "conversation": (
                conversation_fact_writer_card(
                    conversation_id=event.conversation_id,
                    membership_version=str(event.membership_version),
                    active_member_ids=event.member_snapshot_ids,
                    source_receipt_refs=(
                        "direct-provision:"
                        f"{model_data_fingerprint(self.provision.as_record())}",),
                ),
            ),
            "memory": memory_cards,
        }
        output_identity = writer_output_identity(
            speaker_participant_id=self.provision.npc.participant_id,
            speaker_kind=self.provision.npc.kind,
            active_member_ids=event.member_snapshot_ids,
            addressed_participant_ids=(self.provision.human.participant_id,),
        )
        prepared = prepare_product_writer_attempt(
            lifecycle=lifecycle,
            journal=self.writer_attempt_journal,
            output_identity=output_identity,
            cards_by_lane=cards_by_lane,
        )
        executed = execute_product_writer_attempt(
            prepared,
            journal=self.writer_attempt_journal,
            invoke=self.responder_invoke,
        )
        authorization = authorize_required_product_reply(lifecycle)
        committed = commit_required_product_writer_action(
            executed=executed,
            authorization=authorization,
            current_position=lifecycle.transaction.session.position,
            session_binding=lifecycle.transaction.session.session_binding,
            conversation_registry=self.registry,
            writer_attempt_journal=self.writer_attempt_journal,
        )
        output_lifecycle = self.output_lifecycle_host.settle_route_dispatch(
            committed)
        return ProductRequiredReplyResult(
            source_event=event,
            lifecycle=lifecycle,
            occupation=occupation,
            executed_attempt=executed,
            committed_action=committed,
            output_lifecycle=output_lifecycle,
        )


__all__ = ("ProductRequiredReplyHost", "ProductRequiredReplyResult")
