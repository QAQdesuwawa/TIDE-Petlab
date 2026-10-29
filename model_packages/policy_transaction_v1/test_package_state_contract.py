"""AUD-399 package API regressions for the finite state contract."""
from __future__ import annotations

import pytest

from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.temporal_domain import (
    REQUIRED_TEMPORAL_OPERATORS,
    TemporalDomainBinding,
    TemporalDomainManifest,
    TemporalPosition,
)
from model_packages.policy_transaction_v1 import PolicyTransactionV1Package
from model_packages.policy_transaction_v1.state_space import STATE_SPACE_VERSION
from model_packages.policy_transaction_v1.state_space import (
    DEFAULT_STATE_SPACE_DEFINITION,
    apply_event_patch,
    empty_state,
)


def _context(event_ref="event:1"):
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
        session_id="session:policy-transaction:test",
        scope_ref="scope:case",
        subject_ref="agent:one",
        event_ref=event_ref,
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
            "policy_contract_digest": "a" * 64,
            "tool_contract_digest": "b" * 64,
            "exact_tool_ids": ["lookup_entity", "change_entity"],
        },
        context=context,
    )["parameters"]


def _patch():
    return {
        "schema_version": STATE_SPACE_VERSION,
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


def test_manifest_declares_finite_state_without_domain_or_bucket_schema():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    manifest = package.manifest.to_record()

    assert package.profile_id == "customer-service"
    assert "domain_ids" not in str(manifest)
    assert "buckets" not in str(manifest)
    assert manifest["state_schema"]["schema_version"] == STATE_SPACE_VERSION
    assert manifest["state_schema"]["definition_fingerprint"] == (
        package.state_space_fingerprint)


def test_event_patch_runs_through_contribution_and_single_settlement():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    state = package.initialize_state(
        payload={"initial_state": None}, context=context,
    )["state"]
    measurement = package.measure_event(
        payload={"event_patch": _patch()}, context=context,
    )["measurement"]
    contribution = package.derive_event_contribution(
        payload={
            "parameters": parameters,
            "state": state,
            "measurement": measurement,
        },
        context=context,
    )["event_contribution"]
    settlement = package.settle_state(
        payload={
            "state": state,
            "progress_contribution": None,
            "event_contribution": contribution,
        },
        context=context,
    )["settlement"]

    entry = settlement["state_candidate"]["slots"]["request.operation"][
        "entries"][0]
    assert entry["value"] == "change"
    assert entry["source_event_ref"] == "event:1"
    assert settlement["state_commit_authority"] is False


def test_record_state_supports_fifty_event_long_chain_but_remains_bounded():
    state = empty_state(DEFAULT_STATE_SPACE_DEFINITION)
    for index in range(64):
        event_id = f"event:claim:{index}"
        state = apply_event_patch(
            state,
            {
                "schema_version": STATE_SPACE_VERSION,
                "source_event_ref": event_id,
                "source_kind": "user_text",
                "source_error": False,
                "operations": [{
                    "operation": "add",
                    "slot_id": "claims",
                    "value": {
                        "entity_id": "case:long",
                        "attribute": f"fact-{index}",
                        "value": index,
                    },
                    "source_event_ref": event_id,
                    "source_kind": "user_text",
                }],
                "reference_slots": ["claims"],
            },
            definition=DEFAULT_STATE_SPACE_DEFINITION,
        )
    assert len(state["slots"]["claims"]["entries"]) == 64

    with pytest.raises(ValueError, match="cardinality exceeds"):
        apply_event_patch(
            state,
            {
                "schema_version": STATE_SPACE_VERSION,
                "source_event_ref": "event:claim:64",
                "source_kind": "user_text",
                "source_error": False,
                "operations": [{
                    "operation": "add",
                    "slot_id": "claims",
                    "value": {
                        "entity_id": "case:long",
                        "attribute": "fact-64",
                        "value": 64,
                    },
                    "source_event_ref": "event:claim:64",
                    "source_kind": "user_text",
                }],
                "reference_slots": ["claims"],
            },
            definition=DEFAULT_STATE_SPACE_DEFINITION,
        )


def test_tampered_sealed_measurement_fails_before_state_derivation():
    package = PolicyTransactionV1Package(profile_id="customer-service")
    context = _context()
    parameters = _parameters(package, context)
    state = package.initialize_state(
        payload={"initial_state": None}, context=context)["state"]
    measurement = package.measure_event(
        payload={"event_patch": _patch()}, context=context)["measurement"]
    measurement["operations"][0]["value"] = "tampered"

    with pytest.raises(ValueError, match="fingerprint is invalid"):
        package.derive_event_contribution(
            payload={
                "parameters": parameters,
                "state": state,
                "measurement": measurement,
            },
            context=context,
        )


def test_package_accepts_domain_neutral_profile_ids_with_same_operator_shape():
    first = PolicyTransactionV1Package(profile_id="customer-service")
    second = PolicyTransactionV1Package(profile_id="workflow-assistant")

    assert first.manifest.capabilities == second.manifest.capabilities
    assert first.manifest.schema_fingerprint == second.manifest.schema_fingerprint
    assert first.manifest.operator_versions == second.manifest.operator_versions
