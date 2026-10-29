"""Read-only diagnostic export from one committed product observation tick.

The host validates and forwards products already persisted by the formal
lifecycle.  It never recomputes model state, projection, action assessment, or
time, and it owns no UI or action authority.
"""
from __future__ import annotations

from collections.abc import Mapping

from framework.diagnostics import (
    CommittedDiagnosticSource,
    export_diagnostic_snapshot,
    read_diagnostic_descriptor,
)
from framework.model_api import (
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.optional_stage import invoke_optional_model_stage
from ops.runtime_product_observation_tick import (
    RUNTIME_PRODUCT_OBSERVATION_TICK_JOURNAL_KEY,
)
from ops.runtime_product_observation_plan import (
    RUNTIME_OBSERVATION_PLAN_JOURNAL_KEY,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


def _verified_record(value: object, label: str) -> dict:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be committed record data")
    record = dict(thaw_model_data(value))
    fingerprint = record.pop("fingerprint", None)
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError(f"{label} fingerprint is invalid")
    return dict(record, fingerprint=fingerprint)


def _verified_stage(
    row: Mapping[str, object], *, receipt_key: str, output_key: str,
    expected_stage: str, session: BootstrappedRuntimeSession,
) -> tuple[dict, dict]:
    receipt = row.get(receipt_key)
    output = row.get(output_key)
    if not isinstance(receipt, Mapping) or not isinstance(output, Mapping):
        raise ValueError(f"committed diagnostic {expected_stage} product is missing")
    receipt = dict(thaw_model_data(receipt))
    output = dict(thaw_model_data(output))
    binding = session.session_binding.model_binding
    if (
        receipt.get("stage") != expected_stage
        or receipt.get("package_id") != binding.package_id
        or receipt.get("package_version") != binding.package_version
        or receipt.get("operator_version")
        != binding.operator_versions.get(expected_stage)
        or receipt.get("session_id") != session.session_binding.session_id
        or receipt.get("scope_ref") is not None
        or receipt.get("subject_ref") != row.get("participant_id")
        or receipt.get("event_ref") != row.get("tick_event_id")
        or receipt.get("action_ref") is not None
        or receipt.get("runtime_ordinal") != row.get("settled_runtime_ordinal")
        or receipt.get("output_fingerprint") != model_data_fingerprint(output)
    ):
        raise ValueError(
            f"committed diagnostic {expected_stage} lineage is invalid")
    return receipt, output


def _destination_conversation(
    session: BootstrappedRuntimeSession, row: Mapping[str, object],
) -> str:
    plan_id = str(row.get("consumed_plan_id") or "").strip()
    raw_plans = session.profile.get(RUNTIME_OBSERVATION_PLAN_JOURNAL_KEY, ())
    if not plan_id or not isinstance(raw_plans, (list, tuple)):
        raise ValueError("diagnostic tick consumed plan lineage is missing")
    matches = []
    for raw in raw_plans:
        plan = _verified_record(raw, "observation plan")
        if plan.get("plan_id") == plan_id:
            matches.append(plan)
    if len(matches) != 1:
        raise ValueError("diagnostic tick consumed plan identity is invalid")
    plan = matches[0]
    conversation_id = str(
        plan.get("destination_conversation_id") or "").strip()
    if (
        plan.get("session_binding_fingerprint")
        != session.session_binding.fingerprint
        or not conversation_id
    ):
        raise ValueError("diagnostic tick destination lineage is invalid")
    return conversation_id


def export_latest_product_diagnostic_snapshot(
    *, session: BootstrappedRuntimeSession,
    model_runtime: BoundInteractionModel,
):
    """Export the latest coherent formal diagnostic slice, or ``None``.

    Absence means no committed autonomous observation exists yet, or the bound
    runtime variant deliberately disables diagnostic metadata.
    """
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("product diagnostics require a bootstrapped session")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("product diagnostics require a bound model runtime")
    session.session_binding.verify_integrity()
    if (
        model_runtime.session_id != session.session_binding.session_id
        or model_runtime.binding != session.session_binding.model_binding
    ):
        raise ValueError("product diagnostic runtime differs from session")
    raw_rows = session.profile.get(
        RUNTIME_PRODUCT_OBSERVATION_TICK_JOURNAL_KEY, ())
    if not raw_rows:
        return None
    if isinstance(raw_rows, (str, bytes)) or not isinstance(raw_rows, (list, tuple)):
        raise ValueError("product observation tick journal is invalid")
    row = _verified_record(raw_rows[-1], "product observation tick")
    if row.get("session_binding_fingerprint") != session.session_binding.fingerprint:
        raise ValueError("product observation tick session lineage is invalid")
    conversation_id = _destination_conversation(session, row)
    shared_receipt, shared_output = _verified_stage(
        row,
        receipt_key="shared_projection_stage_receipt",
        output_key="shared_projection_output",
        expected_stage="project_shared_semantics",
        session=session,
    )
    assessment_receipt, assessment_output = _verified_stage(
        row,
        receipt_key="assessment_stage_receipt",
        output_key="assessment_output",
        expected_stage="assess_action",
        session=session,
    )
    shared = shared_output.get("shared_projection")
    if not isinstance(shared, Mapping):
        raise ValueError("committed diagnostic shared projection is invalid")
    model_state = shared.get("source_state_candidate")
    if not isinstance(model_state, Mapping):
        raise ValueError("committed diagnostic model state is missing")

    context = ModelInvocationContextV2(
        session_id=session.session_binding.session_id,
        scope_ref=conversation_id,
        subject_ref=str(row.get("participant_id") or ""),
        event_ref=str(row.get("tick_event_id") or ""),
        action_ref=None,
        temporal_position=session.position.position,
        runtime_ordinal=session.position.runtime_ordinal,
        metadata={},
    )
    outcome = invoke_optional_model_stage(
        runtime=model_runtime,
        session_binding=session.session_binding,
        stage="diagnostic_descriptor",
        payload={},
        context=context,
    )
    if outcome.execution is None:
        return None
    descriptor = read_diagnostic_descriptor(
        outcome.execution, expected_context=context)
    shared_receipt_fingerprint = model_data_fingerprint(shared_receipt)
    return export_diagnostic_snapshot(
        descriptor=descriptor,
        runtime_variant=session.session_binding.runtime_variant,
        sources={
            "committed_model_state": CommittedDiagnosticSource.create(
                source_id="committed_model_state",
                payload=model_state,
                source_receipt_fingerprint=shared_receipt_fingerprint,
            ),
            "committed_shared_projection": CommittedDiagnosticSource.create(
                source_id="committed_shared_projection",
                payload=shared_output,
                source_receipt_fingerprint=shared_receipt_fingerprint,
            ),
            "committed_action_assessment": CommittedDiagnosticSource.create(
                source_id="committed_action_assessment",
                payload=assessment_output,
                source_receipt_fingerprint=model_data_fingerprint(
                    assessment_receipt),
            ),
        },
    )


__all__ = ("export_latest_product_diagnostic_snapshot",)
