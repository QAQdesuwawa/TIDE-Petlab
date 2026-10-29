"""AUD-476: package-owned exact-ID RAG address boundary regressions."""
from __future__ import annotations

import pytest

from framework.model_api import model_data_fingerprint
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.temporal_domain import (
    REQUIRED_TEMPORAL_OPERATORS,
    TemporalDomainBinding,
    TemporalDomainManifest,
    TemporalPosition,
)
from model_packages.policy_transaction_v1 import PolicyTransactionV1Package
from model_packages.policy_transaction_v1.memory import (
    MEMORY_EVENT_VERSION,
    PATH_INDEXED_MEMORY_EVENT_VERSION,
    STRUCTURED_MEMORY_EVENT_VERSION,
    STRUCTURED_MEMORY_RECALL_FUSED_VERSION,
    build_rag_address_receipt,
    build_rag_score_receipt,
    reference_claim_fingerprint_from_measurement,
    query_reference_fingerprints_from_measurement,
    reference_index_fingerprints_from_measurement,
    reference_path_edges_from_measurement,
    select_task_history,
)
from model_packages.policy_transaction_v1.rag_address import (
    build_embedding_address_request,
    resolve_embedding_address,
)
from model_packages.policy_transaction_v1.memory_content_claims import (
    fuse_memory_relevance,
    read_memory_content_claims,
)


SCOPE = {"session_id": "session:rag", "task_scope_ref": "task:rag"}


def test_fused_relevance_is_symmetric_bounded_and_preserves_single_source():
    assert fuse_memory_relevance(0.8, 0.0, agreement_gain=0.5) == pytest.approx(0.8)
    assert fuse_memory_relevance(0.0, 0.8, agreement_gain=0.5) == pytest.approx(0.8)
    both = fuse_memory_relevance(0.8, 0.8, agreement_gain=0.5)
    assert both == pytest.approx(0.88)
    assert both > 0.8
    assert both <= 1.0
    assert fuse_memory_relevance(0.9, 0.4, agreement_gain=0.5) == (
        fuse_memory_relevance(0.4, 0.9, agreement_gain=0.5)
    )


def _score_receipt(*event_scores, status="available"):
    candidate_ids = ["event:old:office", "event:old:weather"]
    return build_rag_score_receipt(
        status=status,
        provider_identity="provider:qwen3-local-v2",
        model_identity="qwen3-embedding-0.6b-q8-d256",
        index_identity="index:session-rag:v4",
        query_fingerprint="2" * 64,
        candidate_event_ids=candidate_ids,
        candidate_scores=[
            {"event_id": event_id, "similarity": score}
            for event_id, score in event_scores
        ],
        scoring_policy_identity="cosine-complete-vector-v2",
    )


def test_complete_rag_score_receipt_rejects_missing_candidate_score():
    with pytest.raises(ValueError, match="complete|candidate"):
        _score_receipt(("event:old:office", 0.8))


def test_package_fuses_local_and_rag_once_then_keeps_every_qualified_id():
    events = [
        _event("event:old:office", 10, slots=("request.status",)),
        _event("event:old:weather", 20),
    ]
    receipt = _score_receipt(
        ("event:old:weather", 0.72),
        ("event:old:office", 0.40),
    )
    recall = select_task_history(
        scope=SCOPE,
        query={"reference_slots": ["request.status"], "exact_tool_ids": []},
        candidate_events=events,
        rag_score_receipt=receipt,
        memory_relevance_policy={
            "identity": "symmetric-fusion-v2",
            "minimum_fused_relevance": 0.60,
            "agreement_gain": 0.25,
        },
    )

    # The local-only and RAG-only paths use the same fusion function.  Both
    # qualified IDs survive; score order cannot replace committed chronology.
    assert recall["selected_event_ids"] == [
        "event:old:office", "event:old:weather"]
    witnesses = {row["event_id"]: row for row in recall["selection_witnesses"]}
    assert witnesses["event:old:office"]["local_score"] == 1.0
    assert witnesses["event:old:office"]["rag_score"] == 0.4
    assert witnesses["event:old:office"]["fused_score"] == pytest.approx(1.0)
    assert witnesses["event:old:weather"]["local_score"] == 0.0
    assert witnesses["event:old:weather"]["rag_score"] == 0.72
    assert witnesses["event:old:weather"]["fused_score"] == pytest.approx(0.72)
    assert recall["selection_rule"] == (
        "task-local-symmetric-rag-fusion-single-qualification-v3")
    assert recall["local_score_rule"] == (
        "reference-slot-overlap-coefficient-v1")


def test_local_overlap_is_not_diluted_by_additional_query_slots():
    events = [
        _event("event:old:office", 10, slots=("constraints",)),
        _event("event:old:weather", 20, slots=("claims",)),
    ]
    receipt = _score_receipt(
        ("event:old:office", 0.98),
        ("event:old:weather", 0.20),
    )
    policy = {
        "identity": "symmetric-fusion-v2",
        "minimum_fused_relevance": 0.99,
        "agreement_gain": 0.25,
    }

    narrow = select_task_history(
        scope=SCOPE,
        query={"reference_slots": ["constraints"], "exact_tool_ids": []},
        candidate_events=events,
        rag_score_receipt=receipt,
        memory_relevance_policy=policy,
    )
    wide = select_task_history(
        scope=SCOPE,
        query={
            "reference_slots": [
                "request.operation", "request.status", "target.entities",
                "constraints",
            ],
            "exact_tool_ids": [],
        },
        candidate_events=events,
        rag_score_receipt=receipt,
        memory_relevance_policy=policy,
    )

    assert narrow["selected_event_ids"] == ["event:old:office"]
    assert wide["selected_event_ids"] == narrow["selected_event_ids"]
    narrow_witness = narrow["selection_witnesses"][0]
    wide_witness = wide["selection_witnesses"][0]
    assert wide_witness["matched_reference_slots"] == ["constraints"]
    assert wide_witness["local_score"] == narrow_witness["local_score"] == 1.0
    assert wide_witness["fused_score"] == narrow_witness["fused_score"]


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
        session_id=SCOPE["session_id"],
        scope_ref=SCOPE["task_scope_ref"],
        subject_ref="agent:one",
        event_ref="event:current",
        action_ref=None,
        temporal_position=TemporalPosition.create(
            binding=TemporalDomainBinding.from_manifest(manifest), coordinate=4),
        runtime_ordinal=4,
        metadata={},
    )


def _event(event_id: str, ordinal: int, *, slots=()):
    return {
        "version": MEMORY_EVENT_VERSION,
        "event_id": event_id,
        "session_id": SCOPE["session_id"],
        "task_scope_ref": SCOPE["task_scope_ref"],
        "ordinal": ordinal,
        "source_kind": "user_text",
        "reference_slots": list(slots),
        "exact_tool_id": None,
        "effectiveness": "not_applicable",
        "episode_ref": None,
    }


def _record_claim(*, entity_id: str, attribute: str) -> str:
    event_id = "event:claim"
    return reference_claim_fingerprint_from_measurement({
        "schema_version": "policy-transaction-semantic-state-v1",
        "source_event_ref": event_id,
        "source_kind": "user_text",
        "source_error": False,
        "operations": [{
            "operation": "add",
            "slot_id": "constraints",
            "value": {"entity_id": entity_id, "attribute": attribute},
            "source_event_ref": event_id,
            "source_kind": "user_text",
        }],
        "reference_slots": ["constraints"],
    })


def _structured_event(
    event_id: str, ordinal: int, *, claim: str | None, slots=("constraints",),
):
    return {
        **_event(event_id, ordinal, slots=slots),
        "version": STRUCTURED_MEMORY_EVENT_VERSION,
        "reference_claim_fingerprint": claim,
    }


def test_structured_local_evidence_uses_exact_record_claim_not_generic_slot_name():
    destination = _record_claim(entity_id="PX-17", attribute="destination")
    phone = _record_claim(entity_id="PX-17", attribute="phone")
    events = [
        _structured_event("event:old:office", 10, claim=destination),
        _structured_event("event:old:weather", 20, claim=phone),
    ]
    receipt = _score_receipt(
        ("event:old:office", 0.0),
        ("event:old:weather", 0.0),
    )
    policy = {
        "identity": "symmetric-fusion-v2",
        "minimum_fused_relevance": 0.99,
        "agreement_gain": 0.25,
    }

    recall = select_task_history(
        scope=SCOPE,
        query={
            "reference_slots": [
                "request.status", "target.entities", "constraints"],
            "exact_tool_ids": [],
            "reference_claim_fingerprint": destination,
        },
        candidate_events=events,
        rag_score_receipt=receipt,
        memory_relevance_policy=policy,
    )

    assert recall["selected_event_ids"] == ["event:old:office"]
    witness = recall["selection_witnesses"][0]
    assert witness["local_score"] == 1.0
    assert witness["matched_reference_claim_fingerprint"] == destination
    assert recall["local_score_rule"] == "exact-record-claim-or-exact-tool-v1"


def test_empty_query_index_keeps_structured_claim_matching():
    destination = _record_claim(entity_id="PX-17", attribute="destination")
    recall = select_task_history(
        scope=SCOPE,
        query={
            "reference_slots": ["constraints"],
            "exact_tool_ids": [],
            "reference_claim_fingerprint": destination,
            "reference_query_fingerprints": [],
        },
        candidate_events=[
            _structured_event("event:old:office", 10, claim=destination),
            _structured_event(
                "event:old:weather", 20,
                claim=_record_claim(entity_id="PX-17", attribute="phone"),
            ),
        ],
        rag_score_receipt=_score_receipt(
            ("event:old:office", 0.0), ("event:old:weather", 0.0)),
        memory_relevance_policy={
            "identity": "symmetric-fusion-v2",
            "minimum_fused_relevance": 0.99,
            "agreement_gain": 0.25,
        },
    )

    assert recall["selected_event_ids"] == ["event:old:office"]
    assert recall["version"] == STRUCTURED_MEMORY_RECALL_FUSED_VERSION
    assert recall["local_score_rule"] == "exact-record-claim-or-exact-tool-v1"


def test_structured_local_evidence_does_not_promote_scalar_status_overlap():
    events = [
        _structured_event(
            "event:old:office", 10, claim=None,
            slots=("request.status",),
        ),
        _structured_event(
            "event:old:weather", 20, claim=None,
            slots=("request.status",),
        ),
    ]
    receipt = _score_receipt(
        ("event:old:office", 0.0),
        ("event:old:weather", 0.0),
    )
    recall = select_task_history(
        scope=SCOPE,
        query={
            "reference_slots": ["request.status"],
            "exact_tool_ids": [],
            "reference_claim_fingerprint": None,
        },
        candidate_events=events,
        rag_score_receipt=receipt,
        memory_relevance_policy={
            "identity": "symmetric-fusion-v2",
            "minimum_fused_relevance": 0.99,
            "agreement_gain": 0.25,
        },
    )

    assert recall["selected_event_ids"] == []


def _indexed_measurement(
    *, event_id: str, slot_id: str, entity_id: str, attribute: str,
    query=False,
):
    return {
        "schema_version": "policy-transaction-semantic-state-v1",
        "source_event_ref": event_id,
        "source_kind": "user_text",
        "source_error": False,
        "operations": [] if query else [{
            "operation": "add",
            "slot_id": slot_id,
            "value": {
                "entity_id": entity_id,
                "attribute": attribute,
                "value": "historical-value",
            },
            "source_event_ref": event_id,
            "source_kind": "user_text",
        }],
        "reference_slots": [slot_id],
        "query_references": ([{
            "slot_id": slot_id,
            "match_fields": {
                "entity_id": entity_id,
                "attribute": attribute,
            },
        }] if query else []),
    }


def test_answer_blind_query_reference_matches_historical_record_index_only():
    destination_measurement = _indexed_measurement(
        event_id="event:old:office", slot_id="target.entities",
        entity_id="PX-17", attribute="destination",
    )
    phone_measurement = _indexed_measurement(
        event_id="event:old:weather", slot_id="claims",
        entity_id="PX-17", attribute="delivery_phone_suffix",
    )
    query_measurement = _indexed_measurement(
        event_id="event:query", slot_id="target.entities",
        entity_id="PX-17", attribute="destination", query=True,
    )
    destination_indexes = reference_index_fingerprints_from_measurement(
        destination_measurement)
    phone_indexes = reference_index_fingerprints_from_measurement(phone_measurement)
    query_indexes = query_reference_fingerprints_from_measurement(query_measurement)
    events = [
        {
            **_event("event:old:office", 1, slots=("target.entities",)),
            "version": "policy-transaction-memory-event-v3",
            "reference_claim_fingerprint": None,
            "reference_index_fingerprints": destination_indexes,
        },
        {
            **_event("event:old:weather", 2, slots=("claims",)),
            "version": "policy-transaction-memory-event-v3",
            "reference_claim_fingerprint": None,
            "reference_index_fingerprints": phone_indexes,
        },
    ]
    receipt = _score_receipt(
        ("event:old:office", 0.0), ("event:old:weather", 0.0))

    recall = select_task_history(
        scope=SCOPE,
        query={
            "reference_slots": ["target.entities"],
            "exact_tool_ids": [],
            "reference_claim_fingerprint": None,
            "reference_query_fingerprints": query_indexes,
        },
        candidate_events=events,
        rag_score_receipt=receipt,
        memory_relevance_policy={
            "identity": "symmetric-fusion-v2",
            "minimum_fused_relevance": 0.99,
            "agreement_gain": 0.25,
        },
    )

    assert recall["selected_event_ids"] == ["event:old:office"]
    witness = recall["selection_witnesses"][0]
    assert witness["local_score"] == 1.0
    assert witness["matched_reference_query_fingerprints"] == query_indexes
    assert recall["local_score_rule"] == (
        "answer-blind-record-query-index-or-exact-tool-v2")


def test_reference_vocabulary_compiles_relation_target_and_attribute_alias():
    """AUD-523: relation target + alias must share one answer-blind address."""
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v2",
        "slots": [{
            "slot_id": "claims",
            "attributes": ["delivery_time", "service_arrival_time"],
            "attribute_families": [{
                "identity": "arrival_time",
                "members": ["delivery_time", "service_arrival_time"],
            }],
            "entity_fields": ["entity_id", "target_entity_id"],
        }],
    }
    history = {
        "schema_version": "policy-transaction-semantic-state-v1",
        "source_event_ref": "event:mediated:20",
        "source_kind": "user_text",
        "source_error": False,
        "operations": [{
            "operation": "add",
            "slot_id": "claims",
            "value": {
                "entity_id": "Daybridge-1",
                "target_entity_id": "M-510",
                "attribute": "delivery_time",
                "value": "16:45",
            },
            "source_event_ref": "event:mediated:20",
            "source_kind": "user_text",
        }],
        "reference_slots": ["claims"],
    }
    query = {
        "schema_version": "policy-transaction-semantic-state-v1",
        "source_event_ref": "event:mediated:query",
        "source_kind": "user_text",
        "source_error": False,
        "operations": [],
        "reference_slots": ["claims"],
        "query_references": [{
            "slot_id": "claims",
            "match_fields": {
                "entity_id": "M-510",
                "attribute": "service_arrival_time",
            },
        }],
    }

    history_indexes = reference_index_fingerprints_from_measurement(
        history, reference_query_vocabulary=vocabulary)
    query_indexes = query_reference_fingerprints_from_measurement(
        query, reference_query_vocabulary=vocabulary)

    assert len(history_indexes) == 2
    assert set(history_indexes).intersection(query_indexes) == set(query_indexes)


def test_reference_vocabulary_collapses_distinct_aliases_to_one_read_address():
    """AUD-526: raw alias rows may be distinct while their canonical address is one."""
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
                {"attribute": "delivery_time", "meaning": "arrival time"},
                {"attribute": "service_arrival_time", "meaning": "arrival time"},
            ],
        }],
    }
    query = {
        "schema_version": "policy-transaction-semantic-state-v1",
        "source_event_ref": "event:query",
        "source_kind": "user_text",
        "source_error": False,
        "operations": [],
        "reference_slots": ["claims"],
        "query_references": [
            {
                "slot_id": "claims",
                "match_fields": {
                    "entity_id": "M-510", "attribute": "delivery_time",
                },
            },
            {
                "slot_id": "claims",
                "match_fields": {
                    "entity_id": "M-510", "attribute": "service_arrival_time",
                },
            },
        ],
    }

    assert len(query_reference_fingerprints_from_measurement(
        query, reference_query_vocabulary=vocabulary)) == 1

    query["query_references"][1] = dict(query["query_references"][0])
    with pytest.raises(ValueError, match="cannot repeat"):
        query_reference_fingerprints_from_measurement(
            query, reference_query_vocabulary=vocabulary)


def test_reference_path_qualifies_and_delivers_both_committed_hops():
    """AUD-529: terminal intent resolves through a package-defined relation path."""
    vocabulary = {
        "version": "policy-transaction-reference-query-vocabulary-v4",
        "slots": [{
            "slot_id": "claims",
            "attributes": ["route", "handling_point"],
            "attribute_families": [],
            "entity_fields": ["entity_id", "target_entity_id"],
            "attribute_semantics": [
                {"attribute": "route", "meaning": "route assigned to an order"},
                {"attribute": "handling_point", "meaning": "current route handling point"},
            ],
        }],
        "reference_paths": [{
            "identity": "order-route-handling-point",
            "source_slot_id": "claims",
            "source_attribute": "route",
            "target_slot_id": "claims",
            "target_attribute": "handling_point",
            "link_value_field": "value",
        }],
    }
    route = _indexed_measurement(
        event_id="event:route", slot_id="claims",
        entity_id="R-710", attribute="route")
    route["operations"][0]["value"]["value"] = "Route-A"
    point = _indexed_measurement(
        event_id="event:point", slot_id="claims",
        entity_id="Route-A", attribute="handling_point")
    query = _indexed_measurement(
        event_id="event:query", slot_id="claims",
        entity_id="R-710", attribute="handling_point", query=True)
    events = []
    for ordinal, (event_id, measurement) in enumerate((
        ("event:route", route), ("event:point", point)), start=1,
    ):
        events.append({
            **_event(event_id, ordinal, slots=("claims",)),
            "version": PATH_INDEXED_MEMORY_EVENT_VERSION,
            "reference_claim_fingerprint": None,
            "reference_index_fingerprints": (
                reference_index_fingerprints_from_measurement(
                    measurement, reference_query_vocabulary=vocabulary)),
            "reference_path_edges": reference_path_edges_from_measurement(
                measurement, reference_query_vocabulary=vocabulary),
        })
    scores = build_rag_score_receipt(
        status="available", provider_identity="test:rag",
        model_identity="model:test", index_identity="index:test",
        query_fingerprint=model_data_fingerprint({"q": "point"}),
        candidate_event_ids=["event:route", "event:point"],
        candidate_scores=[
            {"event_id": "event:route", "similarity": 0.0},
            {"event_id": "event:point", "similarity": 0.0},
        ],
        scoring_policy_identity="test:complete-vector",
    )
    recall = select_task_history(
        scope=SCOPE,
        query={
            "reference_slots": ["claims"], "exact_tool_ids": [],
            "reference_claim_fingerprint": None,
            "reference_query_fingerprints": (
                query_reference_fingerprints_from_measurement(
                    query, reference_query_vocabulary=vocabulary)),
        },
        candidate_events=events,
        rag_score_receipt=scores,
        memory_relevance_policy={
            "identity": "symmetric-bounded-fusion-single-qualification-v2",
            "minimum_fused_relevance": 0.99,
            "agreement_gain": 0.25,
        },
    )
    assert recall["selected_event_ids"] == ["event:route", "event:point"]


def test_query_reference_and_rag_dual_evidence_produce_one_selected_event():
    measurement = _indexed_measurement(
        event_id="event:old:office", slot_id="target.entities",
        entity_id="PX-17", attribute="destination",
    )
    query = _indexed_measurement(
        event_id="event:query", slot_id="target.entities",
        entity_id="PX-17", attribute="destination", query=True,
    )
    event = {
        **_event("event:old:office", 1, slots=("target.entities",)),
        "version": "policy-transaction-memory-event-v3",
        "reference_claim_fingerprint": None,
        "reference_index_fingerprints": (
            reference_index_fingerprints_from_measurement(measurement)),
    }
    recall = select_task_history(
        scope=SCOPE,
        query={
            "reference_slots": ["target.entities"],
            "exact_tool_ids": [],
            "reference_claim_fingerprint": None,
            "reference_query_fingerprints": (
                query_reference_fingerprints_from_measurement(query)),
        },
        candidate_events=[
            event,
            {
                **_event("event:old:weather", 2, slots=("claims",)),
                "version": "policy-transaction-memory-event-v3",
                "reference_claim_fingerprint": None,
                "reference_index_fingerprints": [],
            },
        ],
        rag_score_receipt=_score_receipt(
            ("event:old:office", 0.5), ("event:old:weather", 0.0)),
        memory_relevance_policy={
            "identity": "symmetric-fusion-v2",
            "minimum_fused_relevance": 0.99,
            "agreement_gain": 0.25,
        },
    )

    assert recall["selected_event_ids"] == ["event:old:office"]
    assert len(recall["selection_witnesses"]) == 1
    assert recall["selection_witnesses"][0]["fused_score"] == 1.0


def _address(*event_scores, status="available"):
    candidate_ids = ["event:old:office", "event:old:weather"]
    return build_rag_address_receipt(
        status=status,
        provider_identity="provider:qwen3-local-v1",
        model_identity="qwen3-embedding-0.6b-q8-d256",
        index_identity="index:session-rag:v3",
        query_fingerprint="1" * 64,
        candidate_event_ids=candidate_ids,
        addressed_events=[
            {"event_id": event_id, "similarity": score}
            for event_id, score in event_scores
        ],
        selection_policy_identity="cosine-calibration:diagnostic-v1",
    )


def test_rag_address_adds_semantic_exact_id_without_replacing_structured_witness():
    events = [
        _event("event:old:office", 1),
        _event("event:old:weather", 2, slots=("request.status",)),
    ]
    address = _address(("event:old:office", 0.74))

    recall = select_task_history(
        scope=SCOPE,
        query={"reference_slots": ["request.status"], "exact_tool_ids": []},
        candidate_events=events,
        rag_address_receipt=address,
    )

    assert recall["selected_event_ids"] == [
        "event:old:office", "event:old:weather"]
    assert recall["selection_rule"] == (
        "task-local-structured-union-rag-exact-address")
    assert recall["source_rag_address_fingerprint"] == address["fingerprint"]
    witnesses = {item["event_id"]: item for item in recall["selection_witnesses"]}
    assert witnesses["event:old:office"]["rag_address"] == {
        "similarity": 0.74,
        "source_receipt_fingerprint": address["fingerprint"],
    }
    assert witnesses["event:old:office"]["matched_reference_slots"] == []
    assert witnesses["event:old:weather"]["rag_address"] is None
    assert witnesses["event:old:weather"]["matched_reference_slots"] == [
        "request.status"]
    assert recall["state_commit_authority"] is False


def test_rag_score_order_cannot_replace_committed_time_order():
    events = [
        _event("event:old:office", 10),
        _event("event:old:weather", 20),
    ]
    address = _address(
        ("event:old:weather", 0.99),
        ("event:old:office", 0.61),
    )

    recall = select_task_history(
        scope=SCOPE,
        query={"reference_slots": [], "exact_tool_ids": []},
        candidate_events=events,
        rag_address_receipt=address,
    )

    assert recall["selected_event_ids"] == [
        "event:old:office", "event:old:weather"]


def test_rag_address_rejects_unknown_or_out_of_scope_event_ids():
    events = [
        _event("event:old:office", 1),
        _event("event:old:weather", 2),
    ]
    forged = dict(_address(("event:old:office", 0.74)))
    material = {key: value for key, value in forged.items() if key != "fingerprint"}
    material["addressed_events"] = [
        {"event_id": "event:foreign", "similarity": 0.99}]
    forged = {**material, "fingerprint": model_data_fingerprint(material)}

    with pytest.raises(ValueError, match="candidate"):
        select_task_history(
            scope=SCOPE,
            query={"reference_slots": [], "exact_tool_ids": []},
            candidate_events=events,
            rag_address_receipt=forged,
        )


def test_unavailable_rag_preserves_deterministic_selection_and_publishes_witness():
    events = [
        _event("event:old:office", 1),
        _event("event:old:weather", 2, slots=("request.status",)),
    ]
    address = _address(status="unavailable")
    baseline = select_task_history(
        scope=SCOPE,
        query={"reference_slots": ["request.status"], "exact_tool_ids": []},
        candidate_events=events,
    )
    configured = select_task_history(
        scope=SCOPE,
        query={"reference_slots": ["request.status"], "exact_tool_ids": []},
        candidate_events=events,
        rag_address_receipt=address,
    )

    assert configured["selected_event_ids"] == baseline["selected_event_ids"]
    assert configured["source_rag_address_fingerprint"] == address["fingerprint"]
    assert configured["rag_address_status"] == "unavailable"
    assert "body" not in str(configured)


def test_package_recall_stage_accepts_address_receipt_as_opaque_optional_input():
    package = PolicyTransactionV1Package("rag-test")
    events = [
        _event("event:old:office", 1),
        _event("event:old:weather", 2),
    ]
    address = _address(("event:old:office", 0.74))

    result = package.recall_history(
        payload={
            "candidate_event_ids": [item["event_id"] for item in events],
            "scope": SCOPE,
            "query": {"reference_slots": [], "exact_tool_ids": []},
            "candidate_events": events,
            "rag_address_receipt": address,
        },
        context=_context(),
    )

    assert result["recall_decision"]["recalled_event_ids"] == [
        "event:old:office"]
    assert result["rag_address_receipt"] == address
    assert result["memory_recall"]["source_rag_address_fingerprint"] == (
        address["fingerprint"])


def _document(event_id: str, text: str, *, session_id="session:rag",
              task_scope_ref="task:rag"):
    return {
        "event_id": event_id,
        "session_id": session_id,
        "task_scope_ref": task_scope_ref,
        "content_fingerprint": model_data_fingerprint({"text": text}),
        "text": text,
    }


def test_replaceable_embedding_provider_selects_a_score_band_not_fixed_top_n():
    documents = [
        _document("event:phone:new", "新号码是 13900001111。"),
        _document("event:phone:old", "旧号码是 13800002222。"),
        _document("event:address", "收货地址改到海棠路。"),
    ]
    request = build_embedding_address_request(
        scope=SCOPE,
        query_text="我后来更新过的联系电话是什么？",
        candidate_documents=documents,
        query_context={"public_task_scope": "account profile maintenance"},
    )
    calls = []

    def provider(payload):
        calls.append(payload)
        return {
            "status": "available",
            "provider_identity": "provider:qwen3-local-v1",
            "model_identity": "qwen3-embedding-0.6b-q8-d256",
            "index_identity": "index:session-rag:v1",
            "candidate_scores": [
                {"event_id": "event:phone:new", "similarity": 0.801},
                {"event_id": "event:phone:old", "similarity": 0.778},
                {"event_id": "event:address", "similarity": 0.608},
            ],
        }

    receipt = resolve_embedding_address(
        request=request,
        provider_invoke=provider,
        minimum_similarity=0.60,
        maximum_distance_from_best=0.03,
        selection_policy_identity="cosine-qualified-band-v1",
    )

    assert calls == [request]
    assert [row["event_id"] for row in receipt["addressed_events"]] == [
        "event:phone:new", "event:phone:old"]
    assert receipt["selection_policy_identity"] == "cosine-qualified-band-v1"
    assert "13900001111" not in str(receipt)
    assert "13800002222" not in str(receipt)
    assert "海棠路" not in str(receipt)


def test_embedding_address_request_rejects_cross_scope_candidates():
    with pytest.raises(ValueError, match="scope"):
        build_embedding_address_request(
            scope=SCOPE,
            query_text="Find the earlier constraint.",
            candidate_documents=[
                _document(
                    "event:foreign", "Do not take a red-eye flight.",
                    task_scope_ref="task:other",
                )
            ],
            query_context={"public_task_scope": "flight booking"},
        )


@pytest.mark.parametrize("scores", [
    [{"event_id": "event:unknown", "similarity": 0.9}],
    [{"event_id": "event:known", "similarity": 0.9},
     {"event_id": "event:extra", "similarity": 0.8}],
])
def test_embedding_provider_cannot_change_the_closed_candidate_set(scores):
    request = build_embedding_address_request(
        scope=SCOPE,
        query_text="Find the earlier constraint.",
        candidate_documents=[
            _document("event:known", "Do not take a red-eye flight.")],
        query_context={"public_task_scope": "flight booking"},
    )
    with pytest.raises(ValueError, match="candidate"):
        resolve_embedding_address(
            request=request,
            provider_invoke=lambda _payload: {
                "status": "available",
                "provider_identity": "provider:qwen3-local-v1",
                "model_identity": "qwen3-embedding-0.6b-q8-d256",
                "index_identity": "index:session-rag:v1",
                "candidate_scores": scores,
            },
            minimum_similarity=0.60,
            maximum_distance_from_best=0.03,
            selection_policy_identity="cosine-qualified-band-v1",
        )


def test_unavailable_embedding_provider_returns_content_free_address_receipt():
    request = build_embedding_address_request(
        scope=SCOPE,
        query_text="Find the earlier constraint.",
        candidate_documents=[
            _document("event:known", "Do not take a red-eye flight.")],
        query_context={"public_task_scope": "flight booking"},
    )
    receipt = resolve_embedding_address(
        request=request,
        provider_invoke=lambda _payload: {
            "status": "unavailable",
            "provider_identity": "provider:qwen3-local-v1",
            "model_identity": "qwen3-embedding-0.6b-q8-d256",
            "index_identity": "index:unavailable",
            "candidate_scores": [],
        },
        minimum_similarity=0.60,
        maximum_distance_from_best=0.03,
        selection_policy_identity="cosine-qualified-band-v1",
    )
    assert receipt["status"] == "unavailable"
    assert receipt["addressed_events"] == []
    assert "red-eye" not in str(receipt)


def test_rag_recall_publishes_text_free_attention_claims_from_address_strength():
    package = PolicyTransactionV1Package("rag-claims")
    events = [
        _event("event:old:office", 1),
        _event("event:old:weather", 2, slots=("request.status",)),
    ]
    address = _address(("event:old:office", 0.74))
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
    execution = runtime.invoke(
        "recall_history",
        payload={
            "candidate_event_ids": [item["event_id"] for item in events],
            "scope": SCOPE,
            "query": {"reference_slots": ["request.status"], "exact_tool_ids": []},
            "candidate_events": events,
            "rag_address_receipt": address,
        },
        context=context,
    )

    claims = read_memory_content_claims(
        execution,
        expected_context=context,
        recalled_event_ids=("event:old:office", "event:old:weather"),
    )

    assert [claim.event_id for claim in claims.claims] == [
        "event:old:office", "event:old:weather"]
    assert [claim.strength for claim in claims.claims] == [0.74, 1.0]
    assert all(set(row) == {
        "event_id", "strength", "claim_version",
        "source_evidence_fingerprint",
    } for row in execution.payload["memory_content_claims"]["claims"])
