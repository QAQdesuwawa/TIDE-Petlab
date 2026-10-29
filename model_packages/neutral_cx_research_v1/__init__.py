"""Independent neutral C-X package family for the paper E3 ladder.

The family is intentionally small and domain neutral.  Its three levels share
one parameter/state/measurement vocabulary and differ by declared computation
capabilities, not by prompt text or framework changes.
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
import math
from typing import Any

from framework.model_api import (
    BASE_MODEL_CAPABILITIES_V2,
    MODEL_API_VERSION_V2,
    InteractionModelBinding,
    InteractionModelManifest,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2


PACKAGE_ID = "tide.neutral-cx-research"
LEVELS = ("P0", "P1", "P2")
PACKAGE_VERSION_BY_LEVEL = {
    "P0": "1.0.2-p0",
    "P1": "1.0.2-p1",
    "P2": "1.0.2-p2",
}
PAPER_CAPABILITIES_BY_LEVEL = {
    "P0": (
        "neutral_cx_schema",
        "basic_event_action_mapping",
    ),
    "P1": (
        "neutral_cx_schema",
        "basic_event_action_mapping",
        "explicit_c_to_x_transition",
        "key_constraints",
    ),
    "P2": (
        "neutral_cx_schema",
        "basic_event_action_mapping",
        "explicit_c_to_x_transition",
        "key_constraints",
        "time_dynamics",
        "forward_historical_influence",
        "shared_operational_semantic_projection",
    ),
}

PARAMETER_NAMES = (
    "response_gain",
    "retention",
    "action_boundary",
    "history_gain",
)
STATE_NAMES = ("activation", "stability")
MEASUREMENT_NAMES = ("positive_signal", "negative_signal")
RESOURCE_NAMES = ("advance", "hold", "clarify")

_P1_CAPABILITIES = (*BASE_MODEL_CAPABILITIES_V2, "plan_observation")
_P2_CAPABILITIES = (
    *_P1_CAPABILITIES,
    "advance_progress",
    "read_historical_influence",
    "aggregate_historical_influence",
    "project_semantic_resources",
)
CAPABILITIES_BY_LEVEL = {
    "P0": BASE_MODEL_CAPABILITIES_V2,
    "P1": _P1_CAPABILITIES,
    "P2": _P2_CAPABILITIES,
}


def _level(value: object) -> str:
    normalized = str(value or "").strip().upper()
    if normalized not in LEVELS:
        raise ValueError("level must be P0, P1, or P2")
    return normalized


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


def _payload(value: object, required: set[str], field: str, *, optional=()) -> dict[str, Any]:
    result = _mapping(value, field)
    allowed = required.union(str(item) for item in optional)
    if not required.issubset(result) or not set(result).issubset(allowed):
        raise ValueError(f"{field} payload fields are invalid")
    return result


def _finite(value: object, field: str, *, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return result


def _exact_unit_record(value: object, names: tuple[str, ...], field: str) -> dict[str, float]:
    raw = _mapping(value, field)
    if set(raw) != set(names):
        raise ValueError(f"{field} fields are invalid")
    return {
        name: _finite(raw[name], f"{field}.{name}", low=0.0, high=1.0)
        for name in names
    }


def _measurement(value: object) -> dict[str, float]:
    raw = _mapping(value, "measurement")
    if set(raw) != set(MEASUREMENT_NAMES):
        raise ValueError("measurement fields are invalid")
    return {
        name: _finite(raw[name], f"measurement.{name}", low=0.0, high=1.0)
        for name in MEASUREMENT_NAMES
    }


def _context(value: object) -> ModelInvocationContextV2:
    if not isinstance(value, ModelInvocationContextV2):
        raise ValueError("context must be ModelInvocationContextV2")
    return value


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _signed(value: object, field: str) -> float:
    return _finite(value, field, low=-1.0, high=1.0)


def _fingerprinted(material: Mapping[str, Any]) -> dict[str, Any]:
    record = dict(material)
    return {**record, "fingerprint": model_data_fingerprint(record)}


def _verify_fingerprinted(value: object, field: str) -> dict[str, Any]:
    record = _mapping(value, field)
    fingerprint = record.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(record):
        raise ValueError(f"{field} fingerprint is invalid")
    return {**record, "fingerprint": fingerprint}


def _parameters(value: object) -> dict[str, float]:
    record = _verify_fingerprinted(value, "parameters")
    if record.get("version") != "neutral-cx-parameters-v1":
        raise ValueError("parameter version is invalid")
    return _exact_unit_record(record.get("values"), PARAMETER_NAMES, "parameter values")


def _state(value: object, field: str = "state") -> dict[str, float]:
    return _exact_unit_record(value, STATE_NAMES, field)


def _operator_versions(level: str) -> dict[str, str]:
    shared_projection_version = 2
    versions = {
        "initialize_parameters": "neutral-cx-initialize-parameters-v1",
        "initialize_state": "neutral-cx-initialize-state-v1",
        "measure_event": "neutral-cx-measure-event-v2",
        "derive_event_contribution": f"neutral-cx-event-{level.lower()}-v2",
        "settle_state": f"neutral-cx-settlement-{level.lower()}-v1",
        "project_shared_semantics": (
            f"neutral-cx-shared-{level.lower()}-v{shared_projection_version}"
        ),
        "assess_action": f"neutral-cx-action-{level.lower()}-v1",
        "plan_observation": "neutral-cx-observation-plan-v1",
        "advance_progress": "neutral-cx-progress-v1",
        "read_historical_influence": "neutral-cx-history-read-v1",
        "aggregate_historical_influence": "neutral-cx-history-aggregate-v1",
        "project_semantic_resources": "neutral-cx-resources-v1",
    }
    return {name: versions[name] for name in CAPABILITIES_BY_LEVEL[level]}


class NeutralCXResearchV1Package:
    """One level of the independently authored neutral research family."""

    def __init__(self, level: str) -> None:
        self.level = _level(level)
        capabilities = CAPABILITIES_BY_LEVEL[self.level]
        self.manifest = InteractionModelManifest.create(
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION_BY_LEVEL[self.level],
            api_version=MODEL_API_VERSION_V2,
            capability_profile=self.level,
            capabilities=capabilities,
            parameters_schema={
                "type": "object",
                "fields": list(PARAMETER_NAMES),
                "range": [0.0, 1.0],
            },
            state_schema={
                "type": "object",
                "axes": list(STATE_NAMES),
                "range": [0.0, 1.0],
            },
            measurement_schema={
                "type": "object",
                "fields": list(MEASUREMENT_NAMES),
                "range": [0.0, 1.0],
            },
            shared_projection_schema={
                "type": "object",
                "fields": ["activation", "stability", "immediate_signal", "action_score"],
            },
            progress_delta_schema={
                "type": "object",
                "source": "bound-temporal-domain",
                "enabled": self.level == "P2",
            },
            parameter_package={
                "version": "neutral-cx-research-parameters-v1",
                "paper_capabilities": list(PAPER_CAPABILITIES_BY_LEVEL[self.level]),
                "resource_names": list(RESOURCE_NAMES) if self.level == "P2" else [],
            },
            operator_versions=_operator_versions(self.level),
        )

    def initialize_parameters(self, *, payload, context):
        _context(context)
        values = _exact_unit_record(payload, PARAMETER_NAMES, "parameters")
        return {"parameters": _fingerprinted({
            "version": "neutral-cx-parameters-v1",
            "values": values,
        })}

    def initialize_state(self, *, payload, context):
        _context(context)
        return {"state": _state(payload, "initial state")}

    def measure_event(self, *, payload, context):
        _context(context)
        return {"measurement": _measurement(payload)}

    def derive_event_contribution(self, *, payload, context):
        _context(context)
        record = _payload(
            payload,
            {"parameters", "state", "measurement"},
            "derive_event_contribution",
            optional=("historical_influence", "progress_contribution"),
        )
        parameters = _parameters(record["parameters"])
        before = _state(record["state"])
        if "progress_contribution" in record:
            if self.level != "P2":
                raise ValueError(
                    "progress contribution is not declared at this level")
            progress = _verify_fingerprinted(
                record["progress_contribution"], "progress contribution")
            if _state(
                progress.get("state_before"), "progress state_before",
            ) != before:
                raise ValueError(
                    "progress contribution does not continue current state")
            before = _state(
                progress.get("state_candidate"), "progress state_candidate")
        measured = _measurement(record["measurement"])
        signal = _signed(
            measured["positive_signal"] - measured["negative_signal"],
            "event signal",
        )
        history_value = 0.0
        effective_signal = signal
        if "historical_influence" in record:
            if self.level != "P2":
                raise ValueError("historical influence is not declared at this level")
            history = _verify_fingerprinted(record["historical_influence"], "historical influence")
            history_value = _signed(history.get("value"), "historical influence value")
            history_gain = parameters["history_gain"]
            effective_signal = _signed(
                (signal + history_gain * history_value) / (1.0 + history_gain),
                "effective signal",
            )
        candidate = dict(before)
        if self.level in {"P1", "P2"}:
            requested_step = parameters["response_gain"] * effective_signal
            max_step = 0.25
            bounded_step = min(max(requested_step, -max_step), max_step)
            candidate["activation"] = _clamp(before["activation"] + bounded_step)
            stability_target = 1.0 - abs(effective_signal)
            candidate["stability"] = _clamp(
                parameters["retention"] * before["stability"]
                + (1.0 - parameters["retention"]) * stability_target
            )
        material = {
            "version": f"neutral-cx-event-{self.level.lower()}-v2",
            "state_before": before,
            "state_candidate": candidate,
            "immediate_signal": signal,
            "historical_influence": history_value,
            "effective_signal": effective_signal,
            "constraints": {
                "maximum_absolute_step": 0.25 if self.level in {"P1", "P2"} else 0.0,
                "state_range": [0.0, 1.0],
            },
            "source_measurement_fingerprint": model_data_fingerprint(measured),
        }
        return {"event_contribution": _fingerprinted(material)}

    def settle_state(self, *, payload, context):
        _context(context)
        record = _payload(
            payload,
            {"state", "progress_contribution", "event_contribution"},
            "settle_state",
        )
        before = _state(record["state"])
        current = before
        immediate_signal = 0.0
        source_fingerprints: dict[str, str | None] = {"progress": None, "event": None}
        for kind in ("progress", "event"):
            raw = record[f"{kind}_contribution"]
            if raw is None:
                continue
            contribution = _verify_fingerprinted(raw, f"{kind} contribution")
            if _state(contribution.get("state_before"), f"{kind} state_before") != current:
                raise ValueError(f"{kind} contribution does not continue current state")
            current = _state(contribution.get("state_candidate"), f"{kind} state_candidate")
            immediate_signal = float(contribution.get("immediate_signal", immediate_signal))
            source_fingerprints[kind] = contribution["fingerprint"]
        if current == before and all(value is None for value in source_fingerprints.values()):
            raise ValueError("settlement requires a contribution")
        material = {
            "version": f"neutral-cx-settlement-{self.level.lower()}-v1",
            "state_before": before,
            "state_candidate": current,
            "immediate_signal": _signed(immediate_signal, "settlement immediate signal"),
            "source_progress_contribution_fingerprint": source_fingerprints["progress"],
            "source_event_contribution_fingerprint": source_fingerprints["event"],
            "state_commit_authority": False,
        }
        return {"settlement": _fingerprinted(material)}

    def project_shared_semantics(self, *, payload, context):
        _context(context)
        record = _payload(payload, {"parameters", "settlement"}, "project_shared_semantics")
        parameters = _parameters(record["parameters"])
        settlement = _verify_fingerprinted(record["settlement"], "settlement")
        values = _state(settlement.get("state_candidate"), "settled state")
        immediate = _signed(settlement.get("immediate_signal", 0.0), "immediate signal")
        basic_event_salience = abs(immediate)
        state_salience = _clamp(
            (values["activation"] + (1.0 - values["stability"])) / 2.0
        )
        action_score = (
            basic_event_salience
            if self.level == "P0"
            else max(basic_event_salience, state_salience)
        )
        shared_projection_version = 2
        material = {
            "version": (
                f"neutral-cx-shared-{self.level.lower()}-v"
                f"{shared_projection_version}"
            ),
            "values": values,
            "immediate_signal": immediate,
            "action_score": action_score,
            "action_boundary": parameters["action_boundary"],
            "source_settlement_fingerprint": settlement["fingerprint"],
        }
        return {"shared_projection": _fingerprinted(material)}

    def assess_action(self, *, payload, context):
        _context(context)
        record = _payload(payload, {"shared_projection"}, "assess_action")
        shared = _verify_fingerprinted(record["shared_projection"], "shared projection")
        score = _finite(shared.get("action_score"), "action score", low=0.0, high=1.0)
        boundary = _finite(shared.get("action_boundary"), "action boundary", low=0.0, high=1.0)
        return {"action_assessment": {
            "version": f"neutral-cx-action-{self.level.lower()}-v1",
            "source_shared_projection_fingerprint": shared["fingerprint"],
            "action_kind": "emit_neutral_signal",
            "eligible": score >= boundary,
            "constraints": {"score_range": [0.0, 1.0]},
            "observation_advice": (
                {"declared": False}
                if self.level == "P0"
                else {"after_steps": 1}
            ),
            "action_commit_authority": False,
        }}

    def plan_observation(self, *, payload, context):
        if self.level not in {"P1", "P2"}:
            raise ValueError("plan_observation is not declared at this level")
        _context(context)
        record = _payload(payload, {"shared_projection"}, "plan_observation")
        shared = _verify_fingerprinted(record["shared_projection"], "shared projection")
        score = _finite(shared.get("action_score"), "action score", low=0.0, high=1.0)
        return {"observation_plan": {
            "version": "neutral-cx-observation-plan-v1",
            "source_shared_projection_output_fingerprint": model_data_fingerprint(
                {"shared_projection": shared}
            ),
            "observation_advice": {"after_steps": 1 if score >= 0.5 else 2},
            "observation_projection": {"score": score, "unit": "steps"},
            "action_assessment_authority": False,
            "observation_schedule_authority": False,
        }}

    def advance_progress(self, *, payload, context):
        if self.level != "P2":
            raise ValueError("advance_progress is not declared at this level")
        _context(context)
        record = _payload(payload, {"parameters", "state", "progress_delta"}, "advance_progress")
        parameters = _parameters(record["parameters"])
        before = _state(record["state"])
        progress = _mapping(record["progress_delta"], "progress_delta")
        progress_payload = _mapping(progress.get("payload"), "progress_delta.payload")
        steps = _finite(
            progress_payload.get("elapsed_steps"),
            "progress elapsed_steps",
            low=0.0,
            high=1_000_000.0,
        )
        retention = parameters["retention"] ** steps
        candidate = {
            "activation": _clamp(before["activation"] * retention),
            "stability": _clamp(0.5 + (before["stability"] - 0.5) * retention),
        }
        return {"progress_contribution": _fingerprinted({
            "version": "neutral-cx-progress-v1",
            "state_before": before,
            "state_candidate": candidate,
            "immediate_signal": 0.0,
            "source_progress_fingerprint": model_data_fingerprint(progress),
        })}

    def read_historical_influence(self, *, payload, context):
        if self.level != "P2":
            raise ValueError("read_historical_influence is not declared at this level")
        _context(context)
        record = _payload(payload, {"fact_ref", "signed_effect", "age_steps"}, "read_historical_influence")
        fact_ref = str(record["fact_ref"] or "").strip()
        if not fact_ref:
            raise ValueError("fact_ref must be non-empty")
        effect = _signed(record["signed_effect"], "signed_effect")
        age = _finite(record["age_steps"], "age_steps", low=0.0, high=1_000_000.0)
        value = effect / (1.0 + age)
        return {"historical_influence": _fingerprinted({
            "version": "neutral-cx-history-read-v1",
            "fact_ref": fact_ref,
            "value": value,
            "forward_only": True,
        })}

    def aggregate_historical_influence(self, *, payload, context):
        if self.level != "P2":
            raise ValueError("aggregate_historical_influence is not declared at this level")
        _context(context)
        record = _payload(payload, {"influences"}, "aggregate_historical_influence")
        raw = record["influences"]
        if not isinstance(raw, list):
            raise ValueError("influences must be a list")
        values = []
        source_refs = []
        for item in raw:
            influence = _verify_fingerprinted(item, "historical influence")
            if influence.get("forward_only") is not True:
                raise ValueError("historical influence must be forward only")
            values.append(_signed(influence.get("value"), "historical influence value"))
            source_refs.append(influence["fingerprint"])
        total = sum(values)
        bounded = total / (1.0 + abs(total)) if total else 0.0
        return {"historical_influence": _fingerprinted({
            "version": "neutral-cx-history-aggregate-v1",
            "value": bounded,
            "source_influence_fingerprints": source_refs,
            "forward_only": True,
        })}

    def project_semantic_resources(self, *, payload, context):
        if self.level != "P2":
            raise ValueError("project_semantic_resources is not declared at this level")
        _context(context)
        record = _payload(
            payload,
            {"shared_projection", "source_shared_projection_output_fingerprint", "committed_remainders"},
            "project_semantic_resources",
        )
        shared = _verify_fingerprinted(record["shared_projection"], "shared projection")
        source = str(record["source_shared_projection_output_fingerprint"] or "").strip()
        if not source:
            raise ValueError("shared projection output fingerprint is required")
        _mapping(record["committed_remainders"], "committed_remainders")
        score = _finite(shared["action_score"], "action score", low=0.0, high=1.0)
        stability = _finite(shared["values"]["stability"], "stability", low=0.0, high=1.0)
        scores = {"advance": score, "hold": 1.0 - score, "clarify": 1.0 - stability}
        resources = []
        selected = []
        for name in RESOURCE_NAMES:
            value = _clamp(scores[name])
            resource_id = f"neutral-cx:{name}"
            resources.append({
                "resource_id": resource_id,
                "lane": "neutral_control",
                "descriptor": {"kind": "neutral_control", "direction": name},
                "score": value,
                "domain": {"low": 0.0, "high": 1.0},
                "neutral": 0.5,
                "deviation": (value - 0.5) / 0.5,
                "estimated_cost": 1.0,
                "source_receipt_refs": (source,),
            })
            if value > 0.5:
                selected.append(resource_id)
        return {"expression_resource_batch": {
            "version": "neutral-cx-resources-v1",
            "source_shared_projection_output_fingerprint": source,
            "resources": tuple(resources),
            "selection": {
                "version": "neutral-cx-resource-selection-v1",
                "selected_resource_ids": tuple(selected),
            },
            "selection_evidence": {},
        }}


def build_e3_capability_mapping(
    packages: Iterable[NeutralCXResearchV1Package],
) -> dict[str, Any]:
    by_level: dict[str, NeutralCXResearchV1Package] = {}
    for package in packages:
        if not isinstance(package, NeutralCXResearchV1Package):
            raise ValueError("E3 mapping requires neutral C-X research packages")
        if package.level in by_level:
            raise ValueError(f"duplicate E3 package level: {package.level}")
        by_level[package.level] = package
    if set(by_level) != set(LEVELS):
        raise ValueError("E3 mapping requires exactly P0, P1, and P2")
    levels = []
    for level in LEVELS:
        manifest = by_level[level].manifest
        binding = InteractionModelBinding.from_manifest(manifest)
        levels.append({
            "id": level,
            "paper_capabilities": list(PAPER_CAPABILITIES_BY_LEVEL[level]),
            "package_version": manifest.package_version,
            "capability_profile": manifest.capability_profile,
            "capability_ids": list(manifest.capabilities),
            "operator_versions": dict(manifest.operator_versions),
            "schema_fingerprint": manifest.schema_fingerprint,
            "parameter_fingerprint": manifest.parameter_fingerprint,
            "model_binding_fingerprint": model_data_fingerprint(binding.to_record()),
        })
    capability_sets = [set(item["capability_ids"]) for item in levels]
    if not (capability_sets[0] < capability_sets[1] < capability_sets[2]):
        raise ValueError("E3 Model API capabilities must be strict supersets")
    return {
        "schema_version": "tide-e3-capability-mapping-v1",
        "model_api_version": MODEL_API_VERSION_V2,
        "package_family": PACKAGE_ID,
        "levels": levels,
    }


__all__ = (
    "CAPABILITIES_BY_LEVEL",
    "LEVELS",
    "MEASUREMENT_NAMES",
    "PACKAGE_ID",
    "PACKAGE_VERSION_BY_LEVEL",
    "PAPER_CAPABILITIES_BY_LEVEL",
    "PARAMETER_NAMES",
    "RESOURCE_NAMES",
    "STATE_NAMES",
    "NeutralCXResearchV1Package",
    "build_e3_capability_mapping",
)
