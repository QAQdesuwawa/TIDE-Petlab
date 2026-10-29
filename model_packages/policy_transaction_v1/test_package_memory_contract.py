"""Package-method qualification for task-local forward memory receipts."""
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
from model_packages.policy_transaction_v1.memory import MEMORY_EVENT_VERSION
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
        session_id="session:memory:test",
        scope_ref="task:memory:test",
        subject_ref="agent:one",
        event_ref="event:current",
        action_ref=None,
        temporal_position=TemporalPosition.create(
            binding=TemporalDomainBinding.from_manifest(manifest), coordinate=3),
        runtime_ordinal=3,
        metadata={},
    )


def _policy():
    return {
        "version": POLICY_CONTRACT_VERSION,
        "rules": [{
            "tool_id": "lookup_entity",
            "source_refs": ["policy:lookup"],
            "requirements": [],
            "blockers": [],
        }],
    }


def _parameters(package, context):
    return package.initialize_parameters(
        payload={
            "profile_id": package.profile_id,
            "policy_contract_digest": model_data_fingerprint(_policy()),
            "tool_contract_digest": "b" * 64,
            "exact_tool_ids": ["lookup_entity"],
        },
        context=context,
    )["parameters"]


def _event(context):
    return {
        "version": MEMORY_EVENT_VERSION,
        "event_id": "event:prior:result",
        "session_id": context.session_id,
        "task_scope_ref": context.scope_ref,
        "ordinal": 2,
        "source_kind": "tool_result",
        "reference_slots": ["request.operation"],
        "exact_tool_id": "lookup_entity",
        "effectiveness": "effective",
        "episode_ref": "episode:one",
    }


def _settlement(package, context, parameters):
    state = package.initialize_state(
        payload={"initial_state": None}, context=context)["state"]
    progress = package.advance_progress(
        payload={"parameters": parameters, "state": state, "progress_delta": {}},
        context=context,
    )["progress_contribution"]
    return state, package.settle_state(
        payload={
            "state": state,
            "progress_contribution": progress,
            "event_contribution": None,
        },
        context=context,
    )["settlement"]


def test_package_memory_receipts_feed_one_canonical_descriptor():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    event = _event(context)
    recall = package.recall_history(
        payload={
            "candidate_event_ids": [event["event_id"]],
            "scope": {
                "session_id": context.session_id,
                "task_scope_ref": context.scope_ref,
            },
            "query": {
                "reference_slots": ["request.operation"],
                "exact_tool_ids": ["lookup_entity"],
            },
            "candidate_events": [event],
        },
        context=context,
    )["memory_recall"]
    influence = package.read_historical_influence(
        payload={
            "parameters": parameters,
            "memory_recall": recall,
            "recalled_event_ids": recall["selected_event_ids"],
            "historical_events": [event],
        },
        context=context,
    )
    aggregate = package.aggregate_historical_influence(
        payload={
            "recalled_event_ids": recall["selected_event_ids"],
            "event_influences": influence["historical_influence"][
                "event_influences"
            ],
            "source_influence_output_fingerprint": model_data_fingerprint(
                influence
            ),
            "experience_influence": influence["experience_influence"],
        },
        context=context,
    )["historical_aggregate"]["aggregate"]
    _, settlement = _settlement(package, context, parameters)
    shared = package.project_shared_semantics(
        payload={
            "parameters": parameters,
            "settlement": settlement,
            "policy_contract": _policy(),
            "experience_aggregate": aggregate,
        },
        context=context,
    )["shared_projection"]

    descriptor = shared["canonical_tool_descriptors"][0]
    assert descriptor["tool_id"] == "lookup_entity"
    assert descriptor["recommendation"] == "preferred"
    assert descriptor["experience_witness"]["source_event_refs"] == [
        "event:prior:result"]
    assert descriptor["auto_execute"] is False


def test_memory_scope_tamper_and_state_replay_fail_closed():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    event = _event(context)

    with pytest.raises(ValueError, match="invocation scope"):
        package.recall_history(
            payload={
                "candidate_event_ids": [],
                "scope": {
                    "session_id": context.session_id,
                    "task_scope_ref": "task:other",
                },
                "query": {"reference_slots": [], "exact_tool_ids": []},
                "candidate_events": [],
            },
            context=context,
        )

    state, settlement = _settlement(package, context, parameters)
    with pytest.raises(ValueError, match="fields are invalid"):
        package.settle_state(
            payload={
                "state": state,
                "progress_contribution": None,
                "event_contribution": None,
                "historical_aggregate": settlement,
            },
            context=context,
        )


def test_refingerprinted_semantically_invalid_aggregate_is_rejected():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    _, settlement = _settlement(package, context, parameters)
    material = {
        "version": "policy-transaction-experience-aggregate-v2",
        "session_id": context.session_id,
        "task_scope_ref": context.scope_ref,
        "exact_tool_ids": ["lookup_entity"],
        "experience_witnesses": [{
            "tool_id": "invented_tool",
            "disposition": "support",
            "source_episode_refs": ["episode:forged"],
            "source_event_refs": ["event:forged"],
        }],
        "source_influence_fingerprint": "c" * 64,
        "reused_prior_aggregate_fingerprint": None,
        "newly_materialized_event_ids": ["event:forged"],
        "materialized_through_ordinal": 2,
        "aggregation_rule": "committed-order-latest-disposition-no-path-enumeration",
        "forward_only": True,
        "state_commit_authority": False,
    }
    forged = {**material, "fingerprint": model_data_fingerprint(material)}

    with pytest.raises(ValueError, match="tool identity"):
        package.project_shared_semantics(
            payload={
                "parameters": parameters,
                "settlement": settlement,
                "policy_contract": _policy(),
                "experience_aggregate": forged,
            },
            context=context,
        )


def test_refingerprinted_wrong_settlement_version_is_rejected():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    _, settlement = _settlement(package, context, parameters)
    material = {
        key: value for key, value in settlement.items() if key != "fingerprint"}
    material["version"] = "policy-transaction-settlement-v999"
    forged = {**material, "fingerprint": model_data_fingerprint(material)}

    with pytest.raises(ValueError, match="settlement version"):
        package.project_shared_semantics(
            payload={
                "parameters": parameters,
                "settlement": forged,
                "policy_contract": _policy(),
                "experience_aggregate": None,
            },
            context=context,
        )
