"""AUD-393 package-local regressions for discrete per-tool policy gates."""
from __future__ import annotations

from model_packages.policy_transaction_v1.policy_gate import (
    POLICY_CONTRACT_VERSION,
    build_canonical_tool_descriptors,
    evaluate_policy_gates,
)
from model_packages.policy_transaction_v1.state_space import (
    DEFAULT_STATE_SPACE_DEFINITION,
    STATE_SPACE_VERSION,
    apply_event_patch,
    empty_state,
)


TOOLS = ("lookup_entity", "change_entity", "cancel_entity")


def _state(*, authorization="pending"):
    state = empty_state(DEFAULT_STATE_SPACE_DEFINITION)
    patch = {
        "schema_version": STATE_SPACE_VERSION,
        "source_event_ref": "event:user:1",
        "source_kind": "user_text",
        "source_error": False,
        "operations": [
            {
                "operation": "set",
                "slot_id": "request.operation",
                "value": "change",
                "source_event_ref": "event:user:1",
                "source_kind": "user_text",
            },
            {
                "operation": "set",
                "slot_id": "authorization.decision",
                "value": authorization,
                "source_event_ref": "event:user:1",
                "source_kind": "user_text",
            },
        ],
        "reference_slots": ["request.operation", "authorization.decision"],
    }
    return apply_event_patch(
        state, patch, definition=DEFAULT_STATE_SPACE_DEFINITION)


def _predicate(requirement_id, slot_id, operator, value=None):
    return {
        "requirement_id": requirement_id,
        "slot_id": slot_id,
        "operator": operator,
        "value": value,
    }


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
                "requirements": [
                    _predicate(
                        "change.requires.grant",
                        "authorization.decision",
                        "equals",
                        "granted",
                    ),
                ],
                "blockers": [],
            },
            {
                "tool_id": "cancel_entity",
                "source_refs": ["policy:cancel"],
                "requirements": [],
                "blockers": [
                    _predicate(
                        "cancel.blocked.for.change",
                        "request.operation",
                        "equals",
                        "change",
                    ),
                ],
            },
        ],
    }


def _witness(tool_id, disposition="support"):
    return {
        "tool_id": tool_id,
        "disposition": disposition,
        "source_episode_refs": ["episode:prior:1"],
        "source_event_refs": ["event:prior:tool-result"],
    }


def test_policy_evaluates_every_exact_tool_to_one_discrete_gate():
    gates = evaluate_policy_gates(
        exact_tool_ids=TOOLS,
        state=_state(),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        policy_contract=_policy(),
    )

    assert [gate["tool_id"] for gate in gates] == list(TOOLS)
    assert [gate["decision"] for gate in gates] == [
        "allowed", "needs_evidence", "blocked"]
    assert gates[1]["missing_requirements"] == ["change.requires.grant"]
    assert gates[2]["matched_blockers"] == ["cancel.blocked.for.change"]


def test_strong_history_cannot_override_a_policy_block():
    gates = evaluate_policy_gates(
        exact_tool_ids=TOOLS,
        state=_state(),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        policy_contract=_policy(),
    )
    descriptors = build_canonical_tool_descriptors(
        exact_tool_ids=TOOLS,
        gates=gates,
        experience_witnesses=[_witness("cancel_entity", "support")],
    )

    cancel = next(item for item in descriptors if item["tool_id"] == "cancel_entity")
    assert cancel["decision"] == "blocked"
    assert cancel["recommendation"] == "do_not_use"
    assert cancel["experience_witness"]["disposition"] == "support"
    assert cancel["auto_execute"] is False
    assert cancel["action_commit_authority"] is False


def test_matching_history_only_strengthens_an_allowed_recommendation():
    gates = evaluate_policy_gates(
        exact_tool_ids=TOOLS,
        state=_state(authorization="granted"),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        policy_contract=_policy(),
    )
    descriptors = build_canonical_tool_descriptors(
        exact_tool_ids=TOOLS,
        gates=gates,
        experience_witnesses=[_witness("change_entity", "support")],
    )

    change = next(item for item in descriptors if item["tool_id"] == "change_entity")
    assert change["decision"] == "allowed"
    assert change["recommendation"] == "preferred"
    assert "arguments" not in change
    assert change["auto_execute"] is False


def test_descriptor_is_unique_per_exact_tool_and_text_reply_is_independent():
    gates = evaluate_policy_gates(
        exact_tool_ids=TOOLS,
        state=_state(),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        policy_contract=_policy(),
    )
    descriptors = build_canonical_tool_descriptors(
        exact_tool_ids=TOOLS,
        gates=gates,
        experience_witnesses=[],
    )

    assert len(descriptors) == len(TOOLS)
    assert len({item["tool_id"] for item in descriptors}) == len(TOOLS)
    assert all(item["text_response_required"] is True for item in descriptors)
    assert "opening_impulse" not in str(descriptors)
    assert "follow_up_impulse" not in str(descriptors)


def test_text_only_contract_has_no_synthetic_tool_gate_or_descriptor():
    """AUD-481: text-only memory QA must not invent a placeholder tool."""
    contract = {
        "version": POLICY_CONTRACT_VERSION,
        "rules": [],
    }

    gates = evaluate_policy_gates(
        exact_tool_ids=[],
        state=empty_state(DEFAULT_STATE_SPACE_DEFINITION),
        state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
        policy_contract=contract,
    )
    descriptors = build_canonical_tool_descriptors(
        exact_tool_ids=[], gates=gates, experience_witnesses=[])

    assert gates == []
    assert descriptors == []

