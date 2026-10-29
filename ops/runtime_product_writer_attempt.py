"""Prepare one audited Writer attempt from a complete product lifecycle.

This application composition seam allocates an operational request, assembles
the lifecycle-bound Packet, and archives the exact prompt produced by the pure
Responder adapter.  It does not execute a responder, create a candidate,
authorize an action, commit interaction history, or send content.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from agents.responder import (
    PromptAssembly,
    ResponderAttemptResult,
    execute_prepared_responder_attempt,
    prepare_responder_attempt,
)
from framework.model_api import freeze_model_payload, model_data_fingerprint
from product_adapters.cx_six_seven_v1.writer_attempts import (
    WriterAttemptIdentity,
    WriterAttemptJournal,
    WriterAttemptJournalError,
)
from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterCard,
    WriterOutputIdentity,
)
from ops.runtime_product_fact_lifecycle import RuntimeProductFactLifecycle
from ops.runtime_product_writer_packet_shadow import (
    RuntimeProductWriterPacketShadow,
    build_product_writer_packet_shadow,
)
from ops.writer_attempt_receipts import (
    link_writer_attempt_to_action_receipt,
)


PRODUCT_WRITER_ATTEMPT_VERSION = "runtime-product-writer-attempt-v1"


@dataclass(frozen=True)
class RuntimePreparedWriterAttempt:
    """One allocated request with its exact archived Packet and prompt."""

    lifecycle: RuntimeProductFactLifecycle
    identity: WriterAttemptIdentity
    product_packet_shadow: RuntimeProductWriterPacketShadow
    assembly: PromptAssembly
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


@dataclass(frozen=True)
class RuntimeExecutedWriterAttempt:
    """One candidate-ready attempt that still has no action authority."""

    prepared: RuntimePreparedWriterAttempt
    result: ResponderAttemptResult
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


@dataclass(frozen=True)
class RuntimeCommittedWriterAttemptLink:
    """Body-free proof that one candidate became an already committed action."""

    executed: RuntimeExecutedWriterAttempt
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _non_negative_time(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _verify_body_free_receipt(receipt: Mapping[str, Any], *, field: str) -> None:
    if not isinstance(receipt, Mapping):
        raise ValueError(f"{field} must be a mapping")
    material = dict(receipt)
    fingerprint = material.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(material):
        raise ValueError(f"{field} fingerprint is invalid")


def _archive_packet_assembly_failure(
    *,
    journal: WriterAttemptJournal,
    identity: WriterAttemptIdentity,
    lifecycle: RuntimeProductFactLifecycle,
    cause: Exception,
) -> None:
    try:
        journal.record_terminal(
            identity.writer_request_id,
            status="failed",
            error={
                "stage": "product_writer_packet_assembly",
                "type": type(cause).__name__,
                "message": str(cause),
                "lifecycle_receipt_fingerprint": model_data_fingerprint(
                    lifecycle.receipt),
            },
        )
    except Exception as audit_exc:
        raise WriterAttemptJournalError(
            "product Packet assembly failure could not be archived; "
            f"original={type(cause).__name__}: {cause}"
        ) from audit_exc


def prepare_product_writer_attempt(
    *,
    lifecycle: RuntimeProductFactLifecycle,
    journal: WriterAttemptJournal,
    output_identity: WriterOutputIdentity,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
    budget_chars: int | None = None,
) -> RuntimePreparedWriterAttempt:
    """Allocate, assemble, and archive a prompt without running the Writer."""
    if not isinstance(lifecycle, RuntimeProductFactLifecycle):
        raise ValueError("product Writer attempt requires a complete lifecycle")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("product Writer attempt requires WriterAttemptJournal")

    identity = journal.allocate_request(
        parent_event_id=lifecycle.evidence.event_id)
    try:
        product_packet_shadow = build_product_writer_packet_shadow(
            lifecycle=lifecycle,
            shadow_request_id=identity.writer_request_id,
            output_identity=output_identity,
            cards_by_lane=cards_by_lane,
        )
    except Exception as exc:
        _archive_packet_assembly_failure(
            journal=journal,
            identity=identity,
            lifecycle=lifecycle,
            cause=exc,
        )
        raise

    packet = product_packet_shadow.packet_shadow.packet
    assembly = prepare_responder_attempt(
        packet,
        journal=journal,
        budget_chars=budget_chars,
    )
    material = {
        "version": PRODUCT_WRITER_ATTEMPT_VERSION,
        "status": "prompt_prepared",
        "writer_request_id": identity.writer_request_id,
        "parent_event_id": identity.parent_event_id,
        "attempt_ordinal": identity.attempt_ordinal,
        "display_suffix": identity.display_suffix,
        "lifecycle_receipt_fingerprint": model_data_fingerprint(
            lifecycle.receipt),
        "product_packet_shadow_receipt_fingerprint": model_data_fingerprint(
            product_packet_shadow.receipt),
        "packet_fingerprint": packet.packet_fingerprint,
        "prompt_fingerprint": assembly.prompt_fingerprint,
        "delivery_receipt_fingerprint": model_data_fingerprint({
            "delivered": assembly.delivered_materials,
            "rejected": assembly.rejected_materials,
            "overflow": assembly.budget_overflow_materials,
        }),
        "authority": {
            "responder_execution": False,
            "candidate": False,
            "action": False,
            "history_write": False,
            "commit": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimePreparedWriterAttempt(
        lifecycle=lifecycle,
        identity=identity,
        product_packet_shadow=product_packet_shadow,
        assembly=assembly,
        receipt=receipt,
    )


def execute_product_writer_attempt(
    prepared: RuntimePreparedWriterAttempt,
    *,
    journal: WriterAttemptJournal,
    invoke,
    provider_metadata: Mapping[str, Any] | None = None,
) -> RuntimeExecutedWriterAttempt:
    """Execute one exact prepared attempt without preparing it again."""
    if not isinstance(prepared, RuntimePreparedWriterAttempt):
        raise ValueError("product Writer execution requires a prepared attempt")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("product Writer execution requires WriterAttemptJournal")
    packet = prepared.product_packet_shadow.packet_shadow.packet
    result = execute_prepared_responder_attempt(
        packet,
        assembly=prepared.assembly,
        journal=journal,
        invoke=invoke,
        provider_metadata=provider_metadata,
    )
    terminal_rows = tuple(
        row for row in journal.read_receipts()
        if row.get("writer_request_id") == prepared.identity.writer_request_id
        and row.get("stage") == "terminal"
    )
    if (
        len(terminal_rows) != 1
        or terminal_rows[0].get("terminal_status") != "candidate_ready"
    ):
        raise WriterAttemptJournalError(
            "executed product attempt lacks one candidate-ready terminal")
    material = {
        "version": PRODUCT_WRITER_ATTEMPT_VERSION,
        "status": "candidate_ready_uncommitted",
        "writer_request_id": prepared.identity.writer_request_id,
        "parent_event_id": prepared.identity.parent_event_id,
        "prepared_attempt_receipt_fingerprint": model_data_fingerprint(
            prepared.receipt),
        "packet_fingerprint": packet.packet_fingerprint,
        "prompt_fingerprint": prepared.assembly.prompt_fingerprint,
        "raw_candidate_fingerprint": model_data_fingerprint({
            "candidate": result.raw_candidate,
        }),
        "parsed_candidate_fingerprint": model_data_fingerprint(result.parsed),
        "terminal_receipt_fingerprint": model_data_fingerprint(
            terminal_rows[0]),
        "authority": {
            "action": False,
            "history_write": False,
            "memory_write": False,
            "commit": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeExecutedWriterAttempt(
        prepared=prepared,
        result=result,
        receipt=receipt,
    )


def link_product_writer_attempt_to_committed_action(
    executed: RuntimeExecutedWriterAttempt,
    *,
    journal: WriterAttemptJournal,
    committed_message: Mapping[str, Any],
    action_receipt: Mapping[str, Any],
    message_receipt_ref: str,
    action_receipt_ref: str,
) -> RuntimeCommittedWriterAttemptLink:
    """Verify exact adoption after commit, then append the no-body action link.

    The action and message must already exist.  This function has no action,
    history, timing, tick, or send authority.
    """
    if not isinstance(executed, RuntimeExecutedWriterAttempt):
        raise ValueError("product action link requires an executed attempt")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("product action link requires WriterAttemptJournal")
    if not isinstance(committed_message, Mapping):
        raise ValueError("committed message must be a mapping")
    if not isinstance(action_receipt, Mapping):
        raise ValueError("action receipt must be a mapping")
    _verify_body_free_receipt(executed.receipt, field="executed attempt receipt")

    prepared = executed.prepared
    packet = prepared.product_packet_shadow.packet_shadow.packet
    request_id = prepared.identity.writer_request_id
    execution_receipt = dict(executed.receipt)
    if (
        execution_receipt.get("status") != "candidate_ready_uncommitted"
        or execution_receipt.get("writer_request_id") != request_id
        or execution_receipt.get("parent_event_id") != prepared.identity.parent_event_id
        or execution_receipt.get("prepared_attempt_receipt_fingerprint")
        != model_data_fingerprint(prepared.receipt)
        or execution_receipt.get("packet_fingerprint")
        != packet.packet_fingerprint
        or execution_receipt.get("prompt_fingerprint")
        != prepared.assembly.prompt_fingerprint
        or execution_receipt.get("raw_candidate_fingerprint")
        != model_data_fingerprint({
            "candidate": executed.result.raw_candidate,
        })
        or execution_receipt.get("parsed_candidate_fingerprint")
        != model_data_fingerprint(executed.result.parsed)
    ):
        raise ValueError("executed attempt receipt differs from candidate result")
    if executed.result.assembly != prepared.assembly:
        raise ValueError("executed candidate references another prompt assembly")
    artifact = journal.read_attempt(request_id)
    terminal = artifact.get("terminal")
    if not isinstance(terminal, Mapping) or terminal.get("status") != "candidate_ready":
        raise ValueError("product action link requires candidate-ready attempt")
    if "commit" in artifact:
        raise ValueError("product action link attempt is already committed")
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
        raise ValueError("archived provider candidate differs from executed attempt")
    terminal_provider = terminal.get("provider_result")
    if (
        not isinstance(terminal_provider, Mapping)
        or terminal_provider.get("parsed") != executed.result.parsed
    ):
        raise ValueError("archived terminal differs from executed candidate")

    parsed = executed.result.parsed
    if not isinstance(parsed, Mapping):
        raise ValueError("executed candidate parse result is invalid")
    candidate_reply = str(parsed.get("reply") or "")
    message_text = str(committed_message.get("text") or "")
    if not candidate_reply or message_text != candidate_reply:
        raise ValueError("committed message differs from candidate reply")
    if str(committed_message.get("role") or "").strip().lower() != "npc":
        raise ValueError("committed candidate message must have NPC role")

    message_action_id = _identifier(
        committed_message.get("action_id"), "committed message action ID")
    receipt_action_id = _identifier(
        action_receipt.get("action_id"), "action receipt action ID")
    if message_action_id != receipt_action_id:
        raise ValueError("committed message action differs from action receipt")
    message_time = _non_negative_time(
        committed_message.get("ts"), "committed message time")
    receipt_time = _non_negative_time(
        action_receipt.get("ts"), "action receipt time")
    if message_time != receipt_time:
        raise ValueError("committed message time differs from action receipt")
    message_source = _identifier(
        committed_message.get("source"), "committed message source")
    receipt_source = _identifier(
        action_receipt.get("source"), "action receipt source")
    if message_source != receipt_source:
        raise ValueError("committed message source differs from action receipt")
    if _identifier(
        committed_message.get("params_sha"), "committed message parameters",
    ) != _identifier(action_receipt.get("params_sha"), "action receipt parameters"):
        raise ValueError("committed message parameters differ from action receipt")

    output_identity = committed_message.get("output_identity")
    if not isinstance(output_identity, Mapping):
        raise ValueError("committed message lacks output speaker identity")
    if (
        _identifier(output_identity.get("participant_id"), "message speaker")
        != packet.output_identity.speaker_participant_id
        or _identifier(output_identity.get("actor_kind"), "message speaker kind")
        != packet.output_identity.speaker_kind
    ):
        raise ValueError("committed message speaker differs from WriterPacket")

    conversation = committed_message.get("conversation_context")
    if not isinstance(conversation, Mapping):
        raise ValueError("committed message lacks conversation context")
    if _identifier(
        conversation.get("conversation_id"), "message conversation",
    ) != packet.conversation_id:
        raise ValueError("committed message conversation differs from WriterPacket")
    if _identifier(
        conversation.get("caused_by_event_id"), "message parent event",
    ) != prepared.identity.parent_event_id:
        raise ValueError("committed message parent differs from Writer attempt")
    visible = tuple(str(item) for item in (
        conversation.get("visible_recipient_ids") or ()))
    if visible != packet.output_identity.audience_participant_ids:
        raise ValueError("committed message audience differs from WriterPacket")
    members = tuple(str(item) for item in (
        conversation.get("member_snapshot_ids") or ()))
    expected_members = (
        packet.output_identity.speaker_participant_id,
        *packet.output_identity.audience_participant_ids,
    )
    if set(members) != set(expected_members) or len(members) != len(expected_members):
        raise ValueError("committed message membership differs from WriterPacket")

    message_ref = _identifier(message_receipt_ref, "message receipt ref")
    action_ref = _identifier(action_receipt_ref, "action receipt ref")
    link = link_writer_attempt_to_action_receipt(
        journal,
        writer_request_id=request_id,
        action_receipt=action_receipt,
        action_receipt_ref=action_ref,
    )
    material = {
        "version": PRODUCT_WRITER_ATTEMPT_VERSION,
        "status": "committed_action_linked",
        "writer_request_id": request_id,
        "parent_event_id": prepared.identity.parent_event_id,
        "action_id": message_action_id,
        "message_receipt_ref": message_ref,
        "action_receipt_ref": action_ref,
        "executed_attempt_receipt_fingerprint": model_data_fingerprint(
            executed.receipt),
        "committed_message_fingerprint": model_data_fingerprint(
            dict(committed_message)),
        "action_receipt_fingerprint": link["action_receipt_fingerprint"],
        "journal_commit_receipt_fingerprint": link[
            "journal_receipt_fingerprint"],
        "candidate_adopted_exactly": True,
        "authority": {
            "action_created": False,
            "history_written": False,
            "temporal_progressed": False,
            "tick_consumed": False,
            "send_performed": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeCommittedWriterAttemptLink(
        executed=executed,
        receipt=receipt,
    )


__all__ = (
    "PRODUCT_WRITER_ATTEMPT_VERSION",
    "RuntimeCommittedWriterAttemptLink",
    "RuntimeExecutedWriterAttempt",
    "RuntimePreparedWriterAttempt",
    "execute_product_writer_attempt",
    "link_product_writer_attempt_to_committed_action",
    "prepare_product_writer_attempt",
)
