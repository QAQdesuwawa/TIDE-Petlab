"""Commit routing evidence for one settled canonical NPC output.

The source fact and its model-state settlement already exist before this host
runs.  This module verifies that exact lineage, derives deterministic
recipient deliveries from the immutable conversation event, and appends one
body-free route receipt.  It does not dispatch an Actor or claim recipient
ingress has completed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import os
import threading
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import context_fact_identity
from interaction_extensions.multi_participant.router import (
    ConversationDelivery,
    route_committed_event,
)
from interaction_extensions.multi_participant.local_projection import (
    LocalProjection,
    project_delivery,
)
from ops.product_conversation_journal import ProductConversationSourceJournal
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.runtime_product_action_commit import RuntimeCommittedProductAction
from ops.runtime_product_fact_lifecycle import (
    RuntimeProductFactLifecycle,
    complete_product_fact_lifecycle,
)


PRODUCT_OUTPUT_ROUTE_VERSION = "runtime-product-output-route-v1"


@dataclass(frozen=True)
class RuntimeProductOutputRoute:
    """One durable route decision; recipient ingress is still pending."""

    deliveries: tuple[ConversationDelivery, ...]
    projections: tuple[LocalProjection, ...]
    receipt: Mapping[str, Any]
    already_committed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "deliveries", tuple(self.deliveries))
        object.__setattr__(self, "projections", tuple(self.projections))
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _verify_fingerprint(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be record data")
    material = dict(value)
    fingerprint = material.pop("fingerprint", None)
    if model_data_fingerprint(material) != fingerprint:
        raise ValueError(f"{label} fingerprint is invalid")
    return material


def _verify_action(
    committed_action: RuntimeCommittedProductAction,
    registry: ProductConversationSessionRegistry,
) -> dict[str, Any]:
    if not isinstance(committed_action, RuntimeCommittedProductAction):
        raise ValueError("output route requires RuntimeCommittedProductAction")
    if not isinstance(registry, ProductConversationSessionRegistry):
        raise ValueError("output route requires ProductConversationSessionRegistry")
    event = committed_action.event
    plan = committed_action.action_plan
    plan_material = plan.fingerprint_material()
    if model_data_fingerprint(plan_material) != plan.fingerprint:
        raise ValueError("output route action plan fingerprint is invalid")
    source_receipt = dict(thaw_model_data(committed_action.source_receipt))
    if (
        source_receipt.get("schema_version") != 1
        or source_receipt.get("kind") != "conversation_event_committed"
        or source_receipt.get("event_id") != event.event_id
        or source_receipt.get("conversation_id") != event.conversation_id
        or source_receipt.get("sender_action_id") != plan.action_id
        or source_receipt.get("source_event") != event.as_record()
        or event.kind != "message"
        or event.sender_action_id != plan.action_id
        or event.sender_participant_id != plan.participant_id
        or event.conversation_id != plan.conversation_id
    ):
        raise ValueError("output route source receipt differs from action")
    action_receipt = _verify_fingerprint(
        thaw_model_data(committed_action.receipt),
        "output route action receipt",
    )
    if (
        action_receipt.get("version") != "runtime-product-action-commit-v1"
        or action_receipt.get("status") != "canonical_source_committed"
        or action_receipt.get("event_id") != event.event_id
        or action_receipt.get("action_id") != plan.action_id
        or action_receipt.get("conversation_id") != event.conversation_id
        or action_receipt.get("participant_id") != event.sender_participant_id
        or action_receipt.get("action_plan_fingerprint") != plan.fingerprint
        or action_receipt.get("source_receipt_fingerprint")
        != model_data_fingerprint(source_receipt)
        or action_receipt.get("committed_runtime_ordinal")
        != plan.source_runtime_ordinal
    ):
        raise ValueError("output route action receipt differs from source")
    session = registry.load_session(event.conversation_id)
    if session is None:
        raise ValueError("output route requires registered conversation snapshot")
    conversation = session.conversation
    if (
        event.membership_version != conversation.membership_version
        or event.member_snapshot_ids != conversation.active_member_ids
        or event.sender_participant_id not in session.participants_by_id
    ):
        raise ValueError("output route event differs from registered session snapshot")
    source_rows = ProductConversationSourceJournal(
        registry=registry,
        conversation_id=event.conversation_id,
    ).read_source_receipts()
    matches = tuple(row for row in source_rows if row.get("event_id") == event.event_id)
    if len(matches) != 1 or matches[0] != source_receipt:
        raise ValueError("output route requires one exact canonical source row")
    return {
        "event": event,
        "plan": plan,
        "source_receipt_fingerprint": model_data_fingerprint(source_receipt),
        "action_receipt_fingerprint": model_data_fingerprint(action_receipt),
    }


def _verify_lifecycle(
    lifecycle: RuntimeProductFactLifecycle,
    *,
    event: object,
    plan: object,
) -> dict[str, Any]:
    if not isinstance(lifecycle, RuntimeProductFactLifecycle):
        raise ValueError("output route requires RuntimeProductFactLifecycle")
    expected = complete_product_fact_lifecycle(
        evidence=lifecycle.evidence,
        transaction=lifecycle.transaction,
        expression=lifecycle.expression,
    )
    if expected.receipt != lifecycle.receipt:
        raise ValueError("output route lifecycle receipt is invalid")
    evidence = lifecycle.evidence
    transaction = lifecycle.transaction
    context = transaction.event_transaction.context
    context_identity = context_fact_identity(context)
    event_input_session = getattr(transaction, "event_input_session", None)
    input_ordinal = (
        context.runtime_ordinal
        if event_input_session is None
        else event_input_session.position.runtime_ordinal
    )
    if (
        evidence.event_kind != "npc_output"
        or evidence.response_policy is not None
        or evidence.event_id != event.event_id
        or evidence.action_id != event.sender_action_id
        or evidence.participant_id != event.sender_participant_id
        or evidence.conversation_id != event.conversation_id
        or evidence.target_coordinate != event.committed_at_ms
        or evidence.expected_runtime_ordinal != plan.source_runtime_ordinal
        or context_identity.event_ref != event.event_id
        or context_identity.action_ref != event.sender_action_id
        or context_identity.subject_ref != event.sender_participant_id
        or context_identity.scope_ref != event.conversation_id
        or input_ordinal != plan.source_runtime_ordinal
        or transaction.session.position.runtime_ordinal
        != plan.source_runtime_ordinal + 1
    ):
        raise ValueError("output route lifecycle differs from canonical action")
    return {
        "lifecycle_receipt_fingerprint": model_data_fingerprint(
            thaw_model_data(lifecycle.receipt)),
        "transaction_receipt_fingerprint": model_data_fingerprint(
            lifecycle.transaction.receipt),
        "settled_runtime_ordinal": transaction.session.position.runtime_ordinal,
    }


class _OutputRouteJournal:
    """Append-only, body-free journal for deterministic route decisions."""

    def __init__(self, registry: ProductConversationSessionRegistry, conversation_id: str):
        self.path = registry.output_route_journal_path(conversation_id)
        self._durable_writes = registry._durable_writes
        self._lock = threading.RLock()

    def _read(self) -> tuple[dict[str, Any], ...]:
        if not os.path.isfile(self.path):
            return ()
        try:
            with open(self.path, "r", encoding="utf-8-sig") as handle:
                rows = tuple(json.loads(line) for line in handle if line.strip())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("output route journal cannot be read") from exc
        seen = set()
        for row in rows:
            material = _verify_fingerprint(row, "output route journal receipt")
            if (
                material.get("version") != PRODUCT_OUTPUT_ROUTE_VERSION
                or material.get("status") != "canonical_output_route_committed"
            ):
                raise ValueError("output route journal receipt schema is invalid")
            event_id = material.get("event_id")
            if event_id in seen:
                raise ValueError("output route journal contains duplicate event")
            seen.add(event_id)
        return rows

    def append_or_reuse(self, receipt: Mapping[str, Any]) -> tuple[Mapping[str, Any], bool]:
        candidate = thaw_model_data(receipt)
        with self._lock:
            rows = self._read()
            matches = tuple(row for row in rows if row.get("event_id") == candidate["event_id"])
            if matches:
                if len(matches) != 1 or matches[0] != candidate:
                    raise ValueError("output route conflicts with existing event route")
                return matches[0], True
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            try:
                with open(self.path, "a", encoding="utf-8", newline="") as handle:
                    handle.write(json.dumps(candidate, ensure_ascii=False) + "\n")
                    handle.flush()
                    if self._durable_writes:
                        os.fsync(handle.fileno())
            except OSError as exc:
                raise ValueError("output route journal write failed") from exc
        return candidate, False


def commit_product_output_route(
    *,
    committed_action: RuntimeCommittedProductAction,
    lifecycle: RuntimeProductFactLifecycle,
    conversation_registry: ProductConversationSessionRegistry,
) -> RuntimeProductOutputRoute:
    """Commit one deterministic route after the same output settled once."""
    action = _verify_action(committed_action, conversation_registry)
    event = action["event"]
    plan = action["plan"]
    settled = _verify_lifecycle(lifecycle, event=event, plan=plan)
    deliveries = route_committed_event(event)
    projections = tuple(project_delivery(event, item) for item in deliveries)
    delivery_records = [item.as_record() for item in deliveries]
    material = {
        "version": PRODUCT_OUTPUT_ROUTE_VERSION,
        "status": "canonical_output_route_committed",
        "conversation_id": event.conversation_id,
        "event_id": event.event_id,
        "action_id": event.sender_action_id,
        "sender_participant_id": event.sender_participant_id,
        "membership_version": event.membership_version,
        "committed_at_ms": event.committed_at_ms,
        "source_runtime_ordinal": plan.source_runtime_ordinal,
        "settled_runtime_ordinal": settled["settled_runtime_ordinal"],
        "source_receipt_fingerprint": action["source_receipt_fingerprint"],
        "action_receipt_fingerprint": action["action_receipt_fingerprint"],
        "lifecycle_receipt_fingerprint": settled[
            "lifecycle_receipt_fingerprint"],
        "transaction_receipt_fingerprint": settled[
            "transaction_receipt_fingerprint"],
        "deliveries": delivery_records,
        "projection_fingerprints": [
            model_data_fingerprint(item.as_record()) for item in projections
        ],
        "authority": {
            "source_write": False,
            "recipient_dispatch": False,
            "recipient_ingress": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    persisted, already_committed = _OutputRouteJournal(
        conversation_registry, event.conversation_id,
    ).append_or_reuse(receipt)
    return RuntimeProductOutputRoute(
        deliveries=deliveries,
        projections=projections,
        receipt=persisted,
        already_committed=already_committed,
    )


def restore_committed_product_output_routes(
    *, conversation_registry: ProductConversationSessionRegistry,
    conversation_id: str,
) -> tuple[RuntimeProductOutputRoute, ...]:
    """Restore exact committed routes without rerunning settlement or routing.

    The route body is deterministically reconstructed from the canonical source
    event.  The body-free route receipt remains the authority and every rebuilt
    delivery/projection must match it exactly.
    """
    if not isinstance(
        conversation_registry, ProductConversationSessionRegistry
    ):
        raise TypeError("route restore requires conversation registry")
    session = conversation_registry.load_session(conversation_id)
    if session is None:
        raise ValueError("route restore conversation is not registered")
    source_rows = ProductConversationSourceJournal(
        registry=conversation_registry,
        conversation_id=conversation_id,
    ).read_source_receipts()
    restored = []
    for raw_receipt in _OutputRouteJournal(
        conversation_registry, conversation_id,
    )._read():
        receipt = dict(raw_receipt)
        source_row = next(
            (row for row in source_rows
             if row.get("event_id") == receipt.get("event_id")),
            None,
        )
        if source_row is None:
            raise ValueError("output route restore lacks canonical source")
        event_record = source_row["source_event"]
        from interaction_extensions.multi_participant.events import (
            CommittedConversationEvent,
        )
        event = CommittedConversationEvent.from_record(event_record)
        deliveries = route_committed_event(event)
        projections = tuple(project_delivery(event, item) for item in deliveries)
        if (
            receipt.get("conversation_id") != event.conversation_id
            or receipt.get("action_id") != event.sender_action_id
            or receipt.get("sender_participant_id")
            != event.sender_participant_id
            or receipt.get("membership_version") != event.membership_version
            or receipt.get("committed_at_ms") != event.committed_at_ms
            or receipt.get("source_receipt_fingerprint")
            != model_data_fingerprint(source_row)
            or receipt.get("deliveries")
            != [item.as_record() for item in deliveries]
            or receipt.get("projection_fingerprints")
            != [model_data_fingerprint(item.as_record()) for item in projections]
        ):
            raise ValueError("output route restore differs from canonical source")
        restored.append(RuntimeProductOutputRoute(
            deliveries=deliveries,
            projections=projections,
            receipt=receipt,
            already_committed=True,
        ))
    return tuple(restored)


__all__ = (
    "PRODUCT_OUTPUT_ROUTE_VERSION",
    "RuntimeProductOutputRoute",
    "commit_product_output_route",
    "restore_committed_product_output_routes",
)
