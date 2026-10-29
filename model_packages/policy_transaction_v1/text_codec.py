"""Benchmark-neutral provider codec for the policy-transaction package."""
from __future__ import annotations

from collections.abc import Mapping
import json
import re
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data

from .state_space import (
    normalize_state_space_definition,
    validate_event_patch,
    validate_state,
)


MEASUREMENT_CONTRACT = "policy-transaction-semantic-measurement-v5"
REALIZATION_CONTRACT = "policy-transaction-realization-v3"
REFERENCE_QUERY_VOCABULARY_VERSION_V1 = (
    "policy-transaction-reference-query-vocabulary-v1")
REFERENCE_QUERY_VOCABULARY_VERSION_V2 = (
    "policy-transaction-reference-query-vocabulary-v2")
REFERENCE_QUERY_VOCABULARY_VERSION_V3 = (
    "policy-transaction-reference-query-vocabulary-v3")
REFERENCE_QUERY_VOCABULARY_VERSION_V4 = (
    "policy-transaction-reference-query-vocabulary-v4")
REFERENCE_QUERY_VOCABULARY_VERSION = REFERENCE_QUERY_VOCABULARY_VERSION_V4
VERBOSE_MEASUREMENT_TRANSPORT = "policy-transaction-verbose-measurement-v1"
COMPACT_MEASUREMENT_TRANSPORT = "policy-transaction-compact-measurement-v1"
SOURCE_BOUND_MEASUREMENT_TRANSPORT = "policy-transaction-compact-source-bound-measurement-v2"
QUERY_BOUND_MEASUREMENT_TRANSPORT = "policy-transaction-compact-query-bound-measurement-v3"
RECORD_BOUND_MEASUREMENT_TRANSPORT = "policy-transaction-compact-record-bound-measurement-v4"
CLOSED_WRITE_MEASUREMENT_TRANSPORT = "policy-transaction-compact-closed-write-measurement-v5"
FORBIDDEN_INPUT_FIELDS = frozenset({
    "answer", "expected_action", "expected_tool_sequence", "reward",
    "evaluator", "hidden_state", "task_id_rule",
})
_EXPLICIT_ENTITY_ID = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]*(?:-[A-Z0-9]+)+(?![A-Za-z0-9])")


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


def _tool_arguments(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "tool candidate arguments must encode one JSON object"
            ) from exc
    return _mapping(value, "tool candidate arguments")


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _sha256(value: object, field: str) -> str:
    result = _identifier(value, field)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return result


def _contract_binding(
    value: object,
    *,
    policy_contract: Mapping[str, Any],
    tool_contract: object,
    tool_names: tuple[str, ...],
) -> dict[str, Any]:
    record = _mapping(value, "contract_binding")
    if set(record) != {
        "policy_contract_digest", "tool_contract_digest", "exact_tool_ids",
        "source_parameters_fingerprint",
    }:
        raise ValueError("contract_binding fields are invalid")
    if record["policy_contract_digest"] != model_data_fingerprint(policy_contract):
        raise ValueError("policy contract differs from frozen binding")
    if record["tool_contract_digest"] != model_data_fingerprint(tool_contract):
        raise ValueError("tool contract differs from frozen binding")
    if not isinstance(record["exact_tool_ids"], (list, tuple)):
        raise ValueError("contract binding exact_tool_ids must be a list")
    exact_tool_ids = tuple(
        _identifier(item, "contract binding exact tool ID")
        for item in record["exact_tool_ids"]
    )
    if exact_tool_ids != tool_names:
        raise ValueError("tool contract names differ from frozen exact tool IDs")
    return {
        "policy_contract_digest": _sha256(
            record["policy_contract_digest"], "policy contract digest"),
        "tool_contract_digest": _sha256(
            record["tool_contract_digest"], "tool contract digest"),
        "exact_tool_ids": list(exact_tool_ids),
        "source_parameters_fingerprint": _sha256(
            record["source_parameters_fingerprint"],
            "source parameters fingerprint",
        ),
    }


def _reject_forbidden(record: Mapping[str, Any], field: str) -> None:
    leaked = FORBIDDEN_INPUT_FIELDS.intersection(record)
    if leaked:
        raise ValueError(f"{field} contains forbidden fields: {sorted(leaked)}")


def _supplement_explicit_query_references(
    measurement: Mapping[str, Any], *, event: object, vocabulary: Mapping[str, Any],
) -> dict[str, Any]:
    """Recover only literal ``entity + declared attribute`` read addresses.

    This is an answer-blind package codec fallback for a provider response that
    is structurally valid but returns an empty read set.  It deliberately does
    not interpret attribute semantics, history, values, or relation paths.
    """
    result = dict(measurement)
    if result.get("query_references"):
        return result
    event_record = _mapping(event, "explicit query event")
    if event_record.get("kind") != "user_text" \
            or not isinstance(event_record.get("content"), str):
        return result
    content = event_record["content"]
    entities = list(dict.fromkeys(_EXPLICIT_ENTITY_ID.findall(content)))
    if not entities:
        return result
    words = " " + re.sub(r"[^a-z0-9]+", " ", content.casefold()).strip() + " "
    references = []
    for slot in vocabulary["slots"]:
        for attribute in slot["attributes"]:
            phrase = " " + re.sub(
                r"[^a-z0-9]+", " ", str(attribute).casefold()).strip() + " "
            if phrase == "  " or phrase not in words:
                continue
            for entity_id in entities:
                references.append({
                    "slot_id": slot["slot_id"],
                    "match_fields": {
                        "entity_id": entity_id,
                        "attribute": attribute,
                    },
                })
    if references:
        result["query_references"] = references
    return result


def _tool_names(tools: object) -> tuple[str, ...]:
    if not isinstance(tools, (list, tuple)):
        raise ValueError("tool contract must be a list")
    names = []
    for index, raw in enumerate(tools):
        item = _mapping(raw, f"tool contract row {index}")
        name = item.get("name")
        if name is None and isinstance(item.get("function"), Mapping):
            name = item["function"].get("name")
        names.append(_identifier(name, f"tool contract row {index} name"))
    if len(names) != len(set(names)):
        raise ValueError("tool contract names must be unique")
    return tuple(names)


def normalize_reference_query_vocabulary(
    value: object,
    *,
    state_space_definition: Mapping[str, Any],
) -> dict[str, Any]:
    record = _mapping(value, "reference query vocabulary")
    version = record.get("version")
    expected_top_fields = (
        {"version", "slots", "reference_paths"}
        if version == REFERENCE_QUERY_VOCABULARY_VERSION_V4
        else {"version", "slots"}
    )
    if set(record) != expected_top_fields or version not in {
        REFERENCE_QUERY_VOCABULARY_VERSION_V1,
        REFERENCE_QUERY_VOCABULARY_VERSION_V2,
        REFERENCE_QUERY_VOCABULARY_VERSION_V3,
        REFERENCE_QUERY_VOCABULARY_VERSION_V4,
    }:
        raise ValueError("reference query vocabulary fields or version are invalid")
    raw_slots = record["slots"]
    if not isinstance(raw_slots, (list, tuple)) or not raw_slots:
        raise ValueError("reference query vocabulary slots must be non-empty")
    definition = normalize_state_space_definition(state_space_definition)
    record_slots = {
        item["slot_id"]
        for item in definition["slots"]
        if item["value_type"] == "record"
    }
    slots = []
    seen_slots = set()
    for index, raw in enumerate(raw_slots):
        item = _mapping(raw, f"reference query vocabulary slot {index}")
        if version == REFERENCE_QUERY_VOCABULARY_VERSION_V1:
            expected_fields = {"slot_id", "attributes"}
        else:
            expected_fields = {
                "slot_id", "attributes", "attribute_families",
                "entity_fields",
            }
            if version in {
                REFERENCE_QUERY_VOCABULARY_VERSION_V3,
                REFERENCE_QUERY_VOCABULARY_VERSION_V4,
            }:
                expected_fields.add("attribute_semantics")
        if set(item) != expected_fields:
            raise ValueError("reference query vocabulary slot fields are invalid")
        slot_id = _identifier(
            item["slot_id"], "reference query vocabulary slot_id")
        if slot_id not in record_slots:
            raise ValueError(
                "reference query vocabulary slot must name a record slot")
        if slot_id in seen_slots:
            raise ValueError("reference query vocabulary slots cannot repeat")
        raw_attributes = item["attributes"]
        if not isinstance(raw_attributes, (list, tuple)) or not raw_attributes:
            raise ValueError(
                "reference query vocabulary attributes must be non-empty")
        attributes = [
            _identifier(attribute, "reference query vocabulary attribute")
            for attribute in raw_attributes
        ]
        if len(attributes) != len(set(attributes)):
            raise ValueError(
                "reference query vocabulary attributes cannot repeat")
        seen_slots.add(slot_id)
        normalized_slot = {
            "slot_id": slot_id,
            "attributes": sorted(attributes),
        }
        if version in {
            REFERENCE_QUERY_VOCABULARY_VERSION_V2,
            REFERENCE_QUERY_VOCABULARY_VERSION_V3,
            REFERENCE_QUERY_VOCABULARY_VERSION_V4,
        }:
            entity_fields = [
                _identifier(field, "reference query vocabulary entity field")
                for field in item["entity_fields"]
            ] if isinstance(item["entity_fields"], (list, tuple)) else []
            if (
                not entity_fields
                or "entity_id" not in entity_fields
                or len(entity_fields) != len(set(entity_fields))
                or not set(entity_fields).issubset({
                    "entity_id", "target_entity_id",
                })
            ):
                raise ValueError(
                    "reference query vocabulary entity fields are invalid")
            raw_families = item["attribute_families"]
            if not isinstance(raw_families, (list, tuple)):
                raise ValueError(
                    "reference query vocabulary attribute families must be a list")
            families = []
            seen_identities = set()
            covered_attributes = set()
            for family_index, raw_family in enumerate(raw_families):
                family = _mapping(
                    raw_family,
                    "reference query vocabulary attribute family "
                    f"{family_index}",
                )
                if set(family) != {"identity", "members"}:
                    raise ValueError(
                        "reference query vocabulary attribute family fields "
                        "are invalid")
                identity = _identifier(
                    family["identity"],
                    "reference query vocabulary attribute family identity",
                )
                members = [
                    _identifier(
                        member,
                        "reference query vocabulary attribute family member",
                    )
                    for member in family["members"]
                ] if isinstance(family["members"], (list, tuple)) else []
                if (
                    not members
                    or len(members) != len(set(members))
                    or not set(members).issubset(set(attributes))
                    or covered_attributes.intersection(members)
                    or identity in seen_identities
                ):
                    raise ValueError(
                        "reference query vocabulary attribute family is invalid")
                seen_identities.add(identity)
                covered_attributes.update(members)
                families.append({
                    "identity": identity,
                    "members": sorted(members),
                })
            families.sort(key=lambda family: family["identity"])
            normalized_slot.update({
                "attribute_families": families,
                "entity_fields": sorted(entity_fields),
            })
        if version in {
            REFERENCE_QUERY_VOCABULARY_VERSION_V3,
            REFERENCE_QUERY_VOCABULARY_VERSION_V4,
        }:
            raw_semantics = item["attribute_semantics"]
            if not isinstance(raw_semantics, (list, tuple)):
                raise ValueError(
                    "reference query vocabulary semantic definitions must be a list")
            semantics = []
            seen_semantic_attributes = set()
            for semantic_index, raw_semantic in enumerate(raw_semantics):
                semantic = _mapping(
                    raw_semantic,
                    "reference query vocabulary semantic definition "
                    f"{semantic_index}",
                )
                if set(semantic) != {"attribute", "meaning"}:
                    raise ValueError(
                        "reference query vocabulary semantic definition fields "
                        "are invalid")
                attribute = _identifier(
                    semantic["attribute"],
                    "reference query vocabulary semantic attribute",
                )
                meaning = _identifier(
                    semantic["meaning"],
                    "reference query vocabulary semantic meaning",
                )
                if (
                    attribute not in attributes
                    or attribute in seen_semantic_attributes
                    or len(meaning) > 500
                ):
                    raise ValueError(
                        "reference query vocabulary semantic definitions are invalid")
                seen_semantic_attributes.add(attribute)
                semantics.append({"attribute": attribute, "meaning": meaning})
            if seen_semantic_attributes != set(attributes):
                raise ValueError(
                    "reference query vocabulary semantic definitions must cover "
                    "every attribute")
            semantics.sort(key=lambda semantic: semantic["attribute"])
            normalized_slot["attribute_semantics"] = semantics
        slots.append(normalized_slot)
    slots.sort(key=lambda item: item["slot_id"])
    normalized = {
        "version": version,
        "slots": slots,
    }
    if version == REFERENCE_QUERY_VOCABULARY_VERSION_V4:
        raw_paths = record["reference_paths"]
        if not isinstance(raw_paths, (list, tuple)):
            raise ValueError("reference query paths must be a list")
        slot_attributes = {
            slot["slot_id"]: set(slot["attributes"]) for slot in slots
        }
        paths = []
        identities = set()
        for index, raw_path in enumerate(raw_paths):
            path = _mapping(raw_path, f"reference query path {index}")
            if set(path) != {
                "identity", "source_slot_id", "source_attribute",
                "target_slot_id", "target_attribute", "link_value_field",
            }:
                raise ValueError("reference query path fields are invalid")
            normalized_path = {
                key: _identifier(value, f"reference query path {key}")
                for key, value in path.items()
            }
            identity = normalized_path["identity"]
            if identity in identities:
                raise ValueError("reference query path identities cannot repeat")
            identities.add(identity)
            if normalized_path["source_attribute"] not in slot_attributes.get(
                    normalized_path["source_slot_id"], set()):
                raise ValueError("reference query path source is outside vocabulary")
            if normalized_path["target_attribute"] not in slot_attributes.get(
                    normalized_path["target_slot_id"], set()):
                raise ValueError("reference query path target is outside vocabulary")
            if normalized_path["link_value_field"] not in {"value", "replaced_value"}:
                raise ValueError("reference query path link field is invalid")
            paths.append(normalized_path)
        paths.sort(key=lambda item: item["identity"])
        normalized["reference_paths"] = paths
    return normalized


def _validate_query_references_against_vocabulary(
    measurement: Mapping[str, Any],
    vocabulary: Mapping[str, Any],
) -> None:
    allowed = {
        (slot["slot_id"], attribute)
        for slot in vocabulary["slots"]
        for attribute in slot["attributes"]
    }
    for reference in measurement.get("query_references", []):
        pair = (
            reference["slot_id"],
            reference["match_fields"]["attribute"],
        )
        if pair not in allowed:
            raise ValueError(
                "query reference is outside the reference query vocabulary")


def _closed_operation_rows_schema(operations: Mapping[str, Any], maximum: int) -> dict[str, Any]:
    """Publish existing source-qualified row types; no business values or state."""
    groups = {}
    tags = {'set': 's', 'add': 'a', 'remove': 'r', 'clear': 'c'}
    for slot_id, entry in operations.items():
        for operation in entry['operations']:
            value = None
            if operation != 'clear':
                kind = entry['value_type']
                value = {'type': {'identifier': 'string', 'string': 'string', 'enum': 'string',
                                  'record': 'object', 'boolean': 'boolean', 'integer': 'integer', 'number': 'number'}[kind]}
                if kind in {'identifier', 'string', 'enum'}:
                    value['minLength'] = 1
                if kind == 'record':
                    value['minProperties'] = 1
                    required = entry.get('required_record_fields', {})
                    if required:
                        value['properties'] = {key: {'const': val} for key, val in required.items()}
                        value['required'] = sorted(required)
                if entry['allowed_values']:
                    value['enum'] = entry['allowed_values']
            key = (tags[operation], json.dumps(value, sort_keys=True))
            groups.setdefault(key, []).append(slot_id)
    branches = []
    for (tag, serialized), slots in groups.items():
        prefix = [{'const': tag}, {'enum': slots}]
        value = json.loads(serialized)
        if value is not None:
            prefix.append(value)
        branches.append({'type': 'array', 'prefixItems': prefix, 'items': False,
                         'minItems': len(prefix), 'maxItems': len(prefix)})
    if not branches:
        return {'type': 'array', 'maxItems': 0, 'items': False}
    return {'type': 'array', 'maxItems': maximum, 'items': {'oneOf': branches}}


def normalize_measurement_transport_profile(value: object) -> str:
    profile = str(value or VERBOSE_MEASUREMENT_TRANSPORT).strip()
    if profile not in {
        VERBOSE_MEASUREMENT_TRANSPORT,
        COMPACT_MEASUREMENT_TRANSPORT,
        SOURCE_BOUND_MEASUREMENT_TRANSPORT,
        QUERY_BOUND_MEASUREMENT_TRANSPORT,
        RECORD_BOUND_MEASUREMENT_TRANSPORT,
        CLOSED_WRITE_MEASUREMENT_TRANSPORT,
    }:
        raise ValueError("measurement transport profile is invalid")
    return profile


def _measurement_source(event: Mapping[str, Any]) -> tuple[str, bool]:
    kind = event.get("kind")
    if kind == "user_text":
        return "user_text", False
    if kind != "tool_results":
        raise ValueError("compact measurement event kind is unsupported")
    results = event.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError("compact measurement tool results are invalid")
    errors = []
    for row in results:
        if not isinstance(row, Mapping) or not isinstance(row.get("error"), bool):
            raise ValueError("compact measurement tool result is invalid")
        errors.append(row["error"])
    return "tool_result", any(errors)


def _expand_compact_measurement(
    value: object,
    *,
    schema_version: str,
    source_event_ref: str,
    event: Mapping[str, Any],
) -> dict[str, Any]:
    record = _mapping(value, "compact measurement response")
    if set(record) != {"v", "o", "r", "q"} or record["v"] != "ptm1":
        raise ValueError("compact measurement response fields or version are invalid")
    source_kind, source_error = _measurement_source(event)
    operations = []
    operation_codes = {
        "s": "set", "a": "add", "r": "remove", "c": "clear",
    }
    raw_operations = record["o"]
    if not isinstance(raw_operations, list):
        raise ValueError("compact measurement operations must be a list")
    for index, raw in enumerate(raw_operations):
        if (
            not isinstance(raw, list) or not raw
            or raw[0] not in operation_codes
            or len(raw) != (2 if raw[0] == "c" else 3)
        ):
            raise ValueError(f"compact measurement operation {index} is invalid")
        operation = {
            "operation": operation_codes[raw[0]],
            "slot_id": _identifier(
                raw[1], f"compact measurement operation {index} slot"),
            "value": (
                None if raw[0] == "c" else _plain(
                    raw[2], f"compact measurement operation {index} value")),
            "source_event_ref": source_event_ref,
            "source_kind": source_kind,
        }
        operations.append(operation)
    raw_slots = record["r"]
    if not isinstance(raw_slots, list):
        raise ValueError("compact measurement reference slots must be a list")
    reference_slots = [
        _identifier(item, "compact measurement reference slot")
        for item in raw_slots
    ]
    raw_queries = record["q"]
    if not isinstance(raw_queries, list):
        raise ValueError("compact measurement query references must be a list")
    query_references = []
    for index, raw in enumerate(raw_queries):
        if not isinstance(raw, list) or len(raw) != 3:
            raise ValueError(
                f"compact measurement query reference {index} is invalid")
        query_references.append({
            "slot_id": _identifier(
                raw[0], f"compact measurement query {index} slot"),
            "match_fields": {
                "entity_id": _identifier(
                    raw[1], f"compact measurement query {index} entity"),
                "attribute": _identifier(
                    raw[2], f"compact measurement query {index} attribute"),
            },
        })
    return {
        "schema_version": schema_version,
        "source_event_ref": source_event_ref,
        "source_kind": source_kind,
        "source_error": source_error,
        "operations": operations,
        "reference_slots": reference_slots,
        "query_references": query_references,
    }


class PolicyTransactionTextCodec:
    measurement_contract = MEASUREMENT_CONTRACT
    realization_contract = REALIZATION_CONTRACT

    @staticmethod
    def _declared_read_only_query(event: Mapping[str, Any]) -> dict[str, Any] | None:
        """An explicit package input role, not a heuristic over user wording."""
        content = event.get("content")
        if event.get("kind") != "user_text" or not isinstance(content, str):
            return None
        try:
            record = json.loads(content)
        except (ValueError, TypeError):
            return None
        if not isinstance(record, dict) or record.get("schema_version") != "policy-read-only-query-v1":
            return None
        if (set(record) != {"schema_version", "input_role", "task", "response_contract"}
                or record["input_role"] != "read_only_query"
                or not isinstance(record["task"], str) or not record["task"].strip()
                or not isinstance(record["response_contract"], dict)):
            raise ValueError("declared read-only query input is invalid")
        _reject_forbidden(record, "declared read-only query")
        return record

    def build_measurement_request(
        self,
        *,
        source_event_ref: str,
        event: Mapping[str, Any],
        current_state: Mapping[str, Any],
        state_space_definition: Mapping[str, Any],
        readonly_context: Mapping[str, Any],
    ) -> dict[str, Any]:
        definition = normalize_state_space_definition(state_space_definition)
        state = validate_state(current_state, definition=definition)
        event_record = _mapping(event, "event")
        context_record = _mapping(readonly_context, "readonly_context")
        _reject_forbidden(event_record, "event")
        _reject_forbidden(context_record, "readonly_context")
        return {
            "contract": self.measurement_contract,
            "source_event_ref": _identifier(source_event_ref, "source_event_ref"),
            "event": event_record,
            "current_state": state,
            "state_space_definition": definition,
            "readonly_context": context_record,
        }

    def build_measurement_transport(self, envelope: Mapping[str, Any]) -> dict[str, Any]:
        record = _mapping(envelope, "measurement envelope")
        required = {
            "contract", "source_event_ref", "event", "current_state",
            "state_space_definition", "readonly_context",
        }
        if set(record) != required or record["contract"] != self.measurement_contract:
            raise ValueError("policy-transaction measurement envelope is invalid")
        definition = normalize_state_space_definition(record["state_space_definition"])
        state = validate_state(record["current_state"], definition=definition)
        event = _mapping(record["event"], "event")
        context = _mapping(record["readonly_context"], "readonly_context")
        _reject_forbidden(event, "event")
        _reject_forbidden(context, "readonly_context")
        # The envelope remains complete for package-side validation and public
        # receipts.  The provider sees only the material needed to measure the
        # current committed event; accumulated state and audit context are not
        # semantic evidence for this objective extraction.
        # Provider caches only identical prefixes. The measurement contract and
        # finite state definition are package-versioned, stable material; the
        # committed event is the per-call suffix. Keep that order explicit so
        # repeated measurements can reuse the expensive definition without
        # relying on hidden provider conversation state. The full definition is
        # still sent and every response remains locally validated against it.
        definition_json = json.dumps(
            definition,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        transport_profile = normalize_measurement_transport_profile(
            context.get("measurement_transport_profile"))
        payload = {"event": event}
        declared_query = self._declared_read_only_query(event)
        if declared_query is not None:
            # Retain the original envelope for provenance. Output format is not
            # factual measurement evidence; only the explicit task is measured.
            payload["event"] = {"kind": event["kind"], "input_role": "read_only_query",
                                "task": declared_query["task"]}
        if transport_profile == VERBOSE_MEASUREMENT_TRANSPORT:
            payload["source_event_ref"] = _identifier(
                record["source_event_ref"], "source_event_ref")
        vocabulary = None
        vocabulary_prompt = ""
        if "reference_query_vocabulary" in context:
            vocabulary = normalize_reference_query_vocabulary(
                context["reference_query_vocabulary"],
                state_space_definition=definition,
            )
            vocabulary_json = json.dumps(
                vocabulary,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            vocabulary_prompt = (
                " When a reference query vocabulary is supplied, every "
                "query_reference must use one declared slot_id and attribute "
                "pair from that vocabulary; entity_id still comes only from the "
                "committed event. Query references are the exhaustive read set "
                "needed to answer the event: include every declared record attribute "
                "needed to evaluate constraints or compare alternatives, without "
                "adding answer values. When attribute_semantics are present, they are "
                "the normative domain definitions; do not substitute a coarser "
                "attribute merely because it mentions the same entity.\n"
                "REFERENCE_QUERY_VOCABULARY_JSON:\n"
                f"{vocabulary_json}"
            )
        if transport_profile in {COMPACT_MEASUREMENT_TRANSPORT, SOURCE_BOUND_MEASUREMENT_TRANSPORT, QUERY_BOUND_MEASUREMENT_TRANSPORT, RECORD_BOUND_MEASUREMENT_TRANSPORT, CLOSED_WRITE_MEASUREMENT_TRANSPORT}:
            response_contract = (
                "Return exactly one COMPACT_MEASUREMENT_RESPONSE JSON object "
                "with fields v,o,r,q. v is 'ptm1'. o is a list of "
                "operation rows: ['s',slot_id,value] for set, "
                "['a',slot_id,value] for add, ['r',slot_id,value] for remove, "
                "or ['c',slot_id] for clear. r is a list of declared "
                "reference slot IDs. q is a list of "
                "[record_slot_id,entity_id,attribute] read-only references. "
                "Do not emit source_event_ref, source_kind, source_error, or "
                "operation provenance; the codec deterministically binds them "
                "from the committed event."
            )
        else:
            response_contract = (
                "Measure the committed event into the supplied finite state definition. "
                "Return exactly one JSON sparse event patch. Its exact top-level fields "
                "are schema_version, source_event_ref, source_kind, source_error, "
                "operations, reference_slots, and query_references; \"source_error\": "
                "boolean, reference_slots is a list of declared slot IDs, and "
                "query_references is a list of read-only records with exactly slot_id "
                "and match_fields. Each match_fields object has exactly entity_id and "
                "attribute and identifies facts the event asks about without supplying "
                "their answer values. For every query_reference, slot_id must name a "
                "declared slot whose value_type is record; enum, identifier, and string "
                "slots cannot be query references. Query references never create state "
                "operations. "
                "Every operation "
                "object has exactly \"operation\" (not \"op\"), \"slot_id\", "
                "\"value\", \"source_event_ref\", and \"source_kind\". Each operation's "
                "source_event_ref and source_kind repeat the patch-level values. Use "
                "only declared slots, operations, value types, source kinds, and enum "
                "values. Omitted slots mean no update. This is a read-only semantic "
                "measurement with no action authority."
            )
        if declared_query is not None:
            response_contract += (
                " The declared input_role is read_only_query: o and r are empty lists "
                "in the compact response (operations and reference_slots are empty "
                "in the verbose response). Only query references identify the "
                "historical attributes requested by the task."
            )
        if transport_profile in {SOURCE_BOUND_MEASUREMENT_TRANSPORT, QUERY_BOUND_MEASUREMENT_TRANSPORT, RECORD_BOUND_MEASUREMENT_TRANSPORT, CLOSED_WRITE_MEASUREMENT_TRANSPORT}:
            source_kind, source_error = _measurement_source(event)
            operations = {}
            if declared_query is None:
                for slot in definition['slots']:
                    if source_kind not in slot['allowed_source_kinds']:
                        continue
                    allowed = slot['allowed_operations']
                    if source_error:
                        if slot['slot_id'] != 'actions' or 'add' not in allowed:
                            continue
                        allowed = ['add']
                    entry = {'operations': allowed, 'value_type': slot['value_type'],
                             'allowed_values': slot['allowed_values']}
                    if source_error:
                        entry['required_record_fields'] = {'status': 'failed'}
                    if transport_profile in {RECORD_BOUND_MEASUREMENT_TRANSPORT, CLOSED_WRITE_MEASUREMENT_TRANSPORT} and slot['value_type'] == 'record':
                        entry['value_schema_by_operation'] = {
                            operation: {'type': 'object', 'minProperties': 1}
                            for operation in allowed if operation != 'clear'}
                        if 'remove' in allowed:
                            entry['remove_match'] = 'EXACT_CANONICAL_VALUE_FINGERPRINT'
                    operations[slot['slot_id']] = entry
            # Objective capabilities from the same definition and source binding
            # used by validation. No state, answer, inferred operation or advice.
            payload['event_write_contract'] = {
                'version': 'policy-transaction-event-write-contract-v1',
                'source_kind': source_kind, 'source_error': source_error,
                'operations_by_slot': operations,
                'reference_slots': [] if declared_query is not None else
                    [slot['slot_id'] for slot in definition['slots']]}
            if transport_profile == CLOSED_WRITE_MEASUREMENT_TRANSPORT:
                payload['event_write_contract']['operation_rows_schema'] = _closed_operation_rows_schema(
                    operations, definition['limits']['max_operations_per_event'])
            if transport_profile in {QUERY_BOUND_MEASUREMENT_TRANSPORT, RECORD_BOUND_MEASUREMENT_TRANSPORT, CLOSED_WRITE_MEASUREMENT_TRANSPORT}:
                payload['query_reference_contract'] = {
                    'version': 'policy-transaction-query-reference-contract-v1',
                    'row_fields': ['record_slot_id', 'entity_id', 'attribute'],
                    'attributes_by_slot': {} if vocabulary is None else {
                        slot['slot_id']: slot['attributes'] for slot in vocabulary['slots']}}
        return {
            "system_prompt": (
                f"{response_contract}\n"
                "STATE_SPACE_DEFINITION_JSON:\n"
                f"{definition_json}"
                f"{vocabulary_prompt}"
            ),
            "user_content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "temperature": 0.0,
            "require_json": True,
        }

    def parse_measurement_response(
        self,
        response: Mapping[str, Any],
        *,
        state_space_definition: Mapping[str, Any],
        reference_query_vocabulary: Mapping[str, Any] | None = None,
        measurement_transport_profile: str | None = None,
        source_event_ref: str | None = None,
        event: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        definition = normalize_state_space_definition(state_space_definition)
        profile = normalize_measurement_transport_profile(
            measurement_transport_profile)
        if profile in {COMPACT_MEASUREMENT_TRANSPORT, SOURCE_BOUND_MEASUREMENT_TRANSPORT, QUERY_BOUND_MEASUREMENT_TRANSPORT, RECORD_BOUND_MEASUREMENT_TRANSPORT, CLOSED_WRITE_MEASUREMENT_TRANSPORT}:
            if source_event_ref is None or event is None:
                raise ValueError(
                    "compact measurement response requires committed event binding")
            record = _expand_compact_measurement(
                response,
                schema_version=definition["schema_version"],
                source_event_ref=_identifier(
                    source_event_ref, "compact measurement source event ref"),
                event=_mapping(event, "compact measurement event"),
            )
        else:
            record = _mapping(response, "measurement response")
        _reject_forbidden(record, "measurement response")
        measurement = validate_event_patch(
            record, definition=definition)
        if event is not None and self._declared_read_only_query(event) is not None:
            if measurement["operations"] or measurement["reference_slots"]:
                raise ValueError("declared read-only query cannot create fact operations or reference slots")
        if reference_query_vocabulary is not None:
            vocabulary = normalize_reference_query_vocabulary(
                reference_query_vocabulary,
                state_space_definition=state_space_definition,
            )
            if event is not None:
                measurement = _supplement_explicit_query_references(
                    measurement, event=event, vocabulary=vocabulary)
            _validate_query_references_against_vocabulary(
                measurement, vocabulary)
        return measurement

    def build_realization_request(
        self,
        *,
        source_event_ref: str,
        event: Mapping[str, Any],
        readonly_context: Mapping[str, Any],
        policy_contract: Mapping[str, Any],
        tool_contract: list[Mapping[str, Any]],
        contract_binding: Mapping[str, Any],
        control: Mapping[str, Any],
    ) -> dict[str, Any]:
        event_record = _mapping(event, "event")
        context_record = _mapping(readonly_context, "readonly_context")
        policy_record = _mapping(policy_contract, "policy_contract")
        control_record = _mapping(control, "control")
        for field, value in (
            ("event", event_record),
            ("readonly_context", context_record),
            ("policy_contract", policy_record),
            ("control", control_record),
        ):
            _reject_forbidden(value, field)
        tools = _plain(tool_contract, "tool_contract")
        names = _tool_names(tools)
        binding = _contract_binding(
            contract_binding,
            policy_contract=policy_record,
            tool_contract=tools,
            tool_names=names,
        )
        return {
            "contract": self.realization_contract,
            "source_event_ref": _identifier(source_event_ref, "source_event_ref"),
            "event": event_record,
            "readonly_context": context_record,
            "policy_contract": policy_record,
            "tool_contract": tools,
            "allowed_tool_names": list(names),
            "contract_binding": binding,
            "control": control_record,
        }

    def build_realization_transport(self, envelope: Mapping[str, Any], *,
            candidate_content_contract: Mapping[str, Any] | None = None) -> dict[str, Any]:
        record = _mapping(envelope, "realization envelope")
        required = {
            "contract", "source_event_ref", "event", "readonly_context",
            "policy_contract", "tool_contract", "allowed_tool_names",
            "contract_binding", "control",
        }
        if set(record) != required or record["contract"] != self.realization_contract:
            raise ValueError("policy-transaction realization envelope is invalid")
        names = list(_tool_names(record["tool_contract"]))
        if record["allowed_tool_names"] != names:
            raise ValueError("realization allowlist differs from tool contract")
        _contract_binding(
            record["contract_binding"],
            policy_contract=_mapping(record["policy_contract"], "policy_contract"),
            tool_contract=record["tool_contract"],
            tool_names=tuple(names),
        )
        context = _mapping(record["readonly_context"], "readonly_context")
        recalled = context.get("recalled_committed_exchanges", [])
        if not isinstance(recalled, list):
            raise ValueError("recalled committed exchanges must be a list")
        selected_memory = []
        for index, raw in enumerate(recalled):
            row = _mapping(raw, f"recalled committed exchange {index}")
            exchange = _mapping(row.get("exchange"), f"recalled exchange {index}")
            if set(exchange) != {"inbound_event", "committed_action"}:
                raise ValueError("recalled exchange content fields are invalid")
            selected_memory.append({
                "event_id": _identifier(
                    row.get("event_id"), f"recalled event ID {index}"),
                **exchange,
            })
        control = _mapping(record["control"], "control")
        if "semantic_state" in control:
            raise ValueError("realizer control cannot expose complete semantic state")
        payload = {
            "source_event_ref": record["source_event_ref"],
            "event": record["event"],
            "selected_memory": selected_memory,
            "tool_contract": record["tool_contract"],
            "allowed_tool_names": record["allowed_tool_names"],
            "control": control,
        }
        structure_description = ""
        if candidate_content_contract is not None:
            content_contract = _mapping(candidate_content_contract, "candidate content contract")
            if (set(content_contract) != {"encoding", "fields"}
                    or content_contract["encoding"] != "json-object-string"
                    or not isinstance(content_contract["fields"], dict) or not content_contract["fields"]):
                raise ValueError("explicit candidate content encoding contract is invalid")
            payload["candidate_content_contract"] = content_contract
            structure_description = (
                " Text candidate.content encoding: JSON-encoded string containing one object. "
                "Its exact field names and value types are candidate_content_contract.fields."
            )
        return {
            "system_prompt": (
                "Use only the supplied committed event, selected memory, exact tool "
                "contract, and selected transaction control. Return exactly one JSON "
                "candidate: either {kind:'text',content:string} or "
                "{kind:'tool',tool_calls:[{id,name,arguments}]}. A candidate has no "
                "execution or commit authority. Never name an unlisted tool or claim "
                "success before a committed result is present." + structure_description
            ),
            "user_content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
            "temperature": 0.0,
            "require_json": True,
        }

    def parse_realization_response(
        self, response: Mapping[str, Any], *, allowed_tool_names: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        record = _mapping(response, "realization response")
        _reject_forbidden(record, "realization response")
        kind = record.get("kind")
        if kind == "text" and set(record) == {"kind", "content"}:
            candidate = {"kind": "text", "content": _identifier(record["content"], "text content")}
        elif kind in {"tool", "tool_call", "tool_calls"} and set(record) == {
            "kind", "tool_calls",
        }:
            calls = []
            raw_calls = record["tool_calls"]
            if not isinstance(raw_calls, list) or not raw_calls:
                raise ValueError("tool candidate requires a non-empty call list")
            allowed = set(allowed_tool_names)
            if not allowed:
                raise ValueError("tool candidate requires an explicit allowlist")
            for index, raw in enumerate(raw_calls):
                item = _mapping(raw, f"tool candidate {index}")
                if set(item) != {"id", "name", "arguments"}:
                    raise ValueError("tool candidate fields are invalid")
                name = _identifier(item["name"], "tool candidate name")
                if name not in allowed:
                    raise ValueError("tool candidate is outside the public tool contract")
                calls.append({
                    "id": _identifier(item["id"], "tool candidate ID"),
                    "name": name,
                    "arguments": _tool_arguments(item["arguments"]),
                })
            candidate = {"kind": "tool", "tool_calls": calls}
        else:
            raise ValueError("realization response must contain exactly one candidate shape")
        return {
            "candidate_payload": candidate,
            "realization_evidence": {
                "codec_contract": self.realization_contract,
                "action_kind": candidate["kind"],
                "candidate_only": True,
                "action_commit_authority": False,
            },
        }


__all__ = (
    "FORBIDDEN_INPUT_FIELDS", "MEASUREMENT_CONTRACT", "REALIZATION_CONTRACT",
    "VERBOSE_MEASUREMENT_TRANSPORT", "COMPACT_MEASUREMENT_TRANSPORT",
    "REFERENCE_QUERY_VOCABULARY_VERSION",
    "PolicyTransactionTextCodec", "normalize_reference_query_vocabulary",
    "normalize_measurement_transport_profile",
)
