"""Logical-step, non-text witness package for the paper framework boundary."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import (
    BASE_MODEL_CAPABILITIES_V2,
    MODEL_API_VERSION_V2,
    InteractionModelManifest,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.invocation_v2 import ModelInvocationContextV2


PACKAGE_ID = "tide.logical-relay"
PACKAGE_VERSION = "1.0.0-alpha04"
PARAMETER_VERSION = "logical-relay-parameters-v1"
EVENT_VERSION = "logical-relay-event-v1"
PROGRESS_VERSION = "logical-relay-progress-v1"
SETTLEMENT_VERSION = "logical-relay-settlement-v1"
PROJECTION_VERSION = "logical-relay-projection-v1"
ACTION_VERSION = "logical-relay-action-v1"
OBSERVATION_VERSION = "logical-relay-observation-v1"


def _mapping(value: object, label: str) -> dict[str, Any]:
    plain = thaw_model_data(value)
    if not isinstance(plain, dict):
        raise ValueError(f"{label} must be a mapping")
    return plain


def _context(value: object) -> ModelInvocationContextV2:
    if not isinstance(value, ModelInvocationContextV2):
        raise ValueError("logical relay requires the neutral v2 invocation context")
    return value


def _integer(value: object, label: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{label} must be an integer >= {minimum}")
    return value


def _state(value: object, label: str = "state") -> dict[str, Any]:
    state = _mapping(value, label)
    if set(state) != {"count", "armed", "idle_steps"}:
        raise ValueError(f"{label} fields are invalid")
    state["count"] = _integer(state["count"], f"{label}.count")
    state["idle_steps"] = _integer(
        state["idle_steps"], f"{label}.idle_steps")
    if type(state["armed"]) is not bool:
        raise ValueError(f"{label}.armed must be boolean")
    return state


def _fingerprinted(value: object, label: str) -> dict[str, Any]:
    record = _mapping(value, label)
    fingerprint = record.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(record):
        raise ValueError(f"{label} fingerprint is invalid")
    record["fingerprint"] = fingerprint
    return record


class LogicalRelayV1Package:
    """A categorical control model with logical progress and no text resources."""

    def __init__(self) -> None:
        capabilities = (
            *BASE_MODEL_CAPABILITIES_V2,
            "advance_progress",
            "plan_observation",
        )
        self.manifest = InteractionModelManifest.create(
            package_id=PACKAGE_ID,
            package_version=PACKAGE_VERSION,
            api_version=MODEL_API_VERSION_V2,
            capability_profile="logical-relay-study-v1",
            capabilities=capabilities,
            parameters_schema={
                "type": "object", "required": ["trip_count"],
            },
            state_schema={
                "type": "object", "required": ["count", "armed", "idle_steps"],
            },
            measurement_schema={
                "type": "object", "required": ["pulse"],
            },
            shared_projection_schema={
                "type": "object", "required": ["armed", "count", "trip_count"],
            },
            progress_delta_schema={
                "type": "object", "required": ["elapsed_steps"],
            },
            parameter_package={
                "version": "logical-relay-formulas-v1",
                "action_domain": "discrete-control",
            },
            operator_versions={
                "initialize_parameters": PARAMETER_VERSION,
                "initialize_state": "logical-relay-initialize-state-v1",
                "measure_event": "logical-relay-measure-event-v1",
                "derive_event_contribution": EVENT_VERSION,
                "settle_state": SETTLEMENT_VERSION,
                "project_shared_semantics": PROJECTION_VERSION,
                "assess_action": ACTION_VERSION,
                "advance_progress": PROGRESS_VERSION,
                "plan_observation": OBSERVATION_VERSION,
            },
        )

    def initialize_parameters(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "parameter input")
        if set(record) != {"trip_count"}:
            raise ValueError("parameter input fields are invalid")
        material = {
            "version": PARAMETER_VERSION,
            "trip_count": _integer(record["trip_count"], "trip_count", minimum=1),
        }
        return {"parameters": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def initialize_state(self, *, payload, context):
        _context(context)
        return {"state": _state(payload, "initial state")}

    def measure_event(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "measurement")
        if set(record) != {"pulse"} or type(record["pulse"]) is not bool:
            raise ValueError("measurement requires one boolean pulse")
        return {"measurement": record}

    def derive_event_contribution(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "event contribution input")
        expected = {"parameters", "state", "measurement"}
        if "progress_contribution" in record:
            expected.add("progress_contribution")
        if set(record) != expected:
            raise ValueError("event contribution input fields are invalid")
        parameters = _fingerprinted(record["parameters"], "parameters")
        before = _state(record["state"])
        if record.get("progress_contribution") is not None:
            progress = _fingerprinted(
                record["progress_contribution"], "progress contribution")
            if _state(progress["state_before"], "progress state_before") != before:
                raise ValueError("progress contribution does not continue state")
            before = _state(progress["state_candidate"], "progress state_candidate")
        measurement = _mapping(record["measurement"], "measurement")
        count = before["count"] + (1 if measurement.get("pulse") is True else 0)
        candidate = {
            "count": count,
            "armed": count >= parameters["trip_count"],
            "idle_steps": 0 if measurement.get("pulse") is True else before["idle_steps"],
        }
        material = {
            "version": EVENT_VERSION,
            "state_before": before,
            "state_candidate": candidate,
            "source_measurement_fingerprint": model_data_fingerprint(measurement),
        }
        return {"event_contribution": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def advance_progress(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "progress input")
        if set(record) != {"parameters", "state", "progress_delta"}:
            raise ValueError("progress input fields are invalid")
        _fingerprinted(record["parameters"], "parameters")
        before = _state(record["state"])
        delta = _mapping(record["progress_delta"], "progress delta")
        delta_payload = _mapping(delta.get("payload"), "progress delta payload")
        elapsed = _integer(delta_payload.get("elapsed_steps"), "elapsed_steps")
        candidate = dict(before)
        candidate["idle_steps"] += elapsed
        material = {
            "version": PROGRESS_VERSION,
            "state_before": before,
            "state_candidate": candidate,
            "source_progress_fingerprint": model_data_fingerprint(delta),
        }
        return {"progress_contribution": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def settle_state(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "settlement input")
        if set(record) != {"state", "progress_contribution", "event_contribution"}:
            raise ValueError("settlement input fields are invalid")
        before = _state(record["state"])
        current = before
        source_refs = {}
        for name in ("progress", "event"):
            contribution = record[f"{name}_contribution"]
            if contribution is None:
                source_refs[name] = None
                continue
            parsed = _fingerprinted(contribution, f"{name} contribution")
            if _state(parsed["state_before"], f"{name} state_before") != current:
                raise ValueError(f"{name} contribution does not continue state")
            current = _state(parsed["state_candidate"], f"{name} state_candidate")
            source_refs[name] = parsed["fingerprint"]
        material = {
            "version": SETTLEMENT_VERSION,
            "state_before": before,
            "state_candidate": current,
            "source_progress_contribution_fingerprint": source_refs["progress"],
            "source_event_contribution_fingerprint": source_refs["event"],
            "state_commit_authority": False,
        }
        return {"settlement": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def project_shared_semantics(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "projection input")
        if set(record) != {"parameters", "settlement"}:
            raise ValueError("projection input fields are invalid")
        parameters = _fingerprinted(record["parameters"], "parameters")
        settlement = _fingerprinted(record["settlement"], "settlement")
        state = _state(settlement["state_candidate"], "settled state")
        material = {
            "version": PROJECTION_VERSION,
            "armed": state["armed"],
            "count": state["count"],
            "trip_count": parameters["trip_count"],
            "source_settlement_fingerprint": settlement["fingerprint"],
        }
        return {"shared_projection": {
            **material, "fingerprint": model_data_fingerprint(material),
        }}

    def assess_action(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "action input")
        if set(record) != {"shared_projection"}:
            raise ValueError("action input fields are invalid")
        shared = _fingerprinted(record["shared_projection"], "shared projection")
        return {"action_assessment": {
            "version": ACTION_VERSION,
            "source_shared_projection_fingerprint": shared["fingerprint"],
            "action_kind": "toggle_relay",
            "eligible": shared["armed"] is True,
            "constraints": {"port": "relay-A"},
            "observation_advice": {"after_steps": 1},
            "action_commit_authority": False,
        }}

    def plan_observation(self, *, payload, context):
        _context(context)
        record = _mapping(payload, "observation input")
        if set(record) != {"shared_projection"}:
            raise ValueError("observation input fields are invalid")
        shared = _fingerprinted(record["shared_projection"], "shared projection")
        return {"observation_plan": {
            "version": OBSERVATION_VERSION,
            "source_shared_projection_output_fingerprint": model_data_fingerprint(
                {"shared_projection": shared}),
            "observation_advice": {"after_steps": 1},
            "observation_projection": {"armed": shared["armed"]},
            "action_assessment_authority": False,
            "observation_schedule_authority": False,
        }}


__all__ = ("LogicalRelayV1Package", "PACKAGE_ID", "PACKAGE_VERSION")
