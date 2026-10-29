"""Discrete policy gates and one canonical descriptor per exact tool ID.

The module consumes a public, versioned policy contract.  It does not execute
tools, create arguments, read benchmark outcomes, or grant Commit authority.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data

from .state_space import (
    normalize_slot_value,
    normalize_state_space_definition,
    validate_state,
)


POLICY_CONTRACT_VERSION = "policy-transaction-policy-contract-v1"
POLICY_GATE_VERSION = "policy-transaction-policy-gate-v1"
TOOL_DESCRIPTOR_VERSION = "policy-transaction-tool-descriptor-v1"
POLICY_DECISIONS = frozenset({"allowed", "blocked", "needs_evidence"})
EXPERIENCE_DISPOSITIONS = frozenset({"support", "conflict", "insufficient"})


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


def _identifiers(value: object, field: str) -> list[str]:
    if not isinstance(value, (list, tuple)):
        raise ValueError(f"{field} must be a list")
    result = [_identifier(item, field) for item in value]
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicates")
    return result


def _exact_tool_ids(value: object) -> list[str]:
    return _identifiers(value, "exact_tool_ids")


def _normalize_predicate(
    value: object, *, definition: Mapping[str, Any], field: str,
) -> dict[str, Any]:
    record = _exact(
        value, {"requirement_id", "slot_id", "operator", "value"}, field)
    requirement_id = _identifier(record["requirement_id"], "requirement_id")
    slot_id = _identifier(record["slot_id"], "slot_id")
    operator = _identifier(record["operator"], "predicate operator")
    if operator not in {"present", "equals"}:
        raise ValueError("policy predicate operator is invalid")
    if operator == "present":
        if record["value"] is not None:
            raise ValueError("present predicate requires a null value")
        normalized_value = None
    else:
        if record["value"] is None:
            raise ValueError("equals predicate requires a value")
        normalized_value = normalize_slot_value(
            record["value"], slot_id=slot_id, definition=definition)
    if slot_id not in {item["slot_id"] for item in definition["slots"]}:
        raise ValueError("policy predicate names an unknown slot")
    return {
        "requirement_id": requirement_id,
        "slot_id": slot_id,
        "operator": operator,
        "value": normalized_value,
    }


def _normalize_policy_contract(
    value: object,
    *,
    exact_tool_ids: Sequence[str],
    state_space_definition: object,
) -> dict[str, Any]:
    definition = normalize_state_space_definition(state_space_definition)
    record = _exact(value, {"version", "rules"}, "policy_contract")
    if record["version"] != POLICY_CONTRACT_VERSION:
        raise ValueError("policy contract version is invalid")
    if not isinstance(record["rules"], (list, tuple)):
        raise ValueError("policy rules must be a list")
    rules = []
    seen_tools = set()
    for index, raw in enumerate(record["rules"]):
        item = _exact(
            raw,
            {"tool_id", "source_refs", "requirements", "blockers"},
            f"policy rule {index}",
        )
        tool_id = _identifier(item["tool_id"], "policy tool_id")
        if tool_id in seen_tools:
            raise ValueError("policy rules repeat an exact tool ID")
        seen_tools.add(tool_id)
        source_refs = _identifiers(item["source_refs"], "policy source_refs")
        if not source_refs:
            raise ValueError("policy rule requires at least one source reference")
        normalized_lists = {}
        requirement_ids = set()
        for name in ("requirements", "blockers"):
            if not isinstance(item[name], (list, tuple)):
                raise ValueError(f"policy {name} must be a list")
            values = []
            for predicate_index, predicate in enumerate(item[name]):
                normalized = _normalize_predicate(
                    predicate,
                    definition=definition,
                    field=f"policy rule {index} {name} {predicate_index}",
                )
                if normalized["requirement_id"] in requirement_ids:
                    raise ValueError("policy requirement IDs cannot repeat within a rule")
                requirement_ids.add(normalized["requirement_id"])
                values.append(normalized)
            normalized_lists[name] = values
        rules.append({
            "tool_id": tool_id,
            "source_refs": source_refs,
            **normalized_lists,
        })
    expected = set(exact_tool_ids)
    if seen_tools != expected:
        raise ValueError("policy rules must cover every and only exact tool ID")
    by_tool = {item["tool_id"]: item for item in rules}
    return {
        "version": POLICY_CONTRACT_VERSION,
        "rules": [by_tool[tool_id] for tool_id in exact_tool_ids],
    }


def _predicate_matches(predicate: Mapping[str, Any], state: Mapping[str, Any]) -> bool:
    entries = state["slots"][predicate["slot_id"]]["entries"]
    if predicate["operator"] == "present":
        return bool(entries)
    expected = model_data_fingerprint(predicate["value"])
    return any(model_data_fingerprint(item["value"]) == expected for item in entries)


def evaluate_policy_gates(
    *,
    exact_tool_ids: object,
    state: object,
    state_space_definition: object,
    policy_contract: object,
) -> list[dict[str, Any]]:
    """Return one source-bound discrete gate for every exact tool ID."""
    tools = _exact_tool_ids(exact_tool_ids)
    definition = normalize_state_space_definition(state_space_definition)
    normalized_state = validate_state(state, definition=definition)
    contract = _normalize_policy_contract(
        policy_contract,
        exact_tool_ids=tools,
        state_space_definition=definition,
    )
    gates = []
    for rule in contract["rules"]:
        matched_blockers = [
            item["requirement_id"] for item in rule["blockers"]
            if _predicate_matches(item, normalized_state)
        ]
        missing_requirements = [
            item["requirement_id"] for item in rule["requirements"]
            if not _predicate_matches(item, normalized_state)
        ]
        if matched_blockers:
            decision = "blocked"
        elif missing_requirements:
            decision = "needs_evidence"
        else:
            decision = "allowed"
        gate_material = {
            "version": POLICY_GATE_VERSION,
            "tool_id": rule["tool_id"],
            "decision": decision,
            "missing_requirements": missing_requirements,
            "matched_blockers": matched_blockers,
            "policy_source_refs": rule["source_refs"],
            "state_fingerprint": model_data_fingerprint(normalized_state),
            "policy_contract_fingerprint": model_data_fingerprint(contract),
        }
        gates.append({
            **gate_material,
            "fingerprint": model_data_fingerprint(gate_material),
        })
    return gates


def _normalize_witness(value: object, field: str) -> dict[str, Any]:
    item = _exact(
        value,
        {"tool_id", "disposition", "source_episode_refs", "source_event_refs"},
        field,
    )
    disposition = _identifier(item["disposition"], "experience disposition")
    if disposition not in EXPERIENCE_DISPOSITIONS:
        raise ValueError("experience disposition is invalid")
    return {
        "tool_id": _identifier(item["tool_id"], "experience tool_id"),
        "disposition": disposition,
        "source_episode_refs": _identifiers(
            item["source_episode_refs"], "source_episode_refs"),
        "source_event_refs": _identifiers(
            item["source_event_refs"], "source_event_refs"),
    }


def validate_policy_gate(value: object) -> dict[str, Any]:
    record = _mapping(value, "policy gate")
    if set(record) != {
        "version", "tool_id", "decision", "missing_requirements",
        "matched_blockers", "policy_source_refs", "state_fingerprint",
        "policy_contract_fingerprint", "fingerprint",
    }:
        raise ValueError("policy gate fields are invalid")
    fingerprint = record.pop("fingerprint")
    if fingerprint != model_data_fingerprint(record):
        raise ValueError("policy gate fingerprint is invalid")
    if record["version"] != POLICY_GATE_VERSION:
        raise ValueError("policy gate version is invalid")
    decision = _identifier(record["decision"], "policy gate decision")
    if decision not in POLICY_DECISIONS:
        raise ValueError("policy gate decision is invalid")
    missing = _identifiers(record["missing_requirements"], "missing_requirements")
    blockers = _identifiers(record["matched_blockers"], "matched_blockers")
    if (
        (decision == "allowed" and (missing or blockers))
        or (decision == "needs_evidence" and (not missing or blockers))
        or (decision == "blocked" and not blockers)
    ):
        raise ValueError("policy gate evidence contradicts its decision")
    normalized = {
        **record,
        "tool_id": _identifier(record["tool_id"], "policy gate tool_id"),
        "decision": decision,
        "missing_requirements": missing,
        "matched_blockers": blockers,
        "policy_source_refs": _identifiers(
            record["policy_source_refs"], "policy_source_refs"),
        "state_fingerprint": _sha256(
            record["state_fingerprint"], "state_fingerprint"),
        "policy_contract_fingerprint": _sha256(
            record["policy_contract_fingerprint"], "policy_contract_fingerprint"),
    }
    return {**normalized, "fingerprint": model_data_fingerprint(normalized)}


def validate_tool_descriptor(value: object) -> dict[str, Any]:
    record = _mapping(value, "tool descriptor")
    if set(record) != {
        "version", "tool_id", "decision", "recommendation",
        "missing_requirements", "matched_blockers", "policy_source_refs",
        "policy_gate_fingerprint", "experience_witness",
        "text_response_required", "auto_execute", "action_commit_authority",
        "fingerprint",
    }:
        raise ValueError("tool descriptor fields are invalid")
    fingerprint = record.pop("fingerprint")
    if fingerprint != model_data_fingerprint(record):
        raise ValueError("tool descriptor fingerprint is invalid")
    if record["version"] != TOOL_DESCRIPTOR_VERSION:
        raise ValueError("tool descriptor version is invalid")
    decision = _identifier(record["decision"], "tool descriptor decision")
    if decision not in POLICY_DECISIONS:
        raise ValueError("tool descriptor decision is invalid")
    recommendation = _identifier(record["recommendation"], "recommendation")
    allowed_recommendations = {
        "blocked": {"do_not_use"},
        "needs_evidence": {"obtain_evidence"},
        "allowed": {"eligible", "preferred", "eligible_with_conflict"},
    }
    if recommendation not in allowed_recommendations[decision]:
        raise ValueError("tool descriptor recommendation contradicts its gate")
    witness = record["experience_witness"]
    if witness is not None:
        witness = _normalize_witness(witness, "experience_witness")
        if witness["tool_id"] != record["tool_id"]:
            raise ValueError("experience witness names a different tool")
    if (
        record["text_response_required"] is not True
        or record["auto_execute"] is not False
        or record["action_commit_authority"] is not False
    ):
        raise ValueError("tool descriptor authority flags are invalid")
    normalized = {
        **record,
        "tool_id": _identifier(record["tool_id"], "tool descriptor tool_id"),
        "decision": decision,
        "recommendation": recommendation,
        "missing_requirements": _identifiers(
            record["missing_requirements"], "missing_requirements"),
        "matched_blockers": _identifiers(
            record["matched_blockers"], "matched_blockers"),
        "policy_source_refs": _identifiers(
            record["policy_source_refs"], "policy_source_refs"),
        "policy_gate_fingerprint": _sha256(
            record["policy_gate_fingerprint"], "policy_gate_fingerprint"),
        "experience_witness": witness,
    }
    return {**normalized, "fingerprint": model_data_fingerprint(normalized)}


def build_canonical_tool_descriptors(
    *, exact_tool_ids: object, gates: object, experience_witnesses: object,
) -> list[dict[str, Any]]:
    """Join gate and optional experience into exactly one descriptor per tool."""
    tools = _exact_tool_ids(exact_tool_ids)
    if not isinstance(gates, (list, tuple)):
        raise ValueError("gates must be a list")
    by_tool = {}
    for index, raw in enumerate(gates):
        gate = validate_policy_gate(raw)
        tool_id = _identifier(gate.get("tool_id"), "gate tool_id")
        if tool_id in by_tool:
            raise ValueError("gates repeat an exact tool ID")
        if gate.get("decision") not in POLICY_DECISIONS:
            raise ValueError("gate decision is invalid")
        by_tool[tool_id] = gate
    if set(by_tool) != set(tools):
        raise ValueError("gates must cover every and only exact tool ID")
    if not isinstance(experience_witnesses, (list, tuple)):
        raise ValueError("experience_witnesses must be a list")
    witnesses = {}
    for index, raw in enumerate(experience_witnesses):
        witness = _normalize_witness(raw, f"experience witness {index}")
        tool_id = witness["tool_id"]
        if tool_id not in set(tools):
            raise ValueError("experience witness names an unknown exact tool ID")
        if tool_id in witnesses:
            raise ValueError("experience witnesses repeat an exact tool ID")
        witnesses[tool_id] = witness
    descriptors = []
    for tool_id in tools:
        gate = by_tool[tool_id]
        witness = witnesses.get(tool_id)
        decision = gate["decision"]
        if decision == "blocked":
            recommendation = "do_not_use"
        elif decision == "needs_evidence":
            recommendation = "obtain_evidence"
        elif witness and witness["disposition"] == "support":
            recommendation = "preferred"
        elif witness and witness["disposition"] == "conflict":
            recommendation = "eligible_with_conflict"
        else:
            recommendation = "eligible"
        material = {
            "version": TOOL_DESCRIPTOR_VERSION,
            "tool_id": tool_id,
            "decision": decision,
            "recommendation": recommendation,
            "missing_requirements": list(gate.get("missing_requirements", [])),
            "matched_blockers": list(gate.get("matched_blockers", [])),
            "policy_source_refs": list(gate.get("policy_source_refs", [])),
            "policy_gate_fingerprint": _identifier(
                gate.get("fingerprint"), "policy gate fingerprint"),
            "experience_witness": witness,
            "text_response_required": True,
            "auto_execute": False,
            "action_commit_authority": False,
        }
        descriptors.append({
            **material,
            "fingerprint": model_data_fingerprint(material),
        })
    return descriptors


__all__ = (
    "EXPERIENCE_DISPOSITIONS",
    "POLICY_CONTRACT_VERSION",
    "POLICY_DECISIONS",
    "POLICY_GATE_VERSION",
    "TOOL_DESCRIPTOR_VERSION",
    "build_canonical_tool_descriptors",
    "evaluate_policy_gates",
    "validate_policy_gate",
    "validate_tool_descriptor",
)
