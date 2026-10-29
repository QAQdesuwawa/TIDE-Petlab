"""Read-only join of one committed Actor tick and a bound model opening result.

This F2 bridge does not prove that an Actor deadline elapsed, invoke a model,
refresh a plan, or authorize an autonomous send. The caller supplies rows from
the same committed owner journal and an already completed model stage.
"""
from __future__ import annotations

from collections.abc import Mapping

from framework.model_api import ModelStageExecution
from framework.model_api.compat_v1.opening import (
    AutonomousOpeningAssessment, read_autonomous_opening_assessment,
)
from framework.model_runtime import BoundInteractionModel
from ops.model_shadow_context import tick_context_from_committed_observation


def read_committed_tick_opening_shadow(
    execution: ModelStageExecution, *,
    p3_receipt: Mapping[str, object],
    wall_boundary: Mapping[str, object],
    actor_observation: Mapping[str, object],
    profile: Mapping[str, object],
    runtime: BoundInteractionModel,
) -> AutonomousOpeningAssessment:
    """Read a generic opening only when its receipt belongs to this owner tick.

    The numeric opening evidence, if any, remains opaque model payload. Exact
    event, owner, session and model versions are compared before its result is
    exposed; no F15/F16 or clock operation is performed here.
    """
    context = tick_context_from_committed_observation(
        p3_receipt, wall_boundary=wall_boundary,
        actor_observation=actor_observation, profile=profile, runtime=runtime,
    )
    if not isinstance(execution, ModelStageExecution):
        raise ValueError("tick model result requires a stage execution")
    receipt = execution.receipt
    manifest = runtime.manifest
    if (receipt.stage != "action_assessment"
            or receipt.session_id != context.session_id
            or receipt.conversation_id is not None
            or receipt.participant_id != context.participant_id
            or receipt.event_id != context.event_id
            or receipt.action_id is not None
            or receipt.at_ms != context.at_ms
            or receipt.package_id != manifest.package_id
            or receipt.package_version != manifest.package_version
            or receipt.schema_fingerprint != manifest.schema_fingerprint
            or receipt.parameter_fingerprint != manifest.parameter_fingerprint
            or receipt.operator_version != manifest.operator_versions["action_assessment"]):
        raise ValueError("tick model receipt differs from committed owner or binding")
    return read_autonomous_opening_assessment(
        execution, tick_event_id=context.event_id, tick_at_ms=context.at_ms,
    )
