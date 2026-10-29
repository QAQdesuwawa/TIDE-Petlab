"""Task-local exact-reference memory and forward experience witnesses.

Memory values contain committed-event identities and public effectiveness
evidence only.  They never carry event bodies, state patches, old
authorization, tool arguments, or state/Commit authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data

from .memory_content_claims import fuse_memory_relevance
from .state_space import SOURCE_KINDS


MEMORY_EVENT_VERSION = "policy-transaction-memory-event-v1"
STRUCTURED_MEMORY_EVENT_VERSION = "policy-transaction-memory-event-v2"
QUERY_INDEXED_MEMORY_EVENT_VERSION = "policy-transaction-memory-event-v3"
PATH_INDEXED_MEMORY_EVENT_VERSION = "policy-transaction-memory-event-v4"
MEMORY_RECALL_VERSION = "policy-transaction-memory-recall-v2"
MEMORY_RECALL_RAG_VERSION = "policy-transaction-memory-recall-v3"
LEGACY_MEMORY_RECALL_FUSED_VERSION = "policy-transaction-memory-recall-v4"
MEMORY_RECALL_FUSED_VERSION = "policy-transaction-memory-recall-v5"
STRUCTURED_MEMORY_RECALL_FUSED_VERSION = "policy-transaction-memory-recall-v6"
QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION = "policy-transaction-memory-recall-v7"
RAG_ADDRESS_RECEIPT_VERSION = "policy-transaction-rag-address-v1"
RAG_SCORE_RECEIPT_VERSION = "policy-transaction-rag-score-vector-v2"
HISTORICAL_INFLUENCE_VERSION = "policy-transaction-historical-influence-v2"
EXPERIENCE_AGGREGATE_VERSION = "policy-transaction-experience-aggregate-v2"
EFFECTIVENESS_VALUES = frozenset({
    "effective", "ineffective", "unknown", "not_applicable",
})
RAG_ADDRESS_STATUSES = frozenset({"available", "unavailable", "failed"})


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


def _identifiers(value: object, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    result = [_identifier(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicates")
    return result


def _sha256(value: object, field: str) -> str:
    result = _identifier(value, field)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return result


def _fingerprinted(material: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(material, "fingerprinted material")
    return {**result, "fingerprint": model_data_fingerprint(result)}


def _verify_fingerprinted(value: object, field: str) -> dict[str, Any]:
    result = _mapping(value, field)
    fingerprint = result.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(result):
        raise ValueError(f"{field} fingerprint is invalid")
    return {**result, "fingerprint": fingerprint}


def _positive_ordinal(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("memory event ordinal must be non-negative")
    return value


def _bounded_ordinal(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < -1:
        raise ValueError(f"{field} must be an integer of at least minus one")
    return value


def _similarity(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("RAG address similarity must be numeric")
    result = float(value)
    if result < -1.0 or result > 1.0:
        raise ValueError("RAG address similarity must be in [-1, 1]")
    return result


def _unit_interval(value: object, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{field} must be numeric")
    result = float(value)
    if result < 0.0 or result > 1.0:
        raise ValueError(f"{field} must be in [0, 1]")
    return result


def _relevance_policy(value: object) -> dict[str, Any]:
    record = _exact(
        value,
        {"identity", "minimum_fused_relevance", "agreement_gain"},
        "memory relevance policy",
    )
    return {
        "identity": _identifier(record["identity"], "relevance policy identity"),
        "minimum_fused_relevance": _unit_interval(
            record["minimum_fused_relevance"], "minimum fused relevance"),
        "agreement_gain": _unit_interval(
            record["agreement_gain"], "agreement gain"),
    }


def _reference_slot_overlap_score(
    query_reference_slots: set[str], event_reference_slots: set[str],
) -> float:
    """Return overlap coverage without penalizing unrelated query detail.

    The overlap coefficient is symmetric in the two declared slot sets.  It
    gives the same evidence when a query adds other independent slots, while
    an empty side or disjoint sets remain zero.  Exact values and free text are
    intentionally outside this package-local structural witness.
    """
    if not query_reference_slots or not event_reference_slots:
        return 0.0
    overlap = query_reference_slots.intersection(event_reference_slots)
    return len(overlap) / min(
        len(query_reference_slots), len(event_reference_slots))


def reference_claim_fingerprint_from_measurement(value: object) -> str | None:
    """Derive one compact exact structured claim from a validated measurement.

    Only record-valued operations explicitly named by ``reference_slots``
    participate.  Lifecycle scalars such as an open request status therefore
    cannot become a universal memory match.  The claim contains no source ID
    or text and is stable across otherwise unrelated query detail.
    """
    measurement = _mapping(value, "semantic measurement")
    required = {
        "schema_version", "source_event_ref", "source_kind", "source_error",
        "operations", "reference_slots",
    }
    if "query_references" in measurement:
        required.add("query_references")
    if set(measurement) != required:
        raise ValueError("semantic measurement fields are invalid")
    references = set(_identifiers(
        measurement["reference_slots"], "measurement reference_slots"))
    operations = measurement["operations"]
    if not isinstance(operations, (list, tuple)):
        raise ValueError("semantic measurement operations must be a list")
    claims = []
    for raw in operations:
        operation = _mapping(raw, "semantic measurement operation")
        if set(operation) != {
            "operation", "slot_id", "value", "source_event_ref", "source_kind",
        }:
            raise ValueError("semantic measurement operation fields are invalid")
        slot_id = _identifier(operation["slot_id"], "claim slot_id")
        value = _plain(operation["value"], "claim value")
        if slot_id not in references or not isinstance(value, dict):
            continue
        claims.append({
            "operation": _identifier(operation["operation"], "claim operation"),
            "slot_id": slot_id,
            "value": value,
        })
    if not claims:
        return None
    claims.sort(key=model_data_fingerprint)
    return model_data_fingerprint({"reference_claims": claims})


def _query_reference_fingerprint(*, slot_id: object, match_fields: object) -> str:
    fields = _exact(
        match_fields, {"entity_id", "attribute"}, "query reference match_fields")
    return model_data_fingerprint({
        "slot_id": _identifier(slot_id, "query reference slot_id"),
        "match_fields": {
            "entity_id": _identifier(fields["entity_id"], "query reference entity_id"),
            "attribute": _identifier(fields["attribute"], "query reference attribute"),
        },
    })


def _reference_vocabulary_slot(
    vocabulary: object, *, slot_id: str,
) -> dict[str, Any] | None:
    if vocabulary is None:
        return None
    record = _mapping(vocabulary, "reference query vocabulary")
    slots = record.get("slots")
    if not isinstance(slots, list):
        raise ValueError("reference query vocabulary slots must be a list")
    matches = [
        _mapping(item, "reference query vocabulary slot")
        for item in slots
        if isinstance(item, Mapping) and item.get("slot_id") == slot_id
    ]
    if len(matches) > 1:
        raise ValueError("reference query vocabulary slot cannot repeat")
    return matches[0] if matches else None


def _canonical_reference_attribute(
    *, slot_id: str, attribute: object, vocabulary: object,
) -> str:
    normalized = _identifier(attribute, "reference attribute")
    slot = _reference_vocabulary_slot(vocabulary, slot_id=slot_id)
    if slot is None or not isinstance(slot.get("attribute_families"), list):
        return normalized
    identities = []
    for raw in slot["attribute_families"]:
        family = _mapping(raw, "reference query attribute family")
        members = family.get("members")
        if isinstance(members, list) and normalized in members:
            identities.append(_identifier(
                family.get("identity"), "reference query attribute identity"))
    if len(identities) > 1:
        raise ValueError("reference attribute belongs to multiple families")
    return identities[0] if identities else normalized


def _reference_entity_fields(*, slot_id: str, vocabulary: object) -> list[str]:
    slot = _reference_vocabulary_slot(vocabulary, slot_id=slot_id)
    if slot is None or not isinstance(slot.get("entity_fields"), list):
        return ["entity_id"]
    fields = [
        _identifier(field, "reference query entity field")
        for field in slot["entity_fields"]
    ]
    if "entity_id" not in fields or len(fields) != len(set(fields)):
        raise ValueError("reference query entity fields are invalid")
    return fields


def query_reference_fingerprints_from_measurement(
    value: object, *, reference_query_vocabulary: object = None,
) -> list[str]:
    """Compile answer-blind read references without creating state writes."""
    measurement = _mapping(value, "semantic measurement")
    references = measurement.get("query_references", [])
    if not isinstance(references, (list, tuple)):
        raise ValueError("measurement query_references must be a list")
    fingerprints = []
    raw_addresses = set()
    for raw in references:
        item = _exact(raw, {"slot_id", "match_fields"}, "query reference")
        slot_id = _identifier(item["slot_id"], "query reference slot_id")
        match_fields = _mapping(
            item["match_fields"], "query reference match_fields")
        raw_address = (
            slot_id,
            _identifier(match_fields["entity_id"], "query reference entity_id"),
            _identifier(match_fields["attribute"], "query reference attribute"),
        )
        if raw_address in raw_addresses:
            raise ValueError("measurement query references cannot repeat")
        raw_addresses.add(raw_address)
        fingerprint = _query_reference_fingerprint(
            slot_id=slot_id,
            match_fields={
                "entity_id": match_fields["entity_id"],
                "attribute": _canonical_reference_attribute(
                    slot_id=slot_id,
                    attribute=match_fields["attribute"],
                    vocabulary=reference_query_vocabulary,
                ),
            },
        )
        if fingerprint not in fingerprints:
            fingerprints.append(fingerprint)
    return fingerprints


def reference_index_fingerprints_from_measurement(
    value: object, *, reference_query_vocabulary: object = None,
) -> list[str]:
    """Compile committed record identities used by future read-only queries."""
    measurement = _mapping(value, "semantic measurement")
    references = set(_identifiers(
        measurement.get("reference_slots", []), "measurement reference_slots"))
    operations = measurement.get("operations")
    if not isinstance(operations, (list, tuple)):
        raise ValueError("semantic measurement operations must be a list")
    fingerprints = []
    for raw in operations:
        operation = _mapping(raw, "semantic measurement operation")
        if set(operation) != {
            "operation", "slot_id", "value", "source_event_ref", "source_kind",
        }:
            raise ValueError("semantic measurement operation fields are invalid")
        slot_id = _identifier(operation["slot_id"], "reference index slot_id")
        value = _plain(operation["value"], "reference index value")
        if (
            slot_id not in references
            or operation["operation"] == "clear"
            or not isinstance(value, dict)
            or "entity_id" not in value
            or "attribute" not in value
        ):
            continue
        attribute = _canonical_reference_attribute(
            slot_id=slot_id,
            attribute=value["attribute"],
            vocabulary=reference_query_vocabulary,
        )
        for entity_field in _reference_entity_fields(
                slot_id=slot_id, vocabulary=reference_query_vocabulary):
            if entity_field not in value:
                continue
            fingerprint = _query_reference_fingerprint(
                slot_id=slot_id,
                match_fields={
                    "entity_id": value[entity_field],
                    "attribute": attribute,
                },
            )
            if fingerprint not in fingerprints:
                fingerprints.append(fingerprint)
    return fingerprints


def reference_path_edges_from_measurement(
    value: object, *, reference_query_vocabulary: object = None,
) -> list[dict[str, Any]]:
    """Compile value-free path metadata used for deterministic multi-hop recall."""
    if reference_query_vocabulary is None:
        return []
    vocabulary = _mapping(
        reference_query_vocabulary, "reference query vocabulary")
    paths = vocabulary.get("reference_paths", [])
    if not isinstance(paths, list):
        raise ValueError("reference query vocabulary paths must be a list")
    measurement = _mapping(value, "semantic measurement")
    operations = measurement.get("operations")
    if not isinstance(operations, (list, tuple)):
        raise ValueError("semantic measurement operations must be a list")
    edges = []
    for raw_operation in operations:
        operation = _mapping(raw_operation, "semantic measurement operation")
        if operation.get("operation") == "remove":
            continue
        record = _plain(operation.get("value"), "reference path value")
        if not isinstance(record, dict) or "entity_id" not in record \
                or "attribute" not in record:
            continue
        slot_id = _identifier(operation.get("slot_id"), "reference path slot")
        attribute = _canonical_reference_attribute(
            slot_id=slot_id, attribute=record["attribute"],
            vocabulary=vocabulary)
        for raw_path in paths:
            path = _mapping(raw_path, "reference query path")
            identity = _identifier(path.get("identity"), "reference path identity")
            source_attribute = _canonical_reference_attribute(
                slot_id=path["source_slot_id"],
                attribute=path["source_attribute"], vocabulary=vocabulary)
            target_attribute = _canonical_reference_attribute(
                slot_id=path["target_slot_id"],
                attribute=path["target_attribute"], vocabulary=vocabulary)
            base = {
                "path_identity": identity,
                "query_slot_id": _identifier(
                    path["target_slot_id"], "reference path target slot"),
                "query_attribute": target_attribute,
            }
            if slot_id == path["source_slot_id"] and attribute == source_attribute:
                link_field = _identifier(
                    path["link_value_field"], "reference path link field")
                linked = record.get(link_field)
                if linked is not None:
                    edges.append({
                        **base,
                        "role": "source",
                        "source_entity_id": _identifier(
                            record["entity_id"], "reference path source entity"),
                        "linked_entity_id": _identifier(
                            linked, "reference path linked entity"),
                    })
            if slot_id == path["target_slot_id"] and attribute == target_attribute:
                edges.append({
                    **base,
                    "role": "target",
                    "source_entity_id": None,
                    "linked_entity_id": _identifier(
                        record["entity_id"], "reference path target entity"),
                })
    unique = {model_data_fingerprint(edge): edge for edge in edges}
    return [unique[key] for key in sorted(unique)]


def reference_path_index_fingerprints_from_events(
    events: object,
) -> dict[str, list[str]]:
    """Join path-edge metadata across one closed committed candidate history."""
    normalized = _normalize_events(events)
    result = {event["event_id"]: [] for event in normalized}
    sources = []
    targets = []
    for event in normalized:
        for edge in event.get("reference_path_edges", []):
            row = (event, edge)
            (sources if edge["role"] == "source" else targets).append(row)
    for source_event, source in sources:
        for target_event, target in targets:
            if (
                source_event["session_id"] != target_event["session_id"]
                or source_event["task_scope_ref"] != target_event["task_scope_ref"]
                or source["path_identity"] != target["path_identity"]
                or source["linked_entity_id"] != target["linked_entity_id"]
                or source["query_slot_id"] != target["query_slot_id"]
                or source["query_attribute"] != target["query_attribute"]
            ):
                continue
            fingerprint = _query_reference_fingerprint(
                slot_id=source["query_slot_id"],
                match_fields={
                    "entity_id": source["source_entity_id"],
                    "attribute": source["query_attribute"],
                },
            )
            for event_id in (source_event["event_id"], target_event["event_id"]):
                if fingerprint not in result[event_id]:
                    result[event_id].append(fingerprint)
    return result


def _candidate_set_fingerprint(candidate_event_ids: object) -> str:
    return model_data_fingerprint({
        "candidate_event_ids": _identifiers(
            candidate_event_ids, "candidate_event_ids"),
    })


def build_rag_address_receipt(
    *, status: object, provider_identity: object, model_identity: object,
    index_identity: object, query_fingerprint: object,
    candidate_event_ids: object, addressed_events: object,
    selection_policy_identity: object,
) -> dict[str, Any]:
    """Build an address-only RAG witness over one closed candidate set."""
    normalized_status = _identifier(status, "RAG address status")
    if normalized_status not in RAG_ADDRESS_STATUSES:
        raise ValueError("RAG address status is invalid")
    candidate_ids = _identifiers(candidate_event_ids, "candidate_event_ids")
    if not isinstance(addressed_events, (list, tuple)):
        raise ValueError("addressed_events must be a list")
    addressed = []
    seen = set()
    candidate_set = set(candidate_ids)
    for raw in addressed_events:
        item = _exact(raw, {"event_id", "similarity"}, "addressed event")
        event_id = _identifier(item["event_id"], "addressed event ID")
        if event_id in seen:
            raise ValueError("addressed events cannot repeat")
        if event_id not in candidate_set:
            raise ValueError("RAG address names an event outside the candidate set")
        seen.add(event_id)
        addressed.append({
            "event_id": event_id,
            "similarity": _similarity(item["similarity"]),
        })
    if normalized_status != "available" and addressed:
        raise ValueError("unavailable or failed RAG address cannot select events")
    material = {
        "version": RAG_ADDRESS_RECEIPT_VERSION,
        "status": normalized_status,
        "provider_identity": _identifier(
            provider_identity, "RAG provider identity"),
        "model_identity": _identifier(model_identity, "RAG model identity"),
        "index_identity": _identifier(index_identity, "RAG index identity"),
        "query_fingerprint": _sha256(
            query_fingerprint, "RAG query fingerprint"),
        "candidate_set_fingerprint": _candidate_set_fingerprint(candidate_ids),
        "addressed_events": addressed,
        "selection_policy_identity": _identifier(
            selection_policy_identity, "RAG selection policy identity"),
        "state_commit_authority": False,
        "historical_influence_authority": False,
        "content_generation_authority": False,
    }
    return {**material, "fingerprint": model_data_fingerprint(material)}


def build_rag_score_receipt(
    *, status: object, provider_identity: object, model_identity: object,
    index_identity: object, query_fingerprint: object,
    candidate_event_ids: object, candidate_scores: object,
    scoring_policy_identity: object,
) -> dict[str, Any]:
    """Build a content-free complete score witness over a closed candidate set.

    Unlike the legacy address receipt, this record has no selection authority:
    an available provider must publish exactly one score for every candidate.
    The model package later performs the only eligibility decision.
    """
    normalized_status = _identifier(status, "RAG score status")
    if normalized_status not in RAG_ADDRESS_STATUSES:
        raise ValueError("RAG score status is invalid")
    candidate_ids = _identifiers(candidate_event_ids, "candidate_event_ids")
    if not isinstance(candidate_scores, (list, tuple)):
        raise ValueError("candidate_scores must be a list")
    scores = []
    seen = set()
    candidates = set(candidate_ids)
    for raw in candidate_scores:
        item = _exact(raw, {"event_id", "similarity"}, "candidate score")
        event_id = _identifier(item["event_id"], "candidate score event ID")
        if event_id in seen:
            raise ValueError("candidate scores cannot repeat")
        if event_id not in candidates:
            raise ValueError("RAG score names an event outside the candidate set")
        seen.add(event_id)
        scores.append({
            "event_id": event_id,
            "similarity": _similarity(item["similarity"]),
        })
    if normalized_status == "available":
        if seen != candidates or len(scores) != len(candidate_ids):
            raise ValueError("available RAG score vector must be complete")
    elif scores:
        raise ValueError("unavailable or failed RAG cannot publish scores")
    material = {
        "version": RAG_SCORE_RECEIPT_VERSION,
        "status": normalized_status,
        "provider_identity": _identifier(
            provider_identity, "RAG provider identity"),
        "model_identity": _identifier(model_identity, "RAG model identity"),
        "index_identity": _identifier(index_identity, "RAG index identity"),
        "query_fingerprint": _sha256(
            query_fingerprint, "RAG query fingerprint"),
        "candidate_set_fingerprint": _candidate_set_fingerprint(candidate_ids),
        "candidate_scores": scores,
        "scoring_policy_identity": _identifier(
            scoring_policy_identity, "RAG scoring policy identity"),
        "selection_authority": False,
        "state_commit_authority": False,
        "historical_influence_authority": False,
        "content_generation_authority": False,
    }
    return {**material, "fingerprint": model_data_fingerprint(material)}


def validate_rag_score_receipt(
    value: object, *, candidate_event_ids: object,
) -> dict[str, Any]:
    record = _verify_fingerprinted(value, "rag_score_receipt")
    material = _exact(
        {key: item for key, item in record.items() if key != "fingerprint"},
        {
            "version", "status", "provider_identity", "model_identity",
            "index_identity", "query_fingerprint",
            "candidate_set_fingerprint", "candidate_scores",
            "scoring_policy_identity", "selection_authority",
            "state_commit_authority", "historical_influence_authority",
            "content_generation_authority",
        },
        "rag score material",
    )
    if material["version"] != RAG_SCORE_RECEIPT_VERSION:
        raise ValueError("RAG score receipt version is invalid")
    if material["selection_authority"] is not False:
        raise ValueError("RAG score receipt cannot select memory")
    rebuilt = build_rag_score_receipt(
        status=material["status"],
        provider_identity=material["provider_identity"],
        model_identity=material["model_identity"],
        index_identity=material["index_identity"],
        query_fingerprint=material["query_fingerprint"],
        candidate_event_ids=candidate_event_ids,
        candidate_scores=material["candidate_scores"],
        scoring_policy_identity=material["scoring_policy_identity"],
    )
    if rebuilt["candidate_set_fingerprint"] != material[
            "candidate_set_fingerprint"]:
        raise ValueError("RAG score candidate set differs from current candidates")
    if rebuilt["fingerprint"] != record["fingerprint"]:
        raise ValueError("RAG score receipt differs from its fingerprint")
    return rebuilt


def validate_rag_address_receipt(
    value: object, *, candidate_event_ids: object,
) -> dict[str, Any]:
    record = _verify_fingerprinted(value, "rag_address_receipt")
    material = _exact(
        {key: item for key, item in record.items() if key != "fingerprint"},
        {
            "version", "status", "provider_identity", "model_identity",
            "index_identity", "query_fingerprint",
            "candidate_set_fingerprint", "addressed_events",
            "selection_policy_identity", "state_commit_authority",
            "historical_influence_authority", "content_generation_authority",
        },
        "rag address material",
    )
    if material["version"] != RAG_ADDRESS_RECEIPT_VERSION:
        raise ValueError("RAG address version is invalid")
    rebuilt = build_rag_address_receipt(
        status=material["status"],
        provider_identity=material["provider_identity"],
        model_identity=material["model_identity"],
        index_identity=material["index_identity"],
        query_fingerprint=material["query_fingerprint"],
        candidate_event_ids=candidate_event_ids,
        addressed_events=material["addressed_events"],
        selection_policy_identity=material["selection_policy_identity"],
    )
    if rebuilt["candidate_set_fingerprint"] != material[
            "candidate_set_fingerprint"]:
        raise ValueError("RAG address candidate set differs from current candidates")
    if rebuilt["fingerprint"] != record["fingerprint"]:
        raise ValueError("RAG address receipt differs from its fingerprint")
    return rebuilt


def validate_memory_recall(value: object) -> dict[str, Any]:
    record = _verify_fingerprinted(value, "memory_recall")
    raw_material = {key: item for key, item in record.items() if key != "fingerprint"}
    version = raw_material.get("version")
    if version == 'e2-provider-memory-recall-v1':
        material = _exact(raw_material, {'version', 'session_id', 'task_scope_ref',
            'selected_event_ids', 'selection_witnesses', 'selection_rule',
            'state_commit_authority', 'source_selection_receipt'}, 'delegated memory recall')
        selected = _identifiers(material['selected_event_ids'], 'selected_event_ids')
        if material['selection_rule'] != 'provider-selected-full-candidate-scope' or material['state_commit_authority'] is not False:
            raise ValueError('delegated recall authority differs')
        witnesses = list(material['selection_witnesses'])
        for row in witnesses:
            _exact(row, {'event_id', 'source_kind'}, 'delegated selection witness')
            if row['source_kind'] not in SOURCE_KINDS:
                raise ValueError('invalid delegated source kind')
        if [row['event_id'] for row in witnesses] != selected:
            raise ValueError('delegated witnesses differ from selection')
        _identifier(material['session_id'], 'session_id')
        _identifier(material['task_scope_ref'], 'task_scope_ref')
        _sha256(material['source_selection_receipt'], 'source_selection_receipt')
        return {**material, 'fingerprint': model_data_fingerprint(material)}
    fields = {
        "version", "session_id", "task_scope_ref", "selected_event_ids",
        "selection_witnesses", "selection_rule", "state_commit_authority",
    }
    fused_versions = {
        LEGACY_MEMORY_RECALL_FUSED_VERSION, MEMORY_RECALL_FUSED_VERSION,
        STRUCTURED_MEMORY_RECALL_FUSED_VERSION,
        QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION,
    }
    if version in {MEMORY_RECALL_RAG_VERSION, *fused_versions}:
        fields.update({"source_rag_address_fingerprint", "rag_address_status"})
    if version in fused_versions:
        fields.update({"memory_relevance_policy"})
    if version in {
        MEMORY_RECALL_FUSED_VERSION, STRUCTURED_MEMORY_RECALL_FUSED_VERSION,
        QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION,
    }:
        fields.add("local_score_rule")
    material = _exact(raw_material, fields, "memory_recall material")
    if version not in {
        MEMORY_RECALL_VERSION, MEMORY_RECALL_RAG_VERSION,
        *fused_versions,
    }:
        raise ValueError("memory recall version is invalid")
    expected_rule = {
        MEMORY_RECALL_VERSION: "task-local-exact-structured-reference",
        MEMORY_RECALL_RAG_VERSION: (
            "task-local-structured-union-rag-exact-address"),
        LEGACY_MEMORY_RECALL_FUSED_VERSION: (
            "task-local-symmetric-rag-fusion-single-qualification-v2"),
        MEMORY_RECALL_FUSED_VERSION: (
            "task-local-symmetric-rag-fusion-single-qualification-v3"),
        STRUCTURED_MEMORY_RECALL_FUSED_VERSION: (
            "task-local-symmetric-rag-fusion-single-qualification-v4"),
        QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION: (
            "task-local-symmetric-rag-fusion-single-qualification-v5"),
    }[version]
    if material["selection_rule"] != expected_rule:
        raise ValueError("memory recall selection rule is invalid")
    if material["state_commit_authority"] is not False:
        raise ValueError("memory recall cannot have state authority")
    selected = _identifiers(material["selected_event_ids"], "selected_event_ids")
    if not isinstance(material["selection_witnesses"], (list, tuple)):
        raise ValueError("selection_witnesses must be a list")
    witnesses = []
    for raw in material["selection_witnesses"]:
        witness_fields = {
            "event_id", "source_kind", "matched_reference_slots",
            "matched_exact_tool_id",
        }
        if version == MEMORY_RECALL_RAG_VERSION:
            witness_fields.add("rag_address")
        elif version in fused_versions:
            witness_fields.update({
                "local_score", "rag_score", "fused_score", "qualified",
            })
            if version == STRUCTURED_MEMORY_RECALL_FUSED_VERSION:
                witness_fields.add("matched_reference_claim_fingerprint")
            elif version == QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION:
                witness_fields.add("matched_reference_query_fingerprints")
        item = _exact(raw, witness_fields, "selection witness")
        source_kind = _identifier(item["source_kind"], "selection source_kind")
        if source_kind not in SOURCE_KINDS:
            raise ValueError("selection witness source kind is invalid")
        tool_id = item["matched_exact_tool_id"]
        if tool_id is not None:
            tool_id = _identifier(tool_id, "matched_exact_tool_id")
        witness = {
            "event_id": _identifier(item["event_id"], "selection event_id"),
            "source_kind": source_kind,
            "matched_reference_slots": _identifiers(
                item["matched_reference_slots"], "matched_reference_slots"),
            "matched_exact_tool_id": tool_id,
        }
        if version == MEMORY_RECALL_RAG_VERSION:
            address = item["rag_address"]
            if address is not None:
                address = _exact(
                    address,
                    {"similarity", "source_receipt_fingerprint"},
                    "selection RAG address",
                )
                address = {
                    "similarity": _similarity(address["similarity"]),
                    "source_receipt_fingerprint": _sha256(
                        address["source_receipt_fingerprint"],
                        "source RAG address fingerprint",
                    ),
                }
            witness["rag_address"] = address
        elif version in fused_versions:
            witness.update({
                "local_score": _unit_interval(
                    item["local_score"], "local relevance score"),
                "rag_score": _unit_interval(
                    item["rag_score"], "RAG relevance score"),
                "fused_score": _unit_interval(
                    item["fused_score"], "fused relevance score"),
                "qualified": item["qualified"],
            })
            if version == STRUCTURED_MEMORY_RECALL_FUSED_VERSION:
                claim = item["matched_reference_claim_fingerprint"]
                if claim is not None:
                    claim = _sha256(claim, "matched reference claim fingerprint")
                witness["matched_reference_claim_fingerprint"] = claim
            elif version == QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION:
                witness["matched_reference_query_fingerprints"] = _identifiers(
                    item["matched_reference_query_fingerprints"],
                    "matched reference query fingerprints",
                )
                for fingerprint in witness["matched_reference_query_fingerprints"]:
                    _sha256(fingerprint, "matched reference query fingerprint")
            if item["qualified"] is not True:
                raise ValueError("selected fused witness must be qualified")
        witnesses.append(witness)
    if [item["event_id"] for item in witnesses] != selected:
        raise ValueError("selection witnesses differ from selected event IDs")
    normalized = {
        "version": version,
        "session_id": _identifier(material["session_id"], "session_id"),
        "task_scope_ref": _identifier(material["task_scope_ref"], "task_scope_ref"),
        "selected_event_ids": selected,
        "selection_witnesses": witnesses,
        "selection_rule": material["selection_rule"],
        "state_commit_authority": False,
    }
    if version in {MEMORY_RECALL_RAG_VERSION, *fused_versions}:
        status = _identifier(material["rag_address_status"], "RAG address status")
        if status not in RAG_ADDRESS_STATUSES:
            raise ValueError("memory recall RAG address status is invalid")
        source_fingerprint = _sha256(
            material["source_rag_address_fingerprint"],
            "source RAG address fingerprint",
        )
        if version == MEMORY_RECALL_RAG_VERSION:
            for witness in witnesses:
                address = witness["rag_address"]
                if address is not None and address[
                        "source_receipt_fingerprint"] != source_fingerprint:
                    raise ValueError(
                        "selection witness names another RAG address receipt")
        normalized.update({
            "source_rag_address_fingerprint": source_fingerprint,
            "rag_address_status": status,
        })
    if version in fused_versions:
        normalized["memory_relevance_policy"] = _relevance_policy(
            material["memory_relevance_policy"])
    if version in {
        MEMORY_RECALL_FUSED_VERSION, STRUCTURED_MEMORY_RECALL_FUSED_VERSION,
        QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION,
    }:
        expected_local_rule = {
            MEMORY_RECALL_FUSED_VERSION: "reference-slot-overlap-coefficient-v1",
            STRUCTURED_MEMORY_RECALL_FUSED_VERSION: (
                "exact-record-claim-or-exact-tool-v1"),
            QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION: (
                "answer-blind-record-query-index-or-exact-tool-v2"),
        }[version]
        if material["local_score_rule"] != expected_local_rule:
            raise ValueError("memory recall local score rule is invalid")
        normalized["local_score_rule"] = material["local_score_rule"]
    return {**normalized, "fingerprint": model_data_fingerprint(normalized)}


def validate_historical_influence(value: object) -> dict[str, Any]:
    record = _verify_fingerprinted(value, "historical_influence")
    material = _exact(
        {key: value for key, value in record.items() if key != "fingerprint"},
        {
            "version", "session_id", "task_scope_ref", "exact_tool_ids",
            "event_influences", "source_recall_fingerprint", "forward_only",
            "state_commit_authority",
        },
        "historical_influence material",
    )
    if material["version"] != HISTORICAL_INFLUENCE_VERSION:
        raise ValueError("historical influence version is invalid")
    if material["forward_only"] is not True or material["state_commit_authority"] is not False:
        raise ValueError("historical influence authority is invalid")
    tools = _identifiers(material["exact_tool_ids"], "exact_tool_ids")
    if not isinstance(material["event_influences"], (list, tuple)):
        raise ValueError("event_influences must be a list")
    influences = []
    seen_events = set()
    for raw in material["event_influences"]:
        item = _exact(
            raw,
            {"event_id", "ordinal", "tool_id", "disposition", "episode_ref"},
            "event influence",
        )
        event_id = _identifier(item["event_id"], "influence event_id")
        if event_id in seen_events:
            raise ValueError("event influences repeat an event")
        seen_events.add(event_id)
        tool_id = _identifier(item["tool_id"], "influence tool_id")
        if tool_id not in tools:
            raise ValueError("event influence names an unknown exact tool")
        disposition = _identifier(item["disposition"], "influence disposition")
        if disposition not in {"support", "conflict", "insufficient"}:
            raise ValueError("event influence disposition is invalid")
        episode_ref = item["episode_ref"]
        if episode_ref is not None:
            episode_ref = _identifier(episode_ref, "influence episode_ref")
        influences.append({
            "event_id": event_id,
            "ordinal": _positive_ordinal(item["ordinal"]),
            "tool_id": tool_id,
            "disposition": disposition,
            "episode_ref": episode_ref,
        })
    normalized = {
        "version": HISTORICAL_INFLUENCE_VERSION,
        "session_id": _identifier(material["session_id"], "session_id"),
        "task_scope_ref": _identifier(material["task_scope_ref"], "task_scope_ref"),
        "exact_tool_ids": tools,
        "event_influences": influences,
        "source_recall_fingerprint": _sha256(
            material["source_recall_fingerprint"], "source recall fingerprint"),
        "forward_only": True,
        "state_commit_authority": False,
    }
    return {**normalized, "fingerprint": model_data_fingerprint(normalized)}


def validate_experience_aggregate(value: object) -> dict[str, Any]:
    record = _verify_fingerprinted(value, "experience_aggregate")
    material = _exact(
        {key: value for key, value in record.items() if key != "fingerprint"},
        {
            "version", "session_id", "task_scope_ref", "exact_tool_ids",
            "experience_witnesses", "source_influence_fingerprint",
            "reused_prior_aggregate_fingerprint", "newly_materialized_event_ids",
            "materialized_through_ordinal", "aggregation_rule", "forward_only",
            "state_commit_authority",
        },
        "experience_aggregate material",
    )
    if material["version"] != EXPERIENCE_AGGREGATE_VERSION:
        raise ValueError("experience aggregate version is invalid")
    if material["aggregation_rule"] != "committed-order-latest-disposition-no-path-enumeration":
        raise ValueError("experience aggregate rule is invalid")
    if material["forward_only"] is not True or material["state_commit_authority"] is not False:
        raise ValueError("experience aggregate authority is invalid")
    tools = _identifiers(material["exact_tool_ids"], "exact_tool_ids")
    if not isinstance(material["experience_witnesses"], (list, tuple)):
        raise ValueError("experience_witnesses must be a list")
    witnesses = []
    seen_tools = set()
    seen_events = set()
    for raw in material["experience_witnesses"]:
        item = _exact(
            raw,
            {"tool_id", "disposition", "source_episode_refs", "source_event_refs"},
            "experience witness",
        )
        tool_id = _identifier(item["tool_id"], "experience tool_id")
        if tool_id not in tools or tool_id in seen_tools:
            raise ValueError("experience witness tool identity is invalid")
        seen_tools.add(tool_id)
        disposition = _identifier(item["disposition"], "experience disposition")
        if disposition not in {"support", "conflict", "insufficient"}:
            raise ValueError("experience witness disposition is invalid")
        source_events = _identifiers(item["source_event_refs"], "source_event_refs")
        if seen_events.intersection(source_events):
            raise ValueError("experience witnesses repeat a source event")
        seen_events.update(source_events)
        witnesses.append({
            "tool_id": tool_id,
            "disposition": disposition,
            "source_episode_refs": _identifiers(
                item["source_episode_refs"], "source_episode_refs"),
            "source_event_refs": source_events,
        })
    prior = material["reused_prior_aggregate_fingerprint"]
    if prior is not None:
        prior = _sha256(prior, "reused prior aggregate fingerprint")
    normalized = {
        "version": EXPERIENCE_AGGREGATE_VERSION,
        "session_id": _identifier(material["session_id"], "session_id"),
        "task_scope_ref": _identifier(material["task_scope_ref"], "task_scope_ref"),
        "exact_tool_ids": tools,
        "experience_witnesses": witnesses,
        "source_influence_fingerprint": _sha256(
            material["source_influence_fingerprint"], "source influence fingerprint"),
        "reused_prior_aggregate_fingerprint": prior,
        "newly_materialized_event_ids": _identifiers(
            material["newly_materialized_event_ids"], "newly materialized event IDs"),
        "materialized_through_ordinal": _bounded_ordinal(
            material["materialized_through_ordinal"], "materialized ordinal"),
        "aggregation_rule": material["aggregation_rule"],
        "forward_only": True,
        "state_commit_authority": False,
    }
    return {**normalized, "fingerprint": model_data_fingerprint(normalized)}


def normalize_memory_event(value: object) -> dict[str, Any]:
    raw = _mapping(value, "memory event")
    version = raw.get("version")
    fields = {
        "version", "event_id", "session_id", "task_scope_ref", "ordinal",
        "source_kind", "reference_slots", "exact_tool_id", "effectiveness",
        "episode_ref",
    }
    if version in {
        STRUCTURED_MEMORY_EVENT_VERSION, QUERY_INDEXED_MEMORY_EVENT_VERSION,
        PATH_INDEXED_MEMORY_EVENT_VERSION,
    }:
        fields.add("reference_claim_fingerprint")
    if version in {
        QUERY_INDEXED_MEMORY_EVENT_VERSION, PATH_INDEXED_MEMORY_EVENT_VERSION,
    }:
        fields.add("reference_index_fingerprints")
    if version == PATH_INDEXED_MEMORY_EVENT_VERSION:
        fields.add("reference_path_edges")
    record = _exact(raw, fields, "memory event")
    if version not in {
        MEMORY_EVENT_VERSION, STRUCTURED_MEMORY_EVENT_VERSION,
        QUERY_INDEXED_MEMORY_EVENT_VERSION, PATH_INDEXED_MEMORY_EVENT_VERSION,
    }:
        raise ValueError("memory event version is invalid")
    source_kind = _identifier(record["source_kind"], "source_kind")
    if source_kind not in SOURCE_KINDS:
        raise ValueError("memory event source kind is invalid")
    effectiveness = _identifier(record["effectiveness"], "effectiveness")
    if effectiveness not in EFFECTIVENESS_VALUES:
        raise ValueError("memory effectiveness is invalid")
    if source_kind == "tool_result":
        tool_id = _identifier(record["exact_tool_id"], "exact_tool_id")
        if effectiveness == "not_applicable":
            raise ValueError("agent tool results require effectiveness evidence")
    elif source_kind == "agent_tool_call":
        tool_id = _identifier(record["exact_tool_id"], "exact_tool_id")
        if effectiveness != "not_applicable":
            raise ValueError("tool calls cannot claim result effectiveness")
    else:
        if record["exact_tool_id"] is not None:
            raise ValueError("non-agent-tool events cannot enter the agent tool lane")
        if effectiveness != "not_applicable":
            raise ValueError("non-result events cannot claim effectiveness")
        tool_id = None
    episode_ref = record["episode_ref"]
    if episode_ref is not None:
        episode_ref = _identifier(episode_ref, "episode_ref")
    normalized = {
        "version": version,
        "event_id": _identifier(record["event_id"], "event_id"),
        "session_id": _identifier(record["session_id"], "session_id"),
        "task_scope_ref": _identifier(record["task_scope_ref"], "task_scope_ref"),
        "ordinal": _positive_ordinal(record["ordinal"]),
        "source_kind": source_kind,
        "reference_slots": _identifiers(record["reference_slots"], "reference_slots"),
        "exact_tool_id": tool_id,
        "effectiveness": effectiveness,
        "episode_ref": episode_ref,
    }
    if version in {
        STRUCTURED_MEMORY_EVENT_VERSION, QUERY_INDEXED_MEMORY_EVENT_VERSION,
        PATH_INDEXED_MEMORY_EVENT_VERSION,
    }:
        claim = record["reference_claim_fingerprint"]
        if claim is not None:
            claim = _sha256(claim, "reference claim fingerprint")
        normalized["reference_claim_fingerprint"] = claim
    if version in {
        QUERY_INDEXED_MEMORY_EVENT_VERSION, PATH_INDEXED_MEMORY_EVENT_VERSION,
    }:
        normalized["reference_index_fingerprints"] = _identifiers(
            record["reference_index_fingerprints"],
            "reference index fingerprints",
        )
        for fingerprint in normalized["reference_index_fingerprints"]:
            _sha256(fingerprint, "reference index fingerprint")
    if version == PATH_INDEXED_MEMORY_EVENT_VERSION:
        raw_edges = record["reference_path_edges"]
        if not isinstance(raw_edges, (list, tuple)):
            raise ValueError("reference path edges must be a list")
        edges = []
        seen_edges = set()
        for raw_edge in raw_edges:
            edge = _exact(raw_edge, {
                "path_identity", "role", "source_entity_id",
                "linked_entity_id", "query_slot_id", "query_attribute",
            }, "reference path edge")
            role = _identifier(edge["role"], "reference path role")
            if role not in {"source", "target"}:
                raise ValueError("reference path edge role is invalid")
            source_entity = edge["source_entity_id"]
            if role == "source":
                source_entity = _identifier(
                    source_entity, "reference path source entity")
            elif source_entity is not None:
                raise ValueError("target path edge cannot name a source entity")
            normalized_edge = {
                "path_identity": _identifier(
                    edge["path_identity"], "reference path identity"),
                "role": role,
                "source_entity_id": source_entity,
                "linked_entity_id": _identifier(
                    edge["linked_entity_id"], "reference path linked entity"),
                "query_slot_id": _identifier(
                    edge["query_slot_id"], "reference path query slot"),
                "query_attribute": _identifier(
                    edge["query_attribute"], "reference path query attribute"),
            }
            fingerprint = model_data_fingerprint(normalized_edge)
            if fingerprint in seen_edges:
                raise ValueError("reference path edges cannot repeat")
            seen_edges.add(fingerprint)
            edges.append(normalized_edge)
        normalized["reference_path_edges"] = edges
    return normalized


def _normalize_events(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, (list, tuple)):
        raise ValueError("candidate events must be a list")
    events = [normalize_memory_event(item) for item in value]
    event_ids = [item["event_id"] for item in events]
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("memory event IDs cannot repeat")
    return events


def select_task_history(
    *, scope: object, query: object, candidate_events: object,
    rag_address_receipt: object | None = None,
    rag_score_receipt: object | None = None,
    memory_relevance_policy: object | None = None,
) -> dict[str, Any]:
    scope_record = _exact(scope, {"session_id", "task_scope_ref"}, "memory scope")
    session_id = _identifier(scope_record["session_id"], "session_id")
    task_scope_ref = _identifier(scope_record["task_scope_ref"], "task_scope_ref")
    query_record = _mapping(query, "memory query")
    query_fields = {"reference_slots", "exact_tool_ids"}
    structured_query = "reference_claim_fingerprint" in query_record
    if structured_query:
        query_fields.add("reference_claim_fingerprint")
    has_query_index = "reference_query_fingerprints" in query_record
    if has_query_index:
        query_fields.add("reference_query_fingerprints")
    query_record = _exact(query_record, query_fields, "memory query")
    reference_slots = set(_identifiers(
        query_record["reference_slots"], "query reference_slots"))
    exact_tool_ids = set(_identifiers(
        query_record["exact_tool_ids"], "query exact_tool_ids"))
    query_claim = None
    if structured_query and query_record["reference_claim_fingerprint"] is not None:
        query_claim = _sha256(
            query_record["reference_claim_fingerprint"],
            "query reference claim fingerprint",
        )
    query_index_fingerprints = set()
    if has_query_index:
        query_index_fingerprints = set(_identifiers(
            query_record["reference_query_fingerprints"],
            "query reference fingerprints",
        ))
        for fingerprint in query_index_fingerprints:
            _sha256(fingerprint, "query reference fingerprint")
    query_indexed = bool(query_index_fingerprints)
    events = _normalize_events(candidate_events)
    path_indexes_by_event = reference_path_index_fingerprints_from_events(events)
    candidate_ids = [event["event_id"] for event in events]
    if rag_address_receipt is not None and rag_score_receipt is not None:
        raise ValueError("legacy RAG address and complete RAG scores are exclusive")
    if (rag_score_receipt is None) != (memory_relevance_policy is None):
        raise ValueError(
            "complete RAG scores and memory relevance policy are required together")
    address = None
    address_by_event = {}
    if rag_address_receipt is not None:
        address = validate_rag_address_receipt(
            rag_address_receipt, candidate_event_ids=candidate_ids)
        address_by_event = {
            item["event_id"]: item for item in address["addressed_events"]
        }
    score_receipt = None
    score_by_event = {}
    relevance_policy = None
    if rag_score_receipt is not None:
        score_receipt = validate_rag_score_receipt(
            rag_score_receipt, candidate_event_ids=candidate_ids)
        score_by_event = {
            item["event_id"]: max(0.0, item["similarity"])
            for item in score_receipt["candidate_scores"]
        }
        relevance_policy = _relevance_policy(memory_relevance_policy)
    selected = []
    witnesses = []
    for event in sorted(events, key=lambda item: (item["ordinal"], item["event_id"])):
        if event["session_id"] != session_id or event["task_scope_ref"] != task_scope_ref:
            continue
        slot_matches = sorted(reference_slots.intersection(event["reference_slots"]))
        tool_match = (
            event["exact_tool_id"] if event["exact_tool_id"] in exact_tool_ids else None)
        if score_receipt is not None:
            event_claim = event.get("reference_claim_fingerprint")
            structured_claim_mode = (
                structured_query
                and event["version"] in {
                    STRUCTURED_MEMORY_EVENT_VERSION,
                    QUERY_INDEXED_MEMORY_EVENT_VERSION,
                    PATH_INDEXED_MEMORY_EVENT_VERSION,
                }
            )
            claim_match = (
                query_claim is not None
                and event_claim is not None
                and query_claim == event_claim
            )
            event_index_fingerprints = set(
                event.get("reference_index_fingerprints", []))
            event_index_fingerprints.update(
                path_indexes_by_event.get(event["event_id"], []))
            matched_query_fingerprints = sorted(
                query_index_fingerprints.intersection(event_index_fingerprints))
            indexed_query_mode = (
                query_indexed
                and event["version"] in {
                    QUERY_INDEXED_MEMORY_EVENT_VERSION,
                    PATH_INDEXED_MEMORY_EVENT_VERSION,
                }
            )
            if indexed_query_mode:
                local_score = (
                    1.0
                    if tool_match is not None or matched_query_fingerprints
                    else 0.0
                )
            elif structured_claim_mode:
                local_score = 1.0 if tool_match is not None or claim_match else 0.0
            else:
                slot_score = _reference_slot_overlap_score(
                    reference_slots, set(event["reference_slots"]))
                local_score = max(1.0 if tool_match is not None else 0.0, slot_score)
            rag_score = score_by_event.get(event["event_id"], 0.0)
            fused_score = fuse_memory_relevance(
                local_score,
                rag_score,
                agreement_gain=relevance_policy["agreement_gain"],
            )
            if fused_score < relevance_policy["minimum_fused_relevance"]:
                continue
            selected.append(event["event_id"])
            witnesses.append({
                "event_id": event["event_id"],
                "source_kind": event["source_kind"],
                "matched_reference_slots": slot_matches,
                "matched_exact_tool_id": tool_match,
                "local_score": local_score,
                "rag_score": rag_score,
                "fused_score": fused_score,
                "qualified": True,
            })
            if indexed_query_mode:
                witnesses[-1]["matched_reference_query_fingerprints"] = (
                    matched_query_fingerprints)
            elif structured_claim_mode:
                witnesses[-1]["matched_reference_claim_fingerprint"] = (
                    query_claim if claim_match else None)
            continue
        rag_match = address_by_event.get(event["event_id"])
        if not slot_matches and tool_match is None and rag_match is None:
            continue
        selected.append(event["event_id"])
        witness = {
            "event_id": event["event_id"],
            "source_kind": event["source_kind"],
            "matched_reference_slots": slot_matches,
            "matched_exact_tool_id": tool_match,
        }
        if address is not None:
            witness["rag_address"] = (
                None if rag_match is None else {
                    "similarity": rag_match["similarity"],
                    "source_receipt_fingerprint": address["fingerprint"],
                }
            )
        witnesses.append(witness)
    if score_receipt is not None:
        selected_ordinals = [
            event["ordinal"] for event in events
            if event["event_id"] in set(selected)
        ]
        if len(selected_ordinals) != len(set(selected_ordinals)):
            raise ValueError(
                "qualified memories require an unambiguous committed chronology")
    material = {
        "version": (
            QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION
            if score_receipt is not None and query_indexed
            else STRUCTURED_MEMORY_RECALL_FUSED_VERSION
            if score_receipt is not None and structured_query
            else MEMORY_RECALL_FUSED_VERSION if score_receipt is not None
            else MEMORY_RECALL_RAG_VERSION if address is not None
            else MEMORY_RECALL_VERSION),
        "session_id": session_id,
        "task_scope_ref": task_scope_ref,
        "selected_event_ids": selected,
        "selection_witnesses": witnesses,
        "selection_rule": (
            "task-local-symmetric-rag-fusion-single-qualification-v5"
            if score_receipt is not None and query_indexed
            else "task-local-symmetric-rag-fusion-single-qualification-v4"
            if score_receipt is not None and structured_query
            else "task-local-symmetric-rag-fusion-single-qualification-v3"
            if score_receipt is not None
            else
            "task-local-structured-union-rag-exact-address"
            if address is not None
            else "task-local-exact-structured-reference"),
        "state_commit_authority": False,
    }
    if address is not None:
        material.update({
            "source_rag_address_fingerprint": address["fingerprint"],
            "rag_address_status": address["status"],
        })
    if score_receipt is not None:
        material.update({
            "source_rag_address_fingerprint": score_receipt["fingerprint"],
            "rag_address_status": score_receipt["status"],
            "memory_relevance_policy": relevance_policy,
            "local_score_rule": (
                "answer-blind-record-query-index-or-exact-tool-v2"
                if query_indexed
                else "exact-record-claim-or-exact-tool-v1"
                if structured_query
                else "reference-slot-overlap-coefficient-v1"
            ),
        })
    return _fingerprinted(material)


def read_historical_influence(
    *, memory_recall: object, historical_events: object, exact_tool_ids: object,
) -> dict[str, Any]:
    recall = validate_memory_recall(memory_recall)
    tools = _identifiers(exact_tool_ids, "exact_tool_ids")
    tool_set = set(tools)
    events = _normalize_events(historical_events)
    index = {item["event_id"]: item for item in events}
    selected = recall["selected_event_ids"]
    if not set(selected).issubset(index):
        raise ValueError("historical events omit a selected event")
    influences = []
    for event_id in selected:
        event = index[event_id]
        if (
            event["session_id"] != recall["session_id"]
            or event["task_scope_ref"] != recall["task_scope_ref"]
        ):
            raise ValueError("selected event escapes the recall scope")
        if event["source_kind"] != "tool_result":
            continue
        if event["exact_tool_id"] not in tool_set:
            raise ValueError("tool result names a non-agent exact tool ID")
        disposition = {
            "effective": "support",
            "ineffective": "conflict",
            "unknown": "insufficient",
        }[event["effectiveness"]]
        influences.append({
            "event_id": event_id,
            "ordinal": event["ordinal"],
            "tool_id": event["exact_tool_id"],
            "disposition": disposition,
            "episode_ref": event["episode_ref"],
        })
    return _fingerprinted({
        "version": HISTORICAL_INFLUENCE_VERSION,
        "session_id": recall["session_id"],
        "task_scope_ref": recall["task_scope_ref"],
        "exact_tool_ids": tools,
        "event_influences": influences,
        "source_recall_fingerprint": recall["fingerprint"],
        "forward_only": True,
        "state_commit_authority": False,
    })


def aggregate_experience_witnesses(
    *, historical_influence: object, prior_aggregate: object | None = None,
) -> dict[str, Any]:
    influence = validate_historical_influence(historical_influence)
    if influence.get("forward_only") is not True:
        raise ValueError("historical influence must be forward only")
    tools = influence["exact_tool_ids"]
    prior_fingerprint = None
    prior_through = -1
    by_tool: dict[str, dict[str, Any]] = {}
    prior_event_ids = set()
    if prior_aggregate is not None:
        prior = validate_experience_aggregate(prior_aggregate)
        if (
            prior.get("session_id") != influence["session_id"]
            or prior.get("task_scope_ref") != influence["task_scope_ref"]
        ):
            raise ValueError("prior aggregate escapes the current memory scope")
        if prior.get("exact_tool_ids") != tools:
            raise ValueError("prior aggregate exact tools differ")
        if prior.get("forward_only") is not True:
            raise ValueError("prior aggregate must be forward only")
        prior_through = prior.get("materialized_through_ordinal")
        if (
            isinstance(prior_through, bool)
            or not isinstance(prior_through, int)
            or prior_through < -1
        ):
            raise ValueError("prior materialized ordinal is invalid")
        for raw in prior.get("experience_witnesses", []):
            witness = _exact(
                raw,
                {
                    "tool_id", "disposition", "source_episode_refs",
                    "source_event_refs",
                },
                "prior experience witness",
            )
            tool_id = _identifier(witness["tool_id"], "prior witness tool_id")
            if tool_id not in tools or tool_id in by_tool:
                raise ValueError("prior witness tool identity is invalid")
            source_events = _identifiers(
                witness["source_event_refs"], "prior source_event_refs")
            if prior_event_ids.intersection(source_events):
                raise ValueError("prior aggregate repeats a source event")
            prior_event_ids.update(source_events)
            by_tool[tool_id] = {
                "tool_id": tool_id,
                "disposition": _identifier(
                    witness["disposition"], "prior disposition"),
                "source_episode_refs": _identifiers(
                    witness["source_episode_refs"], "prior source_episode_refs"),
                "source_event_refs": source_events,
            }
        prior_fingerprint = prior["fingerprint"]
    ordered_new = sorted(
        influence["event_influences"],
        key=lambda row: (row["ordinal"], row["event_id"]),
    )
    for item in ordered_new:
        if item["ordinal"] <= prior_through:
            raise ValueError("new influence does not advance prior materialization")
        if item["event_id"] in prior_event_ids:
            raise ValueError("new influence repeats a materialized event")
        tool_id = item["tool_id"]
        witness = by_tool.setdefault(tool_id, {
            "tool_id": tool_id,
            "disposition": "insufficient",
            "source_episode_refs": [],
            "source_event_refs": [],
        })
        witness["disposition"] = item["disposition"]
        if (
            item["episode_ref"] is not None
            and item["episode_ref"] not in witness["source_episode_refs"]
        ):
            witness["source_episode_refs"].append(item["episode_ref"])
        witness["source_event_refs"].append(item["event_id"])
    witnesses = [by_tool[tool_id] for tool_id in tools if tool_id in by_tool]
    through = max(
        [prior_through] + [item["ordinal"] for item in ordered_new])
    return _fingerprinted({
        "version": EXPERIENCE_AGGREGATE_VERSION,
        "session_id": influence["session_id"],
        "task_scope_ref": influence["task_scope_ref"],
        "exact_tool_ids": tools,
        "experience_witnesses": witnesses,
        "source_influence_fingerprint": influence["fingerprint"],
        "reused_prior_aggregate_fingerprint": prior_fingerprint,
        "newly_materialized_event_ids": [
            item["event_id"] for item in ordered_new],
        "materialized_through_ordinal": through,
        "aggregation_rule": "committed-order-latest-disposition-no-path-enumeration",
        "forward_only": True,
        "state_commit_authority": False,
    })


__all__ = (
    "RAG_ADDRESS_RECEIPT_VERSION",
    "RAG_SCORE_RECEIPT_VERSION",
    "build_rag_address_receipt",
    "build_rag_score_receipt",
    "EFFECTIVENESS_VALUES",
    "EXPERIENCE_AGGREGATE_VERSION",
    "HISTORICAL_INFLUENCE_VERSION",
    "MEMORY_EVENT_VERSION",
    "STRUCTURED_MEMORY_EVENT_VERSION",
    "QUERY_INDEXED_MEMORY_EVENT_VERSION",
    "PATH_INDEXED_MEMORY_EVENT_VERSION",
    "MEMORY_RECALL_VERSION",
    "MEMORY_RECALL_FUSED_VERSION",
    "STRUCTURED_MEMORY_RECALL_FUSED_VERSION",
    "QUERY_INDEXED_MEMORY_RECALL_FUSED_VERSION",
    "LEGACY_MEMORY_RECALL_FUSED_VERSION",
    "aggregate_experience_witnesses",
    "normalize_memory_event",
    "read_historical_influence",
    "reference_claim_fingerprint_from_measurement",
    "query_reference_fingerprints_from_measurement",
    "reference_index_fingerprints_from_measurement",
    "reference_path_edges_from_measurement",
    "reference_path_index_fingerprints_from_events",
    "select_task_history",
    "validate_rag_address_receipt",
    "validate_rag_score_receipt",
    "validate_experience_aggregate",
    "validate_historical_influence",
    "validate_memory_recall",
)
