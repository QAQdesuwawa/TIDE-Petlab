"""Alpha 0.4 C2-R2 forward-DAG live memory readout.

This is an isolated pure layer.  It reads canonical numeric projections and
committed ID-only influence transactions, resolves every edge's frozen Phi
binding, and reconstructs derived live influence at one absolute wall-clock
time.  It does not select memories, write P4, hydrate text, call providers, or
touch Runner/UI state.
"""
from __future__ import annotations

import math
from types import MappingProxyType
from typing import Mapping, Sequence

from .preference_parameters import CONSTANT_ORDER
from .query_budget import check as check_query_budget
from .interaction_geometry import ROOT_ORDER
from .root_dynamics import (
    HOUR_MS,
    LEVER_AXES,
    ROOT_TIME_FLOW_VERSION,
    flow_root_time,
)
from .memory_numeric_contracts import (
    MemoryEventRef,
    MemoryInfluenceGraphIndex,
    MemoryInfluenceTransaction,
    NeutralMemoryReadout,
)
from .memory_projection import CanonicalMemoryProjection
from .memory_runtime_order import validate_node_ordinals, order_record


FORWARD_MEMORY_READOUT_VERSION = "forward-memory-live-readout-v1"
_INTRINSIC_BOUND_RADIUS = math.sqrt(len(ROOT_ORDER))
_COLLINEAR_ABS_TOL = 1e-9


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


def _norm(vector):
    return math.sqrt(sum(float(vector[axis]) ** 2 for axis in ROOT_ORDER))


def forward_live_readout_ref(node_id, as_of_ms, parameter_version, *, runtime_ordinal=None):
    """Return the deterministic receipt identity stored by later edges."""
    node = _identifier(node_id, "node_id")
    at_ms = _timestamp(as_of_ms, "as_of_ms")
    version = _identifier(parameter_version, "parameter_version")
    suffix = "" if runtime_ordinal is None else f":ordinal:{_timestamp(runtime_ordinal, 'runtime_ordinal')}"
    return f"forward-live:{version}:{at_ms}:{node}{suffix}"


def _parameter_package(params):
    if not isinstance(params, Mapping):
        raise ValueError("an explicit versioned forward-readout package is required")
    version = _identifier(params.get("version"), "params.version")
    radius = _finite(params.get("bound_radius"), "params.bound_radius")
    if radius <= 0.0:
        raise ValueError("params.bound_radius must be positive")
    return version, radius


def _projection_index(projections):
    indexed = {}
    for projection in projections:
        if not isinstance(projection, CanonicalMemoryProjection):
            raise ValueError("projections must contain CanonicalMemoryProjection")
        event_id = projection.event.event_id
        if event_id in indexed:
            raise ValueError("duplicate exact event ID in canonical projections")
        indexed[event_id] = projection
    return indexed


def _transaction_index(transactions):
    indexed = {}
    for transaction in transactions:
        if not isinstance(transaction, MemoryInfluenceTransaction):
            raise ValueError("transactions must contain MemoryInfluenceTransaction")
        if transaction.child_anchor_id in indexed:
            raise ValueError("a child anchor can have only one influence transaction")
        indexed[transaction.child_anchor_id] = transaction
    return indexed


def _resolve_time_parameters(version, packages):
    if not isinstance(packages, Mapping):
        raise ValueError("root_time_parameters_by_version must be a mapping")
    package = packages.get(version)
    if not isinstance(package, Mapping):
        raise ValueError("root time parameter version cannot be resolved")
    if str(package.get("version") or "") != version:
        raise ValueError("resolved root time parameters do not match binding")
    return package


def _intrinsic_live(projection, evaluation_time, time_packages):
    if projection.root_time_operator_version != ROOT_TIME_FLOW_VERSION:
        raise ValueError("canonical projection root time operator is unsupported")
    params = _resolve_time_parameters(
        projection.root_time_parameter_version, time_packages)
    age_ms = evaluation_time - projection.event.committed_at_ms
    before = flow_root_time(
        projection.effective_constants,
        projection.x_before,
        age_ms,
        effective_constants_version=projection.effective_constants_version,
        params=params,
    )
    after = flow_root_time(
        projection.effective_constants,
        projection.x_after,
        age_ms,
        effective_constants_version=projection.effective_constants_version,
        params=params,
    )
    vector = {
        axis: float(after.x_after[axis]) - float(before.x_after[axis])
        for axis in ROOT_ORDER
    }
    if _norm(vector) > _INTRINSIC_BOUND_RADIUS + 1e-9:
        raise ValueError("canonical intrinsic transition exceeds its root-space bound")
    return vector, {
        "event_id": projection.event.event_id,
        "committed_at_ms": projection.event.committed_at_ms,
        "age_ms": age_ms,
        "effective_constants_version": projection.effective_constants_version,
        "root_time_operator_version": projection.root_time_operator_version,
        "root_time_parameter_version": projection.root_time_parameter_version,
        "stimulus_receipt_ref": projection.stimulus_receipt_ref,
        "root_response_receipt_ref": projection.root_response_receipt_ref,
        "phi_before_event": dict(before.x_after),
        "phi_after_event": dict(after.x_after),
        "intrinsic_live_vector": vector,
    }


def _resolve_propagation_binding(edge, bindings, time_packages):
    if not isinstance(bindings, Mapping):
        raise ValueError("propagation_bindings_by_ref must be a mapping")
    binding = bindings.get(edge.propagation_binding_ref)
    if not isinstance(binding, Mapping):
        raise ValueError(
            f"propagation binding {edge.propagation_binding_ref!r} cannot be resolved")
    required = {
        "version", "binding_ref", "constant_binding",
        "root_time_operator_version", "root_time_parameter_version",
    }
    missing = required - set(binding)
    if missing:
        raise ValueError("propagation binding is missing: " + ", ".join(sorted(missing)))
    _identifier(binding.get("version"), "propagation binding version")
    if binding.get("binding_ref") != edge.propagation_binding_ref:
        raise ValueError("resolved propagation binding identity does not match edge")
    if binding.get("root_time_operator_version") != ROOT_TIME_FLOW_VERSION:
        raise ValueError("propagation root time operator is unsupported")
    parameter_version = _identifier(
        binding.get("root_time_parameter_version"),
        "propagation root_time_parameter_version",
    )
    time_params = _resolve_time_parameters(parameter_version, time_packages)
    constant_binding = binding.get("constant_binding")
    if not isinstance(constant_binding, Mapping):
        raise ValueError("propagation constant binding is unavailable")
    constant_required = {
        "version", "base_constants_version", "b0_version",
        "b0_mapping_version", "correction_version",
        "effective_constants_version", "effective_constants",
    }
    constant_missing = constant_required - set(constant_binding)
    if constant_missing:
        raise ValueError(
            "propagation constant binding is missing: "
            + ", ".join(sorted(constant_missing)))
    _identifier(
        constant_binding.get("version"),
        "propagation constant_binding.version",
    )
    _identifier(
        constant_binding.get("base_constants_version"),
        "propagation base_constants_version",
    )
    _identifier(
        constant_binding.get("b0_version"),
        "propagation b0_version",
    )
    mapping_version = constant_binding.get("b0_mapping_version")
    if mapping_version is None:
        # The original parameter resolver publishes None when B0 is disabled.
        # Verify its exact version binding; never invent a mapping or drop edges.
        from .preference_parameters import VERSION as constants_contract_version
        expected_disabled = (
            f"{constants_contract_version}:{constant_binding['b0_version']}:disabled:"
            f"{constant_binding.get('correction_version') or 'base'}"
        )
        if constant_binding.get("effective_constants_version") != expected_disabled:
            raise ValueError("propagation b0_mapping_version is missing without a disabled source binding")
    else:
        _identifier(mapping_version, "propagation b0_mapping_version")
    correction_version = constant_binding.get("correction_version")
    if correction_version is not None:
        _identifier(correction_version, "propagation correction_version")
    constants_version = _identifier(
        constant_binding.get("effective_constants_version"),
        "propagation effective_constants_version",
    )
    constants = _constant_vector(
        constant_binding.get("effective_constants"),
        "propagation effective_constants",
    )
    return binding, constants, constants_version, parameter_version, time_params


def _propagate_edge_delta(edge, *, age_ms, bindings, time_packages):
    binding, constants, constants_version, parameter_version, time_params = (
        _resolve_propagation_binding(edge, bindings, time_packages)
    )
    rate_probe = flow_root_time(
        constants,
        {axis: 0.0 for axis in ROOT_ORDER},
        0,
        effective_constants_version=constants_version,
        params=time_params,
    )
    rates = rate_probe.receipt["effective_rate_per_hour"]
    factors = {
        axis: (
            1.0 if axis in LEVER_AXES
            else math.exp(-float(rates[axis]) * age_ms / HOUR_MS)
        )
        for axis in ROOT_ORDER
    }
    propagated = {
        axis: float(edge.actual_delta[axis]) * factors[axis]
        for axis in ROOT_ORDER
    }
    return propagated, {
        "binding_version": binding["version"],
        "effective_constants_version": constants_version,
        "root_time_operator_version": ROOT_TIME_FLOW_VERSION,
        "root_time_parameter_version": parameter_version,
        "phi_retention_factors": factors,
    }


def _parent_scale(parent_vector, edge_vector):
    ratios = []
    for axis in ROOT_ORDER:
        parent = float(parent_vector[axis])
        actual = float(edge_vector[axis])
        if abs(parent) <= _COLLINEAR_ABS_TOL:
            if abs(actual) > _COLLINEAR_ABS_TOL:
                raise ValueError("edge actual delta is not derived from its parent live readout")
            continue
        ratios.append(actual / parent)
    if not ratios:
        if any(abs(float(edge_vector[axis])) > _COLLINEAR_ABS_TOL
               for axis in ROOT_ORDER):
            raise ValueError("zero parent readout cannot produce a nonzero edge")
        return 0.0
    scale = sum(ratios) / len(ratios)
    if scale < -_COLLINEAR_ABS_TOL:
        raise ValueError("memory influence edge cannot reverse its parent live direction")
    if any(not math.isclose(ratio, scale, rel_tol=1e-8,
                            abs_tol=_COLLINEAR_ABS_TOL)
           for ratio in ratios):
        raise ValueError("edge actual delta is not one global scale of parent live readout")
    return max(0.0, scale)


def reconstruct_forward_memory_graph(
        *, projections: Sequence[CanonicalMemoryProjection],
        transactions: Sequence[MemoryInfluenceTransaction],
        as_of_ms: int,
        propagation_bindings_by_ref: Mapping[str, Mapping[str, object]],
        root_time_parameters_by_version: Mapping[str, Mapping[str, object]],
        params: Mapping[str, object], canonical_node_ordinals=None, as_of_runtime_ordinal=None, receipt_format="full"):
    """Reconstruct every visible node without expanding causal paths.

    Incoming edges are visible only after their committed child time.  Their
    stored P4-applied deltas are propagated from that child time with the
    edge's own frozen Phi binding.  Parent snapshots are recursively rebuilt at
    the exact child time solely to validate and expose lineage; they are not
    added again, which would double-count the already-applied edge delta.
    """
    if receipt_format not in {"full", "source-reference-v1"}:
        raise ValueError("unsupported forward readout receipt format")
    evaluation_time = _timestamp(as_of_ms, "as_of_ms")
    parameter_version, radius = _parameter_package(params)
    projections_by_id = _projection_index(tuple(projections))
    transactions = tuple(transactions)
    transactions_by_child = _transaction_index(transactions)
    canonical_times = {
        event_id: projection.event.committed_at_ms
        for event_id, projection in projections_by_id.items()
    }
    node_ordinals = (None if canonical_node_ordinals is None else
                     validate_node_ordinals(canonical_times, canonical_node_ordinals))
    if node_ordinals is not None:
        if (isinstance(as_of_runtime_ordinal, bool) or
                not isinstance(as_of_runtime_ordinal, int) or as_of_runtime_ordinal < 0 or
                any(value > as_of_runtime_ordinal for value in node_ordinals.values())):
            raise ValueError("memory nodes exceed committed runtime ordinal")
    elif as_of_runtime_ordinal is not None:
        raise ValueError("memory evaluation ordinal requires canonical order")
    graph = MemoryInfluenceGraphIndex(
        canonical_node_times=canonical_times,
        transactions=transactions,
        canonical_node_ordinals=node_ordinals,
    )
    node_times = dict(canonical_times)
    node_kinds = {node_id: "interaction" for node_id in canonical_times}
    for transaction in transactions:
        node_times[transaction.child_anchor_id] = transaction.committed_at_ms
        node_kinds[transaction.child_anchor_id] = transaction.child_anchor_kind

    visible_order = tuple(
        node_id for node_id, _ in sorted(
            ((node_id, committed_at) for node_id, committed_at in node_times.items()
             if committed_at <= evaluation_time),
            key=lambda item: (item[1], node_ordinals[item[0]] if node_ordinals is not None else item[0]),
        )
    )
    memo = {}
    visiting = set()

    def build(node_id, at_ms, at_ordinal=None):
        check_query_budget()
        key = (node_id, at_ms, at_ordinal)
        if key in memo:
            return memo[key]
        if key in visiting:
            raise ValueError("forward memory graph must be acyclic")
        node_time = node_times.get(node_id)
        if node_time is None:
            raise ValueError("forward readout references an unknown node")
        if node_time > at_ms:
            raise ValueError("future node is not visible at evaluation time")
        if node_ordinals is not None and node_ordinals[node_id] > at_ordinal:
            raise ValueError("future node is not visible at evaluation ordinal")
        visiting.add(key)

        projection = projections_by_id.get(node_id)
        if projection is not None:
            intrinsic, intrinsic_receipt = _intrinsic_live(
                projection, at_ms, root_time_parameters_by_version)
        elif node_kinds[node_id] == "silent_recall":
            intrinsic = {axis: 0.0 for axis in ROOT_ORDER}
            intrinsic_receipt = {
                "event_id": node_id,
                "committed_at_ms": node_time,
                "age_ms": at_ms - node_time,
                "intrinsic_live_vector": dict(intrinsic),
                "silent_anchor": True,
            }
        else:
            raise ValueError("interaction node has no canonical numeric projection")

        edge_terms = []
        incoming_vectors = []
        lineage_edges = set()
        transaction = transactions_by_child.get(node_id)
        edge_visible = (transaction is not None and transaction.committed_at_ms < at_ms)
        if transaction is not None and node_ordinals is not None:
            edge_visible = (transaction.committed_at_ms <= at_ms and node_ordinals[node_id] <= at_ordinal)
        if edge_visible:
            for edge in graph.incoming(node_id):
                check_query_budget()
                parent_ordinal = None if node_ordinals is None else node_ordinals[node_id] - 1
                parent = build(edge.parent_node_id, transaction.committed_at_ms, parent_ordinal)
                expected_ref = forward_live_readout_ref(
                    edge.parent_node_id,
                    transaction.committed_at_ms,
                    parameter_version,
                    runtime_ordinal=parent_ordinal,
                )
                legacy_ref = forward_live_readout_ref(edge.parent_node_id,
                    transaction.committed_at_ms, parameter_version)
                legacy_valid = (node_times[edge.parent_node_id] < transaction.committed_at_ms and
                                edge.parent_live_readout_ref == legacy_ref)
                if edge.parent_live_readout_ref != expected_ref and not legacy_valid:
                    raise ValueError("edge parent live readout ref does not match replay")
                parent_scale = _parent_scale(parent.live_vector, edge.actual_delta)
                age_ms = at_ms - transaction.committed_at_ms
                propagated, propagation_receipt = _propagate_edge_delta(
                    edge,
                    age_ms=age_ms,
                    bindings=propagation_bindings_by_ref,
                    time_packages=root_time_parameters_by_version,
                )
                parent_lineage = tuple(parent.receipt.get("lineage_edge_ids", ()))
                lineage_edges.update(parent_lineage)
                lineage_edges.add(edge.edge_id)
                incoming_vectors.append(propagated)
                edge_terms.append({
                    "edge_id": edge.edge_id,
                    "parent_node_id": edge.parent_node_id,
                    "child_anchor_id": node_id,
                    "child_committed_at_ms": transaction.committed_at_ms,
                    "age_ms": age_ms,
                    "parent_live_readout_ref": edge.parent_live_readout_ref,
                    "parent_lineage_edge_ids": parent_lineage,
                    "p4_receipt_ref": edge.p4_receipt_ref,
                    "propagation_binding_ref": edge.propagation_binding_ref,
                    "edge_formula_version": edge.formula_version,
                    "actual_delta": dict(edge.actual_delta),
                    "applied_parent_scale": parent_scale,
                    "propagated_delta": propagated,
                    "propagation_receipt": propagation_receipt,
                })

        pre_bound = {
            axis: intrinsic[axis] + sum(vector[axis] for vector in incoming_vectors)
            for axis in ROOT_ORDER
        }
        raw_norm = _norm(pre_bound)
        bound_factor = max(1.0, raw_norm / radius)
        live = {axis: pre_bound[axis] / bound_factor for axis in ROOT_ORDER}
        magnitude = _norm(live) / radius
        incoming_ids = tuple(term["edge_id"] for term in edge_terms)
        lineage_ids = tuple(sorted(lineage_edges))
        readout_ref = forward_live_readout_ref(node_id, at_ms, parameter_version,
                                              runtime_ordinal=at_ordinal)
        receipt = {
            "operator_version": FORWARD_MEMORY_READOUT_VERSION,
            "parameter_version": parameter_version,
            "readout_ref": readout_ref,
            "node_id": node_id,
            "node_kind": node_kinds[node_id],
            "node_committed_at_ms": node_time,
            "evaluation_time_ms": at_ms,
            "intrinsic_term": intrinsic_receipt,
            "incoming_edge_ids": incoming_ids,
            "incoming_edge_terms": tuple(edge_terms),
            "lineage_edge_ids": lineage_ids,
            "pre_bound_vector": pre_bound,
            "bound_radius": radius,
            "bound_factor": bound_factor,
            "final_live_vector": live,
            "normalized_magnitude": magnitude,
            "strictly_prior_edges_only": True,
            "path_expansion_performed": False,
            "topological_order": tuple(
                candidate for candidate in (visible_order if receipt_format == "full" else ())
                if node_times[candidate] <= at_ms and
                   (node_ordinals is None or node_ordinals[candidate] <= at_ordinal)
            ),
            "derived_only": True,
        }
        if receipt_format == "source-reference-v1":
            # The canonical source and its frozen bindings were validated above.
            # Full diagnostic terms are reproducible; do not freeze/copy them
            # into every operational readout. Keep phi_before for projection.
            receipt = {
                "readout_ref": readout_ref,
                "intrinsic_term": {"phi_before_event": intrinsic_receipt.get("phi_before_event")},
                "lineage_edge_ids": lineage_ids,
                "incoming_edge_ids": incoming_ids,
            }
        if node_ordinals is not None:
            receipt["runtime_order"] = order_record(node_ordinals[node_id])
            receipt["evaluation_runtime_ordinal"] = at_ordinal
        result = NeutralMemoryReadout(
            event=MemoryEventRef(node_id, node_time),
            as_of_ms=at_ms,
            live_vector=live,
            magnitude=magnitude,
            formula_version=FORWARD_MEMORY_READOUT_VERSION,
            lineage=(node_id, *lineage_ids),
            receipt=receipt,
        )
        visiting.remove(key)
        memo[key] = result
        return result

    result = {node_id: build(node_id, evaluation_time, as_of_runtime_ordinal) for node_id in visible_order}
    return MappingProxyType(result)
