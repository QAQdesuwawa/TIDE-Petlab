"""A deliberately small, non-six/seven Interaction Model package.

This package is a framework substitution asset, not a quality baseline.  It
owns two parameters, three state coordinates, two event measurements and four
expression resources.  It has no memory, model-time, Store, Writer or send
authority.
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
    MinimalAffinityParticipant,
    make_autonomous_participant,
)


PACKAGE_ID = "tide.minimal-affinity"
PACKAGE_VERSION = "1.0.0-alpha04-c5b"
PARAMETER_VERSION = "minimal-affinity-parameters-v1"
EVENT_OPERATOR_VERSION = "minimal-affinity-event-affine-v1"
SETTLEMENT_VERSION = "minimal-affinity-settlement-v1"
PROJECTION_VERSION = "minimal-affinity-shared-projection-v1"
ACTION_VERSION = "minimal-affinity-action-v1"
OBSERVATION_PLAN_VERSION = "minimal-affinity-observation-plan-v1"
RESOURCE_VERSION = "minimal-affinity-expression-resources-v1"
PROGRESS_VERSION = "minimal-affinity-progress-hold-v1"

PARAMETER_NAMES = ("responsiveness", "restraint")
STATE_NAMES = ("affinity", "tension", "expression_drive")
MEASUREMENT_NAMES = ("affinity_stimulus", "conflict_stimulus")
RESOURCE_NAMES = ("approach", "avoid", "question", "soothe")


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
        raise ValueError("minimal parameter version is invalid")
    values = _unit_record(wrapper["values"], PARAMETER_NAMES, "parameter values")
    material = {"version": PARAMETER_VERSION, "values": values}
    if wrapper["fingerprint"] != model_data_fingerprint(material):
        raise ValueError("minimal parameter fingerprint is invalid")
    return values


def _settlement(value: object) -> dict[str, Any]:
    result = _mapping(value, "settlement")
    fingerprint = result.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(result):
        raise ValueError("minimal settlement fingerprint is invalid")
    _unit_record(result.get("state_before"), STATE_NAMES, "state_before")
    _unit_record(result.get("state_candidate"), STATE_NAMES, "state_candidate")
    result["fingerprint"] = fingerprint
    return result


def _shared(value: object) -> dict[str, Any]:
    result = _mapping(value, "shared_projection")
    fingerprint = result.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(result):
        raise ValueError("minimal shared projection fingerprint is invalid")
    _unit_record(result.get("values"), STATE_NAMES, "shared projection values")
    _finite(
        result.get("action_threshold"),
        "shared projection action_threshold",
        low=0.0,
        high=1.0,
    )
    result["fingerprint"] = fingerprint
    return result


class MinimalAffinityV1Package:
    """Small formal package proving that the Kernel is dimension agnostic."""

    def __init__(self, *, observation_unit: str = "steps") -> None:
        if observation_unit not in {"steps", "milliseconds"}:
            raise ValueError(
                "observation_unit must be steps or milliseconds")
        self._observation_unit = observation_unit
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
            capability_profile="minimal",
            capabilities=capabilities,
            parameters_schema={
                "type": "object", "fields": list(PARAMETER_NAMES),
                "range": [0.0, 1.0],
            },
            state_schema={
                "type": "object", "axes": list(STATE_NAMES),
                "range": [0.0, 1.0],
            },
            measurement_schema={
                "type": "object", "fields": list(MEASUREMENT_NAMES),
                "range": [-1.0, 1.0],
            },
            shared_projection_schema={
                "type": "object", "axes": list(STATE_NAMES),
            },
            progress_delta_schema={
                "type": "object",
                "source": "bound-temporal-domain",
                "state_effect": "hold",
            },
            parameter_package={
                "version": "minimal-affinity-formulas-v1",
                "resource_names": list(RESOURCE_NAMES),
                "observation_unit": observation_unit,
            },
            operator_versions={
                "initialize_parameters": PARAMETER_VERSION,
                "initialize_state": "minimal-affinity-initialize-state-v1",
                "measure_event": "minimal-affinity-measure-event-v1",
                "derive_event_contribution": EVENT_OPERATOR_VERSION,
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
        return {"parameters": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def initialize_state(self, *, payload, context):
        _context(context)
        return {"state": _unit_record(payload, STATE_NAMES, "initial state")}

    def measure_event(self, *, payload, context):
        _context(context)
        return {"measurement": _measurement(payload)}

    def committed_measurement_evidence(
        self, *, projection, source_event_id, local_event_id, event_kind,
    ) -> dict[str, Any]:
        """Bind this package's tiny measurement to one committed source fact."""
        source_event = _identifier(source_event_id, "source_event_id")
        local_event = _identifier(local_event_id, "local_event_id")
        kind = _identifier(event_kind, "event_kind")
        record = _measurement(projection)
        projection_fingerprint = model_data_fingerprint(record)
        material = {
            "version": "committed-model-measurement-evidence-v1",
            "model_binding": InteractionModelBinding.from_manifest(
                self.manifest).to_record(),
            "source_event_id": source_event,
            "local_event_id": local_event,
            "event_kind": kind,
            "projection_fingerprint": projection_fingerprint,
            "measurement_payload": record,
        }
        return dict(material, fingerprint=model_data_fingerprint(material))

    def derive_event_contribution(self, *, payload, context):
        _context(context)
        expected = {"parameters", "state", "measurement"}
        if isinstance(payload, Mapping) and "progress_contribution" in payload:
            expected.add("progress_contribution")
        record = _payload(
            payload, expected,
            "derive_event_contribution")
        params = _parameters(record["parameters"])
        before = _unit_record(record["state"], STATE_NAMES, "state")
        progress_value = record.get("progress_contribution")
        if progress_value is not None:
            progress = _mapping(progress_value, "progress_contribution")
            fingerprint = progress.pop("fingerprint", None)
            if fingerprint != model_data_fingerprint(progress):
                raise ValueError("minimal progress contribution fingerprint is invalid")
            if _unit_record(
                progress.get("state_before"), STATE_NAMES,
                "progress contribution state_before",
            ) != before:
                raise ValueError(
                    "minimal progress contribution does not continue current state")
            before = _unit_record(
                progress.get("state_candidate"), STATE_NAMES,
                "progress contribution state_candidate",
            )
        measured = _measurement(record["measurement"])
        response = params["responsiveness"]
        restraint = params["restraint"]
        affinity_input = measured["affinity_stimulus"]
        conflict_input = measured["conflict_stimulus"]
        candidate = {
            "affinity": _clamp(before["affinity"] + response * affinity_input),
            "tension": _clamp(
                before["tension"] + response * conflict_input
                - restraint * max(affinity_input, 0.0)),
            "expression_drive": _clamp(
                before["expression_drive"]
                + response * (abs(affinity_input) + abs(conflict_input)) / 2.0
                - restraint * before["expression_drive"]),
        }
        material = {
            "version": EVENT_OPERATOR_VERSION,
            "state_before": before,
            "state_candidate": candidate,
            "delta": {
                name: candidate[name] - before[name] for name in STATE_NAMES
            },
            "source_measurement_fingerprint": model_data_fingerprint(measured),
        }
        return {"event_contribution": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def advance_progress(self, *, payload, context):
        """Accept temporal progress while keeping this tiny model state fixed."""
        _context(context)
        record = _payload(
            payload, {"parameters", "state", "progress_delta"},
            "advance_progress")
        _parameters(record["parameters"])
        before = _unit_record(record["state"], STATE_NAMES, "state")
        progress_delta = _mapping(record["progress_delta"], "progress_delta")
        material = {
            "version": PROGRESS_VERSION,
            "state_before": before,
            "state_candidate": before,
            "source_progress_fingerprint": model_data_fingerprint(
                progress_delta),
        }
        return {"progress_contribution": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def settle_state(self, *, payload, context):
        _context(context)
        record = _payload(
            payload,
            {"state", "progress_contribution", "event_contribution"},
            "settle_state",
        )
        before = _unit_record(record["state"], STATE_NAMES, "state")
        progress = record["progress_contribution"]
        event = record["event_contribution"]
        if progress is None and event is None:
            raise ValueError("minimal settlement requires a contribution")
        current = before
        fingerprints = {"progress": None, "event": None}
        for kind, value in (("progress", progress), ("event", event)):
            if value is None:
                continue
            contribution = _mapping(value, f"{kind}_contribution")
            contribution_fingerprint = contribution.pop("fingerprint", None)
            if contribution_fingerprint != model_data_fingerprint(contribution):
                raise ValueError("minimal contribution fingerprint is invalid")
            source_before = _unit_record(
                contribution.get("state_before"), STATE_NAMES,
                f"{kind} contribution state_before")
            if source_before != current:
                raise ValueError("minimal contribution does not continue current state")
            current = _unit_record(
                contribution.get("state_candidate"), STATE_NAMES,
                f"{kind} contribution state_candidate")
            fingerprints[kind] = contribution_fingerprint
        candidate = current
        material = {
            "version": SETTLEMENT_VERSION,
            "state_before": before,
            "state_candidate": candidate,
            "net_delta": {
                name: candidate[name] - before[name] for name in STATE_NAMES
            },
            "source_event_contribution_fingerprint": (
                fingerprints["event"]),
            "source_progress_contribution_fingerprint": (
                fingerprints["progress"]),
            "state_commit_authority": False,
        }
        return {"settlement": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def project_shared_semantics(self, *, payload, context):
        _context(context)
        record = _payload(
            payload, {"parameters", "settlement"},
            "project_shared_semantics")
        params = _parameters(record["parameters"])
        settlement = _settlement(record["settlement"])
        material = {
            "version": PROJECTION_VERSION,
            "values": settlement["state_candidate"],
            "action_threshold": params["restraint"],
            "source_settlement_fingerprint": settlement["fingerprint"],
        }
        return {"shared_projection": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def assess_action(self, *, payload, context):
        _context(context)
        record = _payload(
            payload, {"shared_projection"}, "assess_action")
        shared = _shared(record["shared_projection"])
        wants = (
            shared["values"]["expression_drive"]
            > shared["action_threshold"]
        )
        return {"action_assessment": {
            "version": ACTION_VERSION,
            "source_shared_projection_fingerprint": shared["fingerprint"],
            "action_kind": "emit_affinity_signal",
            "eligible": wants,
            "constraints": {},
            "observation_advice": self._observation_advice(),
            "action_commit_authority": False,
        }}

    def _observation_advice(self) -> dict[str, int]:
        return (
            {"after_steps": 1}
            if self._observation_unit == "steps"
            else {"after_ms": 1}
        )

    def plan_observation(self, *, payload, context):
        _context(context)
        record = _payload(
            payload, {"shared_projection"}, "plan_observation")
        shared = _shared(record["shared_projection"])
        advice = self._observation_advice()
        return {"observation_plan": {
            "version": OBSERVATION_PLAN_VERSION,
            "source_shared_projection_output_fingerprint": (
                model_data_fingerprint({"shared_projection": shared})),
            "observation_advice": advice,
            "observation_projection": {
                "expression_drive": shared["values"]["expression_drive"],
                "distance": 1,
                "unit": self._observation_unit,
            },
            "action_assessment_authority": False,
            "observation_schedule_authority": False,
        }}

    def project_semantic_resources(self, *, payload, context):
        _context(context)
        record = _payload(
            payload,
            {
                "shared_projection",
                "source_shared_projection_output_fingerprint",
                "committed_remainders",
            },
            "project_semantic_resources",
        )
        shared = _shared(record["shared_projection"])
        source = str(record["source_shared_projection_output_fingerprint"] or "").strip()
        if not source:
            raise ValueError("shared projection output fingerprint is required")
        _mapping(record["committed_remainders"], "committed_remainders")
        values = shared["values"]
        scores = {
            "approach": values["affinity"],
            "avoid": values["tension"],
            "question": (values["tension"] + values["expression_drive"]) / 2.0,
            "soothe": 1.0 - abs(values["affinity"] - values["tension"]),
        }
        resources = []
        evidence = {}
        selected = []
        for name in RESOURCE_NAMES:
            score = _clamp(scores[name])
            deviation = (score - 0.5) / 0.5
            is_selected = score > 0.5
            resource_id = f"minimal-affinity:{name}"
            resources.append({
                "resource_id": resource_id,
                "lane": "interaction_tendency",
                "descriptor": {
                    "kind": "interaction_tendency", "direction": name,
                },
                "score": score,
                "domain": {"low": 0.0, "high": 1.0},
                "neutral": 0.5,
                "deviation": deviation,
                "estimated_cost": 1.0,
                "source_receipt_refs": (source,),
            })
            evidence[resource_id] = {
                "score": score, "selected": is_selected,
            }
            if is_selected:
                selected.append(resource_id)
        return {"expression_resource_batch": {
            "version": RESOURCE_VERSION,
            "source_shared_projection_output_fingerprint": source,
            "resources": tuple(resources),
            "selection": {
                "version": "minimal-affinity-selection-v1",
                "selected_resource_ids": tuple(selected),
                "resource_evidence": evidence,
            },
        }}


__all__ = (
    "MEASUREMENT_NAMES",
    "MinimalAffinityParticipant",
    "MinimalAffinityV1Package",
    "PACKAGE_ID",
    "PACKAGE_VERSION",
    "PARAMETER_NAMES",
    "RESOURCE_NAMES",
    "STATE_NAMES",
    "make_autonomous_participant",
)
