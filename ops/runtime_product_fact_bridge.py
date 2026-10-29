"""Verify committed product fact receipts before a formal runtime transaction.

This is an application composition seam for the current wall-clock product.
It does not inspect dialogue text, call a provider, run dynamic tick, select a
model package, or write Store.  The caller must provide model-owned measurement
evidence derived from the already committed semantic projection.
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
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import RuntimeSessionBinding
from framework.runtime_variant import RuntimeVariantBinding
from framework.temporal_runtime import BoundTemporalDomain
from interaction_extensions.multi_participant.events import (
    CommittedConversationEvent,
)
from interaction_extensions.multi_participant.local_projection import LocalProjection


PRODUCT_FACT_BRIDGE_VERSION = "runtime-product-fact-bridge-v1"
MEASUREMENT_EVIDENCE_VERSION = "committed-model-measurement-evidence-v1"
OUTPUT_SEMANTIC_RECEIPT_VERSION = "committed-output-semantic-receipt-v1"
WALL_CLOCK_DOMAIN_ID = "tide.wall-clock-ms"
ROUTED_INPUT_FACT_BRIDGE_VERSION = "runtime-routed-input-fact-bridge-v1"
DIRECT_INPUT_FACT_BRIDGE_VERSION = "runtime-direct-input-fact-bridge-v1"


def _record(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be committed record data")
    return value


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be an exact non-empty identifier")
    return value


def _time(value: object, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be an exact non-negative wall-clock time")
    return value


@dataclass(frozen=True)
class RuntimeCommittedFactEvidence:
    """One verified, transient fact input to the committed-fact host."""

    target_coordinate: object
    conversation_id: str
    participant_id: str
    event_id: str
    action_id: str | None
    event_kind: str
    response_policy: str | None
    expected_runtime_ordinal: int | None
    measurement_payload: Mapping[str, Any]
    context_metadata: Mapping[str, Any]
    model_binding: Mapping[str, Any]
    temporal_binding: Mapping[str, Any]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        if self.event_kind in {
            "human_input", "peer_input", "simulation_input",
        }:
            if (
                self.response_policy not in {"required", "eligible"}
                or self.action_id is not None
            ):
                raise ValueError("verified input fact policy or action is invalid")
        elif self.event_kind == "npc_output":
            if self.response_policy is not None:
                raise ValueError("verified output fact cannot carry input policy")
            _identifier(self.action_id, "verified output action ID")
        else:
            raise ValueError("verified fact event kind is invalid")
        if (
            self.expected_runtime_ordinal is not None
            and (
                isinstance(self.expected_runtime_ordinal, bool)
                or not isinstance(self.expected_runtime_ordinal, int)
                or self.expected_runtime_ordinal < 0
            )
        ):
            raise ValueError("verified fact expected runtime ordinal is invalid")
        object.__setattr__(
            self, "measurement_payload",
            freeze_model_payload(self.measurement_payload),
        )
        object.__setattr__(
            self, "context_metadata",
            freeze_model_payload(self.context_metadata),
        )
        object.__setattr__(
            self, "model_binding", freeze_model_payload(self.model_binding),
        )
        object.__setattr__(
            self, "temporal_binding", freeze_model_payload(self.temporal_binding),
        )
        object.__setattr__(
            self, "receipt", freeze_model_payload(self.receipt),
        )

    def verify_for(
        self, *,
        model_runtime: BoundInteractionModel,
        temporal_runtime: BoundTemporalDomain,
    ) -> None:
        if not isinstance(model_runtime, BoundInteractionModel):
            raise ValueError("verified fact requires a registry-bound model runtime")
        if not isinstance(temporal_runtime, BoundTemporalDomain):
            raise ValueError("verified fact requires a registry-bound temporal runtime")
        model_binding = model_runtime.binding.to_record()
        temporal_binding = temporal_runtime.binding.to_record()
        if thaw_model_data(self.model_binding) != model_binding:
            raise ValueError("verified fact model binding differs from runtime")
        if thaw_model_data(self.temporal_binding) != temporal_binding:
            raise ValueError("verified fact temporal binding differs from runtime")
        receipt = thaw_model_data(self.receipt)
        fingerprint = receipt.pop("fingerprint", None)
        if model_data_fingerprint(receipt) != fingerprint:
            raise ValueError("verified fact receipt fingerprint is invalid")
        if (
            receipt.get("version") not in {
                PRODUCT_FACT_BRIDGE_VERSION,
                ROUTED_INPUT_FACT_BRIDGE_VERSION,
                DIRECT_INPUT_FACT_BRIDGE_VERSION,
            }
            or receipt.get("event_id") != self.event_id
            or receipt.get("conversation_id") != self.conversation_id
            or receipt.get("participant_id") != self.participant_id
            or receipt.get("event_kind") != self.event_kind
            or receipt.get("action_id") != self.action_id
            or receipt.get("response_policy") != self.response_policy
            or receipt.get("expected_runtime_ordinal")
            != self.expected_runtime_ordinal
            or receipt.get("target_coordinate") != self.target_coordinate
            or receipt.get("model_binding") != model_binding
            or receipt.get("temporal_binding") != temporal_binding
        ):
            raise ValueError("verified fact receipt differs from transaction input")
        context = thaw_model_data(self.context_metadata)
        if (
            context.get("committed_event_kind") != self.event_kind
            or context.get("response_policy") != self.response_policy
            or context.get("expected_runtime_ordinal")
            != self.expected_runtime_ordinal
        ):
            raise ValueError("verified fact context identity differs from evidence")


def _verify_fingerprinted_record(
    value: Mapping[str, Any], label: str,
) -> dict[str, Any]:
    record = dict(_record(value, label))
    fingerprint = record.pop("fingerprint", None)
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError(f"{label} fingerprint is invalid")
    return record


def _verify_measurement_evidence(
    *,
    measurement_evidence: Mapping[str, Any],
    source_event_id: str,
    local_event_id: str,
    event_kind: str,
    projection: Mapping[str, Any],
    model_runtime: BoundInteractionModel,
) -> tuple[Mapping[str, Any], str]:
    measurement = _verify_fingerprinted_record(
        measurement_evidence, "committed measurement evidence")
    fingerprint = model_data_fingerprint(measurement)
    required = {
        "version", "model_binding", "source_event_id", "local_event_id",
        "event_kind", "projection_fingerprint", "measurement_payload",
    }
    if set(measurement) != required:
        raise ValueError("committed measurement evidence fields are invalid")
    if measurement["version"] != MEASUREMENT_EVIDENCE_VERSION:
        raise ValueError("committed measurement evidence version is unsupported")
    if measurement["model_binding"] != model_runtime.binding.to_record():
        raise ValueError(
            "committed measurement evidence model binding differs from runtime")
    if (
        measurement["source_event_id"] != source_event_id
        or measurement["local_event_id"] != local_event_id
        or measurement["event_kind"] != event_kind
    ):
        raise ValueError("committed measurement evidence identifies another event")
    if measurement["projection_fingerprint"] != model_data_fingerprint(
        projection
    ):
        raise ValueError("committed measurement evidence projection was changed")
    payload = _record(
        measurement["measurement_payload"], "model measurement payload")
    return payload, fingerprint


def _verify_product_input(
    *,
    semantic_receipt: Mapping[str, Any],
    f03_receipt: Mapping[str, Any],
    delivery_receipt: Mapping[str, Any],
    input_message: Mapping[str, Any],
) -> dict[str, Any]:
    semantic = _record(semantic_receipt, "semantic receipt")
    f03 = _record(f03_receipt, "F03 receipt")
    delivery = _record(delivery_receipt, "delivery receipt")
    message = _record(input_message, "input message")
    if (
        semantic.get("schema_version") != 1
        or semantic.get("kind") != "semantic_projected"
        or f03.get("schema_version") != 1
        or f03.get("kind") != "input_measurement_committed"
        or delivery.get("schema_version") != 1
        or delivery.get("kind") != "conversation_delivery_committed"
        or message.get("role") != "user"
    ):
        raise ValueError("product input requires committed semantic/F03/delivery/message evidence")
    projection = _record(semantic.get("projection"), "semantic projection")
    turn_id = _identifier(semantic.get("turn_id"), "semantic local turn ID")
    at_ms = _time(semantic.get("ts"), "semantic committed time")
    if (
        f03.get("turn_id") != turn_id
        or delivery.get("turn_id") != turn_id
        or message.get("turn_id") != turn_id
        or _time(f03.get("ts"), "F03 committed time") != at_ms
        or _time(delivery.get("ts"), "delivery committed time") != at_ms
        or _time(message.get("ts"), "message committed time") != at_ms
        or not semantic.get("params_sha")
        or semantic.get("params_sha") != f03.get("params_sha")
        or semantic.get("params_sha") != delivery.get("params_sha")
    ):
        raise ValueError("semantic measurement differs from committed product turn")
    reference = _record(f03.get("delivery_reference"), "F03 delivery reference")
    if delivery.get("delivery_reference") != reference:
        raise ValueError("F03 and delivery reference different source facts")
    response_policy = str(delivery.get("response_policy") or "").strip().lower()
    if response_policy not in {"required", "eligible"}:
        raise ValueError("delivery response policy is missing or invalid")
    if f03.get("response_policy") != response_policy:
        raise ValueError("F03 and delivery response policy differ")
    provenance = _record(
        message.get("conversation_provenance"), "input conversation provenance")
    if any(provenance.get(key) != value for key, value in reference.items()):
        raise ValueError("input message and delivery reference different source facts")
    participant_id = _identifier(
        delivery.get("recipient_participant_id"), "delivery recipient")
    if (
        provenance.get("recipient_participant_id") != participant_id
        or participant_id not in (provenance.get("visible_recipient_ids") or ())
    ):
        raise ValueError("input recipient differs from committed delivery")
    source_kind = _identifier(
        delivery.get("sender_participant_kind"), "sender participant kind")
    event_kind = {
        "human": "human_input",
        "npc": "peer_input",
        "simulation": "simulation_input",
    }.get(source_kind)
    anchor = _record(f03.get("expression_anchor"), "F03 event anchor")
    if (
        event_kind is None
        or anchor.get("event_kind") != event_kind
        or f03.get("participant_source_kind") != source_kind
        or message.get("participant_source_kind") != source_kind
    ):
        raise ValueError("input event kind differs from committed participant source")
    return {
        "conversation_id": _identifier(
            reference.get("conversation_id"), "source conversation"),
        "participant_id": participant_id,
        "event_id": _identifier(reference.get("event_id"), "source event ID"),
        "local_event_id": turn_id,
        "event_kind": event_kind,
        "response_policy": response_policy,
        "at_ms": at_ms,
        "projection": dict(projection),
        "delivery_reference": dict(reference),
        "source_receipt_fingerprints": {
            "semantic": model_data_fingerprint(semantic),
            "f03": model_data_fingerprint(f03),
            "delivery": model_data_fingerprint(delivery),
            "message": model_data_fingerprint(message),
        },
    }


def build_committed_input_fact_evidence(
    *,
    semantic_receipt: Mapping[str, Any],
    f03_receipt: Mapping[str, Any],
    delivery_receipt: Mapping[str, Any],
    input_message: Mapping[str, Any],
    measurement_evidence: Mapping[str, Any],
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    runtime_variant: RuntimeVariantBinding,
) -> RuntimeCommittedFactEvidence:
    """Bind four exact product receipts to one package-owned measurement."""
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("product fact bridge requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("product fact bridge requires a registry-bound temporal runtime")
    if not isinstance(runtime_variant, RuntimeVariantBinding):
        raise ValueError(
            "product fact bridge requires a RuntimeVariantBinding")
    runtime_variant.verify_for(
        model_binding=model_runtime.binding,
        temporal_binding=temporal_runtime.binding,
    )
    if temporal_runtime.binding.domain_id != WALL_CLOCK_DOMAIN_ID:
        raise ValueError("current product receipts require the wall-clock temporal domain")
    source = _verify_product_input(
        semantic_receipt=semantic_receipt,
        f03_receipt=f03_receipt,
        delivery_receipt=delivery_receipt,
        input_message=input_message,
    )
    payload, fingerprint = _verify_measurement_evidence(
        measurement_evidence=measurement_evidence,
        source_event_id=source["event_id"],
        local_event_id=source["local_event_id"],
        event_kind=source["event_kind"],
        projection=source["projection"],
        model_runtime=model_runtime,
    )
    model_binding = model_runtime.binding.to_record()
    temporal_binding = temporal_runtime.binding.to_record()
    context_metadata = {
        "committed_event_kind": source["event_kind"],
        "response_policy": source["response_policy"],
        "local_turn_id": source["local_event_id"],
        "delivery_reference": source["delivery_reference"],
        "product_evidence_fingerprint": model_data_fingerprint({
            "source_receipt_fingerprints": source["source_receipt_fingerprints"],
            "measurement_evidence_fingerprint": fingerprint,
        }),
    }
    material = {
        "version": PRODUCT_FACT_BRIDGE_VERSION,
        "conversation_id": source["conversation_id"],
        "participant_id": source["participant_id"],
        "event_id": source["event_id"],
        "target_coordinate": source["at_ms"],
        "event_kind": source["event_kind"],
        "response_policy": source["response_policy"],
        "expected_runtime_ordinal": None,
        "local_event_id": source["local_event_id"],
        "source_receipt_fingerprints": source["source_receipt_fingerprints"],
        "measurement_evidence_fingerprint": fingerprint,
        "measurement_payload_fingerprint": model_data_fingerprint(payload),
        "model_binding": model_binding,
        "temporal_binding": temporal_binding,
        "authority": {
            "provider_call": False,
            "dynamic_tick": False,
            "writer": False,
            "send": False,
            "default_path_switch": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeCommittedFactEvidence(
        target_coordinate=source["at_ms"],
        conversation_id=source["conversation_id"],
        participant_id=source["participant_id"],
        event_id=source["event_id"],
        action_id=None,
        event_kind=source["event_kind"],
        response_policy=source["response_policy"],
        expected_runtime_ordinal=None,
        measurement_payload=payload,
        context_metadata=context_metadata,
        model_binding=model_binding,
        temporal_binding=temporal_binding,
        receipt=receipt,
    )


def build_committed_direct_input_fact_evidence(
    *,
    source_event: CommittedConversationEvent,
    source_receipt: Mapping[str, Any],
    sender_participant_kind: str,
    recipient_participant_id: str,
    semantic_projection: Mapping[str, Any],
    measurement_evidence: Mapping[str, Any],
    expected_runtime_ordinal: int,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    runtime_variant: RuntimeVariantBinding,
) -> RuntimeCommittedFactEvidence:
    """Bind one canonical human direct source fact to a formal required fact.

    The caller obtains participant kind and recipient identity from the exact
    registered direct session.  This bridge deliberately consumes neither a
    legacy F03/message row nor an NPC output route.
    """
    if not isinstance(source_event, CommittedConversationEvent):
        raise ValueError("direct input requires a committed source event")
    if source_event.kind != "message" or source_event.sender_action_id is not None:
        raise ValueError("direct human input cannot carry an NPC action")
    if _identifier(sender_participant_kind, "direct input sender kind") != "human":
        raise ValueError("direct required input sender must be human")
    recipient = _identifier(
        recipient_participant_id, "direct input recipient participant")
    if (
        tuple(source_event.addressed_participant_ids) != (recipient,)
        or recipient not in source_event.visible_recipient_ids
        or recipient == source_event.sender_participant_id
    ):
        raise ValueError("direct input recipient differs from committed address")
    if not isinstance(source_receipt, Mapping):
        raise ValueError("direct input source receipt must be committed record data")
    expected_source_receipt = {
        "schema_version": 1,
        "kind": "conversation_event_committed",
        "event_id": source_event.event_id,
        "conversation_id": source_event.conversation_id,
        "membership_version": source_event.membership_version,
        "event_kind": source_event.kind,
        "sender_action_id": None,
        "source_event": source_event.as_record(),
    }
    if dict(source_receipt) != expected_source_receipt:
        raise ValueError("direct input source receipt differs from committed event")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("direct input requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("direct input requires a registry-bound temporal runtime")
    if not isinstance(runtime_variant, RuntimeVariantBinding):
        raise ValueError("direct input requires a RuntimeVariantBinding")
    runtime_variant.verify_for(
        model_binding=model_runtime.binding,
        temporal_binding=temporal_runtime.binding,
    )
    if temporal_runtime.binding.domain_id != WALL_CLOCK_DOMAIN_ID:
        raise ValueError("current direct product input requires wall-clock time")
    if (
        isinstance(expected_runtime_ordinal, bool)
        or not isinstance(expected_runtime_ordinal, int)
        or expected_runtime_ordinal < 0
    ):
        raise ValueError("direct input expected runtime ordinal is invalid")

    projection = _record(
        semantic_projection, "direct input semantic projection")
    local_event_id = f"ingress:{source_event.event_id}"
    payload, measurement_fingerprint = _verify_measurement_evidence(
        measurement_evidence=measurement_evidence,
        source_event_id=source_event.event_id,
        local_event_id=local_event_id,
        event_kind="human_input",
        projection=projection,
        model_runtime=model_runtime,
    )
    model_binding = model_runtime.binding.to_record()
    temporal_binding = temporal_runtime.binding.to_record()
    source_fingerprint = model_data_fingerprint(source_receipt)
    projection_fingerprint = model_data_fingerprint(projection)
    context_metadata = {
        "committed_event_kind": "human_input",
        "response_policy": "required",
        "expected_runtime_ordinal": expected_runtime_ordinal,
        "local_turn_id": local_event_id,
        "delivery_reference": {
            "event_id": source_event.event_id,
            "conversation_id": source_event.conversation_id,
            "sender_participant_id": source_event.sender_participant_id,
            "recipient_participant_id": recipient,
            "membership_version": source_event.membership_version,
        },
        "source_receipt_fingerprint": source_fingerprint,
        "runtime_variant_fingerprint": runtime_variant.fingerprint,
    }
    material = {
        "version": DIRECT_INPUT_FACT_BRIDGE_VERSION,
        "conversation_id": source_event.conversation_id,
        "participant_id": recipient,
        "event_id": source_event.event_id,
        "target_coordinate": source_event.committed_at_ms,
        "event_kind": "human_input",
        "response_policy": "required",
        "action_id": None,
        "expected_runtime_ordinal": expected_runtime_ordinal,
        "local_event_id": local_event_id,
        "source_receipt_fingerprint": source_fingerprint,
        "semantic_projection_fingerprint": projection_fingerprint,
        "measurement_evidence_fingerprint": measurement_fingerprint,
        "measurement_payload_fingerprint": model_data_fingerprint(payload),
        "model_binding": model_binding,
        "temporal_binding": temporal_binding,
        "runtime_variant_fingerprint": runtime_variant.fingerprint,
        "authority": {
            "provider_call": False,
            "writer": False,
            "dynamic_tick": False,
            "route": False,
            "send": False,
            "legacy_receipts": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeCommittedFactEvidence(
        target_coordinate=source_event.committed_at_ms,
        conversation_id=source_event.conversation_id,
        participant_id=recipient,
        event_id=source_event.event_id,
        action_id=None,
        event_kind="human_input",
        response_policy="required",
        expected_runtime_ordinal=expected_runtime_ordinal,
        measurement_payload=payload,
        context_metadata=context_metadata,
        model_binding=model_binding,
        temporal_binding=temporal_binding,
        receipt=receipt,
    )


def build_routed_input_fact_evidence(
    *,
    projection: LocalProjection,
    route_receipt: Mapping[str, Any],
    semantic_projection: Mapping[str, Any],
    measurement_evidence: Mapping[str, Any],
    sender_participant_kind: str,
    expected_runtime_ordinal: int,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    runtime_variant: RuntimeVariantBinding,
) -> RuntimeCommittedFactEvidence:
    """Bind one committed output route directly to recipient-local F03 input.

    This bridge does not recreate the legacy Runner semantic/F03/message rows.
    The route already owns source text and delivery identity; the model-owned
    measurement evidence owns the semantic projection.  The result is a
    transient formal fact for the exact recipient session.
    """
    if not isinstance(projection, LocalProjection):
        raise ValueError("routed input requires a LocalProjection")
    if projection.delivery_scope != "addressed":
        raise ValueError("only addressed delivery can become a formal input fact")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("routed input requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("routed input requires a registry-bound temporal runtime")
    if not isinstance(runtime_variant, RuntimeVariantBinding):
        raise ValueError("routed input requires a RuntimeVariantBinding")
    runtime_variant.verify_for(
        model_binding=model_runtime.binding,
        temporal_binding=temporal_runtime.binding,
    )
    if temporal_runtime.binding.domain_id != WALL_CLOCK_DOMAIN_ID:
        raise ValueError(
            "current product routes require the wall-clock temporal domain")
    if (
        isinstance(expected_runtime_ordinal, bool)
        or not isinstance(expected_runtime_ordinal, int)
        or expected_runtime_ordinal < 0
    ):
        raise ValueError("routed input expected runtime ordinal is invalid")

    route = _verify_fingerprinted_record(
        route_receipt, "committed output route receipt")
    delivery_record = {
        "schema_version": 1,
        "delivery_id": projection.delivery_id,
        "source_event_id": projection.source_event_id,
        "conversation_id": projection.conversation_id,
        "membership_version": projection.membership_version,
        "recipient_participant_id": projection.recipient_participant_id,
        "sender_participant_id": projection.sender_participant_id,
        "event_kind": projection.event_kind,
        "delivery_scope": projection.delivery_scope,
    }
    if (
        route.get("version") != "runtime-product-output-route-v1"
        or route.get("status") != "canonical_output_route_committed"
        or route.get("conversation_id") != projection.conversation_id
        or route.get("event_id") != projection.source_event_id
        or route.get("sender_participant_id")
        != projection.sender_participant_id
        or route.get("membership_version") != projection.membership_version
        or route.get("committed_at_ms") != projection.committed_at_ms
        or route.get("deliveries", []).count(delivery_record) != 1
        or model_data_fingerprint(projection.as_record())
        not in route.get("projection_fingerprints", [])
    ):
        raise ValueError("routed input projection differs from committed route")
    sender_kind = _identifier(
        sender_participant_kind, "routed input sender participant kind")
    event_kind = {
        "human": "human_input",
        "npc": "peer_input",
        "simulation": "simulation_input",
    }.get(sender_kind)
    if event_kind is None:
        raise ValueError("routed input sender participant kind is invalid")
    response_policy = "required" if sender_kind == "human" else "eligible"
    local_event_id = f"ingress:{projection.delivery_id}"
    payload, measurement_fingerprint = _verify_measurement_evidence(
        measurement_evidence=measurement_evidence,
        source_event_id=projection.source_event_id,
        local_event_id=local_event_id,
        event_kind=event_kind,
        projection=_record(
            semantic_projection, "routed input semantic projection"),
        model_runtime=model_runtime,
    )
    model_binding = model_runtime.binding.to_record()
    temporal_binding = temporal_runtime.binding.to_record()
    route_fingerprint = model_data_fingerprint(route_receipt)
    source_receipt_fingerprint = _identifier(
        route.get("source_receipt_fingerprint"),
        "routed input canonical source receipt fingerprint",
    )
    projection_fingerprint = model_data_fingerprint(projection.as_record())
    semantic_fingerprint = model_data_fingerprint(semantic_projection)
    context_metadata = {
        "committed_event_kind": event_kind,
        "response_policy": response_policy,
        "expected_runtime_ordinal": expected_runtime_ordinal,
        "local_turn_id": local_event_id,
        "delivery_reference": {
            "event_id": projection.source_event_id,
            "conversation_id": projection.conversation_id,
            "delivery_id": projection.delivery_id,
            "sender_participant_id": projection.sender_participant_id,
            "membership_version": projection.membership_version,
        },
        "route_receipt_fingerprint": route_fingerprint,
        "source_receipt_fingerprint": source_receipt_fingerprint,
        "runtime_variant_fingerprint": runtime_variant.fingerprint,
    }
    material = {
        "version": ROUTED_INPUT_FACT_BRIDGE_VERSION,
        "conversation_id": projection.conversation_id,
        "participant_id": projection.recipient_participant_id,
        "event_id": projection.source_event_id,
        "target_coordinate": projection.committed_at_ms,
        "event_kind": event_kind,
        "response_policy": response_policy,
        "action_id": None,
        "expected_runtime_ordinal": expected_runtime_ordinal,
        "local_event_id": local_event_id,
        "delivery_id": projection.delivery_id,
        "route_receipt_fingerprint": route_fingerprint,
        "source_receipt_fingerprint": source_receipt_fingerprint,
        "local_projection_fingerprint": projection_fingerprint,
        "semantic_projection_fingerprint": semantic_fingerprint,
        "measurement_evidence_fingerprint": measurement_fingerprint,
        "measurement_payload_fingerprint": model_data_fingerprint(payload),
        "model_binding": model_binding,
        "temporal_binding": temporal_binding,
        "runtime_variant_fingerprint": runtime_variant.fingerprint,
        "authority": {
            "provider_call": False,
            "writer": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeCommittedFactEvidence(
        target_coordinate=projection.committed_at_ms,
        conversation_id=projection.conversation_id,
        participant_id=projection.recipient_participant_id,
        event_id=projection.source_event_id,
        action_id=None,
        event_kind=event_kind,
        response_policy=response_policy,
        expected_runtime_ordinal=expected_runtime_ordinal,
        measurement_payload=payload,
        context_metadata=context_metadata,
        model_binding=model_binding,
        temporal_binding=temporal_binding,
        receipt=receipt,
    )


def _verify_committed_output_action(
    committed_action: object,
) -> dict[str, Any]:
    from ops.runtime_product_action_commit import RuntimeCommittedProductAction

    if not isinstance(committed_action, RuntimeCommittedProductAction):
        raise ValueError(
            "product output requires RuntimeCommittedProductAction")
    plan = committed_action.action_plan
    plan_material = plan.fingerprint_material()
    if model_data_fingerprint(plan_material) != plan.fingerprint:
        raise ValueError("committed output action plan fingerprint is invalid")
    event = committed_action.event
    source = dict(_record(
        thaw_model_data(committed_action.source_receipt),
        "committed output source receipt",
    ))
    action_receipt = _verify_fingerprinted_record(
        thaw_model_data(committed_action.receipt),
        "committed output action receipt",
    )
    if (
        source.get("schema_version") != 1
        or source.get("kind") != "conversation_event_committed"
        or source.get("event_id") != event.event_id
        or source.get("conversation_id") != event.conversation_id
        or source.get("sender_action_id") != plan.action_id
        or source.get("source_event") != event.as_record()
        or event.kind != "message"
        or event.sender_action_id != plan.action_id
        or event.sender_participant_id != plan.participant_id
        or event.conversation_id != plan.conversation_id
    ):
        raise ValueError("committed output source receipt differs from action")
    source_fingerprint = model_data_fingerprint(source)
    if (
        action_receipt.get("version") != "runtime-product-action-commit-v1"
        or action_receipt.get("status") != "canonical_source_committed"
        or action_receipt.get("event_id") != event.event_id
        or action_receipt.get("action_id") != plan.action_id
        or action_receipt.get("conversation_id") != event.conversation_id
        or action_receipt.get("participant_id") != event.sender_participant_id
        or action_receipt.get("action_plan_fingerprint") != plan.fingerprint
        or action_receipt.get("source_receipt_fingerprint") != source_fingerprint
        or action_receipt.get("committed_runtime_ordinal")
        != plan.source_runtime_ordinal
    ):
        raise ValueError("committed output action receipt differs from source")
    return {
        "event": event,
        "plan": plan,
        "source_receipt_fingerprint": source_fingerprint,
        "action_receipt_fingerprint": model_data_fingerprint(action_receipt),
    }


def build_committed_output_semantic_receipt(
    *,
    committed_action: object,
    projection: Mapping[str, Any],
    params_sha: str,
) -> Mapping[str, Any]:
    """Bind one already-produced projection to one canonical output fact.

    This constructor does not call Agent 1 or any provider.  It only records
    which exact committed body the caller says was measured, so downstream
    product composition can reject cross-action or stale projections.
    """
    source = _verify_committed_output_action(committed_action)
    event = source["event"]
    projection_record = dict(_record(projection, "output semantic projection"))
    parameters = _identifier(params_sha, "output semantic params fingerprint")
    material = {
        "version": OUTPUT_SEMANTIC_RECEIPT_VERSION,
        "kind": "committed_output_semantic_projection",
        "conversation_id": event.conversation_id,
        "participant_id": event.sender_participant_id,
        "event_id": event.event_id,
        "action_id": event.sender_action_id,
        "local_event_id": event.event_id,
        "target_coordinate": event.committed_at_ms,
        "event_kind": "npc_output",
        "projection": projection_record,
        "params_sha": parameters,
        "source_text_fingerprint": model_data_fingerprint(
            {"text": event.text}),
        "source_receipt_fingerprint": source[
            "source_receipt_fingerprint"],
        "action_receipt_fingerprint": source[
            "action_receipt_fingerprint"],
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


def build_committed_output_fact_evidence(
    *,
    committed_action: object,
    semantic_receipt: Mapping[str, Any],
    measurement_evidence: Mapping[str, Any],
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    runtime_variant: RuntimeVariantBinding,
) -> RuntimeCommittedFactEvidence:
    """Verify one canonical NPC output for the existing fact transaction."""
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("product fact bridge requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("product fact bridge requires a registry-bound temporal runtime")
    if not isinstance(runtime_variant, RuntimeVariantBinding):
        raise ValueError(
            "product fact bridge requires a RuntimeVariantBinding")
    if temporal_runtime.binding.domain_id != WALL_CLOCK_DOMAIN_ID:
        raise ValueError(
            "current product receipts require the wall-clock temporal domain")
    source = _verify_committed_output_action(committed_action)
    event = source["event"]
    plan = source["plan"]
    runtime_binding = RuntimeSessionBinding.create(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        runtime_variant=runtime_variant,
    )
    if runtime_binding.fingerprint != plan.session_binding_fingerprint:
        raise ValueError("committed output action belongs to another runtime session")
    semantic = _verify_fingerprinted_record(
        semantic_receipt, "committed output semantic receipt")
    required = {
        "version", "kind", "conversation_id", "participant_id",
        "event_id", "action_id", "local_event_id", "target_coordinate",
        "event_kind", "projection", "params_sha",
        "source_text_fingerprint", "source_receipt_fingerprint",
        "action_receipt_fingerprint",
    }
    if set(semantic) != required:
        raise ValueError("committed output semantic receipt fields are invalid")
    projection = _record(
        semantic.get("projection"), "output semantic projection")
    expected = {
        "version": OUTPUT_SEMANTIC_RECEIPT_VERSION,
        "kind": "committed_output_semantic_projection",
        "conversation_id": event.conversation_id,
        "participant_id": event.sender_participant_id,
        "event_id": event.event_id,
        "action_id": plan.action_id,
        "local_event_id": event.event_id,
        "target_coordinate": event.committed_at_ms,
        "event_kind": "npc_output",
        "params_sha": semantic.get("params_sha"),
        "source_text_fingerprint": model_data_fingerprint(
            {"text": event.text}),
        "source_receipt_fingerprint": source[
            "source_receipt_fingerprint"],
        "action_receipt_fingerprint": source[
            "action_receipt_fingerprint"],
    }
    if (
        not isinstance(semantic.get("params_sha"), str)
        or not semantic["params_sha"].strip()
        or any(semantic.get(key) != value for key, value in expected.items())
    ):
        raise ValueError(
            "committed output semantic receipt differs from canonical action")
    payload, measurement_fingerprint = _verify_measurement_evidence(
        measurement_evidence=measurement_evidence,
        source_event_id=event.event_id,
        local_event_id=event.event_id,
        event_kind="npc_output",
        projection=projection,
        model_runtime=model_runtime,
    )
    model_binding = model_runtime.binding.to_record()
    temporal_binding = temporal_runtime.binding.to_record()
    evidence_fingerprint = model_data_fingerprint({
        "source_receipt_fingerprint": source[
            "source_receipt_fingerprint"],
        "action_receipt_fingerprint": source[
            "action_receipt_fingerprint"],
        "semantic_receipt_fingerprint": model_data_fingerprint(semantic),
        "measurement_evidence_fingerprint": measurement_fingerprint,
    })
    context_metadata = {
        "committed_event_kind": "npc_output",
        "response_policy": None,
        "local_turn_id": event.event_id,
        "source_action_id": plan.action_id,
        "source_receipt_fingerprint": source[
            "source_receipt_fingerprint"],
        "expected_runtime_ordinal": plan.source_runtime_ordinal,
        "product_evidence_fingerprint": evidence_fingerprint,
    }
    material = {
        "version": PRODUCT_FACT_BRIDGE_VERSION,
        "conversation_id": event.conversation_id,
        "participant_id": event.sender_participant_id,
        "event_id": event.event_id,
        "action_id": plan.action_id,
        "target_coordinate": event.committed_at_ms,
        "event_kind": "npc_output",
        "response_policy": None,
        "expected_runtime_ordinal": plan.source_runtime_ordinal,
        "local_event_id": event.event_id,
        "source_receipt_fingerprint": source[
            "source_receipt_fingerprint"],
        "action_receipt_fingerprint": source[
            "action_receipt_fingerprint"],
        "semantic_receipt_fingerprint": model_data_fingerprint(semantic),
        "measurement_evidence_fingerprint": measurement_fingerprint,
        "measurement_payload_fingerprint": model_data_fingerprint(payload),
        "model_binding": model_binding,
        "temporal_binding": temporal_binding,
        "authority": {
            "provider_call": False,
            "source_append": False,
            "writer": False,
            "dynamic_tick": False,
            "route_delivery": False,
            "send": False,
            "default_path_switch": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeCommittedFactEvidence(
        target_coordinate=event.committed_at_ms,
        conversation_id=event.conversation_id,
        participant_id=event.sender_participant_id,
        event_id=event.event_id,
        action_id=plan.action_id,
        event_kind="npc_output",
        response_policy=None,
        expected_runtime_ordinal=plan.source_runtime_ordinal,
        measurement_payload=payload,
        context_metadata=context_metadata,
        model_binding=model_binding,
        temporal_binding=temporal_binding,
        receipt=receipt,
    )


__all__ = (
    "ROUTED_INPUT_FACT_BRIDGE_VERSION",
    "MEASUREMENT_EVIDENCE_VERSION",
    "OUTPUT_SEMANTIC_RECEIPT_VERSION",
    "PRODUCT_FACT_BRIDGE_VERSION",
    "RuntimeCommittedFactEvidence",
    "build_committed_input_fact_evidence",
    "build_committed_output_fact_evidence",
    "build_committed_output_semantic_receipt",
    "build_routed_input_fact_evidence",
)
