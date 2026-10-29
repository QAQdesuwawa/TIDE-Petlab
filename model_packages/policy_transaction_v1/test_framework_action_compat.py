"""AUD-401: current framework v2 action reader must consume this package."""
from __future__ import annotations

from framework.model_api import model_data_fingerprint
from framework.model_api.action import read_action_assessment
from framework.model_api.identity import context_fact_identity
from framework.model_runtime import BoundInteractionModel
from model_packages.policy_transaction_v1 import PolicyTransactionV1Package
from model_packages.policy_transaction_v1.policy_gate import POLICY_CONTRACT_VERSION
from model_packages.policy_transaction_v1.state_space import STATE_SPACE_VERSION

from model_packages.policy_transaction_v1.test_framework_memory_compat import _context


def _policy():
    return {
        "version": POLICY_CONTRACT_VERSION,
        "rules": [
            {
                "tool_id": "allowed_tool",
                "source_refs": ["policy:allowed"],
                "requirements": [],
                "blockers": [],
            },
            {
                "tool_id": "blocked_tool",
                "source_refs": ["policy:blocked"],
                "requirements": [],
                "blockers": [{
                    "requirement_id": "blocked.when.change",
                    "slot_id": "request.operation",
                    "operator": "equals",
                    "value": "change",
                }],
            },
            {
                "tool_id": "evidence_tool",
                "source_refs": ["policy:evidence"],
                "requirements": [{
                    "requirement_id": "evidence.requires.entity",
                    "slot_id": "target.entities",
                    "operator": "present",
                    "value": None,
                }],
                "blockers": [],
            },
        ],
    }


def test_current_framework_reader_accepts_package_action_projection():
    package = PolicyTransactionV1Package(profile_id="framework-action")
    context = _context()
    runtime = BoundInteractionModel(
        package,
        package.manifest,
        operators={
            capability: getattr(package, capability)
            for capability in package.manifest.capabilities
        },
        session_id=context.session_id,
    )
    policy = _policy()
    parameters = runtime.invoke(
        "initialize_parameters",
        payload={
            "profile_id": package.profile_id,
            "policy_contract_digest": model_data_fingerprint(policy),
            "tool_contract_digest": "b" * 64,
            "exact_tool_ids": [
                "allowed_tool", "blocked_tool", "evidence_tool",
            ],
        },
        context=context,
    ).payload["parameters"]
    state = runtime.invoke(
        "initialize_state", payload={"initial_state": None}, context=context,
    ).payload["state"]
    measurement = runtime.invoke(
        "measure_event",
        payload={"event_patch": {
            "schema_version": STATE_SPACE_VERSION,
            "source_event_ref": context.event_ref,
            "source_kind": "user_text",
            "source_error": False,
            "operations": [{
                "operation": "set",
                "slot_id": "request.operation",
                "value": "change",
                "source_event_ref": context.event_ref,
                "source_kind": "user_text",
            }],
            "reference_slots": ["request.operation"],
        }},
        context=context,
    ).payload["measurement"]
    contribution = runtime.invoke(
        "derive_event_contribution",
        payload={
            "parameters": parameters,
            "state": state,
            "measurement": measurement,
        },
        context=context,
    ).payload["event_contribution"]
    settlement = runtime.invoke(
        "settle_state",
        payload={
            "state": state,
            "progress_contribution": None,
            "event_contribution": contribution,
        },
        context=context,
    ).payload["settlement"]
    shared_execution = runtime.invoke(
        "project_shared_semantics",
        payload={
            "parameters": parameters,
            "settlement": settlement,
            "policy_contract": policy,
            "experience_aggregate": None,
        },
        context=context,
    )
    assessment_execution = runtime.invoke(
        "assess_action",
        payload={
            "shared_projection": shared_execution.payload["shared_projection"],
        },
        context=context,
    )
    identity = context_fact_identity(context)
    assessment = read_action_assessment(
        assessment_execution,
        source_fact_ref=identity.event_ref,
        source_position=identity.temporal_position,
        source_runtime_ordinal=identity.runtime_ordinal,
    )

    assert assessment.action_kind == "policy_transaction_response"
    assert assessment.eligible is True
    by_tool = {
        item["tool_id"]: item
        for item in assessment.constraints["canonical_tool_descriptors"]
    }
    assert by_tool["allowed_tool"]["decision"] == "allowed"
    assert by_tool["blocked_tool"]["decision"] == "blocked"
    assert by_tool["evidence_tool"]["decision"] == "needs_evidence"
    assert assessment.constraints["tool_execution_authority"] is False
    assert assessment.observation_advice["text_response_required"] is True
    assert assessment_execution.payload["action_assessment"][
        "source_shared_projection_fingerprint"
    ] == shared_execution.receipt.output_fingerprint
