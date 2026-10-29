"""Alpha 0.4 C2 canonical numeric projection and transient live-memory kernel.

The canonical interaction remains the only fact source.  This module freezes a
numeric projection of that fact and reconstructs one exact event's live
influence from its historical binding, the C1 Phi operator, real wall-clock
time and strictly-prior append-only recall residuals.

It does not accept text, retrieval scores, expression tokens, writer output,
tick state, Runner or UI state and performs no persistence.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from collections.abc import Mapping, Sequence

from .preference_parameters import CONSTANT_ORDER
from .interaction_geometry import ROOT_ORDER
from .root_dynamics import (
    HOUR_MS,
    LEVER_AXES,
    RECOVERABLE_AXES,
    ROOT_TIME_FLOW_VERSION,
    RootEventResponse,
    flow_root_time,
)
from .memory_numeric_contracts import (
    MemoryEventRef,
    MemoryRecallEvent,
    NeutralMemoryReadout,
)


CANONICAL_MEMORY_PROJECTION_SCHEMA = 1
CANONICAL_MEMORY_PROJECTION_VERSION = "canonical-memory-projection-v1"
LIVE_MEMORY_KERNEL_VERSION = "live-memory-kernel-phi-difference-v1"


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer timestamp")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer timestamp") from exc
    if parsed < 0 or parsed != value:
        raise ValueError(f"{field} must be a non-negative integer timestamp")
    return parsed


def _finite(value, field):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} must be finite")
    return parsed


def _vector(value, field, *, closed_unit=False, signed_unit=False):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    parsed = {}
    for axis in ROOT_ORDER:
        number = _finite(value[axis], f"{field}.{axis}")
        if closed_unit and not 0.0 <= number <= 1.0:
            raise ValueError(f"{field}.{axis} must be between zero and one")
        if signed_unit and not -1.0 <= number <= 1.0:
            raise ValueError(f"{field}.{axis} must be between minus one and one")
        parsed[axis] = number
    return parsed


def _constant_vector(value, field):
    if not isinstance(value, Mapping) or set(value) != set(CONSTANT_ORDER):
        raise ValueError(f"{field} must contain exactly the seven role constants")
    parsed = {}
    for axis in CONSTANT_ORDER:
        number = _finite(value[axis], f"{field}.{axis}")
        if not 0.0 <= number <= 1.0:
            raise ValueError(f"{field}.{axis} must be between zero and one")
        parsed[axis] = number
    return parsed


def _freeze(value, field="value"):
    if type(value) in (tuple, list) and all(type(item) is str for item in value):
        return tuple(value)
    if value is None or type(value) in (str, bool, int): return value
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}") for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} must contain only finite serializable values")
        return value
    raise ValueError(f"{field} must contain only serializable values")


def _thaw(value):
    if type(value) is tuple and all(type(item) is str for item in value):
        return list(value)
    if value is None or type(value) in (str, bool, int): return value
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _norm(vector):
    return math.sqrt(sum(float(vector[axis]) ** 2 for axis in ROOT_ORDER))


@dataclass(frozen=True)
class CanonicalMemoryProjection:
    """Frozen numeric view of one canonical committed InteractionEvent.

    This is not a second memory truth.  Every field either is the exact event
    identity, a version binding, or a C1 receipt-derived numeric fact.
    """

    event: MemoryEventRef
    constant_binding: Mapping[str, object]
    stimulus: Mapping[str, float]
    x_before: Mapping[str, float]
    delta_x_event: Mapping[str, float]
    x_after: Mapping[str, float]
    stimulus_receipt_ref: str
    root_response_receipt_ref: str
    root_response_operator_version: str
    root_response_parameter_version: str
    root_time_operator_version: str
    root_time_parameter_version: str

    def __post_init__(self):
        if not isinstance(self.event, MemoryEventRef):
            raise ValueError("event must be a MemoryEventRef")
        binding = self.constant_binding
        if not isinstance(binding, Mapping):
            raise ValueError("constant_binding must be a mapping")
        required = {
            "version", "base_constants_version", "b0_version",
            "b0_mapping_version", "correction_version",
            "effective_constants_version", "effective_constants",
        }
        missing = required - set(binding)
        if missing:
            raise ValueError("constant_binding is missing: " + ", ".join(sorted(missing)))
        for field in ("version", "base_constants_version", "b0_version",
                      "effective_constants_version"):
            _identifier(binding.get(field), f"constant_binding.{field}")
        if binding.get("b0_mapping_version") is not None:
            _identifier(binding.get("b0_mapping_version"),
                        "constant_binding.b0_mapping_version")
        if binding.get("correction_version") is not None:
            _identifier(binding.get("correction_version"),
                        "constant_binding.correction_version")
        constants = _constant_vector(
            binding.get("effective_constants"),
            "constant_binding.effective_constants")
        normalized_binding = dict(binding)
        normalized_binding["effective_constants"] = constants
        object.__setattr__(self, "constant_binding", _freeze(
            normalized_binding, "constant_binding"))

        stimulus = _vector(self.stimulus, "stimulus", signed_unit=True)
        before = _vector(self.x_before, "x_before", closed_unit=True)
        delta = _vector(self.delta_x_event, "delta_x_event", signed_unit=True)
        after = _vector(self.x_after, "x_after", closed_unit=True)
        for axis in ROOT_ORDER:
            if not math.isclose(before[axis] + delta[axis], after[axis],
                                rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("x_after must equal x_before plus actual delta_x_event")
        for field, value in (("stimulus", stimulus), ("x_before", before),
                             ("delta_x_event", delta), ("x_after", after)):
            object.__setattr__(self, field, MappingProxyType(value))
        for field in (
                "stimulus_receipt_ref", "root_response_receipt_ref",
                "root_response_operator_version", "root_response_parameter_version",
                "root_time_operator_version", "root_time_parameter_version"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))

    @property
    def effective_constants_version(self):
        return str(self.constant_binding["effective_constants_version"])

    @property
    def effective_constants(self):
        return self.constant_binding["effective_constants"]

    def to_record(self):
        return {
            "schema_version": CANONICAL_MEMORY_PROJECTION_SCHEMA,
            "projection_version": CANONICAL_MEMORY_PROJECTION_VERSION,
            "event": self.event.to_record(),
            "constant_binding": _thaw(self.constant_binding),
            "stimulus": dict(self.stimulus),
            "x_before": dict(self.x_before),
            "delta_x_event": dict(self.delta_x_event),
            "x_after": dict(self.x_after),
            "stimulus_receipt_ref": self.stimulus_receipt_ref,
            "root_response_receipt_ref": self.root_response_receipt_ref,
            "root_response_operator_version": self.root_response_operator_version,
            "root_response_parameter_version": self.root_response_parameter_version,
            "root_time_operator_version": self.root_time_operator_version,
            "root_time_parameter_version": self.root_time_parameter_version,
        }

    @classmethod
    def from_record(cls, record):
        if not isinstance(record, Mapping):
            raise ValueError("canonical projection record must be a mapping")
        required = {
            "schema_version", "projection_version", "event", "constant_binding",
            "stimulus", "x_before", "delta_x_event", "x_after",
            "stimulus_receipt_ref", "root_response_receipt_ref",
            "root_response_operator_version", "root_response_parameter_version",
            "root_time_operator_version", "root_time_parameter_version",
        }
        if set(record) != required:
            raise ValueError("canonical projection record fields do not match schema")
        if record.get("schema_version") != CANONICAL_MEMORY_PROJECTION_SCHEMA:
            raise ValueError("canonical projection schema_version is unsupported")
        if record.get("projection_version") != CANONICAL_MEMORY_PROJECTION_VERSION:
            raise ValueError("canonical projection version is unsupported")
        return cls(
            event=MemoryEventRef.from_record(record["event"]),
            constant_binding=record["constant_binding"],
            stimulus=record["stimulus"],
            x_before=record["x_before"],
            delta_x_event=record["delta_x_event"],
            x_after=record["x_after"],
            stimulus_receipt_ref=record["stimulus_receipt_ref"],
            root_response_receipt_ref=record["root_response_receipt_ref"],
            root_response_operator_version=record["root_response_operator_version"],
            root_response_parameter_version=record["root_response_parameter_version"],
            root_time_operator_version=record["root_time_operator_version"],
            root_time_parameter_version=record["root_time_parameter_version"],
        )


def project_canonical_memory_event(
        *, event_id: str, committed_at_ms: int,
        constant_binding: Mapping[str, object], stimulus_receipt_ref: str,
        root_response_receipt_ref: str, response: RootEventResponse,
        root_time_parameter_version: str) -> CanonicalMemoryProjection:
    """Project one committed C1 response into a numeric memory view."""
    if not isinstance(response, RootEventResponse):
        raise ValueError("response must be a committed RootEventResponse")
    receipt = response.receipt
    inputs = receipt.get("inputs")
    if not isinstance(inputs, Mapping):
        raise ValueError("root response receipt is missing inputs")
    if not isinstance(constant_binding, Mapping):
        raise ValueError("constant_binding must be a mapping")
    bound_version = str(constant_binding.get("effective_constants_version") or "")
    if bound_version != str(receipt.get("effective_constants_version") or ""):
        raise ValueError("root response and constant binding versions do not match")
    bound_constants = _constant_vector(
        constant_binding.get("effective_constants"),
        "constant_binding.effective_constants")
    receipt_constants = _constant_vector(inputs.get("C_effective"),
                                         "root_response.C_effective")
    if any(not math.isclose(bound_constants[axis], receipt_constants[axis],
                            rel_tol=0.0, abs_tol=1e-12)
           for axis in CONSTANT_ORDER):
        raise ValueError("root response and constant binding values do not match")
    return CanonicalMemoryProjection(
        event=MemoryEventRef(_identifier(event_id, "event_id"),
                             _timestamp(committed_at_ms, "committed_at_ms")),
        constant_binding=constant_binding,
        stimulus=inputs.get("stimulus"),
        x_before=inputs.get("X_before"),
        delta_x_event=response.delta_x_event,
        x_after=response.x_after,
        stimulus_receipt_ref=stimulus_receipt_ref,
        root_response_receipt_ref=root_response_receipt_ref,
        root_response_operator_version=receipt.get("operator_version"),
        root_response_parameter_version=receipt.get("parameter_version"),
        root_time_operator_version=ROOT_TIME_FLOW_VERSION,
        root_time_parameter_version=root_time_parameter_version,
    )


def _projection_index(projections: Sequence[CanonicalMemoryProjection]):
    indexed = {}
    for projection in projections:
        if not isinstance(projection, CanonicalMemoryProjection):
            raise ValueError("projections must contain CanonicalMemoryProjection")
        event_id = projection.event.event_id
        if event_id in indexed:
            raise ValueError("duplicate exact event ID in canonical projections")
        indexed[event_id] = projection
    return indexed


def _strictly_prior_recall_events(recall_events, projections_by_id, target_id, as_of_ms):
    all_by_id = {}
    for event in recall_events:
        if not isinstance(event, MemoryRecallEvent):
            raise ValueError("recall_events must contain MemoryRecallEvent")
        if event.recall_event_id in all_by_id:
            raise ValueError("duplicate exact recall event ID")
        all_by_id[event.recall_event_id] = event

    eligible = {
        event_id: event for event_id, event in all_by_id.items()
        if event.committed_at_ms < as_of_ms
    }
    seeds = sorted(
        (event for event in eligible.values() if event.target_event_id == target_id),
        key=lambda row: (row.committed_at_ms, row.recall_event_id),
    )
    visiting = set()
    visited = set()
    ordered = []

    def visit(recall_id):
        if recall_id in visited:
            return
        if recall_id in visiting:
            raise ValueError("recall residual graph must be acyclic")
        event = eligible.get(recall_id)
        if event is None:
            raise ValueError("reachable recall residual must be strictly prior")
        target = projections_by_id.get(event.target_event_id)
        trigger = projections_by_id.get(event.trigger_event_id)
        if target is None or trigger is None:
            raise ValueError("recall residual references unknown canonical exact ID")
        if not (target.event.committed_at_ms < trigger.event.committed_at_ms
                <= event.committed_at_ms):
            raise ValueError("recall residual must point from trigger to past target")
        visiting.add(recall_id)
        for parent_id in event.parent_recall_event_ids:
            parent = eligible.get(parent_id)
            if parent is None or parent.committed_at_ms >= event.committed_at_ms:
                raise ValueError("recall residual parent must be known and earlier")
            visit(parent_id)
        visiting.remove(recall_id)
        visited.add(recall_id)
        ordered.append(event)

    for seed in seeds:
        visit(seed.recall_event_id)
    return tuple(ordered)


def reconstruct_live_memory(
        *, projections: Sequence[CanonicalMemoryProjection], target_event_id: str,
        as_of_ms: int, recall_events: Sequence[MemoryRecallEvent] = (),
        root_time_parameters_by_version: Mapping[str, Mapping[str, object]],
        bound_radius: float, parameter_version: str) -> NeutralMemoryReadout:
    """Reconstruct one exact event's transient live influence at ``as_of_ms``."""
    target_id = _identifier(target_event_id, "target_event_id")
    evaluation_time = _timestamp(as_of_ms, "as_of_ms")
    kernel_parameter_version = _identifier(parameter_version, "parameter_version")
    radius = _finite(bound_radius, "bound_radius")
    if radius <= 0.0:
        raise ValueError("bound_radius must be positive")
    if not isinstance(root_time_parameters_by_version, Mapping):
        raise ValueError("root_time_parameters_by_version must be a mapping")

    indexed = _projection_index(tuple(projections))
    target = indexed.get(target_id)
    if target is None:
        raise ValueError("target_event_id is not a canonical exact event ID")
    if target.event.committed_at_ms > evaluation_time:
        raise ValueError("future target is not visible at evaluation time")
    if target.root_time_operator_version != ROOT_TIME_FLOW_VERSION:
        raise ValueError("target root time operator version is unsupported")
    time_params = root_time_parameters_by_version.get(
        target.root_time_parameter_version)
    if not isinstance(time_params, Mapping):
        raise ValueError("target root time parameter version cannot be resolved")
    if str(time_params.get("version") or "") != target.root_time_parameter_version:
        raise ValueError("resolved root time parameter version does not match target")

    age_ms = evaluation_time - target.event.committed_at_ms
    before_flow = flow_root_time(
        target.effective_constants, target.x_before, age_ms,
        effective_constants_version=target.effective_constants_version,
        params=time_params)
    after_flow = flow_root_time(
        target.effective_constants, target.x_after, age_ms,
        effective_constants_version=target.effective_constants_version,
        params=time_params)
    base = {
        axis: after_flow.x_after[axis] - before_flow.x_after[axis]
        for axis in ROOT_ORDER
    }

    prior = _strictly_prior_recall_events(
        tuple(recall_events), indexed, target_id, evaluation_time)
    residual_terms = []
    residual_vectors = []
    for event in prior:
        propagation_binding = event.propagation_binding
        if not isinstance(propagation_binding, Mapping):
            raise ValueError(
                "Alpha 0.4 live kernel requires residual propagation binding")
        if propagation_binding.get("root_time_operator_version") != ROOT_TIME_FLOW_VERSION:
            raise ValueError("residual root time operator version is unsupported")
        residual_parameter_version = _identifier(
            propagation_binding.get("root_time_parameter_version"),
            "residual root_time_parameter_version")
        residual_time_params = root_time_parameters_by_version.get(
            residual_parameter_version)
        if not isinstance(residual_time_params, Mapping):
            raise ValueError("residual root time parameter version cannot be resolved")
        if str(residual_time_params.get("version") or "") != residual_parameter_version:
            raise ValueError("resolved residual time parameters do not match binding")
        residual_constant_binding = propagation_binding.get("constant_binding")
        if not isinstance(residual_constant_binding, Mapping):
            raise ValueError("residual constant binding is unavailable")
        residual_constants_version = _identifier(
            residual_constant_binding.get("effective_constants_version"),
            "residual effective_constants_version")
        residual_constants = _constant_vector(
            residual_constant_binding.get("effective_constants"),
            "residual effective_constants")
        residual_flow = flow_root_time(
            residual_constants, target.x_before, 0,
            effective_constants_version=residual_constants_version,
            params=residual_time_params)
        rates = residual_flow.receipt["effective_rate_per_hour"]
        residual_age_ms = evaluation_time - event.committed_at_ms
        factors = {
            axis: (1.0 if axis in LEVER_AXES else math.exp(
                -float(rates[axis]) * residual_age_ms / HOUR_MS))
            for axis in ROOT_ORDER
        }
        contribution = {
            axis: float(event.reconsolidation_delta[axis]) * factors[axis]
            for axis in ROOT_ORDER
        }
        residual_vectors.append(contribution)
        residual_terms.append({
            "recall_event_id": event.recall_event_id,
            "target_event_id": event.target_event_id,
            "trigger_event_id": event.trigger_event_id,
            "parent_recall_event_ids": tuple(event.parent_recall_event_ids),
            "committed_at_ms": event.committed_at_ms,
            "age_ms": residual_age_ms,
            "effective_constants_version": residual_constants_version,
            "root_time_operator_version": ROOT_TIME_FLOW_VERSION,
            "root_time_parameter_version": residual_parameter_version,
            "raw_residual": dict(event.reconsolidation_delta),
            "phi_retention_factors": factors,
            "propagated_residual": contribution,
        })

    pre_bound = {
        axis: base[axis] + sum(vector[axis] for vector in residual_vectors)
        for axis in ROOT_ORDER
    }
    raw_norm = _norm(pre_bound)
    bound_factor = max(1.0, raw_norm / radius)
    live = {axis: pre_bound[axis] / bound_factor for axis in ROOT_ORDER}
    magnitude = _norm(live) / radius
    recall_ids = tuple(event.recall_event_id for event in prior)
    receipt = {
        "operator_version": LIVE_MEMORY_KERNEL_VERSION,
        "parameter_version": kernel_parameter_version,
        "target_event_id": target_id,
        "target_committed_at_ms": target.event.committed_at_ms,
        "evaluation_time_ms": evaluation_time,
        "age_ms": age_ms,
        "canonical_projection_version": CANONICAL_MEMORY_PROJECTION_VERSION,
        "effective_constants_version": target.effective_constants_version,
        "root_response_operator_version": target.root_response_operator_version,
        "root_response_parameter_version": target.root_response_parameter_version,
        "root_time_operator_version": target.root_time_operator_version,
        "root_time_parameter_version": target.root_time_parameter_version,
        "stimulus_receipt_ref": target.stimulus_receipt_ref,
        "root_response_receipt_ref": target.root_response_receipt_ref,
        "X_before": dict(target.x_before),
        "Delta_X_event": dict(target.delta_x_event),
        "X_after": dict(target.x_after),
        "phi_before_event": dict(before_flow.x_after),
        "phi_after_event": dict(after_flow.x_after),
        "base_live_vector": base,
        "prior_recall_event_ids": recall_ids,
        "residual_propagation_binding_rule": "recall_event_own_root_time_binding",
        "residual_terms": tuple(residual_terms),
        "pre_bound_vector": pre_bound,
        "bound_radius": radius,
        "bound_factor": bound_factor,
        "final_live_vector": live,
        "normalized_magnitude": magnitude,
        "strictly_prior_only": True,
        "derived_only": True,
    }
    lineage = (target_id, *recall_ids)
    return NeutralMemoryReadout(
        event=target.event,
        as_of_ms=evaluation_time,
        live_vector=live,
        magnitude=magnitude,
        formula_version=LIVE_MEMORY_KERNEL_VERSION,
        lineage=lineage,
        receipt=receipt,
    )
