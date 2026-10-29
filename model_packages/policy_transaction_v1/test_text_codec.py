"""AUD-399 package-local regressions for a benchmark-neutral text codec."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from framework.model_api import model_data_fingerprint
from model_packages.policy_transaction_v1.state_space import (
    DEFAULT_STATE_SPACE_DEFINITION,
    STATE_SPACE_VERSION,
    empty_state,
)
from model_packages.policy_transaction_v1.text_codec import PolicyTransactionTextCodec


def _event(content="Cancel order A-1."):
    return {"kind": "user_text", "content": content}


def _patch(operation="cancel"):
    return {
        "schema_version": STATE_SPACE_VERSION,
        "source_event_ref": "event:1",
        "source_kind": "user_text",
        "source_error": False,
        "operations": [{
            "operation": "set",
            "slot_id": "request.operation",
            "value": operation,
            "source_event_ref": "event:1",
            "source_kind": "user_text",
        }],
        "reference_slots": ["request.operation"],
        "query_references": [],
    }


def _measurement_request(codec, content="Cancel order A-1."):
    return codec.build_measurement_request(
        source_event_ref="event:1",
        event=_event(content),
        current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        readonly_context={"recent_committed_events": []},
    )


def test_codec_source_has_no_benchmark_or_experiment_import():
    path = Path(__file__).with_name("text_codec.py")
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    assert not any(
        token in name.lower()
        for name in imported
        for token in ("tau", "benchmark", "eval")
    )


def test_measurement_receives_state_definition_but_no_tool_authority():
    codec = PolicyTransactionTextCodec()
    request = _measurement_request(codec)
    transport = codec.build_measurement_transport(request)
    payload = json.loads(transport["user_content"])

    assert payload["source_event_ref"] == "event:1"
    assert "state_space_definition" not in payload
    assert json.dumps(
        DEFAULT_STATE_SPACE_DEFINITION,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) in transport["system_prompt"]
    assert "tools" not in payload
    assert "tool_contract" not in payload
    assert "allowed_tool_names" not in payload
    assert "tool_contract" not in transport["system_prompt"]
    assert "allowed_tool_names" not in transport["system_prompt"]
    assert "query_references" in transport["system_prompt"]
    assert "value_type is record" in transport["system_prompt"]


def test_measurement_reference_vocabulary_is_stable_answer_blind_schema():
    """AUD-523: the provider must not invent index attribute identities."""
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v1",
        "slots": [
            {
                "slot_id": "constraints",
                "attributes": ["delivery_time", "service_level"],
            },
            {
                "slot_id": "claims",
                "attributes": ["service_arrival_time"],
            },
        ],
    }

    def transport(event_id, content):
        envelope = codec.build_measurement_request(
            source_event_ref=event_id,
            event=_event(content),
            current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
            state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
            readonly_context={"reference_query_vocabulary": vocabulary},
        )
        return codec.build_measurement_transport(envelope)

    first = transport(
        "event:1",
        "Which courier fits the recipient availability for M-510?",
    )
    second = transport(
        "event:2",
        "Which service fits the recipient availability for M-511?",
    )

    assert first["system_prompt"] == second["system_prompt"]
    assert "REFERENCE_QUERY_VOCABULARY_JSON:" in first["system_prompt"]
    assert json.dumps(
        {
            "version": "policy-transaction-reference-query-vocabulary-v1",
            "slots": [
                {
                    "slot_id": "claims",
                    "attributes": ["service_arrival_time"],
                },
                {
                    "slot_id": "constraints",
                    "attributes": ["delivery_time", "service_level"],
                },
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ) in first["system_prompt"]
    assert "recipient_availability" not in first["system_prompt"]
    assert set(json.loads(first["user_content"])) == {
        "source_event_ref", "event",
    }


def test_compact_measurement_transport_has_reusable_prefix_and_semantic_suffix():
    """AUD-524: transient receipt IDs must not defeat exact request reuse."""
    codec = PolicyTransactionTextCodec()

    def transport(event_ref):
        envelope = codec.build_measurement_request(
            source_event_ref=event_ref,
            event=_event("Cancel order A-1."),
            current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
            state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
            readonly_context={
                "measurement_transport_profile": (
                    "policy-transaction-compact-measurement-v1"),
            },
        )
        return codec.build_measurement_transport(envelope)

    first = transport("event:session-a:17")
    second = transport("event:session-b:91")

    assert first == second
    assert "COMPACT_MEASUREMENT_RESPONSE" in first["system_prompt"]
    assert json.loads(first["user_content"]) == {"event": _event()}


def test_compact_measurement_response_expands_to_canonical_patch():
    """AUD-524: compression changes transport, never canonical semantics."""
    codec = PolicyTransactionTextCodec()
    parsed = codec.parse_measurement_response(
        {
            "v": "ptm1",
            "o": [["s", "request.operation", "cancel"]],
            "r": ["request.operation", "target.entities"],
            "q": [["target.entities", "A-1", "destination"]],
        },
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        measurement_transport_profile=(
            "policy-transaction-compact-measurement-v1"),
        source_event_ref="event:1",
        event=_event(),
    )

    assert parsed == {
        **_patch(),
        "reference_slots": ["request.operation", "target.entities"],
        "query_references": [{
            "slot_id": "target.entities",
            "match_fields": {
                "entity_id": "A-1",
                "attribute": "destination",
            },
        }],
    }


def test_empty_compact_read_set_recovers_literal_declared_address():
    """AUD-530: an explicit current query cannot silently lose its address."""
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v3",
        "slots": [{
            "slot_id": "constraints",
            "attributes": ["delivery_time", "service_level"],
            "attribute_families": [],
            "entity_fields": ["entity_id"],
            "attribute_semantics": [
                {"attribute": "delivery_time", "meaning": "delivery time"},
                {"attribute": "service_level", "meaning": "service level"},
            ],
        }],
    }
    parsed = codec.parse_measurement_response(
        {"v": "ptm1", "o": [], "r": [], "q": []},
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        reference_query_vocabulary=vocabulary,
        measurement_transport_profile=(
            "policy-transaction-compact-measurement-v1"),
        source_event_ref="event:query",
        event=_event("What service level is active for account AC-912?"),
    )

    assert parsed["query_references"] == [{
        "slot_id": "constraints",
        "match_fields": {
            "entity_id": "AC-912",
            "attribute": "service_level",
        },
    }]


@pytest.mark.parametrize("content", [
    "What is active for account AC-912?",
    "What service level is active for this account?",
])
def test_empty_read_set_is_not_supplemented_without_literal_complete_address(content):
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v3",
        "slots": [{
            "slot_id": "constraints",
            "attributes": ["service_level"],
            "attribute_families": [],
            "entity_fields": ["entity_id"],
            "attribute_semantics": [{
                "attribute": "service_level", "meaning": "service level"}],
        }],
    }
    parsed = codec.parse_measurement_response(
        {"v": "ptm1", "o": [], "r": [], "q": []},
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        reference_query_vocabulary=vocabulary,
        measurement_transport_profile=(
            "policy-transaction-compact-measurement-v1"),
        source_event_ref="event:query", event=_event(content),
    )
    assert parsed["query_references"] == []


def test_nonempty_provider_read_set_is_never_augmented():
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v3",
        "slots": [{
            "slot_id": "constraints",
            "attributes": ["delivery_time", "service_level"],
            "attribute_families": [],
            "entity_fields": ["entity_id"],
            "attribute_semantics": [
                {"attribute": "delivery_time", "meaning": "delivery time"},
                {"attribute": "service_level", "meaning": "service level"},
            ],
        }],
    }
    parsed = codec.parse_measurement_response(
        {"v": "ptm1", "o": [], "r": [], "q": [[
            "constraints", "AC-912", "delivery_time"]]},
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        reference_query_vocabulary=vocabulary,
        measurement_transport_profile=(
            "policy-transaction-compact-measurement-v1"),
        source_event_ref="event:query",
        event=_event("What service level is active for account AC-912?"),
    )
    assert parsed["query_references"] == [{
        "slot_id": "constraints",
        "match_fields": {
            "entity_id": "AC-912",
            "attribute": "delivery_time",
        },
    }]


@pytest.mark.parametrize("response", [
    {"v": "ptm1", "o": [], "r": [], "q": [], "extra": True},
    {"v": "ptm1", "o": [["delete", "request.status", "open"]], "r": [], "q": []},
    {"v": "unknown", "o": [], "r": [], "q": []},
])
def test_compact_measurement_response_is_fail_closed(response):
    codec = PolicyTransactionTextCodec()
    with pytest.raises(ValueError, match="compact measurement"):
        codec.parse_measurement_response(
            response,
            state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
            measurement_transport_profile=(
                "policy-transaction-compact-measurement-v1"),
            source_event_ref="event:1",
            event=_event(),
        )


@pytest.mark.parametrize("vocabulary", [
    {
        "version": "policy-transaction-reference-query-vocabulary-v1",
        "slots": [{"slot_id": "request.status", "attributes": ["status"]}],
    },
    {
        "version": "policy-transaction-reference-query-vocabulary-v1",
        "slots": [{"slot_id": "claims", "attributes": []}],
    },
    {
        "version": "unknown",
        "slots": [{"slot_id": "claims", "attributes": ["route"]}],
    },
])
def test_measurement_reference_vocabulary_rejects_invalid_schema(vocabulary):
    codec = PolicyTransactionTextCodec()
    envelope = codec.build_measurement_request(
        source_event_ref="event:1",
        event=_event(),
        current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        readonly_context={"reference_query_vocabulary": vocabulary},
    )

    with pytest.raises(ValueError, match="reference query vocabulary"):
        codec.build_measurement_transport(envelope)


def test_measurement_parser_rejects_query_reference_outside_vocabulary():
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v1",
        "slots": [{
            "slot_id": "constraints",
            "attributes": ["delivery_time"],
        }],
    }
    response = {
        **_patch(),
        "operations": [],
        "reference_slots": ["constraints"],
        "query_references": [{
            "slot_id": "constraints",
            "match_fields": {
                "entity_id": "M-510",
                "attribute": "recipient_availability",
            },
        }],
    }

    with pytest.raises(ValueError, match="outside"):
        codec.parse_measurement_response(
            response,
            state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
            reference_query_vocabulary=vocabulary,
        )


def test_v2_reference_vocabulary_normalizes_relation_alias_contract():
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v2",
        "slots": [{
            "slot_id": "claims",
            "attributes": ["service_arrival_time", "delivery_time"],
            "attribute_families": [{
                "identity": "arrival_time",
                "members": ["service_arrival_time", "delivery_time"],
            }],
            "entity_fields": ["target_entity_id", "entity_id"],
        }],
    }
    envelope = codec.build_measurement_request(
        source_event_ref="event:1",
        event=_event(),
        current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        readonly_context={"reference_query_vocabulary": vocabulary},
    )

    prompt = codec.build_measurement_transport(envelope)["system_prompt"]

    assert "policy-transaction-reference-query-vocabulary-v2" in prompt
    assert '"entity_fields":["entity_id","target_entity_id"]' in prompt
    assert '"identity":"arrival_time"' in prompt


def test_v3_reference_vocabulary_places_domain_semantics_in_stable_prefix():
    """AUD-526: legal field names alone are not a sufficient read-set contract."""
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v3",
        "slots": [{
            "slot_id": "claims",
            "attributes": ["delivery_time", "service_arrival_time"],
            "attribute_families": [{
                "identity": "arrival_time",
                "members": ["delivery_time", "service_arrival_time"],
            }],
            "entity_fields": ["entity_id", "target_entity_id"],
            "attribute_semantics": [
                {
                    "attribute": "delivery_time",
                    "meaning": "candidate delivery arrival time used for timing compatibility",
                },
                {
                    "attribute": "service_arrival_time",
                    "meaning": "candidate service arrival time used for timing compatibility",
                },
            ],
        }],
    }
    first = codec.build_measurement_request(
        source_event_ref="event:1",
        event=_event(content="Which courier fits the recipient schedule?"),
        current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        readonly_context={"reference_query_vocabulary": vocabulary},
    )
    second = codec.build_measurement_request(
        source_event_ref="event:2",
        event=_event(content="Where should PX-1 be delivered?"),
        current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        readonly_context={"reference_query_vocabulary": vocabulary},
    )

    first_transport = codec.build_measurement_transport(first)
    second_transport = codec.build_measurement_transport(second)

    assert first_transport["system_prompt"] == second_transport["system_prompt"]
    assert "exhaustive read set" in first_transport["system_prompt"]
    assert "candidate service arrival time" in first_transport["system_prompt"]
    assert "recipient schedule" not in first_transport["system_prompt"]


def test_v3_reference_vocabulary_requires_one_semantic_definition_per_attribute():
    codec = PolicyTransactionTextCodec()
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v3",
        "slots": [{
            "slot_id": "claims",
            "attributes": ["delivery_time", "service_arrival_time"],
            "attribute_families": [],
            "entity_fields": ["entity_id"],
            "attribute_semantics": [{
                "attribute": "delivery_time",
                "meaning": "candidate delivery arrival time",
            }],
        }],
    }

    with pytest.raises(ValueError, match="semantic definitions"):
        codec.build_measurement_transport(codec.build_measurement_request(
            source_event_ref="event:1", event=_event(),
            current_state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
            state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
            readonly_context={"reference_query_vocabulary": vocabulary},
        ))


def test_query_references_are_read_only_and_do_not_become_state_operations():
    codec = PolicyTransactionTextCodec()
    response = {
        **_patch(),
        "operations": [],
        "reference_slots": ["target.entities"],
        "query_references": [{
            "slot_id": "target.entities",
            "match_fields": {
                "entity_id": "PX-17",
                "attribute": "destination",
            },
        }],
    }
    parsed = codec.parse_measurement_response(
        response, state_space_definition=DEFAULT_STATE_SPACE_DEFINITION)

    assert parsed["operations"] == []
    assert parsed["query_references"] == response["query_references"]


@pytest.mark.parametrize("query_reference", [
    {
        "slot_id": "target.entities",
        "match_fields": {
            "entity_id": "PX-17",
            "attribute": "destination",
            "value": "North Office",
        },
    },
    {
        "slot_id": "request.status",
        "match_fields": {
            "entity_id": "PX-17",
            "attribute": "destination",
        },
    },
])
def test_query_references_reject_answer_values_and_scalar_slots(query_reference):
    codec = PolicyTransactionTextCodec()
    response = {
        **_patch(),
        "operations": [],
        "reference_slots": [],
        "query_references": [query_reference],
    }

    with pytest.raises(ValueError):
        codec.parse_measurement_response(
            response, state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        )


def test_measurement_parser_validates_sparse_patch_and_rejects_tool_output():
    codec = PolicyTransactionTextCodec()
    assert codec.parse_measurement_response(
        _patch(), state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
    ) == _patch()

    leaked = {**_patch(), "tool_calls": [{"name": "cancel_order"}]}
    with pytest.raises(ValueError, match="fields"):
        codec.parse_measurement_response(
            leaked, state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        )


def test_same_measurement_codec_accepts_two_domain_neutral_events():
    codec = PolicyTransactionTextCodec()
    first = _measurement_request(codec, "Cancel entity A-1.")
    second = _measurement_request(codec, "Diagnose entity line-2.")

    assert set(first) == set(second)
    assert first["contract"] == second["contract"]
    assert first["event"] != second["event"]


def test_realization_candidate_is_limited_to_injected_exact_tool_ids():
    codec = PolicyTransactionTextCodec()
    policy_contract = {"version": "policy:v1"}
    tool_contract = [{
        "name": "lookup_entity",
        "description": "Read one entity.",
        "parameters": {"entity_ref": "string"},
    }]
    request = codec.build_realization_request(
        source_event_ref="event:1",
        event=_event(),
        readonly_context={"recent_committed_events": []},
        policy_contract=policy_contract,
        tool_contract=tool_contract,
        contract_binding={
            "policy_contract_digest": model_data_fingerprint(policy_contract),
            "tool_contract_digest": model_data_fingerprint(tool_contract),
            "exact_tool_ids": ["lookup_entity"],
            "source_parameters_fingerprint": "a" * 64,
        },
        control={"tool_descriptors": []},
    )
    transport = codec.build_realization_transport(request)
    payload = json.loads(transport["user_content"])
    assert payload["allowed_tool_names"] == ["lookup_entity"]

    parsed = codec.parse_realization_response(
        {
            "kind": "tool",
            "tool_calls": [{
                "id": "call:1",
                "name": "lookup_entity",
                "arguments": {"entity_ref": "A-1"},
            }],
        },
        allowed_tool_names=("lookup_entity",),
    )
    assert parsed["candidate_payload"]["tool_calls"][0]["name"] == "lookup_entity"

    with pytest.raises(ValueError, match="outside"):
        codec.parse_realization_response(
            {
                "kind": "tool",
                "tool_calls": [{
                    "id": "call:2", "name": "hidden_tool", "arguments": {},
                }],
            },
            allowed_tool_names=("lookup_entity",),
        )


def test_realization_rejects_same_named_but_changed_tool_contract():
    codec = PolicyTransactionTextCodec()
    policy_contract = {"version": "policy:v1"}
    frozen_tools = [{"name": "lookup_entity", "parameters": {"id": "string"}}]
    changed_tools = [{"name": "lookup_entity", "parameters": {"id": "integer"}}]
    binding = {
        "policy_contract_digest": model_data_fingerprint(policy_contract),
        "tool_contract_digest": model_data_fingerprint(frozen_tools),
        "exact_tool_ids": ["lookup_entity"],
        "source_parameters_fingerprint": "a" * 64,
    }

    with pytest.raises(ValueError, match="tool contract differs"):
        codec.build_realization_request(
            source_event_ref="event:1",
            event=_event(),
            readonly_context={},
            policy_contract=policy_contract,
            tool_contract=changed_tools,
            contract_binding=binding,
            control={"canonical_tool_descriptors": []},
        )
