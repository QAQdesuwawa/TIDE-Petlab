"""Paper-aligned v2 package seam for TIDE's six-root/seven-constant model.

This module rehangs already-approved pure formulas behind InteractionModel API
v2.  It owns no Store, Runner, clock, timer, observation, commit, send, Writer,
or UI authority.  Contributions remain derived evidence until ``settle_state``
publishes one state candidate; the Kernel remains the only commit authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from .continuous_root_projection import project_continuous_roots
from .preference_parameters import CONSTANT_ORDER, resolve_effective_constants
from .interaction_geometry import (
    ROOT_ORDER,
    next_observation_interval,
    opening_readout,
    relationship_transition,
)
from .root_dynamics import flow_root_time, respond_to_root_stimulus
from framework.model_api import (
    BASE_MODEL_CAPABILITIES_V2,
    MODEL_API_VERSION_V2,
    InteractionModelManifest,
    ModelInvocationContext,
    model_data_fingerprint,
    thaw_model_data,
    InteractionModelBinding,
)
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from .semantic_projection import coerce_projection
from model_packages.cx_six_seven_v1.semantic_resources import (
    project_expression_resources,
)


PACKAGE_ID = "tide.cx-six-seven"
PACKAGE_VERSION = "2.0.0-alpha04-f5b"
PARAMETER_BINDING_VERSION = "cx67-parameter-binding-v2"
STATE_SETTLEMENT_VERSION = "cx67-state-settlement-v1"
SHARED_PROJECTION_VERSION = "cx67-shared-projection-v1"
ACTION_ASSESSMENT_VERSION = "cx67-action-assessment-from-shared-projection-v2"
SEMANTIC_RESOURCE_OPERATOR_VERSION = "cx67-project-semantic-resources-v1"


def _plain(value: object, field: str) -> Any:
    try:
        return thaw_model_data(value)
    except ValueError as exc:
        raise ValueError(f"{field} must contain model data") from exc


def _mapping(value: object, field: str) -> dict[str, Any]:
    plain = _plain(value, field)
    if not isinstance(plain, dict):
        raise ValueError(f"{field} must be a mapping")
    return plain


def _exact_payload(payload: Mapping[str, Any], keys: set[str], stage: str) -> None:
    if not isinstance(payload, Mapping) or set(payload) != keys:
        missing = sorted(keys - set(payload)) if isinstance(payload, Mapping) else sorted(keys)
        extra = sorted(set(payload) - keys) if isinstance(payload, Mapping) else []
        raise ValueError(
            f"{stage} payload keys do not match contract; "
            f"missing={missing}, unexpected={extra}"
        )


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be a non-empty identifier")
    return result


def _context(
    context: object,
) -> ModelInvocationContext | ModelInvocationContextV2:
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError(
            "context must be ModelInvocationContext or ModelInvocationContextV2")
    return context


def _root_vector(value: object, field: str) -> dict[str, float]:
    raw = _mapping(value, field)
    if set(raw) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    result = {}
    for axis in ROOT_ORDER:
        try:
            item = float(raw[axis])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}.{axis} must be numeric") from exc
        if not 0.0 <= item <= 1.0:
            raise ValueError(f"{field}.{axis} must be between zero and one")
        result[axis] = item
    return result


def _same_roots(left: Mapping[str, float], right: Mapping[str, float]) -> bool:
    return all(abs(float(left[axis]) - float(right[axis])) <= 1e-12
               for axis in ROOT_ORDER)


def _constant_binding(parameters: object) -> dict[str, Any]:
    wrapper = _mapping(parameters, "parameters")
    if set(wrapper) != {"constant_binding", "fingerprint"}:
        raise ValueError("parameters must contain constant_binding and fingerprint")
    binding = _mapping(wrapper["constant_binding"], "parameters.constant_binding")
    required = {
        "version", "base_constants_version", "b0_version",
        "b0_mapping_version", "correction_version",
        "effective_constants_version", "effective_constants",
    }
    if set(binding) != required:
        raise ValueError("constant_binding fields do not match the v2 contract")
    if model_data_fingerprint(binding) != _identifier(
            wrapper["fingerprint"], "parameters.fingerprint"):
        raise ValueError("parameters fingerprint does not match constant binding")
    constants = _mapping(binding["effective_constants"], "effective_constants")
    if set(constants) != set(CONSTANT_ORDER):
        raise ValueError("effective_constants must contain exactly seven constants")
    return binding


def _contribution_record(
    *, kind: str, x_before: Mapping[str, float], delta: Mapping[str, float],
    x_after: Mapping[str, float], operator_receipt: Mapping[str, Any],
    source_field: str, source_value: str,
) -> dict[str, Any]:
    record = {
        "kind": kind,
        "x_before": dict(x_before),
        "delta": dict(delta),
        "x_after": dict(x_after),
        "operator_receipt": dict(operator_receipt),
        source_field: source_value,
    }
    record["fingerprint"] = model_data_fingerprint(record)
    return record


def _validated_contribution(value: object, *, kind: str) -> dict[str, Any]:
    record = _mapping(value, f"{kind}_contribution")
    fingerprint = _identifier(record.pop("fingerprint", None), "contribution.fingerprint")
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError(f"{kind} contribution fingerprint is invalid")
    if record.get("kind") != kind:
        raise ValueError(f"expected a {kind} contribution")
    before = _root_vector(record.get("x_before"), f"{kind}.x_before")
    after = _root_vector(record.get("x_after"), f"{kind}.x_after")
    delta = _mapping(record.get("delta"), f"{kind}.delta")
    if set(delta) != set(ROOT_ORDER):
        raise ValueError(f"{kind}.delta must contain exactly the six root axes")
    for axis in ROOT_ORDER:
        if abs((before[axis] + float(delta[axis])) - after[axis]) > 1e-9:
            raise ValueError(f"{kind} contribution delta does not match before/after")
    record.update({"x_before": before, "x_after": after, "delta": delta,
                   "fingerprint": fingerprint})
    return record


def _verified_settlement(value: object) -> dict[str, Any]:
    record = _mapping(value, "settlement")
    fingerprint = _identifier(record.pop("fingerprint", None), "settlement.fingerprint")
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError("settlement fingerprint is invalid")
    _root_vector(record.get("state_before"), "settlement.state_before")
    _root_vector(record.get("state_candidate"), "settlement.state_candidate")
    record["fingerprint"] = fingerprint
    return record


def _verified_shared_projection(value: object) -> dict[str, Any]:
    record = _mapping(value, "shared_projection")
    fingerprint = _identifier(
        record.pop("fingerprint", None), "shared_projection.fingerprint")
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError("shared projection fingerprint is invalid")
    vector = _mapping(record.get("vector"), "shared_projection.vector")
    if set(vector) != {"exchange", "withhold", "tension"}:
        raise ValueError("shared projection must contain three directions")
    record["vector"] = vector
    record["fingerprint"] = fingerprint
    return record


class CxSixSevenV2Package:
    """Standard profile: core lifecycle plus reference progress dynamics."""

    def __init__(self, *, parameter_package: Mapping[str, Any]) -> None:
        params = _mapping(parameter_package, "parameter_package")
        required = {
            "version", "initial_state_default", "event_response", "progress_flow",
            "shared_projection", "action_projection", "semantic_resources",
        }
        if set(params) != required:
            raise ValueError("parameter_package fields do not match reference contract")
        for field in required:
            if field != "version" and not isinstance(params[field], dict):
                raise ValueError(f"parameter_package.{field} must be a mapping")
        _identifier(params["version"], "parameter_package.version")
        _root_vector(params["initial_state_default"], "initial_state_default")
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
                "type": "object",
                "required": ["constant_binding", "fingerprint"],
            },
            state_schema={
                "type": "object", "axes": list(ROOT_ORDER),
                "range": [0.0, 1.0],
            },
            measurement_schema={
                "type": "object", "variable_deltas": list(ROOT_ORDER),
                "range": [-1.0, 1.0],
            },
            shared_projection_schema={
                "type": "object",
                "axes": ["exchange", "withhold", "tension"],
            },
            progress_delta_schema={
                "type": "object", "required": ["elapsed_ms"], "unit": "ms",
            },
            parameter_package=params,
            operator_versions={
                "initialize_parameters": PARAMETER_BINDING_VERSION,
                "initialize_state": "cx67-initialize-state-v2",
                "measure_event": "cx67-measure-event-v3",
                "derive_event_contribution": "cx67-derive-event-contribution-v1",
                "settle_state": STATE_SETTLEMENT_VERSION,
                "project_shared_semantics": SHARED_PROJECTION_VERSION,
                "assess_action": ACTION_ASSESSMENT_VERSION,
                "plan_observation": "cx67-observation-plan-v1",
                "advance_progress": "cx67-advance-progress-v1",
                "project_semantic_resources": SEMANTIC_RESOURCE_OPERATOR_VERSION,
            },
        )

    def _package_params(self, name: str) -> dict[str, Any]:
        return _mapping(self.manifest.parameter_package[name], f"parameters.{name}")

    def initialize_parameters(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        _exact_payload(payload, {
            "base_constants", "base_constants_version", "b0_binding", "correction",
        }, "initialize_parameters")
        base = _mapping(payload["base_constants"], "base_constants")
        base_version = _identifier(
            payload["base_constants_version"], "base_constants_version")
        b0 = _mapping(payload["b0_binding"], "b0_binding")
        if set(b0) != {"version", "enabled", "weights"}:
            raise ValueError("b0_binding fields do not match initialization contract")
        receipt = resolve_effective_constants(
            base,
            b0_version=_identifier(b0["version"], "b0_binding.version"),
            b0=_plain(b0["weights"], "b0_binding.weights"),
            b0_enabled=bool(b0["enabled"]),
            correction=_plain(payload["correction"], "correction"),
        )
        binding = {
            "version": PARAMETER_BINDING_VERSION,
            "base_constants_version": base_version,
            "b0_version": receipt["b0_version"],
            "b0_mapping_version": receipt["b0_mapping_version"],
            "correction_version": receipt["correction_version"],
            "effective_constants_version": receipt["effective_constants_version"],
            "effective_constants": receipt["effective_constants"],
        }
        return {"parameters": {
            "constant_binding": binding,
            "fingerprint": model_data_fingerprint(binding),
        }, "initialization_receipt": receipt}

    def initialize_state(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        _exact_payload(payload, {"initial_state"}, "initialize_state")
        raw = _plain(payload["initial_state"], "initial_state")
        if raw is not None and not isinstance(raw, dict):
            raise ValueError("initial_state must be a mapping or None")
        base = _root_vector(
            self.manifest.parameter_package["initial_state_default"],
            "initial_state_default")
        if raw is not None:
            supplied = raw.get("vars", raw)
            if not isinstance(supplied, dict):
                raise ValueError("initial_state.vars must be a mapping")
            unknown = set(supplied) - set(ROOT_ORDER)
            if unknown:
                raise ValueError(
                    "initial_state contains unknown axes: "
                    + ", ".join(sorted(unknown)))
            for axis, value in supplied.items():
                if isinstance(value, bool):
                    raise ValueError(f"initial_state.{axis} must be numeric")
                try:
                    numeric = float(value)
                except (TypeError, ValueError) as exc:
                    raise ValueError(f"initial_state.{axis} must be numeric") from exc
                if numeric != numeric or numeric in (float("inf"), float("-inf")):
                    raise ValueError(f"initial_state.{axis} must be finite")
                base[axis] = min(1.0, max(0.0, numeric))
        return {"state": base}

    def measure_event(self, *, payload, context) -> dict[str, Any]:
        context = _context(context)
        event_ref = context_fact_identity(context).event_ref
        if event_ref is None:
            raise ValueError("measure_event requires an event reference")
        keys = set(payload) if isinstance(payload, Mapping) else set()
        if keys == {"event_kind", "raw_measurement"}:
            mode = "raw"
        elif keys == {
            "event_kind", "committed_measurement", "measurement_source",
        }:
            mode = "committed"
        else:
            _exact_payload(payload, {"event_kind", "raw_measurement"}, "measure_event")
            raise AssertionError("unreachable")
        kind = _identifier(payload["event_kind"], "event_kind")
        committed = _identifier(
            context.metadata.get("committed_event_kind"), "committed event kind")
        if kind != committed:
            raise ValueError("measure_event direction differs from committed event")
        if mode == "committed":
            source = _mapping(payload["measurement_source"], "measurement_source")
            required = {
                "version", "source_event_id", "local_event_id",
                "projection_fingerprint",
            }
            if set(source) != required or source.get("version") != (
                    "cx67-committed-measurement-source-v1"):
                raise ValueError("committed measurement source fields are invalid")
            if source.get("source_event_id") != event_ref:
                raise ValueError("committed measurement source event differs from context")
            local_event_id = _identifier(
                source.get("local_event_id"), "measurement local event ID")
            if context.metadata.get("local_turn_id") != local_event_id:
                raise ValueError("committed measurement local event differs from context")
            projection = _mapping(
                payload["committed_measurement"], "committed_measurement")
            if model_data_fingerprint(projection) != _identifier(
                    source.get("projection_fingerprint"),
                    "measurement projection fingerprint"):
                raise ValueError("committed measurement projection fingerprint is invalid")
            validated = self._validate_committed_measurement(
                projection=projection,
                local_event_id=local_event_id,
                event_kind=kind,
            )
            return {"measurement": validated}
        raw = _plain(payload["raw_measurement"], "raw_measurement")
        if isinstance(raw, Mapping) and "axis_presence" in raw:
            raise ValueError("measure_event received an already-normalized measurement")
        result = coerce_projection(raw, turn_id=event_ref, event_kind=kind)
        return {"measurement": result.as_record() if result is not None else None}

    @staticmethod
    def _validate_committed_measurement(
        *, projection: Mapping[str, Any], local_event_id: str, event_kind: str,
    ) -> dict[str, Any]:
        record = _mapping(projection, "committed projection")
        presence = _mapping(record.get("axis_presence"), "axis_presence")
        if presence.get("version") != "semantic-axis-presence-v1":
            raise ValueError("committed projection axis presence is invalid")
        provided = presence.get("provided_axes")
        deltas = _mapping(record.get("variable_deltas"), "variable_deltas")
        if (
            not isinstance(provided, list)
            or len(set(provided)) != len(provided)
            or any(axis not in ROOT_ORDER for axis in provided)
            or set(deltas) != set(ROOT_ORDER)
        ):
            raise ValueError("committed projection axis presence is invalid")
        raw = dict(record)
        raw.pop("axis_presence", None)
        raw["variable_deltas"] = {axis: deltas[axis] for axis in provided}
        normalized = coerce_projection(
            raw, turn_id=local_event_id, event_kind=event_kind)
        if normalized is None or normalized.as_record() != record:
            raise ValueError("committed projection does not match reference normalization")
        return record

    def committed_measurement_evidence(
        self, *, projection, source_event_id, local_event_id, event_kind,
    ) -> dict[str, Any]:
        """Adapt one already committed projection without rerunning Agent 1."""
        source_event = _identifier(source_event_id, "source_event_id")
        local_event = _identifier(local_event_id, "local_event_id")
        kind = _identifier(event_kind, "event_kind")
        record = self._validate_committed_measurement(
            projection=projection,
            local_event_id=local_event,
            event_kind=kind,
        )
        projection_fingerprint = model_data_fingerprint(record)
        payload = {
            "event_kind": kind,
            "committed_measurement": record,
            "measurement_source": {
                "version": "cx67-committed-measurement-source-v1",
                "source_event_id": source_event,
                "local_event_id": local_event,
                "projection_fingerprint": projection_fingerprint,
            },
        }
        material = {
            "version": "committed-model-measurement-evidence-v1",
            "model_binding": InteractionModelBinding.from_manifest(
                self.manifest).to_record(),
            "source_event_id": source_event,
            "local_event_id": local_event,
            "event_kind": kind,
            "projection_fingerprint": projection_fingerprint,
            "measurement_payload": payload,
        }
        return dict(material, fingerprint=model_data_fingerprint(material))

    def advance_progress(self, *, payload, context) -> dict[str, Any]:
        context = _context(context)
        _exact_payload(
            payload, {"parameters", "state", "progress_delta"}, "advance_progress")
        binding = _constant_binding(payload["parameters"])
        state = _root_vector(payload["state"], "state")
        progress = _mapping(payload["progress_delta"], "progress_delta")
        progress_payload = _mapping(progress.get("payload"), "progress_delta.payload")
        if set(progress_payload) != {"elapsed_ms"}:
            raise ValueError("reference progress delta must contain only elapsed_ms")
        if context.temporal_position is None:
            raise ValueError("advance_progress requires a temporal position")
        if _plain(progress.get("binding"), "progress_delta.binding") != (
                context.temporal_position.binding.to_record()):
            raise ValueError("progress delta binding differs from invocation position")
        elapsed = progress_payload["elapsed_ms"]
        if isinstance(elapsed, bool) or not isinstance(elapsed, int) or elapsed < 0:
            raise ValueError("elapsed_ms must be a non-negative integer")
        if bool(progress.get("advanced")) != (elapsed > 0):
            raise ValueError("progress advanced flag disagrees with elapsed_ms")
        source_fingerprint = _identifier(
            progress.get("fingerprint"), "progress_delta.fingerprint")
        result = flow_root_time(
            binding["effective_constants"], state, elapsed,
            effective_constants_version=binding["effective_constants_version"],
            params=self._package_params("progress_flow"),
        )
        return {"progress_contribution": _contribution_record(
            kind="progress", x_before=state,
            delta=result.delta_x_time, x_after=result.x_after,
            operator_receipt=result.receipt,
            source_field="source_progress_fingerprint",
            source_value=source_fingerprint,
        )}

    def derive_event_contribution(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        expected = {"parameters", "state", "measurement"}
        if isinstance(payload, Mapping) and "progress_contribution" in payload:
            expected.add("progress_contribution")
        _exact_payload(
            payload, expected,
            "derive_event_contribution")
        binding = _constant_binding(payload["parameters"])
        state = _root_vector(payload["state"], "state")
        progress_value = payload.get("progress_contribution")
        if progress_value is not None:
            progress = _validated_contribution(progress_value, kind="progress")
            if not _same_roots(state, progress["x_before"]):
                raise ValueError(
                    "event progress contribution must continue current state")
            state = progress["x_after"]
        measurement = _mapping(payload["measurement"], "measurement")
        stimulus = _mapping(
            measurement.get("variable_deltas"), "measurement.variable_deltas")
        result = respond_to_root_stimulus(
            binding["effective_constants"], state, stimulus,
            effective_constants_version=binding["effective_constants_version"],
            params=self._package_params("event_response"),
        )
        return {"event_contribution": _contribution_record(
            kind="event", x_before=state,
            delta=result.delta_x_event, x_after=result.x_after,
            operator_receipt=result.receipt,
            source_field="source_measurement_fingerprint",
            source_value=model_data_fingerprint(measurement),
        )}

    def settle_state(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        _exact_payload(
            payload, {"state", "progress_contribution", "event_contribution"},
            "settle_state")
        state_before = _root_vector(payload["state"], "state")
        current = state_before
        applied: list[str] = []
        contribution_fingerprints: dict[str, str] = {}
        progress_value = payload["progress_contribution"]
        if progress_value is not None:
            progress = _validated_contribution(progress_value, kind="progress")
            if not _same_roots(current, progress["x_before"]):
                raise ValueError("progress contribution must continue state")
            current = progress["x_after"]
            applied.append("progress")
            contribution_fingerprints["progress"] = progress["fingerprint"]
        event_value = payload["event_contribution"]
        if event_value is not None:
            event = _validated_contribution(event_value, kind="event")
            if not _same_roots(current, event["x_before"]):
                raise ValueError("event contribution must continue the settled progress state")
            current = event["x_after"]
            applied.append("event")
            contribution_fingerprints["event"] = event["fingerprint"]
        record = {
            "version": STATE_SETTLEMENT_VERSION,
            "state_before": state_before,
            "state_candidate": current,
            "net_delta": {
                axis: current[axis] - state_before[axis] for axis in ROOT_ORDER
            },
            "applied_contributions": applied,
            "contribution_fingerprints": contribution_fingerprints,
            "state_commit_authority": False,
        }
        record["fingerprint"] = model_data_fingerprint(record)
        return {"settlement": record}

    def project_shared_semantics(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        _exact_payload(
            payload, {"parameters", "settlement"}, "project_shared_semantics")
        binding = _constant_binding(payload["parameters"])
        settlement = _verified_settlement(payload["settlement"])
        transition = relationship_transition(
            settlement["state_before"], settlement["state_candidate"])
        projection = project_continuous_roots(
            transition=transition,
            constant_binding=binding,
            params=self._package_params("shared_projection"),
        ).to_record()
        shared = {
            **projection,
            "version": SHARED_PROJECTION_VERSION,
            "source_settlement_fingerprint": settlement["fingerprint"],
            "source_state_candidate": settlement["state_candidate"],
        }
        shared["fingerprint"] = model_data_fingerprint(shared)
        return {"shared_projection": shared}

    def _observation_readout(self, payload, *, stage_name: str):
        _exact_payload(
            payload,
            {"shared_projection", "min_interval_ms", "max_interval_ms"},
            stage_name,
        )
        shared = _verified_shared_projection(payload["shared_projection"])
        low = payload["min_interval_ms"]
        high = payload["max_interval_ms"]
        if (isinstance(low, bool) or not isinstance(low, int)
                or isinstance(high, bool) or not isinstance(high, int)):
            raise ValueError("observation bounds must be integer milliseconds")
        opening = opening_readout(
            {"schedule_vector": shared["vector"]},
            params=self._package_params("action_projection"),
        )
        interval = next_observation_interval(
            opening, min_interval_ms=low, max_interval_ms=high)
        return shared, opening, interval

    def plan_observation(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        shared, _opening, interval = self._observation_readout(
            payload, stage_name="plan_observation")
        return {"observation_plan": {
            "version": "cx67-observation-plan-v1",
            "source_shared_projection_output_fingerprint": (
                model_data_fingerprint({"shared_projection": shared})),
            "observation_advice": {"after_ms": interval["h_next_ms"]},
            "observation_projection": interval,
            "action_assessment_authority": False,
            "observation_schedule_authority": False,
        }}

    def assess_action(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        shared, opening, interval = self._observation_readout(
            payload, stage_name="assess_action")
        return {"action_assessment": {
            "version": ACTION_ASSESSMENT_VERSION,
            "source_shared_projection_fingerprint": shared["fingerprint"],
            "action_kind": "autonomous_opening",
            "eligible": bool(opening["margin"] >= 0.0),
            "constraints": {
                "opening": opening,
                "observation_projection": interval,
            },
            "observation_advice": {"after_ms": interval["h_next_ms"]},
            "action_commit_authority": False,
        }}

    def project_semantic_resources(self, *, payload, context) -> dict[str, Any]:
        _context(context)
        _exact_payload(
            payload,
            {
                "shared_projection",
                "source_shared_projection_output_fingerprint",
                "committed_remainders",
            },
            "project_semantic_resources",
        )
        shared = _verified_shared_projection(payload["shared_projection"])
        source_fingerprint = _identifier(
            payload["source_shared_projection_output_fingerprint"],
            "source_shared_projection_output_fingerprint",
        )
        remainders = _mapping(
            payload["committed_remainders"], "committed_remainders")
        return project_expression_resources(
            shared_projection=shared,
            source_shared_projection_output_fingerprint=source_fingerprint,
            committed_remainders=remainders,
            params=self._package_params("semantic_resources"),
        )


__all__ = ("CxSixSevenV2Package",)
