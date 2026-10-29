"""Model-independent fan-out of one committed output route to owner Actors.

The route already owns recipient visibility and address scope.  This module
only composes transient Actor work.  Addressed semantic measurement is
provided by one recipient-local injected callable; observed deliveries carry
no measurement and therefore cannot enter an event/state transaction.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
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
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.runtime_product_output_route import (
    RuntimeProductOutputRoute,
    restore_committed_product_output_routes,
)
from ops.runtime_product_recipient_work import FormalProductRecipientIngressWork


@dataclass(frozen=True)
class PreparedFormalRecipientMeasurement:
    """Package-owned measurement products for one exact addressed delivery."""

    recipient_participant_id: str
    delivery_id: str
    semantic_projection: Mapping[str, Any]
    measurement_evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        recipient_id = str(self.recipient_participant_id or "").strip()
        delivery_id = str(self.delivery_id or "").strip()
        if not recipient_id or not delivery_id:
            raise ValueError("prepared measurement requires recipient and delivery identity")
        if not isinstance(self.semantic_projection, Mapping):
            raise TypeError("prepared measurement requires semantic projection")
        if not isinstance(self.measurement_evidence, Mapping):
            raise TypeError("prepared measurement requires model evidence")
        object.__setattr__(self, "recipient_participant_id", recipient_id)
        object.__setattr__(self, "delivery_id", delivery_id)
        object.__setattr__(
            self, "semantic_projection",
            freeze_model_payload(self.semantic_projection),
        )
        object.__setattr__(
            self, "measurement_evidence",
            freeze_model_payload(self.measurement_evidence),
        )


@dataclass(frozen=True)
class FormalProductRecipientDispatch:
    """Transient set of validated works queued for one committed route."""

    works: tuple[FormalProductRecipientIngressWork, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "works", tuple(self.works))
        if any(
            not isinstance(item, FormalProductRecipientIngressWork)
            for item in self.works
        ):
            raise TypeError("formal dispatch contains non-work item")


PRODUCT_RECIPIENT_DISPATCH_CONTINUATION_VERSION = (
    "runtime-product-recipient-dispatch-continuation-v1")


class _RecipientDispatchJournal:
    """Append-only handoff between a committed route and owner Actor queues."""

    def __init__(
        self, registry: ProductConversationSessionRegistry, conversation_id: str,
    ) -> None:
        self.path = registry.recipient_dispatch_journal_path(conversation_id)
        self._durable_writes = registry._durable_writes
        self._lock = threading.RLock()

    @staticmethod
    def _verified(row: Mapping[str, Any]) -> dict[str, Any]:
        if not isinstance(row, Mapping):
            raise ValueError("recipient dispatch continuation must be a record")
        material = dict(row)
        fingerprint = material.pop("fingerprint", None)
        if model_data_fingerprint(material) != fingerprint:
            raise ValueError("recipient dispatch continuation fingerprint is invalid")
        if (
            material.get("version")
            != PRODUCT_RECIPIENT_DISPATCH_CONTINUATION_VERSION
            or material.get("status") not in {"preparation_started", "prepared"}
        ):
            raise ValueError("recipient dispatch continuation schema is invalid")
        authority = material.get("authority")
        if not isinstance(authority, Mapping) or any(authority.values()):
            raise ValueError("recipient dispatch continuation claims authority")
        return material

    def read(self) -> tuple[dict[str, Any], ...]:
        if not os.path.isfile(self.path):
            return ()
        try:
            with open(self.path, "r", encoding="utf-8-sig") as handle:
                rows = tuple(json.loads(line) for line in handle if line.strip())
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("recipient dispatch continuation cannot be read") from exc
        states = {}
        started_by_event = {}
        for row in rows:
            material = self._verified(row)
            event_id = str(material.get("event_id") or "").strip()
            if not event_id:
                raise ValueError("recipient dispatch continuation lacks event identity")
            stages = states.setdefault(event_id, [])
            status = material["status"]
            if status in stages or (status == "prepared" and stages != ["preparation_started"]):
                raise ValueError("recipient dispatch continuation stage order is invalid")
            if status == "preparation_started":
                if (
                    not isinstance(material.get("delivery_ids"), list)
                    or not isinstance(material.get("projection_fingerprints"), list)
                    or len(material["delivery_ids"])
                    != len(material["projection_fingerprints"])
                ):
                    raise ValueError(
                        "recipient dispatch preparation identity is invalid")
                started_by_event[event_id] = material
            else:
                started = started_by_event[event_id]
                work_rows = material.get("works")
                if (
                    material.get("conversation_id")
                    != started.get("conversation_id")
                    or material.get("route_receipt_fingerprint")
                    != started.get("route_receipt_fingerprint")
                    or not isinstance(work_rows, list)
                    or [item.get("delivery_id") for item in work_rows]
                    != started.get("delivery_ids")
                    or [item.get("projection_fingerprint") for item in work_rows]
                    != started.get("projection_fingerprints")
                ):
                    raise ValueError(
                        "recipient dispatch prepared lineage is invalid")
            stages.append(status)
        return rows

    def _append(self, material: Mapping[str, Any]) -> dict[str, Any]:
        row = dict(material, fingerprint=model_data_fingerprint(material))
        os.makedirs(os.path.dirname(self.path), exist_ok=True)
        try:
            with open(self.path, "a", encoding="utf-8", newline="") as handle:
                handle.write(json.dumps(row, ensure_ascii=False) + "\n")
                handle.flush()
                if self._durable_writes:
                    os.fsync(handle.fileno())
        except OSError as exc:
            raise ValueError("recipient dispatch continuation write failed") from exc
        return row

    def begin(
        self, *, route: RuntimeProductOutputRoute,
        local_pairs: tuple[tuple[object, object], ...],
    ) -> tuple[dict[str, Any] | None, bool]:
        receipt = dict(thaw_model_data(route.receipt))
        event_id = receipt["event_id"]
        route_fingerprint = receipt["fingerprint"]
        with self._lock:
            rows = self.read()
            matches = tuple(row for row in rows if row.get("event_id") == event_id)
            if matches:
                prepared = tuple(
                    row for row in matches if row.get("status") == "prepared")
                if prepared:
                    if len(prepared) != 1:
                        raise ValueError(
                            "recipient dispatch continuation has duplicate prepared stage")
                    return prepared[0], True
                raise RuntimeError(
                    "recipient dispatch preparation is incomplete; provider result "
                    "cannot be replayed")
            material = {
                "version": PRODUCT_RECIPIENT_DISPATCH_CONTINUATION_VERSION,
                "status": "preparation_started",
                "conversation_id": receipt["conversation_id"],
                "event_id": event_id,
                "route_receipt_fingerprint": route_fingerprint,
                "delivery_ids": [
                    projection.delivery_id for _, projection in local_pairs
                ],
                "projection_fingerprints": [
                    model_data_fingerprint(projection.as_record())
                    for _, projection in local_pairs
                ],
                "authority": {
                    "source_write": False,
                    "route": False,
                    "opening": False,
                    "dynamic_tick": False,
                    "writer": False,
                    "send": False,
                },
            }
            self._append(material)
            return None, False

    def complete(
        self, *, route: RuntimeProductOutputRoute,
        works: tuple[FormalProductRecipientIngressWork, ...],
    ) -> dict[str, Any]:
        receipt = dict(thaw_model_data(route.receipt))
        material = {
            "version": PRODUCT_RECIPIENT_DISPATCH_CONTINUATION_VERSION,
            "status": "prepared",
            "conversation_id": receipt["conversation_id"],
            "event_id": receipt["event_id"],
            "route_receipt_fingerprint": receipt["fingerprint"],
            "works": [_work_record(work) for work in works],
            "authority": {
                "source_write": False,
                "route": False,
                "opening": False,
                "dynamic_tick": False,
                "writer": False,
                "send": False,
            },
        }
        with self._lock:
            rows = self.read()
            matches = tuple(
                row for row in rows
                if row.get("event_id") == receipt["event_id"]
                and row.get("status") == "prepared"
            )
            candidate = dict(material, fingerprint=model_data_fingerprint(material))
            if matches:
                if len(matches) != 1 or matches[0] != candidate:
                    raise ValueError("recipient dispatch prepared continuation conflicts")
                return matches[0]
            return self._append(material)


def _work_record(work: FormalProductRecipientIngressWork) -> dict[str, Any]:
    projection = next(
        item for item in work.route.projections
        if item.recipient_participant_id == work.recipient_participant_id)
    return {
        "recipient_participant_id": work.recipient_participant_id,
        "delivery_id": projection.delivery_id,
        "projection_fingerprint": model_data_fingerprint(projection.as_record()),
        "semantic_projection": (
            None if work.semantic_projection is None
            else thaw_model_data(work.semantic_projection)
        ),
        "measurement_evidence": (
            None if work.measurement_evidence is None
            else thaw_model_data(work.measurement_evidence)
        ),
        "observation_planning_payload": (
            None if work.observation_planning_payload is None
            else thaw_model_data(work.observation_planning_payload)
        ),
    }


def _works_from_prepared(
    *, row: Mapping[str, Any], route: RuntimeProductOutputRoute,
    registry: ProductConversationSessionRegistry,
) -> tuple[FormalProductRecipientIngressWork, ...]:
    material = _RecipientDispatchJournal._verified(row)
    route_receipt = dict(thaw_model_data(route.receipt))
    if (
        material.get("conversation_id") != route_receipt.get("conversation_id")
        or material.get("event_id") != route_receipt.get("event_id")
        or material.get("route_receipt_fingerprint")
        != route_receipt.get("fingerprint")
        or not isinstance(material.get("works"), list)
    ):
        raise ValueError("recipient dispatch continuation differs from route")
    projections = {
        item.recipient_participant_id: item for item in route.projections
    }
    works = []
    seen = set()
    for record in material["works"]:
        if not isinstance(record, Mapping):
            raise ValueError("recipient dispatch prepared work is invalid")
        recipient_id = str(record.get("recipient_participant_id") or "").strip()
        projection = projections.get(recipient_id)
        if (
            projection is None
            or recipient_id in seen
            or record.get("delivery_id") != projection.delivery_id
            or record.get("projection_fingerprint")
            != model_data_fingerprint(projection.as_record())
        ):
            raise ValueError("recipient dispatch prepared work differs from route")
        seen.add(recipient_id)
        works.append(FormalProductRecipientIngressWork(
            route=route,
            conversation_registry=registry,
            recipient_participant_id=recipient_id,
            semantic_projection=record.get("semantic_projection"),
            measurement_evidence=record.get("measurement_evidence"),
            observation_planning_payload=record.get(
                "observation_planning_payload"),
        ))
    return tuple(works)


def _verified_route(
    route: RuntimeProductOutputRoute,
    registry: ProductConversationSessionRegistry,
) -> tuple[object, tuple[tuple[object, object], ...]]:
    if not isinstance(route, RuntimeProductOutputRoute):
        raise TypeError("formal dispatch requires RuntimeProductOutputRoute")
    if not isinstance(registry, ProductConversationSessionRegistry):
        raise TypeError("formal dispatch requires ProductConversationSessionRegistry")
    receipt = dict(thaw_model_data(route.receipt))
    fingerprint = receipt.pop("fingerprint", None)
    if model_data_fingerprint(receipt) != fingerprint:
        raise ValueError("formal dispatch route fingerprint is invalid")
    conversation_id = str(receipt.get("conversation_id") or "").strip()
    session = registry.load_session(conversation_id)
    if session is None:
        raise ValueError("formal dispatch conversation is not registered")
    pairs = tuple(zip(route.deliveries, route.projections))
    if (
        receipt.get("version") != "runtime-product-output-route-v1"
        or receipt.get("status") != "canonical_output_route_committed"
        or len(pairs) != len(route.deliveries)
        or len(route.deliveries) != len(route.projections)
        or receipt.get("deliveries")
        != [delivery.as_record() for delivery in route.deliveries]
        or receipt.get("projection_fingerprints")
        != [model_data_fingerprint(projection.as_record()) for projection in route.projections]
    ):
        raise ValueError("formal dispatch route object differs from receipt")
    for delivery, projection in pairs:
        if (
            projection.delivery_id != delivery.delivery_id
            or projection.source_event_id != delivery.source_event_id
            or projection.conversation_id != delivery.conversation_id
            or projection.recipient_participant_id
            != delivery.recipient_participant_id
            or projection.sender_participant_id != delivery.sender_participant_id
            or projection.delivery_scope != delivery.delivery_scope
            or projection.conversation_id != conversation_id
            or projection.source_event_id != receipt.get("event_id")
            or projection.membership_version
            != session.conversation.membership_version
            or projection.member_snapshot_ids
            != session.conversation.active_member_ids
        ):
            raise ValueError("formal dispatch route projection differs from delivery")
    if session.conversation.membership_version != receipt.get("membership_version"):
        raise ValueError("formal dispatch route differs from registered conversation")
    return session, pairs


def dispatch_formal_product_output_route(
    *,
    route: RuntimeProductOutputRoute,
    conversation_registry: ProductConversationSessionRegistry,
    recipient_actors: Mapping[str, object],
    addressed_measurement_producers: Mapping[
        str, Callable[[object], PreparedFormalRecipientMeasurement]
    ],
    addressed_observation_planning_payloads: (
        Mapping[str, Mapping[str, Any]] | None
    ) = None,
) -> FormalProductRecipientDispatch:
    """Prepare every local delivery, then queue exactly one typed work each.

    Preparation is deliberately completed before the first Actor post.  A
    semantic/provider/package failure therefore cannot leave a partially
    dispatched route.  Queue execution remains owner-local and idempotent at
    recipient ingress.
    """
    if not isinstance(recipient_actors, Mapping):
        raise TypeError("formal dispatch recipient_actors must be a mapping")
    if not isinstance(addressed_measurement_producers, Mapping):
        raise TypeError("formal dispatch producers must be a mapping")
    if (
        addressed_observation_planning_payloads is not None
        and not isinstance(addressed_observation_planning_payloads, Mapping)
    ):
        raise TypeError(
            "formal dispatch observation planning payloads must be a mapping")
    session, pairs = _verified_route(route, conversation_registry)
    participants = session.participants_by_id
    local_pairs = tuple(
        (delivery, projection)
        for delivery, projection in pairs
        if participants[projection.recipient_participant_id].capabilities.has_local_store
        and participants[projection.recipient_participant_id].capabilities.receive_projection
    )
    local_ids = {projection.recipient_participant_id for _, projection in local_pairs}
    if set(recipient_actors) != local_ids:
        raise ValueError("formal dispatch actor set must exactly match local recipients")
    addressed_ids = {
        projection.recipient_participant_id
        for _, projection in local_pairs
        if projection.delivery_scope == "addressed"
    }
    if set(addressed_measurement_producers) != addressed_ids:
        raise ValueError(
            "formal dispatch producer set must exactly match addressed local recipients")
    if (
        addressed_observation_planning_payloads is not None
        and set(addressed_observation_planning_payloads) != addressed_ids
    ):
        raise ValueError(
            "formal dispatch observation planning payload set must exactly "
            "match addressed local recipients")
    for recipient_id, actor in recipient_actors.items():
        if not callable(getattr(actor, "post_formal_product_recipient_ingress", None)):
            raise TypeError(
                f"formal dispatch actor lacks typed ingress: {recipient_id}")

    if not local_pairs:
        return FormalProductRecipientDispatch(works=())
    journal = _RecipientDispatchJournal(
        conversation_registry,
        local_pairs[0][1].conversation_id,
    )
    prepared_row, already_prepared = journal.begin(
        route=route, local_pairs=local_pairs)
    if already_prepared:
        works = _works_from_prepared(
            row=prepared_row,
            route=route,
            registry=conversation_registry,
        )
        if {work.recipient_participant_id for work in works} != local_ids:
            raise ValueError(
                "recipient dispatch continuation local recipient set differs")
        dispatch = FormalProductRecipientDispatch(works=works)
        for work in dispatch.works:
            recipient_actors[
                work.recipient_participant_id
            ].post_formal_product_recipient_ingress(work)
        return dispatch

    works = []
    for _delivery, projection in local_pairs:
        recipient_id = projection.recipient_participant_id
        if projection.delivery_scope == "addressed":
            producer = addressed_measurement_producers[recipient_id]
            if not callable(producer):
                raise TypeError(
                    f"formal dispatch producer is not callable: {recipient_id}")
            prepared = producer(projection)
            if not isinstance(prepared, PreparedFormalRecipientMeasurement):
                raise TypeError("formal dispatch producer returned invalid measurement")
            if (
                prepared.recipient_participant_id != recipient_id
                or prepared.delivery_id != projection.delivery_id
            ):
                raise ValueError(
                    "formal dispatch measurement differs from route projection")
            semantic_projection = prepared.semantic_projection
            measurement_evidence = prepared.measurement_evidence
            observation_planning_payload = (
                addressed_observation_planning_payloads[recipient_id]
                if addressed_observation_planning_payloads is not None
                else None
            )
            if (
                observation_planning_payload is not None
                and not isinstance(observation_planning_payload, Mapping)
            ):
                raise TypeError(
                    "formal dispatch observation planning payload is invalid")
        elif projection.delivery_scope == "observed":
            semantic_projection = None
            measurement_evidence = None
            observation_planning_payload = None
        else:
            raise ValueError("formal dispatch projection scope is invalid")
        works.append(FormalProductRecipientIngressWork(
            route=route,
            conversation_registry=conversation_registry,
            recipient_participant_id=recipient_id,
            semantic_projection=semantic_projection,
            measurement_evidence=measurement_evidence,
            observation_planning_payload=observation_planning_payload,
        ))

    works = tuple(works)
    prepared_row = journal.complete(route=route, works=works)
    persisted_works = _works_from_prepared(
        row=prepared_row,
        route=route,
        registry=conversation_registry,
    )
    dispatch = FormalProductRecipientDispatch(works=persisted_works)
    for work in dispatch.works:
        recipient_actors[
            work.recipient_participant_id
        ].post_formal_product_recipient_ingress(work)
    return dispatch


def recover_prepared_formal_product_recipient_dispatches(
    *, conversation_registry: ProductConversationSessionRegistry,
    recipient_actors: Mapping[str, object],
    recipient_work_completion_checks: Mapping[str, Callable[[object], bool]],
) -> FormalProductRecipientDispatch:
    """Requeue archived route deliveries missing any required owner stage.

    No measurement producer is accepted by this API.  Recovery can therefore
    only consume the already archived products and cannot repeat Agent 1.
    """
    if not isinstance(
        conversation_registry, ProductConversationSessionRegistry
    ):
        raise TypeError("recipient dispatch recovery requires registry")
    if not isinstance(recipient_actors, Mapping):
        raise TypeError("recipient dispatch recovery actors must be a mapping")
    if not isinstance(recipient_work_completion_checks, Mapping):
        raise TypeError(
            "recipient dispatch recovery completion checks must be a mapping")
    if set(recipient_actors) != set(recipient_work_completion_checks):
        raise ValueError("recipient dispatch recovery owner sets differ")
    completion_checks = {}
    for recipient_id, actor in recipient_actors.items():
        if not callable(getattr(
            actor, "post_formal_product_recipient_ingress", None,
        )):
            raise TypeError(
                f"recipient dispatch recovery actor lacks typed ingress: {recipient_id}")
        check = recipient_work_completion_checks[recipient_id]
        if not callable(check):
            raise TypeError(
                "recipient dispatch recovery completion check is invalid")
        completion_checks[recipient_id] = check

    recovered = []
    for registered in conversation_registry.registered_sessions():
        conversation_id = registered.conversation.conversation_id
        journal = _RecipientDispatchJournal(
            conversation_registry, conversation_id)
        if not os.path.isfile(journal.path):
            continue
        rows = journal.read()
        by_event = {}
        for row in rows:
            by_event.setdefault(row["event_id"], []).append(row)
        routes = {
            dict(thaw_model_data(route.receipt))["event_id"]: route
            for route in restore_committed_product_output_routes(
                conversation_registry=conversation_registry,
                conversation_id=conversation_id,
            )
        }
        for event_id, stages in by_event.items():
            prepared = tuple(
                row for row in stages if row.get("status") == "prepared")
            if not prepared:
                raise RuntimeError(
                    "recipient dispatch preparation is incomplete; provider "
                    "result cannot be replayed")
            if len(prepared) != 1 or event_id not in routes:
                raise ValueError(
                    "recipient dispatch continuation lacks exact committed route")
            works = _works_from_prepared(
                row=prepared[0],
                route=routes[event_id],
                registry=conversation_registry,
            )
            for work in works:
                recipient_id = work.recipient_participant_id
                if recipient_id not in recipient_actors:
                    continue
                if completion_checks[recipient_id](work):
                    continue
                recovered.append(work)
    dispatch = FormalProductRecipientDispatch(works=tuple(recovered))
    for work in dispatch.works:
        recipient_actors[
            work.recipient_participant_id
        ].post_formal_product_recipient_ingress(work)
    return dispatch


__all__ = (
    "FormalProductRecipientDispatch",
    "PRODUCT_RECIPIENT_DISPATCH_CONTINUATION_VERSION",
    "PreparedFormalRecipientMeasurement",
    "dispatch_formal_product_output_route",
    "recover_prepared_formal_product_recipient_dispatches",
)
