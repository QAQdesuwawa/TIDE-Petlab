"""Package-method regressions for canonical per-tool control projection."""
from __future__ import annotations

import pytest

from framework.model_api import model_data_fingerprint
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.temporal_domain import (
    REQUIRED_TEMPORAL_OPERATORS,
    TemporalDomainBinding,
    TemporalDomainManifest,
    TemporalPosition,
)
from model_packages.policy_transaction_v1 import PolicyTransactionV1Package
from model_packages.policy_transaction_v1.memory import EXPERIENCE_AGGREGATE_VERSION
from model_packages.policy_transaction_v1.policy_gate import POLICY_CONTRACT_VERSION


def _context():
    manifest = TemporalDomainManifest.create(
        domain_id="fixture.logical-step",
        domain_version="1.0.0",
        position_schema={"type": "integer"},
        progress_schema={"type": "object"},
        observation_schema={"type": "object"},
        parameter_package={"version": "fixture-logical-step-v1"},
        operator_versions={
            name: f"fixture-{name}-v1" for name in REQUIRED_TEMPORAL_OPERATORS
        },
    )
    return ModelInvocationContextV2(
        session_id="session:gate:test",
        scope_ref="scope:case",
        subject_ref="agent:one",
        event_ref="event:1",
        action_ref=None,
        temporal_position=TemporalPosition.create(
            binding=TemporalDomainBinding.from_manifest(manifest), coordinate=1),
        runtime_ordinal=1,
        metadata={},
    )


def _parameters(package, context):
    return package.initialize_parameters(
        payload={
            "profile_id": package.profile_id,
            "policy_contract_digest": model_data_fingerprint(_policy()),
            "tool_contract_digest": "b" * 64,
            "exact_tool_ids": ["lookup_entity", "change_entity"],
        },
        context=context,
    )["parameters"]


def _settlement(package, context, parameters):
    state = package.initialize_state(
        payload={"initial_state": None}, context=context)["state"]
    patch = {
        "schema_version": state["schema_version"],
        "source_event_ref": "event:1",
        "source_kind": "user_text",
        "source_error": False,
        "operations": [{
            "operation": "set",
            "slot_id": "request.operation",
            "value": "change",
            "source_event_ref": "event:1",
            "source_kind": "user_text",
        }],
        "reference_slots": ["request.operation"],
    }
    measurement = package.measure_event(
        payload={"event_patch": patch}, context=context)["measurement"]
    contribution = package.derive_event_contribution(
        payload={
            "parameters": parameters,
            "state": state,
            "measurement": measurement,
        },
        context=context,
    )["event_contribution"]
    return package.settle_state(
        payload={
            "state": state,
            "progress_contribution": None,
            "event_contribution": contribution,
        },
        context=context,
    )["settlement"]


def _policy():
    return {
        "version": POLICY_CONTRACT_VERSION,
        "rules": [
            {
                "tool_id": "lookup_entity",
                "source_refs": ["policy:lookup"],
                "requirements": [],
                "blockers": [],
            },
            {
                "tool_id": "change_entity",
                "source_refs": ["policy:change"],
                "requirements": [{
                    "requirement_id": "change.requires.authorization",
                    "slot_id": "authorization.decision",
                    "operator": "equals",
                    "value": "granted",
                }],
                "blockers": [],
            },
        ],
    }


def _experience_aggregate(context):
    material = {
        "version": EXPERIENCE_AGGREGATE_VERSION,
        "session_id": context.session_id,
        "task_scope_ref": context.scope_ref,
        "exact_tool_ids": ["lookup_entity", "change_entity"],
        "experience_witnesses": [{
            "tool_id": "lookup_entity",
            "disposition": "support",
            "source_episode_refs": ["episode:1"],
            "source_event_refs": ["event:prior:result"],
        }],
        "source_influence_fingerprint": "c" * 64,
        "reused_prior_aggregate_fingerprint": None,
        "newly_materialized_event_ids": ["event:prior:result"],
        "materialized_through_ordinal": 1,
        "aggregation_rule": (
            "committed-order-latest-disposition-no-path-enumeration"),
        "forward_only": True,
        "state_commit_authority": False,
    }
    return {**material, "fingerprint": model_data_fingerprint(material)}


def test_package_projects_one_descriptor_per_tool_without_duplicate_cards():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    settlement = _settlement(package, context, parameters)
    shared = package.project_shared_semantics(
        payload={
            "parameters": parameters,
            "settlement": settlement,
            "policy_contract": _policy(),
            "experience_aggregate": _experience_aggregate(context),
        },
        context=context,
    )["shared_projection"]
    assessment = package.assess_action(
        payload={"shared_projection": shared}, context=context,
    )["action_assessment"]
    resources = package.project_semantic_resources(
        payload={"shared_projection": shared}, context=context,
    )["expression_resource_batch"]

    descriptors = shared["canonical_tool_descriptors"]
    assert [item["tool_id"] for item in descriptors] == [
        "lookup_entity", "change_entity"]
    assert descriptors[0]["recommendation"] == "preferred"
    assert descriptors[1]["decision"] == "needs_evidence"
    assessment_descriptors = assessment["constraints"][
        "canonical_tool_descriptors"
    ]
    assert [
        item["tool_id"] for item in assessment_descriptors
        if item["decision"] == "allowed"
    ] == ["lookup_entity"]
    assert assessment["eligible"] is True
    assert assessment["observation_advice"]["text_response_required"] is True
    assert assessment["constraints"]["tool_execution_authority"] is False
    assert assessment["action_commit_authority"] is False
    control = resources["control"]
    assert set(control) == {
        "canonical_tool_descriptors", "text_response_required"}
    assert "semantic_state" not in control
    assert "policy_cards" not in str(control)
    assert "memory_cards" not in str(control)


def test_package_rejects_policy_that_differs_from_frozen_digest():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    settlement = _settlement(package, context, parameters)
    changed_policy = _policy()
    changed_policy["rules"][0]["source_refs"] = ["policy:tampered"]

    with pytest.raises(ValueError, match="frozen parameters"):
        package.project_shared_semantics(
            payload={
                "parameters": parameters,
                "settlement": settlement,
                "policy_contract": changed_policy,
                "experience_aggregate": None,
            },
            context=context,
        )

