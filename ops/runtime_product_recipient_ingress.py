"""Recipient-local commit for one canonical output route delivery.

The source action, output settlement, and route are already committed.  This
host verifies one exact recipient projection and either records an observed
local fact or applies one addressed formal input transaction.  It owns no
semantic provider, Writer, dynamic tick, source routing, or send authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_runtime import BoundInteractionModel
from framework.runtime_variant import RuntimeVariantBinding
from framework.temporal_runtime import BoundTemporalDomain
from interaction_extensions.multi_participant.local_projection import LocalProjection
from interaction_extensions.multi_participant.object_projection import (
    ParticipantProjectionError,
    require_participant_projection,
)
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.runtime_committed_fact_transaction import (
    COMMITTED_FACT_TRANSACTION_VERSION,
    RuntimeCommittedFactTransaction,
    execute_runtime_committed_fact_transaction,
)
from ops.runtime_full_memory_committed_fact_transaction import (
    FULL_MEMORY_COMMITTED_FACT_TRANSACTION_VERSION,
    RuntimeFullMemoryCommittedFactTransaction,
    execute_runtime_full_memory_committed_fact_transaction,
    supports_full_memory_fact_transaction,
)
from ops.runtime_product_fact_bridge import (
    RuntimeCommittedFactEvidence,
    build_routed_input_fact_evidence,
)
from ops.runtime_product_output_route import (
    RuntimeProductOutputRoute,
)
from ops.runtime_product_expression_continuation import (
    RuntimeProductExpressionContinuation,
    attach_product_expression_continuation,
    build_product_expression_continuation,
    load_product_expression_continuation,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


PRODUCT_RECIPIENT_INGRESS_VERSION = "runtime-product-recipient-ingress-v2"
LEGACY_PRODUCT_RECIPIENT_INGRESS_VERSION = (
    "runtime-product-recipient-ingress-v1")
RUNTIME_RECIPIENT_INGRESS_JOURNAL_KEY = "runtime_recipient_ingresses"


@dataclass(frozen=True)
class RuntimeProductRecipientIngress:
    """One exact recipient-local route result."""

    session: BootstrappedRuntimeSession
    delivery_id: str
    projection: LocalProjection
    evidence: RuntimeCommittedFactEvidence | None
    transaction: (
        RuntimeCommittedFactTransaction
        | RuntimeFullMemoryCommittedFactTransaction
        | None
    )
    expression_continuation: RuntimeProductExpressionContinuation | None
    receipt: Mapping[str, Any]
    already_committed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _verified_receipt(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be record data")
    material = dict(thaw_model_data(value))
    fingerprint = material.pop("fingerprint", None)
    if model_data_fingerprint(material) != fingerprint:
        raise ValueError(f"{label} fingerprint is invalid")
    return material


def _route_projection(
    *,
    route: RuntimeProductOutputRoute,
    registry: ProductConversationSessionRegistry,
    recipient_participant_id: str,
) -> tuple[LocalProjection, object, object]:
    if not isinstance(route, RuntimeProductOutputRoute):
        raise ValueError("recipient ingress requires RuntimeProductOutputRoute")
    if not isinstance(registry, ProductConversationSessionRegistry):
        raise ValueError(
            "recipient ingress requires ProductConversationSessionRegistry")
    receipt = _verified_receipt(
        route.receipt, "recipient ingress route receipt")
    if (
        receipt.get("version") != "runtime-product-output-route-v1"
        or receipt.get("status") != "canonical_output_route_committed"
        or receipt.get("deliveries")
        != [item.as_record() for item in route.deliveries]
        or receipt.get("projection_fingerprints")
        != [
            model_data_fingerprint(item.as_record())
            for item in route.projections
        ]
        or len(route.deliveries) != len(route.projections)
    ):
        raise ValueError("recipient ingress route object differs from receipt")
    recipient_id = str(recipient_participant_id or "").strip()
    matches = tuple(
        (delivery, projection)
        for delivery, projection in zip(route.deliveries, route.projections)
        if delivery.recipient_participant_id == recipient_id
        and projection.recipient_participant_id == recipient_id
    )
    if len(matches) != 1:
        raise ValueError("recipient ingress requires one exact recipient delivery")
    delivery, projection = matches[0]
    if (
        projection.delivery_id != delivery.delivery_id
        or projection.source_event_id != delivery.source_event_id
        or projection.conversation_id != delivery.conversation_id
        or projection.membership_version != delivery.membership_version
        or projection.sender_participant_id != delivery.sender_participant_id
        or projection.event_kind != delivery.event_kind
        or projection.delivery_scope != delivery.delivery_scope
        or receipt.get("event_id") != projection.source_event_id
        or receipt.get("conversation_id") != projection.conversation_id
        or receipt.get("sender_participant_id")
        != projection.sender_participant_id
        or receipt.get("membership_version") != projection.membership_version
        or receipt.get("committed_at_ms") != projection.committed_at_ms
    ):
        raise ValueError("recipient ingress delivery and projection differ")
    session = registry.load_session(projection.conversation_id)
    if session is None:
        raise ValueError("recipient ingress conversation is not registered")
    if (
        session.conversation.membership_version != projection.membership_version
        or session.conversation.active_member_ids
        != projection.member_snapshot_ids
    ):
        raise ValueError(
            "recipient ingress route differs from registered conversation")
    participants = session.participants_by_id
    recipient = participants.get(recipient_id)
    sender = participants.get(projection.sender_participant_id)
    try:
        require_participant_projection(recipient, label="recipient")
        require_participant_projection(sender, label="sender")
    except ParticipantProjectionError as exc:
        raise ValueError("recipient ingress participants are not registered") from exc
    if (
        not recipient.capabilities.receive_projection
        or not recipient.capabilities.has_local_store
    ):
        raise ValueError("recipient ingress requires a local receiving participant")
    if not sender.capabilities.has_writer:
        raise ValueError("canonical output route sender must own writer capability")
    return projection, recipient, sender


def _journal(profile: Mapping[str, Any], *, session_binding) -> tuple[dict[str, Any], ...]:
    raw = profile.get(RUNTIME_RECIPIENT_INGRESS_JOURNAL_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("recipient ingress journal is invalid")
    restored = []
    seen = set()
    for item in raw:
        material = _verified_receipt(item, "recipient ingress journal receipt")
        if (
            material.get("version") not in {
                LEGACY_PRODUCT_RECIPIENT_INGRESS_VERSION,
                PRODUCT_RECIPIENT_INGRESS_VERSION,
            }
            or material.get("session_binding_fingerprint")
            != session_binding.fingerprint
            or material.get("runtime_variant_fingerprint")
            != session_binding.runtime_variant_fingerprint
        ):
            raise ValueError("recipient ingress journal binding is invalid")
        delivery_id = str(material.get("delivery_id") or "").strip()
        if not delivery_id or delivery_id in seen:
            raise ValueError("recipient ingress journal delivery identity is invalid")
        seen.add(delivery_id)
        restored.append(dict(thaw_model_data(item)))
    return tuple(restored)


def _with_journal(
    session: BootstrappedRuntimeSession,
    *,
    existing: tuple[dict[str, Any], ...],
    receipt: Mapping[str, Any],
) -> BootstrappedRuntimeSession:
    profile = dict(session.profile)
    profile[RUNTIME_RECIPIENT_INGRESS_JOURNAL_KEY] = [
        *existing,
        dict(thaw_model_data(receipt)),
    ]
    return replace(session, profile=profile)


def committed_product_recipient_delivery_ids(
    session: BootstrappedRuntimeSession,
) -> frozenset[str]:
    """Return verified owner-local delivery identities, without mutation."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise TypeError("recipient delivery audit requires formal session")
    return frozenset(
        row["delivery_id"]
        for row in _journal(
            session.profile, session_binding=session.session_binding)
    )


def execute_product_recipient_ingress(
    *,
    route: RuntimeProductOutputRoute,
    conversation_registry: ProductConversationSessionRegistry,
    recipient_participant_id: str,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    runtime_variant: RuntimeVariantBinding,
    semantic_projection: Mapping[str, Any] | None,
    measurement_evidence: Mapping[str, Any] | None,
    committed_remainders: Mapping[str, Any],
) -> RuntimeProductRecipientIngress:
    """Apply one route delivery to one recipient session without persistence."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("recipient ingress requires a bootstrapped session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("recipient ingress requires a bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("recipient ingress requires a bound temporal runtime")
    if not isinstance(runtime_variant, RuntimeVariantBinding):
        raise ValueError("recipient ingress requires a runtime variant")
    if not isinstance(committed_remainders, Mapping):
        raise ValueError(
            "recipient ingress requires committed expression remainders")
    session.session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )
    if session.session_binding.runtime_variant != runtime_variant:
        raise ValueError("recipient ingress runtime variant differs from session")
    projection, _recipient, sender = _route_projection(
        route=route,
        registry=conversation_registry,
        recipient_participant_id=recipient_participant_id,
    )
    existing = _journal(
        session.profile, session_binding=session.session_binding)
    route_fingerprint = model_data_fingerprint(route.receipt)
    projection_fingerprint = model_data_fingerprint(projection.as_record())

    evidence = None
    measurement_fingerprint = None
    semantic_fingerprint = None
    if projection.delivery_scope == "addressed":
        if not isinstance(semantic_projection, Mapping):
            raise ValueError(
                "addressed recipient ingress requires semantic projection")
        if not isinstance(measurement_evidence, Mapping):
            raise ValueError(
                "addressed recipient ingress requires measurement evidence")
        evidence = build_routed_input_fact_evidence(
            projection=projection,
            route_receipt=route.receipt,
            semantic_projection=semantic_projection,
            measurement_evidence=measurement_evidence,
            sender_participant_kind=sender.kind,
            expected_runtime_ordinal=session.position.runtime_ordinal,
            model_runtime=model_runtime,
            temporal_runtime=temporal_runtime,
            runtime_variant=runtime_variant,
        )
        evidence.verify_for(
            model_runtime=model_runtime,
            temporal_runtime=temporal_runtime,
        )
        measurement_fingerprint = model_data_fingerprint(
            measurement_evidence)
        semantic_fingerprint = model_data_fingerprint(semantic_projection)
        status = "addressed_formal_fact_committed"
    else:
        if semantic_projection is not None or measurement_evidence is not None:
            raise ValueError(
                "observed recipient ingress cannot carry F03 measurement")
        status = "observed_local_fact_committed"

    identity = {
        "status": status,
        "conversation_id": projection.conversation_id,
        "source_event_id": projection.source_event_id,
        "delivery_id": projection.delivery_id,
        "projection_id": projection.projection_id,
        "recipient_participant_id": projection.recipient_participant_id,
        "sender_participant_id": projection.sender_participant_id,
        "delivery_scope": projection.delivery_scope,
        "committed_at_ms": projection.committed_at_ms,
        "route_receipt_fingerprint": route_fingerprint,
        "local_projection_fingerprint": projection_fingerprint,
        "semantic_projection_fingerprint": semantic_fingerprint,
        "measurement_evidence_fingerprint": measurement_fingerprint,
        "runtime_variant_fingerprint": runtime_variant.fingerprint,
    }
    prior = tuple(
        row for row in existing
        if row.get("delivery_id") == projection.delivery_id
    )
    if prior:
        if len(prior) != 1 or any(
            prior[0].get(key) != value for key, value in identity.items()
        ):
            raise ValueError("recipient ingress conflicts with committed delivery")
        return RuntimeProductRecipientIngress(
            session=session,
            delivery_id=projection.delivery_id,
            projection=projection,
            evidence=evidence,
            transaction=None,
            expression_continuation=(
                load_product_expression_continuation(
                    session.profile,
                    delivery_id=projection.delivery_id,
                    session_binding=session.session_binding,
                )
                if projection.delivery_scope == "addressed"
                else None
            ),
            receipt=prior[0],
            already_committed=True,
        )

    transaction = None
    next_session = session
    initial_ordinal = session.position.runtime_ordinal
    if evidence is not None:
        transaction_host = (
            execute_runtime_full_memory_committed_fact_transaction
            if supports_full_memory_fact_transaction(model_runtime)
            else execute_runtime_committed_fact_transaction
        )
        transaction = transaction_host(
                session=session,
                model_runtime=model_runtime,
                temporal_runtime=temporal_runtime,
                target_coordinate=evidence.target_coordinate,
                scope_ref=evidence.conversation_id,
                subject_ref=evidence.participant_id,
                event_ref=evidence.event_id,
                action_ref=evidence.action_id,
                measurement_payload=evidence.measurement_payload,
                context_metadata=evidence.context_metadata,
            )
        next_session = transaction.session

    material = {
        "version": PRODUCT_RECIPIENT_INGRESS_VERSION,
        **identity,
        "session_binding_fingerprint": session.session_binding.fingerprint,
        "initial_runtime_ordinal": initial_ordinal,
        "final_runtime_ordinal": next_session.position.runtime_ordinal,
        "fact_evidence_receipt_fingerprint": (
            None if evidence is None
            else model_data_fingerprint(evidence.receipt)
        ),
        "fact_transaction_receipt_fingerprint": (
            None if transaction is None
            else model_data_fingerprint(transaction.receipt)
        ),
        "fact_transaction_version": (
            None if transaction is None else (
                FULL_MEMORY_COMMITTED_FACT_TRANSACTION_VERSION
                if isinstance(
                    transaction, RuntimeFullMemoryCommittedFactTransaction)
                else COMMITTED_FACT_TRANSACTION_VERSION
            )
        ),
        "authority": {
            "source_write": False,
            "route": False,
            "semantic_provider": False,
            "writer": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    next_session = _with_journal(
        next_session, existing=existing, receipt=receipt)
    if isinstance(transaction, RuntimeCommittedFactTransaction):
        event_transaction = replace(
            transaction.event_transaction, session=next_session)
        transaction = replace(
            transaction,
            session=next_session,
            event_transaction=event_transaction,
        )
    elif isinstance(transaction, RuntimeFullMemoryCommittedFactTransaction):
        event_transaction = replace(
            transaction.event_transaction, session=next_session)
        profile_commit = replace(
            transaction.profile_commit,
            session=next_session,
            transaction=event_transaction,
        )
        transaction = replace(
            transaction,
            session=next_session,
            event_transaction=event_transaction,
            profile_commit=profile_commit,
        )
    continuation = None
    if transaction is not None:
        continuation = build_product_expression_continuation(
            transaction=transaction,
            evidence=evidence,
            ingress_receipt=receipt,
            committed_remainders=committed_remainders,
        )
        next_session = attach_product_expression_continuation(
            next_session, continuation=continuation)
        if isinstance(transaction, RuntimeCommittedFactTransaction):
            transaction = replace(
                transaction,
                session=next_session,
                event_transaction=replace(
                    transaction.event_transaction, session=next_session),
            )
        else:
            event_transaction = replace(
                transaction.event_transaction, session=next_session)
            transaction = replace(
                transaction,
                session=next_session,
                event_transaction=event_transaction,
                profile_commit=replace(
                    transaction.profile_commit,
                    session=next_session,
                    transaction=event_transaction,
                ),
            )
    return RuntimeProductRecipientIngress(
        session=next_session,
        delivery_id=projection.delivery_id,
        projection=projection,
        evidence=evidence,
        transaction=transaction,
        expression_continuation=continuation,
        receipt=receipt,
        already_committed=False,
    )


__all__ = (
    "PRODUCT_RECIPIENT_INGRESS_VERSION",
    "LEGACY_PRODUCT_RECIPIENT_INGRESS_VERSION",
    "RUNTIME_RECIPIENT_INGRESS_JOURNAL_KEY",
    "RuntimeProductRecipientIngress",
    "committed_product_recipient_delivery_ids",
    "execute_product_recipient_ingress",
)
