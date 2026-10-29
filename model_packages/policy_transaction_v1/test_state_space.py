"""AUD-399 package-local regressions for finite semantic state and sparse patches."""
from __future__ import annotations

import pytest

from model_packages.policy_transaction_v1.state_space import (
    DEFAULT_STATE_SPACE_DEFINITION,
    STATE_SPACE_VERSION,
    apply_event_patch,
    empty_state,
    normalize_state_space_definition,
    validate_event_patch,
)


def _patch(*operations):
    return {
        "schema_version": STATE_SPACE_VERSION,
        "source_event_ref": "event:user:1",
        "source_kind": "user_text",
        "source_error": False,
        "operations": list(operations),
        "reference_slots": [item["slot_id"] for item in operations],
    }


def _operation(operation, slot_id, value):
    return {
        "operation": operation,
        "slot_id": slot_id,
        "value": value,
        "source_event_ref": "event:user:1",
        "source_kind": "user_text",
    }


def test_default_definition_is_finite_and_has_no_free_key_bucket():
    definition = normalize_state_space_definition(DEFAULT_STATE_SPACE_DEFINITION)

    assert definition["schema_version"] == STATE_SPACE_VERSION
    assert 1 <= len(definition["slots"]) <= definition["limits"]["max_slots"]
    assert len({item["slot_id"] for item in definition["slots"]}) == len(
        definition["slots"])
    assert all("key" not in item for item in definition["slots"])
    assert all(item["max_items"] >= 1 for item in definition["slots"])


def test_unknown_slot_and_user_created_observation_fail_closed():
    definition = normalize_state_space_definition(DEFAULT_STATE_SPACE_DEFINITION)

    with pytest.raises(ValueError, match="unknown slot"):
        validate_event_patch(
            _patch(_operation("set", "model.invented.slot", "x")),
            definition=definition,
        )

    with pytest.raises(ValueError, match="source kind"):
        validate_event_patch(
            _patch(_operation("add", "observations", {"status": "verified"})),
            definition=definition,
        )


def test_sparse_patch_changes_only_present_slots_and_preserves_provenance():
    definition = normalize_state_space_definition(DEFAULT_STATE_SPACE_DEFINITION)
    before = empty_state(definition)
    patch = validate_event_patch(
        _patch(
            _operation("set", "request.operation", "cancel"),
            _operation("add", "target.entities", {
                "entity_type": "order", "entity_ref": "A-1",
            }),
        ),
        definition=definition,
    )

    after = apply_event_patch(before, patch, definition=definition)

    assert after["slots"]["request.operation"]["entries"][0]["value"] == "cancel"
    assert after["slots"]["request.operation"]["entries"][0][
        "source_event_ref"] == "event:user:1"
    assert after["slots"]["target.entities"]["entries"][0]["value"] == {
        "entity_type": "order", "entity_ref": "A-1",
    }
    assert after["slots"]["authorization.decision"] == before["slots"][
        "authorization.decision"]
    assert after["slots"]["observations"] == before["slots"]["observations"]


def test_patch_rejects_duplicate_slot_operation_and_extra_tool_fields():
    definition = normalize_state_space_definition(DEFAULT_STATE_SPACE_DEFINITION)
    duplicated = _patch(
        _operation("set", "request.operation", "cancel"),
        _operation("set", "request.operation", "return"),
    )
    with pytest.raises(ValueError, match="repeats"):
        validate_event_patch(duplicated, definition=definition)

    leaked = _patch(_operation("set", "request.operation", "cancel"))
    leaked["tool_calls"] = [{"name": "cancel_order"}]
    with pytest.raises(ValueError, match="fields"):
        validate_event_patch(leaked, definition=definition)


def test_many_slot_is_bounded_and_deduplicated():
    definition = normalize_state_space_definition(DEFAULT_STATE_SPACE_DEFINITION)
    before = empty_state(definition)
    entity = {"entity_type": "order", "entity_ref": "A-1"}
    patch = validate_event_patch(
        _patch(_operation("add", "target.entities", entity)),
        definition=definition,
    )
    once = apply_event_patch(before, patch, definition=definition)
    twice = apply_event_patch(once, patch, definition=definition)
    assert twice == once

    target_definition = next(
        item for item in definition["slots"] if item["slot_id"] == "target.entities")
    current = before
    for index in range(target_definition["max_items"]):
        candidate = validate_event_patch(
            _patch(_operation("add", "target.entities", {
                "entity_type": "order", "entity_ref": f"A-{index}",
            })),
            definition=definition,
        )
        current = apply_event_patch(current, candidate, definition=definition)
    overflow = validate_event_patch(
        _patch(_operation("add", "target.entities", {
            "entity_type": "order", "entity_ref": "overflow",
        })),
        definition=definition,
    )
    with pytest.raises(ValueError, match="cardinality"):
        apply_event_patch(current, overflow, definition=definition)
