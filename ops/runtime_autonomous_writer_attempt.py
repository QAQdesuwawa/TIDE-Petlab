"""Audited Writer attempt composition for one authorized autonomous tick.

This module adds no autonomous-specific responder or provider lifecycle.  It
only binds the already verified autonomous Packet shadow to the shared
WriterAttempt journal and the shared responder prepare/execute boundaries.
The resulting candidate remains operational evidence, not a conversation fact.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
from typing import Any

from agents.responder import (
    PromptAssembly,
    ResponderAttemptResult,
    execute_prepared_responder_attempt,
    parse_responder,
    prepare_responder_attempt,
)
from framework.model_api import freeze_model_payload, model_data_fingerprint
from framework.model_runtime import BoundInteractionModel
from product_adapters.cx_six_seven_v1.writer_attempts import (
    WriterAttemptIdentity,
    WriterAttemptJournal,
    WriterAttemptJournalError,
)
from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterCard,
    restore_writer_packet,
)
from ops.runtime_autonomous_observation import (
    RuntimeAutonomousObservationTransaction,
)
from ops.runtime_committed_autonomous_grant import (
    RuntimeCommittedAutonomousGrant,
    require_committed_autonomous_grant,
)
from ops.runtime_autonomous_writer_packet_shadow import (
    RuntimeAutonomousWriterPacketShadow,
    build_autonomous_product_writer_packet_shadow,
)
from ops.runtime_product_destination_authorization import (
    RuntimeProductDestinationAuthorization,
)
from ops.writer_packet_shadow import WriterPacketShadow


AUTONOMOUS_PRODUCT_WRITER_ATTEMPT_VERSION = (
    "runtime-autonomous-product-writer-attempt-v1"
)


@dataclass(frozen=True)
class RuntimePreparedAutonomousWriterAttempt:
    """One exact tick-parented Packet and prompt archived before provider I/O."""

    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant
    destination: RuntimeProductDestinationAuthorization
    identity: WriterAttemptIdentity
    autonomous_packet_shadow: RuntimeAutonomousWriterPacketShadow
    assembly: PromptAssembly
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


@dataclass(frozen=True)
class RuntimeExecutedAutonomousWriterAttempt:
    """One autonomous candidate that has no action or send authority."""

    prepared: RuntimePreparedAutonomousWriterAttempt
    result: ResponderAttemptResult
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _archive_packet_assembly_failure(
    *,
    journal: WriterAttemptJournal,
    identity: WriterAttemptIdentity,
    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant,
    destination: RuntimeProductDestinationAuthorization,
    cause: Exception,
) -> None:
    try:
        journal.record_terminal(
            identity.writer_request_id,
            status="failed",
            error={
                "stage": "autonomous_product_writer_packet_assembly",
                "type": type(cause).__name__,
                "message": str(cause),
                "observation_receipt_fingerprint": model_data_fingerprint(
                    observation.receipt),
                "destination_receipt_fingerprint": model_data_fingerprint(
                    destination.receipt),
            },
        )
    except Exception as audit_exc:
        raise WriterAttemptJournalError(
            "autonomous Packet assembly failure could not be archived; "
            f"original={type(cause).__name__}: {cause}"
        ) from audit_exc


def prepare_autonomous_product_writer_attempt(
    *,
    runtime: BoundInteractionModel,
    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant,
    destination: RuntimeProductDestinationAuthorization,
    journal: WriterAttemptJournal,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
    committed_remainders: Mapping[str, object],
    budget_chars: int | None = None,
    source_evidence: Mapping[str, Any] | None = None,
    provider_dispatch_tracked: bool = False,
) -> RuntimePreparedAutonomousWriterAttempt:
    """Allocate and prepare one autonomous attempt without executing Writer."""
    grant = require_committed_autonomous_grant(observation)
    if not isinstance(destination, RuntimeProductDestinationAuthorization):
        raise ValueError(
            "autonomous Writer attempt requires destination authorization")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError(
            "autonomous Writer attempt requires WriterAttemptJournal")
    authorization = grant.authorization
    if authorization is None:
        raise ValueError(
            "autonomous Writer attempt requires eligible authorization")

    identity = journal.allocate_request(
        parent_event_id=authorization.source_event_id)
    try:
        packet_shadow = build_autonomous_product_writer_packet_shadow(
            runtime=runtime,
            observation=observation,
            destination=destination,
            shadow_request_id=identity.writer_request_id,
            cards_by_lane=cards_by_lane,
            committed_remainders=committed_remainders,
        )
    except Exception as exc:
        _archive_packet_assembly_failure(
            journal=journal,
            identity=identity,
            observation=observation,
            destination=destination,
            cause=exc,
        )
        raise

    packet = packet_shadow.packet_shadow.packet
    assembly = prepare_responder_attempt(
        packet,
        journal=journal,
        budget_chars=budget_chars,
        source_evidence=source_evidence,
        provider_dispatch_tracked=provider_dispatch_tracked,
    )
    material = {
        "version": AUTONOMOUS_PRODUCT_WRITER_ATTEMPT_VERSION,
        "status": "prompt_prepared",
        "writer_request_id": identity.writer_request_id,
        "parent_tick_event_id": identity.parent_event_id,
        "attempt_ordinal": identity.attempt_ordinal,
        "display_suffix": identity.display_suffix,
        "observation_receipt_fingerprint": model_data_fingerprint(
            observation.receipt),
        "destination_receipt_fingerprint": model_data_fingerprint(
            destination.receipt),
        "autonomous_packet_shadow_receipt_fingerprint": model_data_fingerprint(
            packet_shadow.receipt),
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
            "source_write": False,
            "route": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimePreparedAutonomousWriterAttempt(
        observation=observation,
        destination=destination,
        identity=identity,
        autonomous_packet_shadow=packet_shadow,
        assembly=assembly,
        receipt=receipt,
    )


def execute_autonomous_product_writer_attempt(
    prepared: RuntimePreparedAutonomousWriterAttempt,
    *,
    journal: WriterAttemptJournal,
    invoke,
    provider_metadata: Mapping[str, Any] | None = None,
) -> RuntimeExecutedAutonomousWriterAttempt:
    """Execute the exact archived autonomous prompt without re-preparing it."""
    if not isinstance(prepared, RuntimePreparedAutonomousWriterAttempt):
        raise ValueError(
            "autonomous Writer execution requires a prepared attempt")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError(
            "autonomous Writer execution requires WriterAttemptJournal")

    packet = prepared.autonomous_packet_shadow.packet_shadow.packet
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
            "executed autonomous attempt lacks one candidate-ready terminal")

    material = {
        "version": AUTONOMOUS_PRODUCT_WRITER_ATTEMPT_VERSION,
        "status": "candidate_ready_uncommitted",
        "writer_request_id": prepared.identity.writer_request_id,
        "parent_tick_event_id": prepared.identity.parent_event_id,
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
            "source_write": False,
            "route": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeExecutedAutonomousWriterAttempt(
        prepared=prepared,
        result=result,
        receipt=receipt,
    )


def restore_candidate_ready_autonomous_writer_attempt(
    *,
    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant,
    destination: RuntimeProductDestinationAuthorization,
    journal: WriterAttemptJournal,
) -> RuntimeExecutedAutonomousWriterAttempt | None:
    """Restore one archived candidate without assembling or calling Writer."""
    grant = require_committed_autonomous_grant(observation)
    if not isinstance(destination, RuntimeProductDestinationAuthorization):
        raise ValueError("candidate recovery requires destination authorization")
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("candidate recovery requires WriterAttemptJournal")
    parent = grant.authorization.source_event_id
    rows = tuple(
        row for row in journal.read_receipts()
        if row.get("parent_event_id") == parent
    )
    request_ids = tuple(dict.fromkeys(
        row["writer_request_id"] for row in rows
        if row.get("stage") == "allocated"
    ))
    candidate_ids = ()
    for request_id in request_ids:
        artifact = journal.read_attempt(request_id)
        terminal = artifact.get("terminal")
        if "commit" in artifact:
            continue
        if isinstance(terminal, Mapping):
            if terminal.get("status") == "candidate_ready":
                candidate_ids = (*candidate_ids, request_id)
            continue
        if "provider" in artifact:
            provider = artifact["provider"]
            raw = provider.get("candidate") if isinstance(provider, Mapping) else None
            if not isinstance(raw, str):
                raise ValueError("archived provider candidate is invalid")
            parsed = parse_responder(raw)
            journal.record_terminal(
                request_id,
                status="candidate_ready",
                provider_result={"parsed": parsed},
            )
            candidate_ids = (*candidate_ids, request_id)
            continue
        raise ValueError(
            "autonomous attempt ended before provider result was archived")
    if not candidate_ids:
        return None
    if len(candidate_ids) != 1:
        raise ValueError("autonomous tick has multiple candidate-ready attempts")

    artifact = journal.read_attempt(candidate_ids[0])
    return _restore_archived_autonomous_candidate(
        artifact=artifact, observation=observation, destination=destination, journal=journal)


def _restore_archived_autonomous_prepared(*, artifact, observation, destination):
    """Common identity and exact-prompt restoration; no model/provider calls."""
    grant = require_committed_autonomous_grant(observation)
    parent = grant.authorization.source_event_id
    packet = restore_writer_packet(
        artifact.get("packet"),
        expected_temporal_position=grant.session.position.position,
    )
    prompt = artifact.get("prompt")
    if not isinstance(prompt, Mapping):
        raise ValueError("candidate-ready archive is incomplete")
    system = prompt.get("system")
    user = prompt.get("user")
    prompt_fingerprint = prompt.get("prompt_fingerprint")
    if (
        not isinstance(system, str)
        or not isinstance(user, str)
        or hashlib.sha256((system + "\0" + user).encode("utf-8")).hexdigest()
        != prompt_fingerprint
    ):
        raise ValueError("archived autonomous prompt fingerprint is invalid")
    identity = WriterAttemptIdentity(
        writer_request_id=artifact["writer_request_id"],
        parent_event_id=artifact["parent_event_id"],
        attempt_ordinal=int(artifact["attempt_ordinal"]),
        display_suffix=artifact["display_suffix"],
    )
    output = packet.output_identity
    if (
        identity.parent_event_id != parent
        or packet.event_id != parent
        or packet.session_id != grant.session.session_binding.session_id
        or packet.runtime_ordinal != grant.session.position.runtime_ordinal
        or packet.conversation_id != destination.conversation_id
        or output.speaker_participant_id != destination.participant_id
        or output.speaker_kind != destination.participant_kind
        or output.audience_participant_ids
        != destination.audience_participant_ids
        or output.addressed_participant_ids
        != destination.addressed_participant_ids
    ):
        raise ValueError("archived autonomous candidate destination is invalid")
    assembly = PromptAssembly(
        system=system,
        user=user,
        budget_chars=len(system) + len(user),
        core_chars=0,
        core_over_budget=False,
        kept_persona_indices=(),
        kept_response_indices=(),
        kept_event_indices=(),
        kept_ledger_indices=(),
        source_packet_fingerprint=packet.packet_fingerprint,
        prompt_fingerprint=prompt_fingerprint,
    )
    archived_assembly = artifact.get("prepared_assembly")
    if archived_assembly is not None:
        metadata_names = set(PromptAssembly.__dataclass_fields__) - {"system", "user"}
        if (not isinstance(archived_assembly, Mapping)
                or archived_assembly.get("version") != "writer-prompt-assembly-metadata-v1"
                or not isinstance(archived_assembly.get("metadata"), Mapping)
                or set(archived_assembly["metadata"]) != metadata_names):
            raise ValueError("archived prompt assembly metadata is invalid")
        metadata = {name: (tuple(value) if isinstance(getattr(assembly, name), tuple) else value)
            for name, value in archived_assembly["metadata"].items()}
        assembly = PromptAssembly(system=system, user=user, **metadata)
        if (assembly.source_packet_fingerprint != packet.packet_fingerprint
                or assembly.prompt_fingerprint != prompt_fingerprint):
            raise ValueError("archived assembly source differs from packet and prompt")
    shadow_material = {
        "version": "runtime-autonomous-writer-packet-recovery-v1",
        "writer_request_id": identity.writer_request_id,
        "packet_fingerprint": packet.packet_fingerprint,
        "authority": {"provider_call": False, "action_commit": False},
    }
    shadow_receipt = dict(
        shadow_material, fingerprint=model_data_fingerprint(shadow_material))
    packet_shadow = RuntimeAutonomousWriterPacketShadow(
        observation=observation,
        destination=destination,
        destination_expression=None,
        packet_shadow=WriterPacketShadow(
            packet=packet, receipt=shadow_receipt),
        receipt=shadow_receipt,
    )
    prepared_material = {
        "version": AUTONOMOUS_PRODUCT_WRITER_ATTEMPT_VERSION,
        "status": "prompt_recovered",
        "writer_request_id": identity.writer_request_id,
        "parent_tick_event_id": identity.parent_event_id,
        "packet_fingerprint": packet.packet_fingerprint,
        "prompt_fingerprint": prompt_fingerprint,
        "authority": {"provider_call": False, "action_commit": False},
    }
    prepared_receipt = dict(
        prepared_material,
        fingerprint=model_data_fingerprint(prepared_material),
    )
    prepared = RuntimePreparedAutonomousWriterAttempt(
        observation=observation,
        destination=destination,
        identity=identity,
        autonomous_packet_shadow=packet_shadow,
        assembly=assembly,
        receipt=prepared_receipt,
    )
    return prepared


def _restore_archived_autonomous_candidate(*, artifact, observation, destination, journal):
    prepared = _restore_archived_autonomous_prepared(
        artifact=artifact, observation=observation, destination=destination)
    provider, terminal = artifact.get("provider"), artifact.get("terminal")
    if not isinstance(provider, Mapping) or not isinstance(terminal, Mapping):
        raise ValueError("candidate-ready archive is incomplete")
    raw_candidate = provider.get("candidate")
    parsed = (terminal.get("provider_result") or {}).get("parsed")
    if (not isinstance(raw_candidate, str) or not isinstance(parsed, Mapping)
            or parse_responder(raw_candidate) != parsed):
        raise ValueError("archived autonomous candidate parse is invalid")
    assembly, identity = prepared.assembly, prepared.identity
    packet = prepared.autonomous_packet_shadow.packet_shadow.packet
    prepared_receipt = prepared.receipt
    prompt_fingerprint = assembly.prompt_fingerprint
    result = ResponderAttemptResult(
        assembly=assembly,
        raw_candidate=raw_candidate,
        parsed=dict(parsed),
    )
    executed_material = {
        "version": AUTONOMOUS_PRODUCT_WRITER_ATTEMPT_VERSION,
        "status": "candidate_ready_uncommitted",
        "writer_request_id": identity.writer_request_id,
        "parent_tick_event_id": identity.parent_event_id,
        "prepared_attempt_receipt_fingerprint": model_data_fingerprint(
            prepared_receipt),
        "packet_fingerprint": packet.packet_fingerprint,
        "prompt_fingerprint": prompt_fingerprint,
        "raw_candidate_fingerprint": model_data_fingerprint({
            "candidate": raw_candidate,
        }),
        "parsed_candidate_fingerprint": model_data_fingerprint(parsed),
        "terminal_receipt_fingerprint": model_data_fingerprint(tuple(
            row for row in journal.read_receipts()
            if row.get("writer_request_id") == identity.writer_request_id
            and row.get("stage") == "terminal"
        )[0]),
        "authority": {
            "action": False,
            "source_write": False,
            "route": False,
            "send": False,
        },
    }
    executed_receipt = dict(
        executed_material,
        fingerprint=model_data_fingerprint(executed_material),
    )
    return RuntimeExecutedAutonomousWriterAttempt(
        prepared=prepared,
        result=result,
        receipt=executed_receipt,
    )


def restore_prepared_autonomous_writer_attempt(*, observation, destination, journal):
    """Resume only tracked attempts known not to have entered provider I/O."""
    grant = require_committed_autonomous_grant(observation)
    rows = tuple(row for row in journal.read_receipts()
        if row.get("parent_event_id") == grant.authorization.source_event_id
        and row.get("stage") == "allocated")
    pending = ()
    candidate_present = False
    for row in rows:
        artifact = journal.read_attempt(row["writer_request_id"])
        if "commit" in artifact:
            continue
        if "provider" in artifact:
            candidate_present = True
            continue
        if "provider_dispatch" in artifact:
            raise ValueError("autonomous provider dispatch has no archived response")
        if "terminal" in artifact:
            if artifact.get("provider_dispatch_tracked") is True:
                raise ValueError("tracked autonomous attempt terminated without a candidate")
            continue
        if artifact.get("provider_dispatch_tracked") is not True:
            raise ValueError("autonomous attempt ended before provider result was archived")
        pending = (*pending, artifact)
    if not pending:
        return None
    if len(pending) != 1:
        raise ValueError("autonomous tick has multiple prepared attempts")
    if candidate_present:
        raise ValueError("autonomous tick has mixed prepared and candidate attempts")
    artifact = pending[0]
    inline = artifact.get("prepared_prompt")
    prompt = artifact.get("prompt")
    if prompt is not None and inline is not None and prompt != inline:
        raise ValueError("archived packet prompt differs from prompt stage")
    if prompt is None:
        if not isinstance(inline, Mapping):
            raise ValueError("tracked packet has no frozen prompt")
        artifact = dict(artifact, prompt=inline)
    prepared = _restore_archived_autonomous_prepared(
        artifact=artifact, observation=observation, destination=destination)
    if prompt is None:
        journal.record_prompt(prepared.identity.writer_request_id,
            system=prepared.assembly.system, user=prepared.assembly.user,
            prompt_fingerprint=prepared.assembly.prompt_fingerprint)
    return prepared


__all__ = (
    "AUTONOMOUS_PRODUCT_WRITER_ATTEMPT_VERSION",
    "RuntimeExecutedAutonomousWriterAttempt",
    "RuntimePreparedAutonomousWriterAttempt",
    "execute_autonomous_product_writer_attempt",
    "prepare_autonomous_product_writer_attempt",
    "restore_candidate_ready_autonomous_writer_attempt",
    "restore_prepared_autonomous_writer_attempt",
)
