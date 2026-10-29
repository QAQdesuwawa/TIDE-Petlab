"""Commit one authorized Writer candidate as a canonical source fact.

The product host is deliberately narrower than the legacy Runner output path.
It validates one exact authorization and archived candidate, appends one
``CommittedConversationEvent`` to the registered conversation journal, then
links the pre-commit Writer audit to that fact.  It does not settle model state,
advance a TemporalDomain, route deliveries, run memory, call a provider, or send.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from interaction_extensions.conversation_action_protocol import (
    RuntimeActionAuthorization,
    RuntimeActionCommitAuthority,
    RuntimeActionCommitPlan,
)
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimePositionState,
    RuntimeSessionBinding,
)
from product_adapters.cx_six_seven_v1.writer_attempts import WriterAttemptJournal
from product_adapters.cx_six_seven_v1.realizer_packet import verify_writer_packet
from interaction_extensions.multi_participant.events import (
    CommittedConversationEvent,
    canonical_npc_output_event_id,
    commit_message_event,
)
from ops.product_conversation_journal import ProductConversationSourceJournal
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.runtime_product_writer_attempt import RuntimeExecutedWriterAttempt
from ops.runtime_autonomous_writer_attempt import (
    RuntimeExecutedAutonomousWriterAttempt,
)
from ops.runtime_product_destination_authorization import (
    RuntimeProductDestinationAuthorization,
)


PRODUCT_ACTION_COMMIT_VERSION = "runtime-product-action-commit-v1"
_WALL_CLOCK_DOMAIN_ID = "tide.wall-clock-ms"


@dataclass(frozen=True)
class RuntimeCommittedProductAction:
    """One irreversible source fact plus body-free commit lineage."""

    action_plan: RuntimeActionCommitPlan
    event: CommittedConversationEvent
    source_receipt: Mapping[str, Any]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "source_receipt", freeze_model_payload(self.source_receipt))
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def restore_committed_product_action(
    *, action_plan_record: Mapping[str, Any], action_receipt: Mapping[str, Any],
    conversation_registry: ProductConversationSessionRegistry,
    session_binding: RuntimeSessionBinding,
) -> RuntimeCommittedProductAction:
    """Restore one exact committed action without Writer or source authority."""
    if not isinstance(action_plan_record, Mapping):
        raise ValueError("committed action plan record must be a mapping")
    required = set(RuntimeActionCommitPlan.__dataclass_fields__)
    if set(action_plan_record) != required:
        raise ValueError("committed action plan fields are invalid")
    plan = RuntimeActionCommitPlan(**dict(action_plan_record))
    if (
        model_data_fingerprint(plan.fingerprint_material()) != plan.fingerprint
        or plan.session_binding_fingerprint != session_binding.fingerprint
    ):
        raise ValueError("committed action plan fingerprint is invalid")
    _verify_body_free_receipt(
        action_receipt, field="restored committed action receipt")
    receipt = thaw_model_data(action_receipt)
    if (
        receipt.get("version") != PRODUCT_ACTION_COMMIT_VERSION
        or receipt.get("status") != "canonical_source_committed"
        or receipt.get("session_id") != session_binding.session_id
        or receipt.get("conversation_id") != plan.conversation_id
        or receipt.get("participant_id") != plan.participant_id
        or receipt.get("source_event_id") != plan.source_event_id
        or receipt.get("event_id") is None
        or receipt.get("action_id") != plan.action_id
        or receipt.get("writer_request_id") != plan.writer_request_id
        or receipt.get("authorization_fingerprint")
        != plan.authorization_fingerprint
        or receipt.get("action_plan_fingerprint") != plan.fingerprint
        or receipt.get("packet_fingerprint") != plan.packet_fingerprint
        or receipt.get("candidate_fingerprint") != plan.candidate_fingerprint
        or receipt.get("source_position_fingerprint")
        != plan.source_position_fingerprint
        or receipt.get("source_runtime_ordinal")
        != plan.source_runtime_ordinal
        or receipt.get("committed_runtime_ordinal")
        != plan.source_runtime_ordinal
    ):
        raise ValueError("restored committed action receipt differs from plan")
    journal = ProductConversationSourceJournal(
        registry=conversation_registry,
        conversation_id=plan.conversation_id,
    )
    matches = tuple(
        row for row in journal.read_source_receipts()
        if row.get("event_id") == receipt["event_id"]
    )
    if len(matches) != 1:
        raise ValueError("restored committed action requires one source row")
    source_receipt = matches[0]
    event = CommittedConversationEvent.from_record(
        source_receipt["source_event"])
    if (
        event.sender_action_id != plan.action_id
        or event.sender_participant_id != plan.participant_id
        or event.conversation_id != plan.conversation_id
        or receipt.get("source_receipt_fingerprint")
        != model_data_fingerprint(source_receipt)
    ):
        raise ValueError("restored committed action source differs from plan")
    return RuntimeCommittedProductAction(
        action_plan=plan,
        event=event,
        source_receipt=source_receipt,
        receipt=action_receipt,
    )


class PostCommitWriterAttemptLinkError(RuntimeError):
    """The source fact is durable but its Writer audit link needs recovery."""

    def __init__(
        self,
        *,
        action_plan: RuntimeActionCommitPlan,
        event: CommittedConversationEvent,
        source_receipt: Mapping[str, Any],
        cause: Exception,
    ) -> None:
        self.action_plan = action_plan
        self.event = event
        self.source_receipt = freeze_model_payload(source_receipt)
        self.cause_type = type(cause).__name__
        material = {
            "version": PRODUCT_ACTION_COMMIT_VERSION,
            "status": "canonical_source_committed_attempt_link_failed",
            "action_id": action_plan.action_id,
            "event_id": event.event_id,
            "writer_request_id": action_plan.writer_request_id,
            "action_plan_fingerprint": action_plan.fingerprint,
            "source_receipt_fingerprint": model_data_fingerprint(
                source_receipt),
            "cause_type": self.cause_type,
            "authority": {
                "source_rollback": False,
                "source_recommit": False,
                "candidate_regeneration": False,
                "dynamic_tick": False,
                "send": False,
            },
        }
        self.receipt = freeze_model_payload(dict(
            material, fingerprint=model_data_fingerprint(material)))
        super().__init__(
            "canonical source is committed; Writer attempt link requires recovery")


def _verify_body_free_receipt(receipt: Mapping[str, Any], *, field: str) -> None:
    if not isinstance(receipt, Mapping):
        raise ValueError(f"{field} must be a mapping")
    material = thaw_model_data(receipt)
    fingerprint = material.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(material):
        raise ValueError(f"{field} fingerprint is invalid")


def _verify_executed_attempt(
    executed: RuntimeExecutedWriterAttempt,
    *,
    journal: WriterAttemptJournal,
) -> tuple[object, str]:
    if not isinstance(executed, RuntimeExecutedWriterAttempt):
        raise ValueError("action Commit requires RuntimeExecutedWriterAttempt")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("action Commit requires WriterAttemptJournal")
    _verify_body_free_receipt(
        executed.receipt, field="executed attempt receipt")
    prepared = executed.prepared
    packet = prepared.product_packet_shadow.packet_shadow.packet
    verify_writer_packet(packet)
    receipt = thaw_model_data(executed.receipt)
    if (
        receipt.get("status") != "candidate_ready_uncommitted"
        or receipt.get("writer_request_id")
        != prepared.identity.writer_request_id
        or receipt.get("parent_event_id") != prepared.identity.parent_event_id
        or receipt.get("prepared_attempt_receipt_fingerprint")
        != model_data_fingerprint(prepared.receipt)
        or receipt.get("packet_fingerprint") != packet.packet_fingerprint
        or receipt.get("prompt_fingerprint")
        != prepared.assembly.prompt_fingerprint
        or receipt.get("raw_candidate_fingerprint")
        != model_data_fingerprint({"candidate": executed.result.raw_candidate})
        or receipt.get("parsed_candidate_fingerprint")
        != model_data_fingerprint(executed.result.parsed)
    ):
        raise ValueError("executed attempt receipt differs from candidate result")
    if executed.result.assembly != prepared.assembly:
        raise ValueError("executed attempt references another prompt assembly")
    artifact = journal.read_attempt(prepared.identity.writer_request_id)
    terminal = artifact.get("terminal")
    if not isinstance(terminal, Mapping) or terminal.get("status") != "candidate_ready":
        raise ValueError("executed attempt lacks candidate-ready terminal")
    if "commit" in artifact:
        raise ValueError("executed attempt is already committed")
    if artifact.get("packet") != packet.to_record():
        raise ValueError("archived WriterPacket differs from executed attempt")
    if artifact.get("prompt") != {
        "system": prepared.assembly.system,
        "user": prepared.assembly.user,
        "prompt_fingerprint": prepared.assembly.prompt_fingerprint,
    }:
        raise ValueError("archived prompt differs from executed attempt")
    provider = artifact.get("provider")
    if (
        not isinstance(provider, Mapping)
        or provider.get("candidate") != executed.result.raw_candidate
    ):
        raise ValueError("archived provider result differs from executed attempt")
    provider_result = terminal.get("provider_result")
    if (
        not isinstance(provider_result, Mapping)
        or provider_result.get("parsed") != executed.result.parsed
    ):
        raise ValueError("archived terminal differs from executed attempt")
    parsed = executed.result.parsed
    if not isinstance(parsed, Mapping):
        raise ValueError("executed candidate parse result is invalid")
    reply = str(parsed.get("reply") or "")
    if not reply.strip():
        raise ValueError("executed candidate reply is empty")
    return packet, reply


def _verify_autonomous_executed_attempt(
    executed: RuntimeExecutedAutonomousWriterAttempt,
    *,
    journal: WriterAttemptJournal,
) -> tuple[object, str]:
    if not isinstance(executed, RuntimeExecutedAutonomousWriterAttempt):
        raise ValueError(
            "autonomous action Commit requires "
            "RuntimeExecutedAutonomousWriterAttempt")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("action Commit requires WriterAttemptJournal")
    _verify_body_free_receipt(
        executed.receipt, field="executed attempt receipt")
    prepared = executed.prepared
    packet = prepared.autonomous_packet_shadow.packet_shadow.packet
    verify_writer_packet(packet)
    receipt = thaw_model_data(executed.receipt)
    if (
        receipt.get("status") != "candidate_ready_uncommitted"
        or receipt.get("writer_request_id")
        != prepared.identity.writer_request_id
        or receipt.get("parent_tick_event_id")
        != prepared.identity.parent_event_id
        or receipt.get("prepared_attempt_receipt_fingerprint")
        != model_data_fingerprint(prepared.receipt)
        or receipt.get("packet_fingerprint") != packet.packet_fingerprint
        or receipt.get("prompt_fingerprint")
        != prepared.assembly.prompt_fingerprint
        or receipt.get("raw_candidate_fingerprint")
        != model_data_fingerprint({"candidate": executed.result.raw_candidate})
        or receipt.get("parsed_candidate_fingerprint")
        != model_data_fingerprint(executed.result.parsed)
    ):
        raise ValueError("executed attempt receipt differs from candidate result")
    if executed.result.assembly != prepared.assembly:
        raise ValueError("executed attempt references another prompt assembly")
    artifact = journal.read_attempt(prepared.identity.writer_request_id)
    terminal = artifact.get("terminal")
    if not isinstance(terminal, Mapping) or terminal.get("status") != "candidate_ready":
        raise ValueError("executed attempt lacks candidate-ready terminal")
    if "commit" in artifact:
        raise ValueError("executed attempt is already committed")
    if artifact.get("packet") != packet.to_record():
        raise ValueError("archived WriterPacket differs from executed attempt")
    if artifact.get("prompt") != {
        "system": prepared.assembly.system,
        "user": prepared.assembly.user,
        "prompt_fingerprint": prepared.assembly.prompt_fingerprint,
    }:
        raise ValueError("archived prompt differs from executed attempt")
    provider = artifact.get("provider")
    if (
        not isinstance(provider, Mapping)
        or provider.get("candidate") != executed.result.raw_candidate
    ):
        raise ValueError("archived provider result differs from executed attempt")
    provider_result = terminal.get("provider_result")
    if (
        not isinstance(provider_result, Mapping)
        or provider_result.get("parsed") != executed.result.parsed
    ):
        raise ValueError("archived terminal differs from executed attempt")
    parsed = executed.result.parsed
    if not isinstance(parsed, Mapping):
        raise ValueError("executed candidate parse result is invalid")
    reply = str(parsed.get("reply") or "")
    if not reply.strip():
        raise ValueError("executed candidate reply is empty")
    return packet, reply


def _verify_destination_receipt(
    destination: RuntimeProductDestinationAuthorization,
) -> str:
    if not isinstance(destination, RuntimeProductDestinationAuthorization):
        raise ValueError(
            "autonomous action Commit requires destination authorization")
    material = thaw_model_data(destination.receipt)
    fingerprint = material.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(material):
        raise ValueError("destination receipt fingerprint is invalid")
    authorization = destination.authorization
    if (
        authorization.mode != "autonomous_opening"
        or material.get("authorization_fingerprint")
        != authorization.fingerprint
        or material.get("source_event_id") != authorization.source_event_id
        or material.get("source_position_fingerprint")
        != authorization.source_position_fingerprint
        or material.get("source_runtime_ordinal")
        != authorization.source_runtime_ordinal
        or material.get("conversation_id") != destination.conversation_id
        or material.get("membership_version")
        != destination.membership_version
        or material.get("participant_id") != destination.participant_id
        or material.get("participant_kind") != destination.participant_kind
        or tuple(material.get("audience_participant_ids") or ())
        != destination.audience_participant_ids
        or tuple(material.get("addressed_participant_ids") or ())
        != destination.addressed_participant_ids
    ):
        raise ValueError(
            "destination receipt differs from autonomous authorization")
    return model_data_fingerprint(destination.receipt)


def _commit_time_ms(
    current_position: RuntimePositionState,
    session_binding: RuntimeSessionBinding,
) -> int:
    current_position.verify_integrity(session_binding)
    position = current_position.position
    if position.binding.domain_id != _WALL_CLOCK_DOMAIN_ID:
        raise ValueError(
            "product conversation journal requires the wall-clock-ms TemporalDomain")
    coordinate = position.coordinate
    if isinstance(coordinate, bool) or not isinstance(coordinate, int) or coordinate < 0:
        raise ValueError("product action position is not a millisecond coordinate")
    return coordinate


def _append_or_reuse_exact_source(
    journal: ProductConversationSourceJournal,
    event: CommittedConversationEvent,
) -> tuple[Mapping[str, Any], bool]:
    matches = tuple(
        row for row in journal.read_source_receipts()
        if row.get("event_id") == event.event_id
    )
    if matches:
        if len(matches) != 1 or matches[0].get("source_event") != event.as_record():
            raise ValueError("canonical source event conflicts with existing journal fact")
        return matches[0], True
    return journal.append_source_event(event), False


def commit_required_product_writer_action(
    *,
    executed: RuntimeExecutedWriterAttempt,
    authorization: RuntimeActionAuthorization,
    current_position: RuntimePositionState,
    session_binding: RuntimeSessionBinding,
    conversation_registry: ProductConversationSessionRegistry,
    writer_attempt_journal: WriterAttemptJournal,
) -> RuntimeCommittedProductAction:
    """Append one exact required-reply candidate to the canonical journal."""
    if not isinstance(authorization, RuntimeActionAuthorization):
        raise RuntimeKernelContractError(
            "action Commit requires RuntimeActionAuthorization")
    if authorization.mode != "required_reply":
        raise RuntimeKernelContractError(
            "required action Commit cannot consume autonomous authorization")
    if not isinstance(conversation_registry, ProductConversationSessionRegistry):
        raise ValueError("action Commit requires ProductConversationSessionRegistry")
    packet, reply = _verify_executed_attempt(
        executed, journal=writer_attempt_journal)
    if packet.session_id != session_binding.session_id:
        raise ValueError("WriterPacket session differs from runtime binding")
    if (
        packet.event_id != authorization.source_event_id
        or executed.prepared.identity.parent_event_id
        != authorization.source_event_id
    ):
        raise ValueError("Writer attempt parent differs from authorization")
    output = packet.output_identity
    if (
        packet.conversation_id != authorization.conversation_id
        or output.speaker_participant_id != authorization.participant_id
    ):
        raise ValueError("WriterPacket speaker or conversation differs from authorization")

    session = conversation_registry.load_session(packet.conversation_id)
    if session is None:
        raise ValueError("action Commit requires a registered conversation")
    conversation = session.conversation
    participant = session.participants_by_id.get(output.speaker_participant_id)
    if participant is None or not participant.capabilities.has_writer:
        raise ValueError("action Commit speaker lacks registered Writer capability")
    expected_audience = tuple(
        member for member in conversation.active_member_ids
        if member != output.speaker_participant_id
    )
    if output.audience_participant_ids != expected_audience:
        raise ValueError("WriterPacket audience differs from conversation snapshot")
    if not output.addressed_participant_ids:
        raise ValueError("required reply must preserve explicit address scope")

    committed_at_ms = _commit_time_ms(current_position, session_binding)
    candidate_fingerprint = model_data_fingerprint(executed.result.parsed)
    authority = RuntimeActionCommitAuthority(session_binding=session_binding)
    action_plan = authority.prepare_required_action(
        authorization=authorization,
        current_position=current_position,
        conversation_id=packet.conversation_id,
        participant_id=output.speaker_participant_id,
        source_event_id=packet.event_id,
        writer_request_id=packet.writer_request_id,
        packet_fingerprint=packet.packet_fingerprint,
        candidate_fingerprint=candidate_fingerprint,
    )
    event_id = canonical_npc_output_event_id(
        conversation_id=packet.conversation_id,
        sender_participant_id=output.speaker_participant_id,
        action_id=action_plan.action_id,
    )
    event = commit_message_event(
        conversation,
        event_id=event_id,
        sender_participant_id=output.speaker_participant_id,
        sender_action_id=action_plan.action_id,
        text=reply,
        committed_at_ms=committed_at_ms,
        addressed_participant_ids=output.addressed_participant_ids,
    )
    source_journal = ProductConversationSourceJournal(
        registry=conversation_registry,
        conversation_id=packet.conversation_id,
    )
    source_receipt, source_already_committed = _append_or_reuse_exact_source(
        source_journal, event)
    source_receipt_fingerprint = model_data_fingerprint(source_receipt)
    source_receipt_ref = (
        f"conversation-source:{packet.conversation_id}#{event.event_id}")
    try:
        attempt_link_receipt = writer_attempt_journal.link_committed_action(
            packet.writer_request_id,
            action_id=action_plan.action_id,
            action_receipt_ref=source_receipt_ref,
            action_receipt_fingerprint=source_receipt_fingerprint,
        )
    except Exception as exc:
        raise PostCommitWriterAttemptLinkError(
            action_plan=action_plan,
            event=event,
            source_receipt=source_receipt,
            cause=exc,
        ) from exc
    material = {
        "version": PRODUCT_ACTION_COMMIT_VERSION,
        "status": "canonical_source_committed",
        "session_id": session_binding.session_id,
        "conversation_id": packet.conversation_id,
        "participant_id": output.speaker_participant_id,
        "source_event_id": authorization.source_event_id,
        "event_id": event.event_id,
        "action_id": action_plan.action_id,
        "writer_request_id": packet.writer_request_id,
        "authorization_fingerprint": authorization.fingerprint,
        "action_plan_fingerprint": action_plan.fingerprint,
        "packet_fingerprint": packet.packet_fingerprint,
        "candidate_fingerprint": candidate_fingerprint,
        "source_position_fingerprint": current_position.position.fingerprint,
        "source_runtime_ordinal": current_position.runtime_ordinal,
        "committed_runtime_ordinal": current_position.runtime_ordinal,
        "source_receipt_ref": source_receipt_ref,
        "source_receipt_fingerprint": source_receipt_fingerprint,
        "source_already_committed": source_already_committed,
        "attempt_link_receipt_fingerprint": attempt_link_receipt[
            "artifact_fingerprint"],
        "authority": {
            "canonical_source_written": True,
            "runtime_ordinal_incremented": False,
            "model_state_settled": False,
            "delivery_routed": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeCommittedProductAction(
        action_plan=action_plan,
        event=event,
        source_receipt=source_receipt,
        receipt=receipt,
    )


def commit_autonomous_product_writer_action(
    *,
    executed: RuntimeExecutedAutonomousWriterAttempt,
    destination: RuntimeProductDestinationAuthorization,
    current_position: RuntimePositionState,
    session_binding: RuntimeSessionBinding,
    conversation_registry: ProductConversationSessionRegistry,
    writer_attempt_journal: WriterAttemptJournal,
) -> RuntimeCommittedProductAction:
    """Commit one destination-bound autonomous candidate as source fact."""
    destination_fingerprint = _verify_destination_receipt(destination)
    authorization = destination.authorization
    authorization.verify_integrity(
        session_binding=session_binding,
        current_position=current_position,
    )
    if not isinstance(conversation_registry, ProductConversationSessionRegistry):
        raise ValueError(
            "action Commit requires ProductConversationSessionRegistry")
    packet, reply = _verify_autonomous_executed_attempt(
        executed, journal=writer_attempt_journal)
    if packet.session_id != session_binding.session_id:
        raise ValueError("WriterPacket session differs from runtime binding")
    if (
        packet.event_id != authorization.source_event_id
        or executed.prepared.identity.parent_event_id
        != authorization.source_event_id
    ):
        raise ValueError("Writer attempt parent differs from authorization")
    output = packet.output_identity
    if (
        packet.conversation_id != destination.conversation_id
        or output.speaker_participant_id != destination.participant_id
        or output.speaker_kind != destination.participant_kind
        or output.audience_participant_ids
        != destination.audience_participant_ids
        or output.addressed_participant_ids
        != destination.addressed_participant_ids
    ):
        raise ValueError(
            "WriterPacket identity differs from autonomous destination")

    session = conversation_registry.load_session(destination.conversation_id)
    if session is None:
        raise ValueError("action Commit requires a registered conversation")
    conversation = session.conversation
    if conversation.membership_version != destination.membership_version:
        raise ValueError(
            "conversation membership differs from autonomous destination")
    participant = session.participants_by_id.get(destination.participant_id)
    if (
        participant is None
        or participant.kind != destination.participant_kind
        or not participant.capabilities.has_writer
    ):
        raise ValueError(
            "action Commit speaker differs from registered Writer participant")
    expected_audience = tuple(
        member for member in conversation.active_member_ids
        if member != destination.participant_id
    )
    if (
        destination.audience_participant_ids != expected_audience
        or not destination.addressed_participant_ids
        or any(
            item not in destination.audience_participant_ids
            for item in destination.addressed_participant_ids
        )
    ):
        raise ValueError(
            "autonomous destination differs from conversation snapshot")

    committed_at_ms = _commit_time_ms(current_position, session_binding)
    candidate_fingerprint = model_data_fingerprint(executed.result.parsed)
    authority = RuntimeActionCommitAuthority(session_binding=session_binding)
    action_plan = authority.prepare_autonomous_action(
        authorization=authorization,
        current_position=current_position,
        destination_evidence_fingerprint=destination_fingerprint,
        conversation_id=packet.conversation_id,
        participant_id=output.speaker_participant_id,
        source_event_id=packet.event_id,
        writer_request_id=packet.writer_request_id,
        packet_fingerprint=packet.packet_fingerprint,
        candidate_fingerprint=candidate_fingerprint,
    )
    event_id = canonical_npc_output_event_id(
        conversation_id=packet.conversation_id,
        sender_participant_id=output.speaker_participant_id,
        action_id=action_plan.action_id,
    )
    event = commit_message_event(
        conversation,
        event_id=event_id,
        sender_participant_id=output.speaker_participant_id,
        sender_action_id=action_plan.action_id,
        text=reply,
        committed_at_ms=committed_at_ms,
        addressed_participant_ids=output.addressed_participant_ids,
    )
    source_journal = ProductConversationSourceJournal(
        registry=conversation_registry,
        conversation_id=packet.conversation_id,
    )
    source_receipt, source_already_committed = _append_or_reuse_exact_source(
        source_journal, event)
    source_receipt_fingerprint = model_data_fingerprint(source_receipt)
    source_receipt_ref = (
        f"conversation-source:{packet.conversation_id}#{event.event_id}")
    try:
        attempt_link_receipt = writer_attempt_journal.link_committed_action(
            packet.writer_request_id,
            action_id=action_plan.action_id,
            action_receipt_ref=source_receipt_ref,
            action_receipt_fingerprint=source_receipt_fingerprint,
        )
    except Exception as exc:
        raise PostCommitWriterAttemptLinkError(
            action_plan=action_plan,
            event=event,
            source_receipt=source_receipt,
            cause=exc,
        ) from exc
    material = {
        "version": PRODUCT_ACTION_COMMIT_VERSION,
        "status": "canonical_source_committed",
        "authorization_mode": authorization.mode,
        "session_id": session_binding.session_id,
        "conversation_id": packet.conversation_id,
        "participant_id": output.speaker_participant_id,
        "source_event_id": authorization.source_event_id,
        "event_id": event.event_id,
        "action_id": action_plan.action_id,
        "writer_request_id": packet.writer_request_id,
        "authorization_fingerprint": authorization.fingerprint,
        "destination_receipt_fingerprint": destination_fingerprint,
        "action_plan_fingerprint": action_plan.fingerprint,
        "packet_fingerprint": packet.packet_fingerprint,
        "candidate_fingerprint": candidate_fingerprint,
        "source_position_fingerprint": current_position.position.fingerprint,
        "source_runtime_ordinal": current_position.runtime_ordinal,
        "committed_runtime_ordinal": current_position.runtime_ordinal,
        "source_receipt_ref": source_receipt_ref,
        "source_receipt_fingerprint": source_receipt_fingerprint,
        "source_already_committed": source_already_committed,
        "attempt_link_receipt_fingerprint": attempt_link_receipt[
            "artifact_fingerprint"],
        "authority": {
            "canonical_source_written": True,
            "runtime_ordinal_incremented": False,
            "model_state_settled": False,
            "delivery_routed": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeCommittedProductAction(
        action_plan=action_plan,
        event=event,
        source_receipt=source_receipt,
        receipt=receipt,
    )


__all__ = (
    "PostCommitWriterAttemptLinkError",
    "PRODUCT_ACTION_COMMIT_VERSION",
    "RuntimeCommittedProductAction",
    "restore_committed_product_action",
    "commit_autonomous_product_writer_action",
    "commit_required_product_writer_action",
)
