"""Durable owner-local observation plans derived from committed lifecycles.

The interaction model publishes opaque advice.  The bound TemporalDomain turns
that advice into an opaque target.  This module only verifies lineage and builds
an append-only profile value; it never schedules, observes, commits an action,
or sends output.
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
from framework.model_api.observation import read_observation_plan
from framework.model_runtime import BoundInteractionModel
from framework.temporal_domain import ObservationTarget
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_product_fact_lifecycle import RuntimeProductFactLifecycle
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


RUNTIME_OBSERVATION_PLAN_VERSION = "runtime-product-observation-plan-v3"
_LEGACY_OBSERVATION_PLAN_VERSION = "runtime-product-observation-plan-v2"
RUNTIME_OBSERVATION_PLAN_JOURNAL_KEY = "runtime_product_observation_plans"
RUNTIME_ACTIVE_OBSERVATION_PLAN_KEY = "runtime_active_observation_plan"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _verified_record(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be record data")
    record = dict(thaw_model_data(value))
    fingerprint = record.pop("fingerprint", None)
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError(f"{label} fingerprint is invalid")
    record["fingerprint"] = fingerprint
    return record


def _stage_identity_value(
    stage: Mapping[str, Any], *, neutral: str, legacy: str,
) -> object:
    """Read persisted model identity without making product names canonical."""
    return stage.get(neutral) if neutral in stage else stage.get(legacy)


@dataclass(frozen=True)
class RuntimeProductObservationPlan:
    plan_id: str
    source_kind: str
    source_id: str
    delivery_id: str | None
    event_id: str
    destination_conversation_id: str | None
    target: ObservationTarget
    observation_advice: Mapping[str, Any]
    planning_payload: Mapping[str, Any]
    receipt: Mapping[str, Any]
    already_committed: bool

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observation_advice",
            freeze_model_payload({"value": self.observation_advice})["value"],
        )
        object.__setattr__(
            self,
            "planning_payload",
            freeze_model_payload({"value": self.planning_payload})["value"],
        )
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _plan_id(source_kind: str, source_id: str) -> str:
    return (
        f"observation-plan:{_identifier(source_kind, 'source_kind')}:"
        f"{_identifier(source_id, 'source_id')}"
    )


def _build_product_observation_plan_from_lifecycle(
    *,
    source_kind: str,
    source_id: str,
    delivery_id: str | None,
    lifecycle: RuntimeProductFactLifecycle,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    planning_payload: Mapping[str, Any],
) -> RuntimeProductObservationPlan:
    """Produce model advice and one TemporalDomain target without authority."""
    source_kind = _identifier(source_kind, "source_kind")
    source_id = _identifier(source_id, "source_id")
    if source_kind == "recipient_delivery":
        delivery_id = _identifier(delivery_id, "delivery_id")
        if source_id != delivery_id:
            raise ValueError("recipient plan source differs from delivery")
    elif source_kind == "committed_output_action":
        if delivery_id is not None:
            raise ValueError("output action plan cannot carry delivery identity")
    else:
        raise ValueError("unsupported lifecycle observation plan source")
    if not isinstance(lifecycle, RuntimeProductFactLifecycle):
        raise ValueError("observation planning requires a committed lifecycle")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("observation planning requires a bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("observation planning requires a bound temporal runtime")
    if not isinstance(planning_payload, Mapping):
        raise ValueError("planning_payload must be a mapping")
    if "shared_projection" in planning_payload:
        raise ValueError("planning_payload cannot replace shared_projection")
    session = lifecycle.transaction.session
    binding = session.session_binding
    if (
        model_runtime.session_id != binding.session_id
        or model_runtime.binding != binding.model_binding
        or temporal_runtime.binding != binding.temporal_binding
    ):
        raise ValueError("observation planning runtimes differ from lifecycle")
    expression = lifecycle.expression
    context = expression.context
    if (
        context.temporal_position is None
        or context.runtime_ordinal != session.position.runtime_ordinal
        or context.temporal_position.fingerprint
        != session.position.position.fingerprint
    ):
        raise ValueError("observation planning lifecycle position is stale")
    shared_execution = expression.shared_projection_execution
    shared_projection = shared_execution.payload.get("shared_projection")
    if not isinstance(shared_projection, Mapping):
        raise ValueError("observation planning lacks shared projection")
    frozen_planning_payload = freeze_model_payload(planning_payload)
    invocation_payload = {
        "shared_projection": shared_projection,
        **dict(thaw_model_data(frozen_planning_payload)),
    }
    execution = model_runtime.invoke(
        "plan_observation", payload=invocation_payload, context=context)
    advice = read_observation_plan(
        execution,
        expected_context=context,
        shared_projection_execution=shared_execution,
    )
    target = temporal_runtime.resolve_observation_target(
        context.temporal_position, advice.observation_advice)
    plan_id = _plan_id(source_kind, source_id)
    material = {
        "version": RUNTIME_OBSERVATION_PLAN_VERSION,
        "status": "observation_target_planned",
        "plan_id": plan_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "delivery_id": delivery_id,
        "session_binding_fingerprint": binding.fingerprint,
        "event_id": lifecycle.evidence.event_id,
        "participant_id": lifecycle.evidence.participant_id,
        "conversation_id": lifecycle.evidence.conversation_id,
        "destination_conversation_id": lifecycle.evidence.conversation_id,
        "runtime_ordinal": context.runtime_ordinal,
        "source_lifecycle_receipt_fingerprint": model_data_fingerprint(
            lifecycle.receipt),
        "source_shared_projection_output_fingerprint": (
            shared_execution.receipt.output_fingerprint),
        "planning_payload_fingerprint": model_data_fingerprint(
            frozen_planning_payload),
        "planning_payload": thaw_model_data(frozen_planning_payload),
        "plan_stage_receipt": execution.receipt.to_record(),
        "observation_advice": thaw_model_data(advice.observation_advice),
        "observation_projection_fingerprint": model_data_fingerprint(
            advice.observation_projection),
        "observation_advice_fingerprint": model_data_fingerprint(
            advice.observation_advice),
        "origin_position": context.temporal_position.to_record(),
        "target": target.to_record(),
        "authority": {
            "position_advance": False,
            "due_proof": False,
            "action_assessment": False,
            "schedule": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeProductObservationPlan(
        plan_id=plan_id,
        source_kind=source_kind,
        source_id=source_id,
        delivery_id=delivery_id,
        event_id=lifecycle.evidence.event_id,
        destination_conversation_id=lifecycle.evidence.conversation_id,
        target=target,
        observation_advice=advice.observation_advice,
        planning_payload=frozen_planning_payload,
        receipt=receipt,
        already_committed=False,
    )


def build_product_observation_plan(
    *,
    delivery_id: str,
    lifecycle: RuntimeProductFactLifecycle,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    planning_payload: Mapping[str, Any],
) -> RuntimeProductObservationPlan:
    """Build the post-recipient plan used by the W5 ingress path."""
    delivery_id = _identifier(delivery_id, "delivery_id")
    return _build_product_observation_plan_from_lifecycle(
        source_kind="recipient_delivery",
        source_id=delivery_id,
        delivery_id=delivery_id,
        lifecycle=lifecycle,
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        planning_payload=planning_payload,
    )


def build_product_observation_plan_from_output(
    *,
    action_id: str,
    lifecycle: RuntimeProductFactLifecycle,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    planning_payload: Mapping[str, Any],
) -> RuntimeProductObservationPlan:
    """Build a replacement target from the owner's committed output state."""
    action_id = _identifier(action_id, "action_id")
    return _build_product_observation_plan_from_lifecycle(
        source_kind="committed_output_action",
        source_id=action_id,
        delivery_id=None,
        lifecycle=lifecycle,
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        planning_payload=planning_payload,
    )


def _restore_plan(
    record: Mapping[str, Any],
    *,
    session_binding,
    temporal_runtime: BoundTemporalDomain,
    already_committed: bool,
) -> RuntimeProductObservationPlan:
    verified = _verified_record(record, "observation plan receipt")
    if (
        verified.get("version") not in {
            RUNTIME_OBSERVATION_PLAN_VERSION,
            _LEGACY_OBSERVATION_PLAN_VERSION,
        }
        or verified.get("status") != "observation_target_planned"
        or verified.get("session_binding_fingerprint")
        != session_binding.fingerprint
        or temporal_runtime.binding != session_binding.temporal_binding
    ):
        raise ValueError("observation plan receipt binding is invalid")
    source_kind = _identifier(verified.get("source_kind"), "source_kind")
    source_id = _identifier(verified.get("source_id"), "source_id")
    if source_kind not in {
        "recipient_delivery",
        "committed_output_action",
        "autonomous_tick",
    }:
        raise ValueError("observation plan source kind is invalid")
    delivery_id = verified.get("delivery_id")
    if source_kind == "recipient_delivery":
        delivery_id = _identifier(delivery_id, "delivery_id")
        if delivery_id != source_id:
            raise ValueError("observation plan delivery source is invalid")
    elif delivery_id is not None:
        raise ValueError("tick observation plan cannot carry delivery identity")
    destination_conversation_id = verified.get("destination_conversation_id")
    if destination_conversation_id is None and (
        verified.get("version") == _LEGACY_OBSERVATION_PLAN_VERSION
        and source_kind == "recipient_delivery"
    ):
        destination_conversation_id = verified.get("conversation_id")
    if destination_conversation_id is not None:
        destination_conversation_id = _identifier(
            destination_conversation_id, "destination_conversation_id")
    if (
        verified.get("version") == RUNTIME_OBSERVATION_PLAN_VERSION
        and destination_conversation_id is None
    ):
        raise ValueError("observation plan destination lineage is missing")
    plan_id = _identifier(verified.get("plan_id"), "plan_id")
    if plan_id != _plan_id(source_kind, source_id):
        raise ValueError("observation plan identity is invalid")
    origin_record = verified.get("origin_position")
    target_record = verified.get("target")
    advice = verified.get("observation_advice")
    planning_payload = verified.get("planning_payload")
    if (
        not isinstance(origin_record, Mapping)
        or not isinstance(target_record, Mapping)
        or not isinstance(advice, Mapping)
        or not isinstance(planning_payload, Mapping)
    ):
        raise ValueError("observation plan temporal products are invalid")
    frozen_planning_payload = freeze_model_payload(planning_payload)
    if verified.get("planning_payload_fingerprint") != model_data_fingerprint(
        frozen_planning_payload
    ):
        raise ValueError("observation plan planning payload is invalid")
    origin = temporal_runtime.normalize_position(origin_record.get("coordinate"))
    if origin.to_record() != dict(origin_record):
        raise ValueError("observation plan origin position is invalid")
    target = temporal_runtime.resolve_observation_target(origin, advice)
    if target.to_record() != dict(target_record):
        raise ValueError("observation plan target is invalid")
    stage = verified.get("plan_stage_receipt")
    expected_stage = (
        "plan_observation"
        if source_kind in {
            "recipient_delivery", "committed_output_action",
        }
        else "assess_action"
    )
    if (
        not isinstance(stage, Mapping)
        or stage.get("stage") != expected_stage
        or stage.get("session_id") != session_binding.session_id
        or _stage_identity_value(
            stage, neutral="event_ref", legacy="event_id",
        ) != verified.get("event_id")
        or _stage_identity_value(
            stage, neutral="subject_ref", legacy="participant_id",
        ) != verified.get("participant_id")
        or _stage_identity_value(
            stage, neutral="scope_ref", legacy="conversation_id",
        ) != verified.get("conversation_id")
        or stage.get("runtime_ordinal") != verified.get("runtime_ordinal")
        or stage.get("operator_version")
        != session_binding.model_binding.operator_versions.get(
            expected_stage)
    ):
        raise ValueError("observation plan stage receipt is invalid")
    if verified.get("observation_advice_fingerprint") != model_data_fingerprint(
        advice
    ):
        raise ValueError("observation plan advice is invalid")
    return RuntimeProductObservationPlan(
        plan_id=plan_id,
        source_kind=source_kind,
        source_id=source_id,
        delivery_id=delivery_id,
        event_id=_identifier(verified.get("event_id"), "event_id"),
        destination_conversation_id=destination_conversation_id,
        target=target,
        observation_advice=advice,
        planning_payload=frozen_planning_payload,
        receipt=record,
        already_committed=already_committed,
    )


def _journal(profile: Mapping[str, Any]) -> tuple[Mapping[str, Any], ...]:
    raw = profile.get(RUNTIME_OBSERVATION_PLAN_JOURNAL_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("observation plan journal is invalid")
    return tuple(raw)


def load_product_observation_plan(
    profile: Mapping[str, Any],
    *,
    delivery_id: str,
    session_binding,
    temporal_runtime: BoundTemporalDomain,
) -> RuntimeProductObservationPlan | None:
    """Load one exact plan; absence is distinct from malformed persisted data."""
    target_id = _identifier(delivery_id, "delivery_id")
    restored = tuple(
        _restore_plan(
            item,
            session_binding=session_binding,
            temporal_runtime=temporal_runtime,
            already_committed=True,
        )
        for item in _journal(profile)
    )
    matches = tuple(
        item for item in restored
        if item.source_kind == "recipient_delivery"
        and item.delivery_id == target_id
    )
    if len(matches) > 1:
        raise ValueError("observation plan delivery is not unique")
    return matches[0] if matches else None


def load_product_observation_plan_by_id(
    profile: Mapping[str, Any],
    *,
    plan_id: str,
    session_binding,
    temporal_runtime: BoundTemporalDomain,
) -> RuntimeProductObservationPlan | None:
    target_id = _identifier(plan_id, "plan_id")
    matches = tuple(
        item
        for item in (
            _restore_plan(
                row,
                session_binding=session_binding,
                temporal_runtime=temporal_runtime,
                already_committed=True,
            )
            for row in _journal(profile)
        )
        if item.plan_id == target_id
    )
    if len(matches) > 1:
        raise ValueError("observation plan identity is not unique")
    return matches[0] if matches else None


def _active_pointer(plan: RuntimeProductObservationPlan) -> dict[str, Any]:
    material = {
        "version": "runtime-active-observation-plan-v1",
        "plan_id": plan.plan_id,
        "source_kind": plan.source_kind,
        "source_id": plan.source_id,
        "plan_receipt_fingerprint": model_data_fingerprint(plan.receipt),
        "target_fingerprint": plan.target.fingerprint,
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


def commit_product_observation_plan(
    session: BootstrappedRuntimeSession,
    *,
    plan: RuntimeProductObservationPlan,
    temporal_runtime: BoundTemporalDomain,
) -> tuple[BootstrappedRuntimeSession, RuntimeProductObservationPlan]:
    """Append a plan and atomically replace only the active pointer."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("observation plan commit requires runtime session")
    if plan.target.origin_position.fingerprint != session.position.position.fingerprint:
        raise ValueError("observation plan origin is not the current position")
    existing = load_product_observation_plan_by_id(
        session.profile,
        plan_id=plan.plan_id,
        session_binding=session.session_binding,
        temporal_runtime=temporal_runtime,
    )
    if existing is not None:
        if existing.receipt != plan.receipt:
            raise ValueError("observation plan conflicts with delivery")
        return session, existing
    profile = dict(session.profile)
    profile[RUNTIME_OBSERVATION_PLAN_JOURNAL_KEY] = [
        *(_verified_record(item, "observation plan receipt")
          for item in _journal(session.profile)),
        dict(thaw_model_data(plan.receipt)),
    ]
    profile[RUNTIME_ACTIVE_OBSERVATION_PLAN_KEY] = _active_pointer(plan)
    return replace(session, profile=profile), plan


def load_active_product_observation_plan(
    profile: Mapping[str, Any],
    *,
    session_binding,
    temporal_runtime: BoundTemporalDomain,
) -> RuntimeProductObservationPlan:
    pointer = _verified_record(
        profile.get(RUNTIME_ACTIVE_OBSERVATION_PLAN_KEY),
        "active observation plan pointer",
    )
    if pointer.get("version") != "runtime-active-observation-plan-v1":
        raise ValueError("active observation plan pointer is invalid")
    plan = load_product_observation_plan_by_id(
        profile,
        plan_id=pointer.get("plan_id"),
        session_binding=session_binding,
        temporal_runtime=temporal_runtime,
    )
    if (
        plan is None
        or pointer.get("source_kind") != plan.source_kind
        or pointer.get("source_id") != plan.source_id
        or pointer.get("plan_receipt_fingerprint")
        != model_data_fingerprint(plan.receipt)
        or pointer.get("target_fingerprint") != plan.target.fingerprint
    ):
        raise ValueError("active observation plan pointer differs from journal")
    return plan


def build_product_observation_plan_from_tick(
    *,
    observation,
    consumed_plan: RuntimeProductObservationPlan,
    temporal_runtime: BoundTemporalDomain,
) -> RuntimeProductObservationPlan:
    """Build the next target solely from one settled formal tick assessment."""
    from ops.runtime_autonomous_observation import (
        RuntimeAutonomousObservationTransaction,
    )

    if not isinstance(observation, RuntimeAutonomousObservationTransaction):
        raise ValueError("next observation plan requires formal tick observation")
    if not isinstance(consumed_plan, RuntimeProductObservationPlan):
        raise ValueError("next observation plan requires consumed plan")
    session = observation.session
    if temporal_runtime.binding != session.session_binding.temporal_binding:
        raise ValueError("next observation plan temporal binding differs")
    if observation.due_proof.target.fingerprint != consumed_plan.target.fingerprint:
        raise ValueError("tick observation did not consume the supplied plan")
    advice = observation.assessment.observation_advice
    target = temporal_runtime.resolve_observation_target(
        session.position.position, advice)
    source_kind = "autonomous_tick"
    source_id = _identifier(
        observation.receipt.get("tick_event_id"), "tick_event_id")
    stage = observation.assessment_execution.receipt
    plan_id = _plan_id(source_kind, source_id)
    material = {
        "version": RUNTIME_OBSERVATION_PLAN_VERSION,
        "status": "observation_target_planned",
        "plan_id": plan_id,
        "source_kind": source_kind,
        "source_id": source_id,
        "delivery_id": None,
        "session_binding_fingerprint": session.session_binding.fingerprint,
        "event_id": source_id,
        "participant_id": observation.receipt.get("participant_id"),
        "conversation_id": None,
        "destination_conversation_id": (
            consumed_plan.destination_conversation_id),
        "runtime_ordinal": session.position.runtime_ordinal,
        "source_lifecycle_receipt_fingerprint": None,
        "source_observation_receipt_fingerprint": model_data_fingerprint(
            observation.receipt),
        "source_shared_projection_output_fingerprint": (
            observation.shared_projection_execution.receipt.output_fingerprint),
        "planning_payload_fingerprint": model_data_fingerprint(
            consumed_plan.planning_payload),
        "planning_payload": thaw_model_data(consumed_plan.planning_payload),
        "plan_stage_receipt": stage.to_record(),
        "observation_advice": thaw_model_data(advice),
        "observation_projection_fingerprint": None,
        "observation_advice_fingerprint": model_data_fingerprint(advice),
        "origin_position": session.position.position.to_record(),
        "target": target.to_record(),
        "authority": {
            "position_advance": False,
            "due_proof": False,
            "action_assessment": False,
            "schedule": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeProductObservationPlan(
        plan_id=plan_id,
        source_kind=source_kind,
        source_id=source_id,
        delivery_id=None,
        event_id=source_id,
        destination_conversation_id=(
            consumed_plan.destination_conversation_id),
        target=target,
        observation_advice=advice,
        planning_payload=consumed_plan.planning_payload,
        receipt=receipt,
        already_committed=False,
    )


__all__ = (
    "RUNTIME_ACTIVE_OBSERVATION_PLAN_KEY",
    "RUNTIME_OBSERVATION_PLAN_JOURNAL_KEY",
    "RUNTIME_OBSERVATION_PLAN_VERSION",
    "RuntimeProductObservationPlan",
    "build_product_observation_plan",
    "build_product_observation_plan_from_output",
    "build_product_observation_plan_from_tick",
    "commit_product_observation_plan",
    "load_active_product_observation_plan",
    "load_product_observation_plan",
    "load_product_observation_plan_by_id",
)
