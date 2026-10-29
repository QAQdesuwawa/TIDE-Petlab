"""Finite semantic state-space and sparse event-patch rules.

The module is package-owned and benchmark-neutral.  It validates model data
only; it has no provider, tool, persistence, Commit, or experiment authority.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data


STATE_SPACE_VERSION = "policy-transaction-semantic-state-v1"
STATE_DEFINITION_VERSION = "policy-transaction-state-definition-v2"
UNBOUNDED_OPERATIONS_DEFINITION_VERSION = "policy-transaction-state-definition-unbounded-operations-v3"
UNBOUNDED_RECORDS_DEFINITION_VERSION = "policy-transaction-state-definition-unbounded-records-v4"
PATCH_OPERATIONS = frozenset({"set", "add", "remove", "clear"})
SOURCE_KINDS = frozenset({
    "user_text",
    "assistant_text",
    "agent_tool_call",
    "tool_result",
    "user_tool_call",
    "user_tool_result",
})
VALUE_TYPES = frozenset({
    "identifier", "string", "boolean", "integer", "number", "enum", "record",
})


DEFAULT_STATE_SPACE_DEFINITION = {
    "definition_version": STATE_DEFINITION_VERSION,
    "schema_version": STATE_SPACE_VERSION,
    "limits": {
        "max_slots": 16,
        "max_operations_per_event": 12,
    },
    "slots": [
        {
            "slot_id": "request.operation",
            "value_type": "identifier",
            "cardinality": "single",
            "allowed_values": [],
            "allowed_operations": ["set", "clear"],
            "allowed_source_kinds": ["user_text"],
            "max_items": 1,
        },
        {
            "slot_id": "request.status",
            "value_type": "enum",
            "cardinality": "single",
            "allowed_values": ["open", "waiting", "satisfied", "withdrawn"],
            "allowed_operations": ["set", "clear"],
            "allowed_source_kinds": ["user_text", "assistant_text", "tool_result"],
            "max_items": 1,
        },
        {
            "slot_id": "target.entities",
            "value_type": "record",
            "cardinality": "many",
            "allowed_values": [],
            "allowed_operations": ["add", "remove", "clear"],
            "allowed_source_kinds": ["user_text", "tool_result"],
            "max_items": 64,
        },
        {
            "slot_id": "constraints",
            "value_type": "record",
            "cardinality": "many",
            "allowed_values": [],
            "allowed_operations": ["add", "remove", "clear"],
            "allowed_source_kinds": ["user_text"],
            "max_items": 64,
        },
        {
            "slot_id": "claims",
            "value_type": "record",
            "cardinality": "many",
            "allowed_values": [],
            "allowed_operations": ["add", "remove", "clear"],
            "allowed_source_kinds": ["user_text"],
            "max_items": 64,
        },
        {
            "slot_id": "authorization.pending_action",
            "value_type": "identifier",
            "cardinality": "single",
            "allowed_values": [],
            "allowed_operations": ["set", "clear"],
            "allowed_source_kinds": ["assistant_text", "agent_tool_call"],
            "max_items": 1,
        },
        {
            "slot_id": "authorization.decision",
            "value_type": "enum",
            "cardinality": "single",
            "allowed_values": ["pending", "granted", "denied", "revoked"],
            "allowed_operations": ["set", "clear"],
            "allowed_source_kinds": ["user_text"],
            "max_items": 1,
        },
        {
            "slot_id": "observations",
            "value_type": "record",
            "cardinality": "many",
            "allowed_values": [],
            "allowed_operations": ["add", "remove", "clear"],
            "allowed_source_kinds": ["tool_result", "user_tool_result"],
            "max_items": 64,
        },
        {
            "slot_id": "actions",
            "value_type": "record",
            "cardinality": "many",
            "allowed_values": [],
            "allowed_operations": ["add", "remove", "clear"],
            "allowed_source_kinds": [
                "agent_tool_call", "tool_result", "user_tool_call", "user_tool_result",
            ],
            "max_items": 64,
        },
        {
            "slot_id": "resolution.status",
            "value_type": "enum",
            "cardinality": "single",
            "allowed_values": ["unresolved", "resolved", "denied", "transferred"],
            "allowed_operations": ["set", "clear"],
            "allowed_source_kinds": ["user_text", "assistant_text", "tool_result"],
            "max_items": 1,
        },
        {
            "slot_id": "resolution.reason",
            "value_type": "string",
            "cardinality": "single",
            "allowed_values": [],
            "allowed_operations": ["set", "clear"],
            "allowed_source_kinds": ["assistant_text", "tool_result"],
            "max_items": 1,
        },
    ],
}


def _plain(value: object, field: str) -> Any:
    try:
        return thaw_model_data(value)
    except ValueError as exc:
        raise ValueError(f"{field} must contain model data") from exc


def _mapping(value: object, field: str) -> dict[str, Any]:
    result = _plain(value, field)
    if not isinstance(result, dict):
        raise ValueError(f"{field} must be a mapping")
    return result


def _exact(value: object, fields: set[str], field: str) -> dict[str, Any]:
    result = _mapping(value, field)
    if set(result) != fields:
        raise ValueError(f"{field} fields are invalid")
    return result


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _positive_int(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{field} must be a positive integer")
    return value


def _string_list(value: object, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    result = [_identifier(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicates")
    return result


def normalize_state_space_definition(value: object) -> dict[str, Any]:
    record = _exact(
        value,
        {"definition_version", "schema_version", "limits", "slots"},
        "state-space definition",
    )
    if record["definition_version"] not in {
            STATE_DEFINITION_VERSION, UNBOUNDED_OPERATIONS_DEFINITION_VERSION,
            UNBOUNDED_RECORDS_DEFINITION_VERSION}:
        raise ValueError("state-space definition version is invalid")
    if record["schema_version"] != STATE_SPACE_VERSION:
        raise ValueError("state-space schema version is invalid")
    limits = _exact(
        record["limits"], {"max_slots", "max_operations_per_event"}, "state limits")
    normalized_limits = {
        "max_slots": _positive_int(limits["max_slots"], "max_slots"),
        "max_operations_per_event": (None if record['definition_version'] in {
            UNBOUNDED_OPERATIONS_DEFINITION_VERSION, UNBOUNDED_RECORDS_DEFINITION_VERSION}
            and limits['max_operations_per_event'] is None else _positive_int(
                limits["max_operations_per_event"], "max_operations_per_event")),
    }
    raw_slots = record["slots"]
    if not isinstance(raw_slots, (list, tuple)) or not raw_slots:
        raise ValueError("state-space slots must be a non-empty list")
    if len(raw_slots) > normalized_limits["max_slots"]:
        raise ValueError("state-space slot count exceeds max_slots")
    slots = []
    seen = set()
    for index, raw in enumerate(raw_slots):
        item = _exact(
            raw,
            {
                "slot_id", "value_type", "cardinality", "allowed_values",
                "allowed_operations", "allowed_source_kinds", "max_items",
            },
            f"slot {index}",
        )
        slot_id = _identifier(item["slot_id"], "slot_id")
        if slot_id in seen:
            raise ValueError("state-space slot IDs cannot repeat")
        seen.add(slot_id)
        value_type = _identifier(item["value_type"], "value_type")
        if value_type not in VALUE_TYPES:
            raise ValueError("slot value type is invalid")
        cardinality = _identifier(item["cardinality"], "cardinality")
        if cardinality not in {"single", "many"}:
            raise ValueError("slot cardinality is invalid")
        max_items = (None if record['definition_version'] == UNBOUNDED_RECORDS_DEFINITION_VERSION
            and cardinality == 'many' and item['value_type'] == 'record'
            and item['max_items'] is None else _positive_int(item['max_items'], 'max_items'))
        if cardinality == "single" and max_items != 1:
            raise ValueError("single slots require max_items equal to one")
        allowed_values = _string_list(item["allowed_values"], "allowed_values")
        if value_type == "enum" and not allowed_values:
            raise ValueError("enum slots require allowed values")
        if value_type != "enum" and allowed_values:
            raise ValueError("only enum slots may declare allowed values")
        operations = _string_list(item["allowed_operations"], "allowed_operations")
        if not operations or not set(operations).issubset(PATCH_OPERATIONS):
            raise ValueError("slot operations are invalid")
        if cardinality == "single" and not set(operations).issubset({"set", "clear"}):
            raise ValueError("single-slot operations are invalid")
        if cardinality == "many" and not set(operations).issubset({"add", "remove", "clear"}):
            raise ValueError("many-slot operations are invalid")
        source_kinds = _string_list(
            item["allowed_source_kinds"], "allowed_source_kinds")
        if not source_kinds or not set(source_kinds).issubset(SOURCE_KINDS):
            raise ValueError("slot source kinds are invalid")
        slots.append({
            "slot_id": slot_id,
            "value_type": value_type,
            "cardinality": cardinality,
            "allowed_values": allowed_values,
            "allowed_operations": operations,
            "allowed_source_kinds": source_kinds,
            "max_items": max_items,
        })
    return {
        "definition_version": record['definition_version'],
        "schema_version": STATE_SPACE_VERSION,
        "limits": normalized_limits,
        "slots": slots,
    }


def _definition_index(definition: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    return {item["slot_id"]: item for item in definition["slots"]}


def _normalize_value(value: object, slot: Mapping[str, Any], field: str) -> Any:
    value_type = slot["value_type"]
    plain = _plain(value, field)
    if value_type == "identifier":
        return _identifier(plain, field)
    if value_type == "string":
        if not isinstance(plain, str) or not plain.strip():
            raise ValueError(f"{field} must be a non-empty string")
        return plain
    if value_type == "boolean":
        if type(plain) is not bool:
            raise ValueError(f"{field} must be boolean")
        return plain
    if value_type == "integer":
        if isinstance(plain, bool) or not isinstance(plain, int):
            raise ValueError(f"{field} must be an integer")
        return plain
    if value_type == "number":
        if isinstance(plain, bool) or not isinstance(plain, (int, float)):
            raise ValueError(f"{field} must be numeric")
        result = float(plain)
        if not math.isfinite(result):
            raise ValueError(f"{field} must be finite")
        return result
    if value_type == "enum":
        result = _identifier(plain, field)
        if result not in slot["allowed_values"]:
            raise ValueError(f"{field} is outside the allowed enum")
        return result
    if value_type == "record":
        if not isinstance(plain, dict) or not plain:
            raise ValueError(f"{field} must be a non-empty record")
        return plain
    raise ValueError("unsupported slot value type")


def normalize_slot_value(
    value: object, *, slot_id: str, definition: object,
) -> Any:
    """Validate one policy/predicate value against a declared state slot."""
    normalized_definition = normalize_state_space_definition(definition)
    index = _definition_index(normalized_definition)
    if slot_id not in index:
        raise ValueError("slot value names an unknown slot")
    return _normalize_value(value, index[slot_id], f"slot value {slot_id}")


def empty_state(definition: object = DEFAULT_STATE_SPACE_DEFINITION) -> dict[str, Any]:
    normalized = normalize_state_space_definition(definition)
    return {
        "schema_version": STATE_SPACE_VERSION,
        "definition_fingerprint": model_data_fingerprint(normalized),
        "slots": {
            item["slot_id"]: {
                "cardinality": item["cardinality"],
                "entries": [],
            }
            for item in normalized["slots"]
        },
    }


def validate_state(value: object, *, definition: object) -> dict[str, Any]:
    normalized_definition = normalize_state_space_definition(definition)
    index = _definition_index(normalized_definition)
    record = _exact(
        value, {"schema_version", "definition_fingerprint", "slots"}, "state")
    if record["schema_version"] != STATE_SPACE_VERSION:
        raise ValueError("state schema version is invalid")
    expected_fingerprint = model_data_fingerprint(normalized_definition)
    if record["definition_fingerprint"] != expected_fingerprint:
        raise ValueError("state definition fingerprint is invalid")
    slots = _mapping(record["slots"], "state slots")
    if set(slots) != set(index):
        raise ValueError("state slots differ from the finite definition")
    normalized_slots = {}
    for slot_id, definition_row in index.items():
        row = _exact(slots[slot_id], {"cardinality", "entries"}, f"state slot {slot_id}")
        if row["cardinality"] != definition_row["cardinality"]:
            raise ValueError("state slot cardinality differs from definition")
        if not isinstance(row["entries"], (list, tuple)):
            raise ValueError("state slot entries must be a list")
        if definition_row["max_items"] is not None and len(row["entries"]) > definition_row["max_items"]:
            raise ValueError("state slot cardinality exceeds its bound")
        entries = []
        fingerprints = set()
        for entry_index, raw in enumerate(row["entries"]):
            entry = _exact(
                raw,
                {"value", "source_event_ref", "source_kind"},
                f"state slot {slot_id} entry {entry_index}",
            )
            source_kind = _identifier(entry["source_kind"], "entry source_kind")
            if source_kind not in definition_row["allowed_source_kinds"]:
                raise ValueError("state entry source kind is invalid for slot")
            normalized_entry = {
                "value": _normalize_value(entry["value"], definition_row, "entry value"),
                "source_event_ref": _identifier(
                    entry["source_event_ref"], "entry source_event_ref"),
                "source_kind": source_kind,
            }
            fingerprint = model_data_fingerprint(normalized_entry["value"])
            if fingerprint in fingerprints:
                raise ValueError("state slot contains duplicate values")
            fingerprints.add(fingerprint)
            entries.append(normalized_entry)
        normalized_slots[slot_id] = {
            "cardinality": definition_row["cardinality"],
            "entries": entries,
        }
    return {
        "schema_version": STATE_SPACE_VERSION,
        "definition_fingerprint": expected_fingerprint,
        "slots": normalized_slots,
    }


def validate_event_patch(value: object, *, definition: object) -> dict[str, Any]:
    normalized_definition = normalize_state_space_definition(definition)
    index = _definition_index(normalized_definition)
    raw_record = _mapping(value, "event patch")
    fields = {
        "schema_version", "source_event_ref", "source_kind", "source_error",
        "operations", "reference_slots",
    }
    has_query_references = "query_references" in raw_record
    if has_query_references:
        fields.add("query_references")
    record = _exact(raw_record, fields, "event patch")
    if record["schema_version"] != STATE_SPACE_VERSION:
        raise ValueError("event-patch schema version is invalid")
    source_event_ref = _identifier(record["source_event_ref"], "source_event_ref")
    source_kind = _identifier(record["source_kind"], "source_kind")
    if source_kind not in SOURCE_KINDS:
        raise ValueError("event-patch source kind is invalid")
    if type(record["source_error"]) is not bool:
        raise ValueError("source_error must be boolean")
    if record["source_error"] and source_kind not in {"tool_result", "user_tool_result"}:
        raise ValueError("only tool-result events may be source errors")
    raw_operations = record["operations"]
    if not isinstance(raw_operations, (list, tuple)):
        raise ValueError("event-patch operations must be a list")
    operation_limit = normalized_definition["limits"]["max_operations_per_event"]
    if operation_limit is not None and len(raw_operations) > operation_limit:
        raise ValueError("event-patch operation count exceeds its bound")
    operations = []
    seen_single = set()
    seen_many = set()
    for operation_index, raw in enumerate(raw_operations):
        item = _exact(
            raw,
            {"operation", "slot_id", "value", "source_event_ref", "source_kind"},
            f"event-patch operation {operation_index}",
        )
        operation = _identifier(item["operation"], "operation")
        slot_id = _identifier(item["slot_id"], "slot_id")
        if slot_id not in index:
            raise ValueError("event patch names an unknown slot")
        slot = index[slot_id]
        if operation not in slot["allowed_operations"]:
            raise ValueError("event patch operation is invalid for slot")
        if item["source_event_ref"] != source_event_ref:
            raise ValueError("event-patch operation has a foreign source event")
        if item["source_kind"] != source_kind:
            raise ValueError("event-patch operation has a foreign source kind")
        if source_kind not in slot["allowed_source_kinds"]:
            raise ValueError("event-patch source kind is invalid for slot")
        if operation == "clear":
            if item["value"] is not None:
                raise ValueError("clear operations require a null value")
            normalized_value = None
        else:
            normalized_value = _normalize_value(
                item["value"], slot, f"operation {operation_index} value")
        if slot["cardinality"] == "single":
            if slot_id in seen_single:
                raise ValueError("event patch repeats a single slot")
            seen_single.add(slot_id)
        else:
            identity = (slot_id, operation, model_data_fingerprint(normalized_value))
            if identity in seen_many:
                raise ValueError("event patch repeats a many-slot operation")
            seen_many.add(identity)
        if record["source_error"]:
            if slot_id != "actions" or operation != "add":
                raise ValueError("failed tool results may only add a failed action")
            if not isinstance(normalized_value, dict) or normalized_value.get("status") != "failed":
                raise ValueError("failed tool results require action status failed")
        operations.append({
            "operation": operation,
            "slot_id": slot_id,
            "value": normalized_value,
            "source_event_ref": source_event_ref,
            "source_kind": source_kind,
        })
    reference_slots = _string_list(record["reference_slots"], "reference_slots")
    if not set(reference_slots).issubset(index):
        raise ValueError("event patch references an unknown slot")
    result = {
        "schema_version": STATE_SPACE_VERSION,
        "source_event_ref": source_event_ref,
        "source_kind": source_kind,
        "source_error": record["source_error"],
        "operations": operations,
        "reference_slots": reference_slots,
    }
    if has_query_references:
        raw_references = record["query_references"]
        if not isinstance(raw_references, (list, tuple)):
            raise ValueError("query_references must be a list")
        query_references = []
        seen_query_references = set()
        for query_index, raw_reference in enumerate(raw_references):
            reference = _exact(
                raw_reference, {"slot_id", "match_fields"},
                f"query reference {query_index}",
            )
            slot_id = _identifier(reference["slot_id"], "query reference slot_id")
            if slot_id not in index or index[slot_id]["value_type"] != "record":
                raise ValueError("query reference requires a declared record slot")
            match_fields = _exact(
                reference["match_fields"], {"entity_id", "attribute"},
                f"query reference {query_index} match_fields",
            )
            normalized_reference = {
                "slot_id": slot_id,
                "match_fields": {
                    "entity_id": _identifier(
                        match_fields["entity_id"], "query reference entity_id"),
                    "attribute": _identifier(
                        match_fields["attribute"], "query reference attribute"),
                },
            }
            fingerprint = model_data_fingerprint(normalized_reference)
            if fingerprint in seen_query_references:
                raise ValueError("query_references cannot contain duplicates")
            seen_query_references.add(fingerprint)
            query_references.append(normalized_reference)
        result["query_references"] = query_references
    return result


def apply_event_patch(
    state: object, patch: object, *, definition: object,
) -> dict[str, Any]:
    normalized_definition = normalize_state_space_definition(definition)
    index = _definition_index(normalized_definition)
    current = validate_state(state, definition=normalized_definition)
    normalized_patch = validate_event_patch(patch, definition=normalized_definition)
    result = _plain(current, "state")
    for operation in normalized_patch["operations"]:
        slot_id = operation["slot_id"]
        slot = index[slot_id]
        entries = result["slots"][slot_id]["entries"]
        if operation["operation"] == "clear":
            entries.clear()
            continue
        entry = {
            "value": _plain(operation["value"], "operation value"),
            "source_event_ref": operation["source_event_ref"],
            "source_kind": operation["source_kind"],
        }
        if slot["cardinality"] == "single":
            entries[:] = [entry]
            continue
        fingerprint = model_data_fingerprint(entry["value"])
        existing = {
            model_data_fingerprint(item["value"]): item for item in entries}
        if operation["operation"] == "add":
            if fingerprint not in existing:
                if slot["max_items"] is not None and len(entries) >= slot["max_items"]:
                    raise ValueError("state slot cardinality exceeds its bound")
                entries.append(entry)
        elif operation["operation"] == "remove":
            entries[:] = [
                item for item in entries
                if model_data_fingerprint(item["value"]) != fingerprint
            ]
        else:
            raise ValueError("many-slot operation is unsupported")
    return validate_state(result, definition=normalized_definition)


__all__ = (
    "DEFAULT_STATE_SPACE_DEFINITION",
    "PATCH_OPERATIONS",
    "SOURCE_KINDS",
    "STATE_DEFINITION_VERSION",
    "STATE_SPACE_VERSION",
    "apply_event_patch",
    "empty_state",
    "normalize_state_space_definition",
    "normalize_slot_value",
    "validate_event_patch",
    "validate_state",
)
