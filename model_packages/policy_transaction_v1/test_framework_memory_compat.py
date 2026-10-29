"""AUD-400: current framework v2 memory readers must consume this package."""
from __future__ import annotations

import pytest

from framework.model_api import model_data_fingerprint
from framework.model_api.memory import (
    read_historical_aggregate,
    read_historical_influence,
    read_recall_decision,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.temporal_domain import (
    REQUIRED_TEMPORAL_OPERATORS,
    TemporalDomainBinding,
    TemporalDomainManifest,
    TemporalPosition,
)
from model_packages.policy_transaction_v1 import PolicyTransactionV1Package
from model_packages.policy_transaction_v1.memory import MEMORY_EVENT_VERSION


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
        session_id="session:framework-compat",
        scope_ref="task:framework-compat",
        subject_ref="agent:one",
        event_ref="event:current",
        action_ref=None,
        temporal_position=TemporalPosition.create(
            binding=TemporalDomainBinding.from_manifest(manifest), coordinate=4),
        runtime_ordinal=4,
        metadata={},
    )


def _event(
    context, event_id, ordinal, source_kind, *, tool_id=None,
    reference_slots=("request.operation",),
):
    return {
        "version": MEMORY_EVENT_VERSION,
        "event_id": event_id,
        "session_id": context.session_id,
        "task_scope_ref": context.scope_ref,
        "ordinal": ordinal,
        "source_kind": source_kind,
        "reference_slots": list(reference_slots),
        "exact_tool_id": tool_id,
        "effectiveness": "effective" if source_kind == "tool_result" else "not_applicable",
        "episode_ref": "episode:one" if tool_id else None,
    }


def _parameters(runtime, context):
    return runtime.invoke(
        "initialize_parameters",
        payload={
            "profile_id": "framework-compat",
            "policy_contract_digest": "a" * 64,
            "tool_contract_digest": "b" * 64,
            "exact_tool_ids": ["lookup_entity"],
        },
        context=context,
    ).payload["parameters"]


def test_current_framework_readers_accept_complete_package_memory_pipeline():
    package = PolicyTransactionV1Package(profile_id="framework-compat")
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
    events = [
        _event(context, "event:user", 1, "user_text"),
        _event(
            context, "event:result", 3, "tool_result",
            tool_id="lookup_entity",
        ),
        _event(
            context, "event:unmatched", 2, "assistant_text",
            reference_slots=("authorization.decision",),
        ),
    ]
    candidate_ids = [item["event_id"] for item in events]
    recall_execution = runtime.invoke(
        "recall_history",
        payload={
            "candidate_event_ids": candidate_ids,
            "scope": {
                "session_id": context.session_id,
                "task_scope_ref": context.scope_ref,
            },
            "query": {
                "reference_slots": ["request.operation"],
                "exact_tool_ids": ["lookup_entity"],
            },
            "candidate_events": events,
        },
        context=context,
    )

    decision = read_recall_decision(
        recall_execution,
        expected_context=context,
        candidate_event_ids=candidate_ids,
    )
    assert decision.recalled_event_ids == tuple(candidate_ids[:2])
    assert set(decision.candidate_evidence) == set(candidate_ids)
    assert decision.candidate_evidence["event:unmatched"]["selected"] is False

    influence_execution = runtime.invoke(
        "read_historical_influence",
        payload={
            "parameters": _parameters(runtime, context),
            "memory_recall": recall_execution.payload["memory_recall"],
            "recalled_event_ids": list(decision.recalled_event_ids),
            "historical_events": events,
        },
        context=context,
    )
    influence = read_historical_influence(
        influence_execution,
        expected_context=context,
        recalled_event_ids=decision.recalled_event_ids,
    )
    assert tuple(influence.event_influences) == tuple(candidate_ids[:2])
    assert {
        edge["parent_event_id"] for edge in influence.later_edges
    } == set(candidate_ids[:2])

    aggregate_execution = runtime.invoke(
        "aggregate_historical_influence",
        payload={
            "recalled_event_ids": list(influence.recalled_event_ids),
            "event_influences": influence.event_influences,
            "source_influence_output_fingerprint": (
                influence.stage_receipt.output_fingerprint
            ),
            "experience_influence": influence_execution.payload[
                "experience_influence"
            ],
        },
        context=context,
    )
    aggregate = read_historical_aggregate(
        aggregate_execution,
        expected_context=context,
        influence=influence,
    )
    assert aggregate.source_influence_output_fingerprint == model_data_fingerprint(
        influence_execution.payload
    )
    assert aggregate.aggregate["experience_witnesses"][0]["tool_id"] == (
        "lookup_entity"
    )

    wrong_source_execution = runtime.invoke(
        "aggregate_historical_influence",
        payload={
            "recalled_event_ids": list(influence.recalled_event_ids),
            "event_influences": influence.event_influences,
            "source_influence_output_fingerprint": "c" * 64,
            "experience_influence": influence_execution.payload[
                "experience_influence"
            ],
        },
        context=context,
    )
    with pytest.raises(ValueError, match="source influence"):
        read_historical_aggregate(
            wrong_source_execution,
            expected_context=context,
            influence=influence,
        )
