"""Policy-constrained transaction Interaction Model for E1/E2.

The package models public task progress rather than the six/seven reference
model's relationship semantics.  It has no benchmark evaluator, hidden task,
provider, persistence, commit, tool-execution, or send authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import (
    MODEL_API_VERSION_V2,
    InteractionModelManifest,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from .memory import (
    aggregate_experience_witnesses as aggregate_memory_witnesses,
    read_historical_influence as read_memory_influence,
    select_task_history,
    validate_experience_aggregate,
    validate_memory_recall,
)
from .memory_content_claims import build_memory_content_claims
from .policy_gate import (
    build_canonical_tool_descriptors,
    evaluate_policy_gates,
    validate_policy_gate,
    validate_tool_descriptor,
)
from .state_space import (
    DEFAULT_STATE_SPACE_DEFINITION,
    STATE_SPACE_VERSION,
    apply_event_patch,
    empty_state,
    normalize_state_space_definition,
    validate_event_patch,
    validate_state,
)


PACKAGE_ID = "tide.policy-transaction"
PACKAGE_VERSION = "1.4.0"
CAPABILITIES = (
    "initialize_parameters",
    "initialize_state",
    "measure_event",
    "derive_event_contribution",
    "settle_state",
    "project_shared_semantics",
    "assess_action",
    "advance_progress",
    "recall_history",
    "read_historical_influence",
    "aggregate_historical_influence",
    "project_semantic_resources",
)


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


def _sha256(value: object, field: str) -> str:
    result = _identifier(value, field)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return result


def _context(value: object) -> ModelInvocationContextV2:
    if not isinstance(value, ModelInvocationContextV2):
        raise ValueError("context must be ModelInvocationContextV2")
    return value


def _fingerprinted(material: Mapping[str, Any]) -> dict[str, Any]:
    result = _mapping(material, "fingerprinted material")
    return {**result, "fingerprint": model_data_fingerprint(result)}


def _verify_fingerprinted(value: object, field: str) -> dict[str, Any]:
    result = _mapping(value, field)
    fingerprint = result.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(result):
        raise ValueError(f"{field} fingerprint is invalid")
    return {**result, "fingerprint": fingerprint}


def _validated_parameters(value: object, *, package: "PolicyTransactionV1Package") -> dict[str, Any]:
    record = _verify_fingerprinted(value, "parameters")
    material = _exact(
        {key: item for key, item in record.items() if key != "fingerprint"},
        {
            "version", "profile_id", "policy_contract_digest",
            "tool_contract_digest", "exact_tool_ids", "state_space_fingerprint",
        },
        "parameters material",
    )
    if material["version"] != "policy-transaction-session-parameters-v2":
        raise ValueError("parameters version is invalid")
    if material["profile_id"] != package.profile_id:
        raise ValueError("parameters profile differs from package profile")
    if material["state_space_fingerprint"] != package.state_space_fingerprint:
        raise ValueError("parameters bind a different state-space definition")
    tools = material["exact_tool_ids"]
    if not isinstance(tools, list):
        raise ValueError("parameters exact_tool_ids must be a list")
    normalized_tools = [_identifier(item, "exact tool ID") for item in tools]
    if len(normalized_tools) != len(set(normalized_tools)):
        raise ValueError("parameters exact_tool_ids cannot contain duplicates")
    normalized = {
        **material,
        "policy_contract_digest": _sha256(
            material["policy_contract_digest"], "policy contract digest"),
        "tool_contract_digest": _sha256(
            material["tool_contract_digest"], "tool contract digest"),
        "exact_tool_ids": normalized_tools,
    }
    return {**normalized, "fingerprint": model_data_fingerprint(normalized)}


def _validated_contribution(
    value: object,
    *,
    field: str,
    package: "PolicyTransactionV1Package",
) -> dict[str, Any]:
    record = _verify_fingerprinted(value, field)
    material = {key: item for key, item in record.items() if key != "fingerprint"}
    if field == "event_contribution":
        material = _exact(
            material,
            {
                "version", "state_before", "state_candidate", "source_event_ref",
                "reference_slots", "operation_count",
            },
            "event contribution material",
        )
        if material["version"] != "policy-transaction-event-contribution-v2":
            raise ValueError("event contribution version is invalid")
        _identifier(material["source_event_ref"], "source_event_ref")
        if not isinstance(material["reference_slots"], list):
            raise ValueError("event contribution reference_slots must be a list")
        if (
            isinstance(material["operation_count"], bool)
            or not isinstance(material["operation_count"], int)
            or material["operation_count"] < 0
        ):
            raise ValueError("event contribution operation_count is invalid")
    elif field == "progress_contribution":
        material = _exact(
            material,
            {
                "version", "state_before", "state_candidate",
                "source_progress_fingerprint", "invented_domain_change",
            },
            "progress contribution material",
        )
        if material["version"] != "policy-transaction-progress-contribution-v1":
            raise ValueError("progress contribution version is invalid")
        _sha256(material["source_progress_fingerprint"], "source progress fingerprint")
        if material["invented_domain_change"] is not False:
            raise ValueError("progress contribution cannot invent domain change")
    else:
        raise ValueError("unknown contribution field")
    validate_state(material["state_before"], definition=package.state_space_definition)
    validate_state(material["state_candidate"], definition=package.state_space_definition)
    return record


def _validated_settlement(
    value: object, *, package: "PolicyTransactionV1Package",
) -> dict[str, Any]:
    record = _verify_fingerprinted(value, "settlement")
    material = _exact(
        {key: item for key, item in record.items() if key != "fingerprint"},
        {
            "version", "state_before", "state_candidate", "applied_contributions",
            "contribution_fingerprints", "state_commit_authority",
        },
        "settlement material",
    )
    if material["version"] != "policy-transaction-settlement-v2":
        raise ValueError("settlement version is invalid")
    if material["state_commit_authority"] is not False:
        raise ValueError("settlement cannot have state Commit authority")
    applied = material["applied_contributions"]
    if not isinstance(applied, list) or not applied:
        raise ValueError("settlement applied_contributions must be non-empty")
    if len(applied) != len(set(applied)) or not set(applied).issubset({
        "progress_contribution", "event_contribution",
    }):
        raise ValueError("settlement applied contributions are invalid")
    fingerprints = _mapping(
        material["contribution_fingerprints"], "contribution_fingerprints")
    if set(fingerprints) != set(applied):
        raise ValueError("settlement contribution fingerprints are incomplete")
    for name, fingerprint in fingerprints.items():
        _sha256(fingerprint, f"{name} fingerprint")
    validate_state(
        material["state_before"], definition=package.state_space_definition)
    validate_state(
        material["state_candidate"], definition=package.state_space_definition)
    return record


def _validated_shared_projection(
    value: object, *, package: "PolicyTransactionV1Package",
) -> dict[str, Any]:
    record = _verify_fingerprinted(value, "shared_projection")
    material = _exact(
        {key: item for key, item in record.items() if key != "fingerprint"},
        {
            "version", "state", "policy_gates", "canonical_tool_descriptors",
            "text_response_required", "source_settlement_fingerprint",
            "source_parameters_fingerprint",
        },
        "shared projection material",
    )
    if material["version"] != "policy-transaction-shared-projection-v2":
        raise ValueError("shared projection version is invalid")
    if material["text_response_required"] is not True:
        raise ValueError("shared projection must retain the text response obligation")
    state = validate_state(
        material["state"], definition=package.state_space_definition)
    if not isinstance(material["policy_gates"], list):
        raise ValueError("policy_gates must be a list")
    if not isinstance(material["canonical_tool_descriptors"], list):
        raise ValueError("canonical_tool_descriptors must be a list")
    gates = [validate_policy_gate(item) for item in material["policy_gates"]]
    descriptors = [
        validate_tool_descriptor(item)
        for item in material["canonical_tool_descriptors"]
    ]
    gate_by_tool = {item["tool_id"]: item for item in gates}
    descriptor_by_tool = {item["tool_id"]: item for item in descriptors}
    if len(gate_by_tool) != len(gates) or len(descriptor_by_tool) != len(descriptors):
        raise ValueError("shared projection repeats an exact tool ID")
    if set(gate_by_tool) != set(descriptor_by_tool):
        raise ValueError("shared projection gate and descriptor tools differ")
    for tool_id, descriptor in descriptor_by_tool.items():
        gate = gate_by_tool[tool_id]
        if (
            descriptor["decision"] != gate["decision"]
            or descriptor["policy_gate_fingerprint"] != gate["fingerprint"]
            or descriptor["missing_requirements"] != gate["missing_requirements"]
            or descriptor["matched_blockers"] != gate["matched_blockers"]
            or descriptor["policy_source_refs"] != gate["policy_source_refs"]
        ):
            raise ValueError("tool descriptor differs from its policy gate")
    normalized = {
        **material,
        "state": state,
        "policy_gates": gates,
        "canonical_tool_descriptors": descriptors,
        "source_settlement_fingerprint": _sha256(
            material["source_settlement_fingerprint"],
            "source settlement fingerprint",
        ),
        "source_parameters_fingerprint": _sha256(
            material["source_parameters_fingerprint"],
            "source parameters fingerprint",
        ),
    }
    return {**normalized, "fingerprint": model_data_fingerprint(normalized)}


def _operator_versions() -> dict[str, str]:
    revisions = {
        "initialize_parameters": 2,
        "initialize_state": 1,
        "measure_event": 2,
        "derive_event_contribution": 2,
        "settle_state": 2,
        "project_shared_semantics": 2,
        "assess_action": 2,
        "advance_progress": 1,
        "recall_history": 4,
        "read_historical_influence": 2,
        "aggregate_historical_influence": 2,
        "project_semantic_resources": 2,
    }
    return {
        name: f"policy-transaction-{name.replace('_', '-')}-v{revisions[name]}"
        for name in CAPABILITIES
    }


class PolicyTransactionV1Package:
    """Benchmark-neutral policy/transaction semantic-state package."""

    def __init__(
        self,
        profile_id: str = "policy-transaction",
        *,
        state_space_definition: object = DEFAULT_STATE_SPACE_DEFINITION,
        measurement_execution_mode: str = "provider",
    ) -> None:
        self.profile_id = _identifier(profile_id, "profile_id")
        self.state_space_definition = normalize_state_space_definition(
            state_space_definition)
        self.state_space_fingerprint = model_data_fingerprint(
            self.state_space_definition)
        if measurement_execution_mode not in {
            "provider", "deterministic_local",
        }:
            raise ValueError("measurement execution mode is invalid")
        self.measurement_execution_mode = measurement_execution_mode
        self.manifest = InteractionModelManifest.create(
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            api_version=MODEL_API_VERSION_V2,
            capability_profile="policy-transaction-full-v2",
            capabilities=CAPABILITIES,
            parameters_schema={
                "type": "object",
                "fields": [
                    "profile_id", "policy_contract_digest",
                    "tool_contract_digest", "exact_tool_ids",
                ],
            },
            state_schema={
                "type": "finite-semantic-state",
                "schema_version": STATE_SPACE_VERSION,
                "definition_fingerprint": self.state_space_fingerprint,
                "definition": self.state_space_definition,
            },
            measurement_schema={
                "type": "sparse-event-patch",
                "schema_version": STATE_SPACE_VERSION,
                "definition_fingerprint": self.state_space_fingerprint,
                "execution_mode": self.measurement_execution_mode,
                "supported_execution_modes": [
                    "provider", "deterministic_local",
                ],
            },
            shared_projection_schema={
                "type": "policy_transaction_projection",
                "fields": [
                    "state", "policy_gates", "canonical_tool_descriptors",
                    "text_response_required",
                ],
            },
            progress_delta_schema={
                "type": "bound-temporal-domain-evidence",
                "semantics": "progress is recorded but does not invent domain change",
            },
            parameter_package={
                "version": "policy-transaction-parameter-contract-v2",
                "profile_id": self.profile_id,
                "allowed_design_sources": [
                    "public_domain_policy", "public_tool_schema",
                    "public_observation", "public_tool_result",
                    "committed_history", "verified_temporal_evidence",
                ],
                "forbidden_design_sources": [
                    "held_out_answer", "evaluator_state", "reward",
                    "expected_tool_sequence", "task_id_rule",
                ],
            },
            operator_versions=_operator_versions(),
        )

    def initialize_parameters(self, *, payload, context):
        _context(context)
        record = _exact(
            payload,
            {
                "profile_id", "policy_contract_digest",
                "tool_contract_digest", "exact_tool_ids",
            },
            "initialize_parameters",
        )
        if record["profile_id"] != self.profile_id:
            raise ValueError("parameter profile differs from package profile")
        if not isinstance(record["exact_tool_ids"], (list, tuple)):
            raise ValueError("exact_tool_ids must be a list")
        exact_tool_ids = [
            _identifier(item, "exact tool ID") for item in record["exact_tool_ids"]]
        if len(exact_tool_ids) != len(set(exact_tool_ids)):
            raise ValueError("exact_tool_ids cannot contain duplicates")
        material = {
            "version": "policy-transaction-session-parameters-v2",
            "profile_id": self.profile_id,
            "policy_contract_digest": _sha256(
                record["policy_contract_digest"], "policy contract digest"),
            "tool_contract_digest": _sha256(
                record["tool_contract_digest"], "tool contract digest"),
            "exact_tool_ids": exact_tool_ids,
            "state_space_fingerprint": self.state_space_fingerprint,
        }
        return {"parameters": _fingerprinted(material)}

    def initialize_state(self, *, payload, context):
        _context(context)
        record = _exact(payload, {"initial_state"}, "initialize_state")
        if record["initial_state"] is None:
            return {"state": empty_state(self.state_space_definition)}
        return {"state": validate_state(
            record["initial_state"], definition=self.state_space_definition)}

    def measure_event(self, *, payload, context):
        invocation = _context(context)
        if invocation.event_ref is None:
            raise ValueError("measure_event requires an event reference")
        record = _exact(payload, {"event_patch"}, "measure_event")
        measurement = validate_event_patch(
            record["event_patch"], definition=self.state_space_definition)
        if measurement["source_event_ref"] != invocation.event_ref:
            raise ValueError("event patch source differs from invocation event")
        return {"measurement": _fingerprinted(measurement)}

    def derive_event_contribution(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "derive_event_contribution")
        allowed = {"parameters", "state", "measurement"}
        if "progress_contribution" in record:
            allowed.add("progress_contribution")
        if set(record) != allowed:
            raise ValueError("derive_event_contribution fields are invalid")
        _validated_parameters(record["parameters"], package=self)
        if "progress_contribution" in record:
            _validated_contribution(
                record["progress_contribution"],
                field="progress_contribution", package=self,
            )
        before = validate_state(
            record["state"], definition=self.state_space_definition)
        sealed_measurement = _verify_fingerprinted(
            record["measurement"], "measurement")
        measurement = validate_event_patch(
            {
                key: item for key, item in sealed_measurement.items()
                if key != "fingerprint"
            },
            definition=self.state_space_definition,
        )
        candidate = apply_event_patch(
            before, measurement, definition=self.state_space_definition)
        return {"event_contribution": _fingerprinted({
            "version": "policy-transaction-event-contribution-v2",
            "state_before": before,
            "state_candidate": candidate,
            "source_event_ref": measurement["source_event_ref"],
            "reference_slots": measurement["reference_slots"],
            "operation_count": len(measurement["operations"]),
        })}

    def advance_progress(self, *, payload, context):
        _context(context)
        record = _exact(
            payload, {"parameters", "state", "progress_delta"},
            "advance_progress")
        _validated_parameters(record["parameters"], package=self)
        before = validate_state(
            record["state"], definition=self.state_space_definition)
        progress = _mapping(record["progress_delta"], "progress_delta")
        return {"progress_contribution": _fingerprinted({
            "version": "policy-transaction-progress-contribution-v1",
            "state_before": before,
            "state_candidate": before,
            "source_progress_fingerprint": model_data_fingerprint(progress),
            "invented_domain_change": False,
        })}

    def settle_state(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "settle_state")
        if set(record) != {
            "state", "progress_contribution", "event_contribution",
        }:
            raise ValueError("settle_state fields are invalid")
        before = validate_state(
            record["state"], definition=self.state_space_definition)
        current = before
        applied = []
        fingerprints = {}
        for name in ("progress_contribution", "event_contribution"):
            if name not in record or record[name] is None:
                continue
            contribution = _validated_contribution(
                record[name], field=name, package=self)
            source_before = validate_state(
                contribution["state_before"],
                definition=self.state_space_definition,
            )
            if source_before != current:
                raise ValueError(f"{name} does not continue current state")
            current = validate_state(
                contribution["state_candidate"],
                definition=self.state_space_definition,
            )
            applied.append(name)
            fingerprints[name] = contribution["fingerprint"]
        if not applied:
            raise ValueError("settlement requires at least one contribution")
        return {"settlement": _fingerprinted({
            "version": "policy-transaction-settlement-v2",
            "state_before": before,
            "state_candidate": current,
            "applied_contributions": applied,
            "contribution_fingerprints": fingerprints,
            "state_commit_authority": False,
        })}

    def project_shared_semantics(self, *, payload, context):
        _context(context)
        record = _exact(
            payload,
            {
                "parameters", "settlement", "policy_contract",
                "experience_aggregate",
            },
            "project_shared_semantics",
        )
        parameters = _validated_parameters(record["parameters"], package=self)
        if parameters.get("policy_contract_digest") != model_data_fingerprint(
            _plain(record["policy_contract"], "policy_contract")
        ):
            raise ValueError("policy contract differs from frozen parameters")
        settlement = _validated_settlement(record["settlement"], package=self)
        state = validate_state(
            settlement["state_candidate"], definition=self.state_space_definition)
        exact_tool_ids = parameters["exact_tool_ids"]
        gates = evaluate_policy_gates(
            exact_tool_ids=exact_tool_ids,
            state=state,
            state_space_definition=self.state_space_definition,
            policy_contract=record["policy_contract"],
        )
        aggregate = record["experience_aggregate"]
        if aggregate is None:
            experience_witnesses = []
        else:
            aggregate = validate_experience_aggregate(aggregate)
            invocation = _context(context)
            if (
                aggregate.get("session_id") != invocation.session_id
                or aggregate.get("task_scope_ref") != invocation.scope_ref
            ):
                raise ValueError("experience aggregate escapes invocation scope")
            if aggregate.get("forward_only") is not True:
                raise ValueError("experience aggregate must be forward only")
            if aggregate.get("state_commit_authority") is not False:
                raise ValueError("experience aggregate cannot have state authority")
            if aggregate.get("exact_tool_ids") != exact_tool_ids:
                raise ValueError("experience aggregate exact tools differ")
            experience_witnesses = aggregate.get("experience_witnesses")
        descriptors = build_canonical_tool_descriptors(
            exact_tool_ids=exact_tool_ids,
            gates=gates,
            experience_witnesses=experience_witnesses,
        )
        return {"shared_projection": _fingerprinted({
            "version": "policy-transaction-shared-projection-v2",
            "state": state,
            "policy_gates": gates,
            "canonical_tool_descriptors": descriptors,
            "text_response_required": True,
            "source_settlement_fingerprint": settlement["fingerprint"],
            "source_parameters_fingerprint": parameters["fingerprint"],
        })}

    def assess_action(self, *, payload, context):
        _context(context)
        record = _exact(payload, {"shared_projection"}, "assess_action")
        shared = _validated_shared_projection(
            record["shared_projection"], package=self)
        descriptors = shared.get("canonical_tool_descriptors")
        if not isinstance(descriptors, list):
            raise ValueError("shared projection omits canonical tool descriptors")
        return {"action_assessment": {
            "version": "policy-transaction-action-assessment-v2",
            "source_shared_projection_fingerprint": model_data_fingerprint({
                "shared_projection": shared,
            }),
            "action_kind": "policy_transaction_response",
            "eligible": True,
            "constraints": {
                "canonical_tool_descriptors": descriptors,
                "tool_execution_authority": False,
                "automatic_tool_selection": False,
            },
            "observation_advice": {
                "text_response_required": True,
                "autonomous_send_authority": False,
            },
            "action_commit_authority": False,
        }}

    def recall_history(self, *, payload, context):
        invocation = _context(context)
        record = _mapping(payload, "recall_history")
        base_fields = {
            "candidate_event_ids", "scope", "query", "candidate_events",
        }
        if set(record) not in {
            frozenset(base_fields),
            frozenset({*base_fields, "rag_address_receipt"}),
            frozenset({
                *base_fields, "rag_score_receipt", "memory_relevance_policy",
            }),
        }:
            raise ValueError("recall_history fields are invalid")
        scope = _mapping(record["scope"], "memory scope")
        if (
            scope.get("session_id") != invocation.session_id
            or scope.get("task_scope_ref") != invocation.scope_ref
        ):
            raise ValueError("memory scope differs from invocation scope")
        if not isinstance(record["candidate_event_ids"], (list, tuple)):
            raise ValueError("candidate_event_ids must be a list")
        candidate_event_ids = [
            _identifier(item, "candidate event ID")
            for item in record["candidate_event_ids"]
        ]
        if len(candidate_event_ids) != len(set(candidate_event_ids)):
            raise ValueError("candidate_event_ids cannot contain duplicates")
        candidate_events = record["candidate_events"]
        if not isinstance(candidate_events, (list, tuple)):
            raise ValueError("candidate_events must be a list")
        published_event_ids = [
            _identifier(
                _mapping(item, "candidate event").get("event_id"),
                "candidate event ID",
            )
            for item in candidate_events
        ]
        if (
            len(published_event_ids) != len(candidate_event_ids)
            or set(published_event_ids) != set(candidate_event_ids)
        ):
            raise ValueError("candidate event records differ from candidate IDs")
        memory_recall = select_task_history(
            scope=scope,
            query=record["query"],
            candidate_events=candidate_events,
            rag_address_receipt=record.get("rag_address_receipt"),
            rag_score_receipt=record.get("rag_score_receipt"),
            memory_relevance_policy=record.get("memory_relevance_policy"),
        )
        witness_by_event = {
            item["event_id"]: item
            for item in memory_recall["selection_witnesses"]
        }
        selected = set(memory_recall["selected_event_ids"])
        candidate_evidence = {}
        for event_id in candidate_event_ids:
            if event_id in selected:
                candidate_evidence[event_id] = {
                    "selected": True,
                    "selection_witness": witness_by_event[event_id],
                }
            else:
                candidate_evidence[event_id] = {
                    "selected": False,
                    "reason": "no_structured_reference_match",
                }
        result = {
            "recall_decision": {
                "version": (
                    "policy-transaction-recall-decision-v4"
                    if "rag_score_receipt" in record
                    else "policy-transaction-recall-decision-v3"
                    if "rag_address_receipt" in record
                    else "policy-transaction-recall-decision-v2"),
                "candidate_event_ids": candidate_event_ids,
                "recalled_event_ids": memory_recall["selected_event_ids"],
                "candidate_evidence": candidate_evidence,
            },
            "memory_recall": memory_recall,
            "memory_content_claims": build_memory_content_claims(
                recalled_event_ids=memory_recall["selected_event_ids"],
                candidate_evidence=candidate_evidence,
            ),
        }
        if "rag_address_receipt" in record:
            result["rag_address_receipt"] = record["rag_address_receipt"]
        if "rag_score_receipt" in record:
            result["rag_score_receipt"] = record["rag_score_receipt"]
        return result

    def read_historical_influence(self, *, payload, context):
        invocation = _context(context)
        record = _exact(
            payload,
            {
                "parameters", "memory_recall", "recalled_event_ids",
                "historical_events",
            },
            "read_historical_influence")
        parameters = _validated_parameters(record["parameters"], package=self)
        memory_recall = validate_memory_recall(record["memory_recall"])
        if not isinstance(record["recalled_event_ids"], (list, tuple)):
            raise ValueError("recalled_event_ids must be a list")
        recalled_event_ids = [
            _identifier(item, "recalled event ID")
            for item in record["recalled_event_ids"]
        ]
        if recalled_event_ids != memory_recall["selected_event_ids"]:
            raise ValueError("recalled event IDs differ from memory recall")
        experience_influence = read_memory_influence(
            memory_recall=record["memory_recall"],
            historical_events=record["historical_events"],
            exact_tool_ids=parameters["exact_tool_ids"],
        )
        experience_by_event = {
            item["event_id"]: item
            for item in experience_influence["event_influences"]
        }
        witness_by_event = {
            item["event_id"]: item
            for item in memory_recall["selection_witnesses"]
        }
        event_influences = {}
        later_edges = []
        for event_id in recalled_event_ids:
            experience = experience_by_event.get(event_id)
            event_influences[event_id] = {
                "version": "policy-transaction-event-influence-v1",
                "selection_witness": witness_by_event[event_id],
                "experience_eligible": experience is not None,
                "experience": experience,
                "state_commit_authority": False,
            }
            later_edges.append({
                "parent_event_id": event_id,
                "child_event_id": invocation.event_ref,
                "edge_evidence": {
                    "version": "policy-transaction-forward-edge-v1",
                    "experience_eligible": experience is not None,
                    "state_commit_authority": False,
                },
            })
        return {
            "historical_influence": {
                "version": "policy-transaction-framework-influence-v2",
                "recalled_event_ids": recalled_event_ids,
                "event_influences": event_influences,
                "later_edges": later_edges,
            },
            "experience_influence": experience_influence,
        }

    def aggregate_historical_influence(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "aggregate_historical_influence")
        if set(record) not in (
            {
                "recalled_event_ids", "event_influences",
                "source_influence_output_fingerprint", "experience_influence",
            },
            {
                "recalled_event_ids", "event_influences",
                "source_influence_output_fingerprint", "experience_influence",
                "prior_experience_aggregate",
            },
        ):
            raise ValueError("aggregate_historical_influence fields are invalid")
        if not isinstance(record["recalled_event_ids"], (list, tuple)):
            raise ValueError("recalled_event_ids must be a list")
        recalled_event_ids = [
            _identifier(item, "recalled event ID")
            for item in record["recalled_event_ids"]
        ]
        if len(recalled_event_ids) != len(set(recalled_event_ids)):
            raise ValueError("recalled_event_ids cannot contain duplicates")
        event_influences = _mapping(
            record["event_influences"], "event_influences")
        if set(event_influences) != set(recalled_event_ids):
            raise ValueError("event influences differ from recalled event IDs")
        for event_id, influence in event_influences.items():
            _mapping(influence, f"event influence {event_id}")
        source_fingerprint = _sha256(
            record["source_influence_output_fingerprint"],
            "source influence output fingerprint",
        )
        experience_aggregate = aggregate_memory_witnesses(
            historical_influence=record["experience_influence"],
            prior_aggregate=record.get("prior_experience_aggregate"),
        )
        return {"historical_aggregate": {
            "version": "policy-transaction-framework-aggregate-v2",
            "recalled_event_ids": recalled_event_ids,
            "source_influence_output_fingerprint": source_fingerprint,
            "aggregate": experience_aggregate,
        }}

    def project_semantic_resources(self, *, payload, context):
        _context(context)
        record = _exact(payload, {"shared_projection"}, "project_semantic_resources")
        shared = _validated_shared_projection(
            record["shared_projection"], package=self)
        return {"expression_resource_batch": {
            "version": "policy-transaction-realizer-control-v3",
            "control": {
                "canonical_tool_descriptors": shared[
                    "canonical_tool_descriptors"],
                "text_response_required": True,
            },
            "source_shared_projection_fingerprint": shared["fingerprint"],
            "state_commit_authority": False,
        }}


__all__ = (
    "CAPABILITIES", "PACKAGE_ID", "PACKAGE_VERSION", "PolicyTransactionV1Package",
)
