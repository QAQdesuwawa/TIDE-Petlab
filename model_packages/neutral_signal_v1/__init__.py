"""Domain-neutral medium Interaction Model package.

This package is deliberately independent from both the minimal affinity sample
and the CX six/seven reference package.  It proves that the framework can host
a richer state transition, temporal decay and expression-resource projection
without importing product-specific mathematics.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from framework.model_api import (
    BASE_MODEL_CAPABILITIES_V2,
    MODEL_API_VERSION_V2,
    InteractionModelBinding,
    InteractionModelManifest,
    ModelInvocationContext,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from .participants import (
    NeutralSignalParticipant,
    make_autonomous_participant,
)


PACKAGE_ID = "tide.neutral-signal"
PACKAGE_VERSION = "1.0.0-alpha04-c5b"
PARAMETER_VERSION = "neutral-signal-parameters-v1"
EVENT_VERSION = "neutral-signal-event-vector-v1"
PROGRESS_VERSION = "neutral-signal-temporal-decay-v1"
SETTLEMENT_VERSION = "neutral-signal-settlement-v1"
PROJECTION_VERSION = "neutral-signal-shared-projection-v1"
ACTION_VERSION = "neutral-signal-action-v1"
OBSERVATION_PLAN_VERSION = "neutral-signal-observation-plan-v1"
RESOURCE_VERSION = "neutral-signal-expression-resources-v1"

PARAMETER_NAMES = ("input_gain", "decay_rate", "activation_bias")
STATE_NAMES = ("level", "imbalance", "novelty", "action_readiness")
MEASUREMENT_NAMES = ("support_signal", "opposition_signal", "novelty_signal")
RESOURCE_NAMES = ("advance", "hold", "inspect", "reframe", "acknowledge")


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


def _exact(value: object, names: tuple[str, ...], field: str) -> dict[str, Any]:
    result = _mapping(value, field)
    if set(result) != set(names):
        raise ValueError(f"{field} fields must be exactly {', '.join(names)}")
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


def _unit_record(value: object, names: tuple[str, ...], field: str) -> dict[str, float]:
    raw = _exact(value, names, field)
    return {
        name: _finite(raw[name], f"{field}.{name}", low=0.0, high=1.0)
        for name in names
    }


def _measurement(value: object) -> dict[str, float]:
    raw = _exact(value, MEASUREMENT_NAMES, "measurement")
    return {
        name: _finite(raw[name], f"measurement.{name}", low=-1.0, high=1.0)
        for name in MEASUREMENT_NAMES
    }


def _context(value: object) -> ModelInvocationContext | ModelInvocationContextV2:
    if not isinstance(value, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("context must be a supported model invocation context")
    return value


def _payload(value: object, fields: set[str], stage: str) -> dict[str, Any]:
    result = _mapping(value, stage)
    if set(result) != fields:
        raise ValueError(f"{stage} payload fields are invalid")
    return result


def _clamp(value: float) -> float:
    return min(1.0, max(0.0, value))


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be a non-empty identifier")
    return result


def _parameters(value: object) -> dict[str, float]:
    wrapper = _payload(value, {"version", "values", "fingerprint"}, "parameters")
    if wrapper["version"] != PARAMETER_VERSION:
        raise ValueError("neutral signal parameter version is invalid")
    values = _unit_record(wrapper["values"], PARAMETER_NAMES, "parameter values")
    material = {"version": PARAMETER_VERSION, "values": values}
    if wrapper["fingerprint"] != model_data_fingerprint(material):
        raise ValueError("neutral signal parameter fingerprint is invalid")
    return values


def _fingerprinted(value: object, field: str) -> dict[str, Any]:
    result = _mapping(value, field)
    fingerprint = result.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(result):
        raise ValueError(f"{field} fingerprint is invalid")
    result["fingerprint"] = fingerprint
    return result


def _shared(value: object) -> dict[str, Any]:
    result = _fingerprinted(value, "shared_projection")
    _unit_record(result.get("values"), STATE_NAMES, "shared projection values")
    _finite(
        result.get("action_threshold"),
        "shared projection action_threshold",
        low=0.0,
        high=1.0,
    )
    return result


def _observation_readout(shared: Mapping[str, Any]) -> tuple[float, int]:
    values = shared["values"]
    pressure = (
        values["action_readiness"]
        + values["novelty"]
        + values["imbalance"]
    ) / 3.0
    return pressure, 200 + round((1.0 - pressure) * 600.0)


class NeutralSignalV1Package:
    """Medium, domain-neutral package with event response and temporal decay."""

    def __init__(self) -> None:
        capabilities = (
            *BASE_MODEL_CAPABILITIES_V2,
            "plan_observation",
            "advance_progress",
            "project_semantic_resources",
        )
        self.manifest = InteractionModelManifest.create(
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            api_version=MODEL_API_VERSION_V2,
            capability_profile="standard",
            capabilities=capabilities,
            parameters_schema={
                "type": "object", "fields": list(PARAMETER_NAMES), "range": [0.0, 1.0],
            },
            state_schema={
                "type": "object", "axes": list(STATE_NAMES), "range": [0.0, 1.0],
            },
            measurement_schema={
                "type": "object", "fields": list(MEASUREMENT_NAMES), "range": [-1.0, 1.0],
            },
            shared_projection_schema={"type": "object", "axes": list(STATE_NAMES)},
            progress_delta_schema={
                "type": "object", "source": "bound-temporal-domain",
                "state_effect": "continuous-decay",
            },
            parameter_package={
                "version": "neutral-signal-formulas-v1",
                "resource_names": list(RESOURCE_NAMES),
            },
            operator_versions={
                "initialize_parameters": PARAMETER_VERSION,
                "initialize_state": "neutral-signal-initialize-state-v1",
                "measure_event": "neutral-signal-measure-event-v1",
                "derive_event_contribution": EVENT_VERSION,
                "settle_state": SETTLEMENT_VERSION,
                "project_shared_semantics": PROJECTION_VERSION,
                "assess_action": ACTION_VERSION,
                "plan_observation": OBSERVATION_PLAN_VERSION,
                "advance_progress": PROGRESS_VERSION,
                "project_semantic_resources": RESOURCE_VERSION,
            },
        )

    def initialize_parameters(self, *, payload, context):
        _context(context)
        values = _unit_record(payload, PARAMETER_NAMES, "parameter input")
        material = {"version": PARAMETER_VERSION, "values": values}
        return {"parameters": {**material, "fingerprint": model_data_fingerprint(material)}}

    def initialize_state(self, *, payload, context):
        _context(context)
        return {"state": _unit_record(payload, STATE_NAMES, "initial state")}

    def measure_event(self, *, payload, context):
        _context(context)
        return {"measurement": _measurement(payload)}

    def committed_measurement_evidence(
        self, *, projection, source_event_id, local_event_id, event_kind,
    ) -> dict[str, Any]:
        record = _measurement(projection)
        material = {
            "version": "committed-model-measurement-evidence-v1",
            "model_binding": InteractionModelBinding.from_manifest(self.manifest).to_record(),
            "source_event_id": _identifier(source_event_id, "source_event_id"),
            "local_event_id": _identifier(local_event_id, "local_event_id"),
            "event_kind": _identifier(event_kind, "event_kind"),
            "projection_fingerprint": model_data_fingerprint(record),
            "measurement_payload": record,
        }
        return dict(material, fingerprint=model_data_fingerprint(material))

    def derive_event_contribution(self, *, payload, context):
        _context(context)
        expected = {"parameters", "state", "measurement"}
        if isinstance(payload, Mapping) and "progress_contribution" in payload:
            expected.add("progress_contribution")
        record = _payload(payload, expected, "derive_event_contribution")
        params = _parameters(record["parameters"])
        before = _unit_record(record["state"], STATE_NAMES, "state")
        progress_value = record.get("progress_contribution")
        if progress_value is not None:
            progress = _fingerprinted(progress_value, "progress_contribution")
            if _unit_record(progress.get("state_before"), STATE_NAMES, "progress contribution state_before") != before:
                raise ValueError("neutral signal progress does not continue current state")
            before = _unit_record(progress.get("state_candidate"), STATE_NAMES, "progress contribution state_candidate")
        measured = _measurement(record["measurement"])
        gain = params["input_gain"]
        support = measured["support_signal"]
        opposition = measured["opposition_signal"]
        novelty = abs(measured["novelty_signal"])
        candidate = {
            "level": _clamp(before["level"] + gain * (support - opposition) / 2.0),
            "imbalance": _clamp(before["imbalance"] + gain * abs(support - opposition) / 2.0),
            "novelty": _clamp(before["novelty"] + gain * novelty),
            "action_readiness": _clamp(
                before["action_readiness"]
                + gain * (abs(support) + abs(opposition) + novelty) / 3.0
            ),
        }
        material = {
            "version": EVENT_VERSION,
            "state_before": before,
            "state_candidate": candidate,
            "delta": {name: candidate[name] - before[name] for name in STATE_NAMES},
            "source_measurement_fingerprint": model_data_fingerprint(measured),
        }
        return {"event_contribution": {**material, "fingerprint": model_data_fingerprint(material)}}

    def advance_progress(self, *, payload, context):
        _context(context)
        record = _payload(payload, {"parameters", "state", "progress_delta"}, "advance_progress")
        params = _parameters(record["parameters"])
        before = _unit_record(record["state"], STATE_NAMES, "state")
        progress = _mapping(record["progress_delta"], "progress_delta")
        progress_payload = _mapping(progress.get("payload"), "progress_delta.payload")
        elapsed_ms = _finite(progress_payload.get("elapsed_ms"), "elapsed_ms", low=0.0, high=10**15)
        fraction = elapsed_ms / (elapsed_ms + 1000.0) if elapsed_ms else 0.0
        retention = 1.0 - params["decay_rate"] * fraction
        candidate = {
            "level": _clamp(0.5 + (before["level"] - 0.5) * retention),
            "imbalance": _clamp(before["imbalance"] * retention),
            "novelty": _clamp(before["novelty"] * retention),
            "action_readiness": _clamp(before["action_readiness"] * retention),
        }
        material = {
            "version": PROGRESS_VERSION,
            "state_before": before,
            "state_candidate": candidate,
            "source_progress_fingerprint": model_data_fingerprint(progress),
        }
        return {"progress_contribution": {**material, "fingerprint": model_data_fingerprint(material)}}

    def settle_state(self, *, payload, context):
        _context(context)
        record = _payload(payload, {"state", "progress_contribution", "event_contribution"}, "settle_state")
        before = _unit_record(record["state"], STATE_NAMES, "state")
        progress = record["progress_contribution"]
        event = record["event_contribution"]
        if progress is None and event is None:
            raise ValueError("neutral signal settlement requires a contribution")
        current = before
        fingerprints = {"progress": None, "event": None}
        for kind, value in (("progress", progress), ("event", event)):
            if value is None:
                continue
            contribution = _fingerprinted(value, f"{kind} contribution")
            if _unit_record(contribution.get("state_before"), STATE_NAMES, f"{kind} contribution state_before") != current:
                raise ValueError("neutral signal contribution does not continue current state")
            current = _unit_record(contribution.get("state_candidate"), STATE_NAMES, f"{kind} contribution state_candidate")
            fingerprints[kind] = contribution["fingerprint"]
        candidate = current
        material = {
            "version": SETTLEMENT_VERSION,
            "state_before": before,
            "state_candidate": candidate,
            "net_delta": {name: candidate[name] - before[name] for name in STATE_NAMES},
            "source_event_contribution_fingerprint": fingerprints["event"],
            "source_progress_contribution_fingerprint": fingerprints["progress"],
            "state_commit_authority": False,
        }
        return {"settlement": {**material, "fingerprint": model_data_fingerprint(material)}}

    def project_shared_semantics(self, *, payload, context):
        _context(context)
        record = _payload(payload, {"parameters", "settlement"}, "project_shared_semantics")
        params = _parameters(record["parameters"])
        settlement = _fingerprinted(record["settlement"], "settlement")
        values = _unit_record(settlement.get("state_candidate"), STATE_NAMES, "settlement state_candidate")
        material = {
            "version": PROJECTION_VERSION,
            "values": values,
            "action_threshold": params["activation_bias"],
            "source_settlement_fingerprint": settlement["fingerprint"],
        }
        return {"shared_projection": {**material, "fingerprint": model_data_fingerprint(material)}}

    def assess_action(self, *, payload, context):
        _context(context)
        record = _payload(payload, {"shared_projection"}, "assess_action")
        shared = _shared(record["shared_projection"])
        pressure, after_ms = _observation_readout(shared)
        return {"action_assessment": {
            "version": ACTION_VERSION,
            "source_shared_projection_fingerprint": shared["fingerprint"],
            "action_kind": "publish_signal_transition",
            "eligible": pressure > shared["action_threshold"],
            "constraints": {"signal_pressure": pressure},
            "observation_advice": {"after_ms": after_ms},
            "action_commit_authority": False,
        }}

    def plan_observation(self, *, payload, context):
        _context(context)
        record = _payload(
            payload, {"shared_projection"}, "plan_observation")
        shared = _shared(record["shared_projection"])
        pressure, after_ms = _observation_readout(shared)
        return {"observation_plan": {
            "version": OBSERVATION_PLAN_VERSION,
            "source_shared_projection_output_fingerprint": (
                model_data_fingerprint({"shared_projection": shared})),
            "observation_advice": {"after_ms": after_ms},
            "observation_projection": {
                "pressure": pressure,
                "interval_ms": after_ms,
            },
            "action_assessment_authority": False,
            "observation_schedule_authority": False,
        }}

    def project_semantic_resources(self, *, payload, context):
        _context(context)
        record = _payload(
            payload,
            {"shared_projection", "source_shared_projection_output_fingerprint", "committed_remainders"},
            "project_semantic_resources",
        )
        values = _shared(record["shared_projection"])["values"]
        source = _identifier(
            record["source_shared_projection_output_fingerprint"],
            "source_shared_projection_output_fingerprint",
        )
        _mapping(record["committed_remainders"], "committed_remainders")
        scores = {
            "advance": (values["level"] + values["action_readiness"]) / 2.0,
            "hold": 1.0 - values["action_readiness"],
            "inspect": (values["novelty"] + values["imbalance"]) / 2.0,
            "reframe": values["imbalance"],
            "acknowledge": 1.0 - values["imbalance"],
        }
        resources = []
        evidence = {}
        selected = []
        for name in RESOURCE_NAMES:
            score = _clamp(scores[name])
            resource_id = f"neutral-signal:{name}"
            is_selected = score > 0.5
            resources.append({
                "resource_id": resource_id,
                "lane": "interaction_tendency",
                "descriptor": {"kind": "interaction_tendency", "direction": name},
                "score": score,
                "domain": {"low": 0.0, "high": 1.0},
                "neutral": 0.5,
                "deviation": (score - 0.5) / 0.5,
                "estimated_cost": 1.0,
                "source_receipt_refs": (source,),
            })
            evidence[resource_id] = {"score": score, "selected": is_selected}
            if is_selected:
                selected.append(resource_id)
        return {"expression_resource_batch": {
            "version": RESOURCE_VERSION,
            "source_shared_projection_output_fingerprint": source,
            "resources": tuple(resources),
            "selection": {
                "version": "neutral-signal-selection-v1",
                "selected_resource_ids": tuple(selected),
                "resource_evidence": evidence,
            },
        }}


__all__ = (
    "MEASUREMENT_NAMES",
    "NeutralSignalParticipant",
    "NeutralSignalV1Package",
    "PACKAGE_ID",
    "PACKAGE_VERSION",
    "PARAMETER_NAMES",
    "RESOURCE_NAMES",
    "STATE_NAMES",
    "make_autonomous_participant",
)
