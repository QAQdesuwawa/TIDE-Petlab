"""Atomic product commit for one due owner-local formal observation.

The product consumes the exact durable active plan, settles one formal tick,
records its model/Kernal evidence, and rotates the next active target in one
new session value.  Persistence remains the Runner's single responsibility.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, replace
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import RuntimeKernelContractError
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_autonomous_observation import (
    RuntimeAutonomousObservationTransaction,
    execute_runtime_autonomous_observation_transaction,
)
from ops.runtime_committed_autonomous_grant import (
    RuntimeCommittedAutonomousGrant,
    grant_from_observation,
    restore_committed_autonomous_grant,
)
from ops.runtime_product_observation_due import (
    probe_active_product_observation_due,
)
from ops.runtime_product_observation_plan import (
    RuntimeProductObservationPlan,
    build_product_observation_plan_from_tick,
    commit_product_observation_plan,
    load_product_observation_plan_by_id,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


RUNTIME_PRODUCT_OBSERVATION_TICK_VERSION = (
    "runtime-product-observation-tick-v2"
)
RUNTIME_PRODUCT_OBSERVATION_TICK_JOURNAL_KEY = (
    "runtime_product_observation_ticks"
)


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _verified_record(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError("observation tick journal row must be record data")
    record = dict(thaw_model_data(value))
    fingerprint = record.pop("fingerprint", None)
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError("observation tick journal fingerprint is invalid")
    record["fingerprint"] = fingerprint
    return record


def _verified_nested_record(value: object, label: str) -> dict[str, Any]:
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
    """Read neutral v2 receipts while retaining persisted v1 replay."""
    return stage.get(neutral) if neutral in stage else stage.get(legacy)


def _journal(profile: Mapping[str, Any]) -> tuple[dict[str, Any], ...]:
    raw = profile.get(RUNTIME_PRODUCT_OBSERVATION_TICK_JOURNAL_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("observation tick journal is invalid")
    return tuple(_verified_record(row) for row in raw)


@dataclass(frozen=True)
class RuntimeProductObservationTick:
    session: BootstrappedRuntimeSession
    consumed_plan: RuntimeProductObservationPlan | None
    next_plan: RuntimeProductObservationPlan
    observation: RuntimeAutonomousObservationTransaction | None
    receipt: Mapping[str, Any]
    already_committed: bool
    committed_grant: RuntimeCommittedAutonomousGrant | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))
        if self.committed_grant is None and self.observation is not None:
            if (
                self.observation.authorization is not None
                and self.observation.assessment.eligible
            ):
                object.__setattr__(
                    self,
                    "committed_grant",
                    grant_from_observation(self.observation),
                )


def _load_existing(
    session: BootstrappedRuntimeSession,
    *,
    temporal_runtime: BoundTemporalDomain,
    target_coordinate: object,
    participant_id: str,
    tick_event_id: str,
) -> RuntimeProductObservationTick | None:
    tick_id = _identifier(tick_event_id, "tick_event_id")
    matches = tuple(
        row for row in _journal(session.profile)
        if row.get("tick_event_id") == tick_id
    )
    if len(matches) > 1:
        raise ValueError("observation tick identity is not unique")
    if not matches:
        return None
    row = matches[0]
    sampled = temporal_runtime.normalize_position(target_coordinate)
    if (
        row.get("version") != RUNTIME_PRODUCT_OBSERVATION_TICK_VERSION
        or row.get("status") != "observation_tick_committed"
        or row.get("session_binding_fingerprint")
        != session.session_binding.fingerprint
        or row.get("participant_id") != _identifier(
            participant_id, "participant_id")
        or row.get("sampled_position_fingerprint") != sampled.fingerprint
    ):
        raise ValueError("committed observation tick replay differs")
    consumed_plan = load_product_observation_plan_by_id(
        session.profile,
        plan_id=row.get("consumed_plan_id"),
        session_binding=session.session_binding,
        temporal_runtime=temporal_runtime,
    )
    if (
        consumed_plan is None
        or row.get("consumed_plan_receipt_fingerprint")
        != model_data_fingerprint(consumed_plan.receipt)
    ):
        raise ValueError("committed observation tick consumed plan is invalid")
    due_probe = _verified_nested_record(
        row.get("due_probe_receipt"), "observation tick due probe")
    observation_receipt = _verified_nested_record(
        row.get("observation_receipt"), "observation tick receipt")
    shared_stage = row.get("shared_projection_stage_receipt")
    shared_output = row.get("shared_projection_output")
    assessment_stage = row.get("assessment_stage_receipt")
    assessment_output = row.get("assessment_output")
    binding = session.session_binding.model_binding
    if (
        due_probe.get("plan_id") != consumed_plan.plan_id
        or due_probe.get("target_fingerprint") != consumed_plan.target.fingerprint
        or not isinstance(due_probe.get("due_proof"), Mapping)
        or due_probe["due_proof"].get("due") is not True
        or observation_receipt.get("tick_event_id") != tick_id
        or observation_receipt.get("participant_id") != row.get("participant_id")
        or observation_receipt.get("target_fingerprint")
        != consumed_plan.target.fingerprint
        or observation_receipt.get("settled_position_fingerprint")
        != row.get("settled_position_fingerprint")
        or not isinstance(shared_stage, Mapping)
        or not isinstance(shared_output, Mapping)
        or shared_stage.get("stage") != "project_shared_semantics"
        or shared_stage.get("operator_version")
        != binding.operator_versions.get("project_shared_semantics")
        or shared_stage.get("output_fingerprint")
        != model_data_fingerprint(shared_output)
        or not isinstance(assessment_stage, Mapping)
        or not isinstance(assessment_output, Mapping)
        or assessment_stage.get("stage") != "assess_action"
        or _stage_identity_value(
            assessment_stage, neutral="event_ref", legacy="event_id",
        ) != tick_id
        or _stage_identity_value(
            assessment_stage, neutral="subject_ref", legacy="participant_id",
        ) != row.get("participant_id")
        or assessment_stage.get("operator_version")
        != binding.operator_versions.get("assess_action")
        or assessment_stage.get("output_fingerprint")
        != model_data_fingerprint(assessment_output)
    ):
        raise ValueError("committed observation tick lineage is invalid")
    shared_projection = shared_output.get("shared_projection")
    if not isinstance(shared_projection, Mapping):
        raise ValueError("committed observation tick shared projection is invalid")
    assessment_input = {
        "shared_projection": shared_projection,
        **dict(thaw_model_data(consumed_plan.planning_payload)),
    }
    if assessment_stage.get("input_fingerprint") != model_data_fingerprint(
        assessment_input
    ):
        raise ValueError("committed observation tick assessment input is invalid")
    authorization = row.get("authorization")
    if authorization is not None:
        if not isinstance(authorization, Mapping):
            raise ValueError("committed observation tick authorization is invalid")
        auth_material = dict(authorization)
        auth_fingerprint = auth_material.pop("fingerprint", None)
        if (
            model_data_fingerprint(auth_material) != auth_fingerprint
            or authorization.get("source_event_id") != tick_id
            or authorization.get("participant_id") != row.get("participant_id")
            or authorization.get("source_position_fingerprint")
            != row.get("settled_position_fingerprint")
            or authorization.get("source_runtime_ordinal")
            != row.get("settled_runtime_ordinal")
            or authorization.get("observation_due_fingerprint")
            != observation_receipt.get("due_proof_fingerprint")
            or authorization.get("action_assessment_receipt_fingerprint")
            != model_data_fingerprint(assessment_stage)
        ):
            raise ValueError("committed observation tick authorization is invalid")
    next_plan = load_product_observation_plan_by_id(
        session.profile,
        plan_id=row.get("next_plan_id"),
        session_binding=session.session_binding,
        temporal_runtime=temporal_runtime,
    )
    if (
        next_plan is None
        or row.get("next_plan_receipt_fingerprint")
        != model_data_fingerprint(next_plan.receipt)
    ):
        raise ValueError("committed observation tick next plan is invalid")
    return RuntimeProductObservationTick(
        session=session,
        consumed_plan=consumed_plan,
        next_plan=next_plan,
        observation=None,
        receipt=row,
        already_committed=True,
        committed_grant=(
            None
            if (
                authorization is None
                or row.get("settled_position_fingerprint")
                != session.position.position.fingerprint
                or row.get("settled_runtime_ordinal")
                != session.position.runtime_ordinal
            )
            else restore_committed_autonomous_grant(session=session, row=row)
        ),
    )


def execute_product_observation_tick(
    *,
    session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    target_coordinate: object,
    participant_id: str,
    tick_event_id: str,
) -> RuntimeProductObservationTick:
    """Return one new atomic session value; do not persist or publish it."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("product observation tick requires runtime session")
    existing = _load_existing(
        session,
        temporal_runtime=temporal_runtime,
        target_coordinate=target_coordinate,
        participant_id=participant_id,
        tick_event_id=tick_event_id,
    )
    if existing is not None:
        return existing

    probe = probe_active_product_observation_due(
        session,
        temporal_runtime=temporal_runtime,
        target_coordinate=target_coordinate,
    )
    if not probe.due_proof.due:
        raise RuntimeKernelContractError("autonomous observation is not due")
    consumed_plan = probe.plan
    observation = execute_runtime_autonomous_observation_transaction(
        session=session,
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        observation_target=consumed_plan.target,
        target_coordinate=target_coordinate,
        participant_id=participant_id,
        tick_event_id=tick_event_id,
        assessment_payload=consumed_plan.planning_payload,
    )
    next_plan = build_product_observation_plan_from_tick(
        observation=observation,
        consumed_plan=consumed_plan,
        temporal_runtime=temporal_runtime,
    )
    planned_session, committed_next = commit_product_observation_plan(
        observation.session,
        plan=next_plan,
        temporal_runtime=temporal_runtime,
    )
    sampled_position = probe.sampled_position
    authorization = (
        None if observation.authorization is None
        else asdict(observation.authorization)
    )
    material = {
        "version": RUNTIME_PRODUCT_OBSERVATION_TICK_VERSION,
        "status": "observation_tick_committed",
        "session_binding_fingerprint": session.session_binding.fingerprint,
        "tick_event_id": _identifier(tick_event_id, "tick_event_id"),
        "participant_id": _identifier(participant_id, "participant_id"),
        "sampled_position_fingerprint": sampled_position.fingerprint,
        "consumed_plan_id": consumed_plan.plan_id,
        "consumed_plan_receipt_fingerprint": model_data_fingerprint(
            consumed_plan.receipt),
        "due_probe_receipt": thaw_model_data(probe.receipt),
        "observation_receipt": thaw_model_data(observation.receipt),
        "shared_projection_stage_receipt": (
            observation.shared_projection_execution.receipt.to_record()),
        "shared_projection_output": thaw_model_data(
            observation.shared_projection_execution.payload),
        "assessment_stage_receipt": (
            observation.assessment_execution.receipt.to_record()),
        "assessment_output": thaw_model_data(
            observation.assessment_execution.payload),
        "settlement_stage_receipt": (
            observation.progress_transaction.settlement_execution.receipt.to_record()),
        "settlement_output": thaw_model_data(
            observation.progress_transaction.settlement_execution.payload),
        "authorization": authorization,
        "next_plan_id": committed_next.plan_id,
        "next_plan_receipt_fingerprint": model_data_fingerprint(
            committed_next.receipt),
        "settled_position_fingerprint": (
            observation.session.position.position.fingerprint),
        "settled_runtime_ordinal": observation.session.position.runtime_ordinal,
        "authority": {
            "writer": False,
            "destination": False,
            "route": False,
            "send": False,
            "ui": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    profile = dict(planned_session.profile)
    profile[RUNTIME_PRODUCT_OBSERVATION_TICK_JOURNAL_KEY] = [
        *_journal(planned_session.profile),
        receipt,
    ]
    final_session = replace(planned_session, profile=profile)
    final_observation = replace(observation, session=final_session)
    return RuntimeProductObservationTick(
        session=final_session,
        consumed_plan=consumed_plan,
        next_plan=committed_next,
        observation=final_observation,
        receipt=receipt,
        already_committed=False,
        committed_grant=(
            None if final_observation.authorization is None
            else grant_from_observation(final_observation)
        ),
    )


def recover_pending_product_observation_ticks(
    *,
    session: BootstrappedRuntimeSession,
    temporal_runtime: BoundTemporalDomain,
) -> tuple[RuntimeProductObservationTick, ...]:
    """Restore eligible grants still anchored at the current exact position."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("pending tick recovery requires runtime session")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("pending tick recovery requires temporal runtime")
    recovered = []
    for row in _journal(session.profile):
        if row.get("version") != RUNTIME_PRODUCT_OBSERVATION_TICK_VERSION:
            continue
        authorization = row.get("authorization")
        if (
            not isinstance(authorization, Mapping)
            or row.get("settled_position_fingerprint")
            != session.position.position.fingerprint
            or row.get("settled_runtime_ordinal")
            != session.position.runtime_ordinal
        ):
            continue
        tick = _load_existing(
            session,
            temporal_runtime=temporal_runtime,
            target_coordinate=session.position.position.coordinate,
            participant_id=row.get("participant_id"),
            tick_event_id=row.get("tick_event_id"),
        )
        if tick is None or tick.committed_grant is None:
            raise ValueError("eligible committed tick grant is not recoverable")
        recovered.append(tick)
    if len(recovered) > 1:
        raise ValueError("multiple eligible grants share the current position")
    return tuple(recovered)


__all__ = (
    "RUNTIME_PRODUCT_OBSERVATION_TICK_JOURNAL_KEY",
    "RUNTIME_PRODUCT_OBSERVATION_TICK_VERSION",
    "RuntimeProductObservationTick",
    "execute_product_observation_tick",
    "recover_pending_product_observation_ticks",
)
