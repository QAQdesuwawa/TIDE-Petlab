"""Read-only WriterPacket shadow for one authorized autonomous tick.

The packet has no fabricated current-input card.  Its expression resources are
reprojected from the exact state settlement already used by the autonomous
assessment, but under the explicit conversation destination authorized later.
No attempt, provider, action, source fact, route, ingress, or send occurs here.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from product_adapters.cx_six_seven_v1.expression_resources import (
    read_expression_resource_batch,
)
from framework.model_runtime import BoundInteractionModel
from product_adapters.cx_six_seven_v1.realizer_packet import (
    WriterCard,
    WriterOutputIdentity,
)
from ops.model_expression_projection_shadow import ExpressionProjectionShadow
from ops.runtime_autonomous_observation import (
    RuntimeAutonomousObservationTransaction,
)
from ops.runtime_committed_autonomous_grant import (
    RuntimeCommittedAutonomousGrant,
    require_committed_autonomous_grant,
)
from ops.runtime_product_destination_authorization import (
    RuntimeProductDestinationAuthorization,
)
from ops.runtime_stage_verification import verify_runtime_stage_execution
from ops.writer_packet_shadow import WriterPacketShadow, build_writer_packet_shadow


AUTONOMOUS_WRITER_PACKET_SHADOW_VERSION = (
    "runtime-autonomous-writer-packet-shadow-v1"
)
AUTONOMOUS_DESTINATION_EXPRESSION_VERSION = (
    "runtime-autonomous-destination-expression-v1"
)


@dataclass(frozen=True)
class RuntimeAutonomousWriterPacketShadow:
    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant
    destination: RuntimeProductDestinationAuthorization
    destination_expression: ExpressionProjectionShadow
    packet_shadow: WriterPacketShadow
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _verify_receipt(receipt: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise ValueError(f"{label} receipt must be a mapping")
    material = thaw_model_data(receipt)
    fingerprint = material.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(material):
        raise ValueError(f"{label} receipt fingerprint is invalid")
    return material


def _verify_lineage(
    *,
    runtime: BoundInteractionModel,
    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant,
    destination: RuntimeProductDestinationAuthorization,
) -> None:
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("autonomous Packet requires a bound model runtime")
    grant = require_committed_autonomous_grant(observation)
    if not isinstance(destination, RuntimeProductDestinationAuthorization):
        raise ValueError("autonomous Packet requires destination authorization")
    observation_receipt = _verify_receipt(
        grant.receipt, "autonomous observation")
    destination_receipt = _verify_receipt(
        destination.receipt, "autonomous destination")
    authorization = grant.authorization
    if authorization is None or not grant.assessment.eligible:
        raise ValueError("autonomous observation has no eligible authorization")
    binding = grant.session.session_binding
    # RuntimeSessionBinding.verify_runtimes also requires the temporal runtime;
    # here the model half is checked directly because this read-only bridge does
    # not own or invoke a TemporalDomain.
    if runtime.session_id != binding.session_id or runtime.binding != binding.model_binding:
        raise ValueError("autonomous Packet model runtime differs from observation")
    current = grant.session.position
    if (
        destination.authorization.fingerprint != authorization.fingerprint
        or destination.participant_id != authorization.participant_id
        or destination_receipt.get("authorization_fingerprint")
        != authorization.fingerprint
        or destination_receipt.get("source_event_id")
        != authorization.source_event_id
        or destination_receipt.get("source_position_fingerprint")
        != current.position.fingerprint
        or destination_receipt.get("source_runtime_ordinal")
        != current.runtime_ordinal
        or observation_receipt.get("authorization_fingerprint")
        != authorization.fingerprint
        or observation_receipt.get("settled_position_fingerprint")
        != current.position.fingerprint
        or observation_receipt.get("settled_runtime_ordinal")
        != current.runtime_ordinal
    ):
        raise ValueError(
            "autonomous destination differs from observation authorization")


def _destination_expression(
    *,
    runtime: BoundInteractionModel,
    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant,
    destination: RuntimeProductDestinationAuthorization,
    committed_remainders: Mapping[str, object],
) -> ExpressionProjectionShadow:
    if not isinstance(committed_remainders, Mapping):
        raise ValueError("committed_remainders must be a mapping")
    frozen_remainders = freeze_model_payload(committed_remainders)
    grant = require_committed_autonomous_grant(observation)
    session = grant.session
    current = session.position
    authorization = grant.authorization
    context = ModelInvocationContextV2(
        session_id=session.session_binding.session_id,
        scope_ref=destination.conversation_id,
        subject_ref=destination.participant_id,
        event_ref=authorization.source_event_id,
        action_ref=None,
        temporal_position=current.position,
        runtime_ordinal=current.runtime_ordinal,
        metadata={
            "committed_event_kind": "tick",
            "destination_authorization_fingerprint": model_data_fingerprint(
                destination.receipt),
        },
    )
    settlement = grant.settlement_execution.payload.get("settlement")
    shared_input = {
        "parameters": session.parameters.parameters,
        "settlement": settlement,
    }
    shared = runtime.invoke(
        "project_shared_semantics", payload=shared_input, context=context)
    verify_runtime_stage_execution(
        shared,
        stage="project_shared_semantics",
        runtime=runtime,
        context=context,
        invocation_payload=shared_input,
    )
    if (
        shared.receipt.output_fingerprint
        != grant.shared_projection_execution.receipt.output_fingerprint
    ):
        raise ValueError(
            "destination shared projection differs from autonomous assessment snapshot")
    shared_projection = shared.payload.get("shared_projection")
    if not isinstance(shared_projection, Mapping):
        raise ValueError("destination shared projection output is missing")
    resource_input = {
        "shared_projection": shared_projection,
        "source_shared_projection_output_fingerprint": (
            shared.receipt.output_fingerprint),
        "committed_remainders": frozen_remainders,
    }
    resources = runtime.invoke(
        "project_semantic_resources", payload=resource_input, context=context)
    verify_runtime_stage_execution(
        resources,
        stage="project_semantic_resources",
        runtime=runtime,
        context=context,
        invocation_payload=resource_input,
    )
    batch = read_expression_resource_batch(
        resources,
        expected_context=context,
        shared_projection_execution=shared,
    )
    receipt = freeze_model_payload({
        "version": AUTONOMOUS_DESTINATION_EXPRESSION_VERSION,
        "mode": "read_only_destination_projection",
        "observation_receipt_fingerprint": model_data_fingerprint(
            grant.receipt),
        "destination_receipt_fingerprint": model_data_fingerprint(
            destination.receipt),
        "context": context.to_record(),
        "actor_local_shared_projection_output_fingerprint": (
            grant.shared_projection_execution.receipt.output_fingerprint),
        "destination_shared_projection_receipt": shared.receipt.to_record(),
        "semantic_resource_receipt": resources.receipt.to_record(),
        "selected_resource_ids": list(batch.selected_resource_ids),
        "authority": {
            "state_write": False,
            "writer_attempt": False,
            "provider_call": False,
            "action_commit": False,
            "send": False,
        },
    })
    return ExpressionProjectionShadow(
        context=context,
        shared_projection_execution=shared,
        semantic_resource_execution=resources,
        batch=batch,
        receipt=receipt,
    )


def build_autonomous_product_writer_packet_shadow(
    *,
    runtime: BoundInteractionModel,
    observation: RuntimeAutonomousObservationTransaction | RuntimeCommittedAutonomousGrant,
    destination: RuntimeProductDestinationAuthorization,
    shadow_request_id: str,
    cards_by_lane: Mapping[str, Sequence[WriterCard]],
    committed_remainders: Mapping[str, object],
) -> RuntimeAutonomousWriterPacketShadow:
    """Build one tick-parented Packet without allocating a Writer attempt."""
    _verify_lineage(
        runtime=runtime, observation=observation, destination=destination)
    if not isinstance(cards_by_lane, Mapping):
        raise ValueError("cards_by_lane must be a mapping")
    current_input = cards_by_lane.get("current_input", ())
    if current_input:
        raise ValueError("autonomous Packet current input lane must be empty")
    expression = _destination_expression(
        runtime=runtime,
        observation=observation,
        destination=destination,
        committed_remainders=committed_remainders,
    )
    output_identity = WriterOutputIdentity(
        speaker_participant_id=destination.participant_id,
        speaker_kind=destination.participant_kind,
        audience_participant_ids=destination.audience_participant_ids,
        addressed_participant_ids=destination.addressed_participant_ids,
    )
    packet_shadow = build_writer_packet_shadow(
        context=expression.context,
        shadow_request_id=shadow_request_id,
        shared_projection_execution=expression.shared_projection_execution,
        semantic_resource_execution=expression.semantic_resource_execution,
        output_identity=output_identity,
        cards_by_lane=cards_by_lane,
    )
    packet = packet_shadow.packet
    material = {
        "version": AUTONOMOUS_WRITER_PACKET_SHADOW_VERSION,
        "mode": "read_only_shadow",
        "session_id": packet.session_id,
        "conversation_id": packet.conversation_id,
        "participant_id": destination.participant_id,
        "parent_tick_event_id": packet.event_id,
        "current_input_card_count": len(packet.sections["current_input"]),
        "observation_receipt_fingerprint": model_data_fingerprint(
            require_committed_autonomous_grant(observation).receipt),
        "destination_receipt_fingerprint": model_data_fingerprint(
            destination.receipt),
        "destination_expression_receipt_fingerprint": model_data_fingerprint(
            expression.receipt),
        "packet_shadow_receipt_fingerprint": model_data_fingerprint(
            packet_shadow.receipt),
        "packet_fingerprint": packet.packet_fingerprint,
        "authority": {
            "writer_attempt": False,
            "provider_call": False,
            "candidate": False,
            "action_commit": False,
            "source_write": False,
            "route": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeAutonomousWriterPacketShadow(
        observation=observation,
        destination=destination,
        destination_expression=expression,
        packet_shadow=packet_shadow,
        receipt=receipt,
    )


__all__ = (
    "AUTONOMOUS_DESTINATION_EXPRESSION_VERSION",
    "AUTONOMOUS_WRITER_PACKET_SHADOW_VERSION",
    "RuntimeAutonomousWriterPacketShadow",
    "build_autonomous_product_writer_packet_shadow",
)
