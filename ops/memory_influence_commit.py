"""Alpha 0.4 C4: convert one applied P4 batch into forward ID-only edges.

The operator is pure.  It does not write state or storage, select memories,
read attention occupation, hydrate content, or call runtime surfaces.  Every
edge stores the per-parent share that P4 actually applied after the one global
radial projection already recorded by ``RecallStateImpactBatch``.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType

from core.interaction_geometry import ROOT_ORDER
from ops.memory_actual_state import ActualRecallStateImpactBatch
from ops.memory_root_transition import ActualRecallRootTransition
from ops.memory_contracts import MemoryInfluenceEdge, MemoryInfluenceTransaction
from ops.memory_state_impact import RecallStateImpactBatch


MEMORY_INFLUENCE_COMMIT_VERSION = "memory-influence-commit-v1"


def _identifier(value, field):
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    return result


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if result < 0 or result != value:
        raise ValueError(f"{field} must be a non-negative integer")
    return result


def _finite(value, field):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _root_vector(value, field):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    return MappingProxyType({
        axis: _finite(value[axis], f"{field}.{axis}") for axis in ROOT_ORDER
    })


def _same_vector(left, right):
    return all(math.isclose(
        float(left[axis]), float(right[axis]), rel_tol=1e-12, abs_tol=1e-12)
        for axis in ROOT_ORDER)


def _freeze(value, field="receipt"):
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}") for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain finite serializable values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class MemoryInfluenceCommitPlan:
    transaction: MemoryInfluenceTransaction
    delta_x_memory: Mapping[str, float]
    formula_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        if not isinstance(self.transaction, MemoryInfluenceTransaction):
            raise ValueError("transaction must be a MemoryInfluenceTransaction")
        object.__setattr__(self, "delta_x_memory", _root_vector(
            self.delta_x_memory, "delta_x_memory"))
        object.__setattr__(self, "formula_version", _identifier(
            self.formula_version, "formula_version"))
        if self.formula_version != self.transaction.formula_version:
            raise ValueError("plan and transaction formula versions must match")
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(self.receipt))
        edge_sum = {axis: 0.0 for axis in ROOT_ORDER}
        for edge in self.transaction.edges:
            edge_sum = {
                axis: edge_sum[axis] + edge.actual_delta[axis]
                for axis in ROOT_ORDER
            }
        if not _same_vector(edge_sum, self.delta_x_memory):
            raise ValueError("forward edge sum must equal applied P4 delta")

    def to_record(self):
        return {
            "schema_version": 1,
            "kind": "memory_influence_commit_plan",
            "transaction": self.transaction.to_record(),
            "delta_x_memory": dict(self.delta_x_memory),
            "formula_version": self.formula_version,
            "receipt": _thaw(self.receipt),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "kind", "transaction", "delta_x_memory",
            "formula_version", "receipt",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("memory influence commit plan schema is unsupported")
        if record.get("schema_version") != 1 or record.get("kind") != (
                "memory_influence_commit_plan"):
            raise ValueError("memory influence commit plan schema is unsupported")
        return cls(
            transaction=MemoryInfluenceTransaction.from_record(record["transaction"]),
            delta_x_memory=record["delta_x_memory"],
            formula_version=record["formula_version"],
            receipt=record["receipt"],
        )


def build_memory_influence_commit_plan(
        *, impact_batch: RecallStateImpactBatch | ActualRecallStateImpactBatch,
        root_transition=None,
        transaction_id,
        child_anchor_id, child_anchor_kind, committed_at_ms,
        state_transition_receipt_ref, p4_receipt_ref, parent_bindings,
        formula_version) -> MemoryInfluenceCommitPlan:
    """Create one child influence transaction from the actually applied result."""
    if not isinstance(impact_batch, (
            RecallStateImpactBatch, ActualRecallStateImpactBatch)):
        raise ValueError(
            "impact_batch must be a supported settled state impact batch")
    uses_actual_recall_v2 = isinstance(impact_batch, ActualRecallStateImpactBatch)
    if uses_actual_recall_v2:
        if not isinstance(root_transition, ActualRecallRootTransition):
            raise ValueError("actual recall v2 requires one root transition")
        if (root_transition.trigger_event_id != impact_batch.trigger_event_id
                or root_transition.evaluation_time_ms
                != impact_batch.evaluation_time_ms):
            raise ValueError("root transition must share the impact trigger and time")
        if (set(root_transition.source_event_ids)
                != {item.event_id for item in impact_batch.items}):
            raise ValueError("root transition source IDs must match impact IDs")
        if not _same_vector(
                root_transition.requested_delta_x_memory,
                impact_batch.delta_x_memory):
            raise ValueError("root transition requested delta must match impact")
    elif root_transition is not None:
        raise ValueError("legacy impact cannot bind an actual-recall root transition")
    if not impact_batch.items:
        raise ValueError("impact batch must contain at least one applied parent")
    version = _identifier(formula_version, "formula_version")
    if version != MEMORY_INFLUENCE_COMMIT_VERSION:
        raise ValueError("memory influence commit version is unregistered")
    transaction_id = _identifier(transaction_id, "transaction_id")
    child_anchor_id = _identifier(child_anchor_id, "child_anchor_id")
    child_kind = str(child_anchor_kind or "").strip()
    if child_kind not in {"interaction", "silent_recall"}:
        raise ValueError("child_anchor_kind is unsupported")
    committed_at = _timestamp(committed_at_ms, "committed_at_ms")
    if committed_at != impact_batch.evaluation_time_ms:
        raise ValueError("child committed time must equal P4 evaluation time")
    if child_anchor_id != impact_batch.trigger_event_id and child_kind == "interaction":
        raise ValueError("interaction child must be the P4 trigger event")
    state_ref = _identifier(
        state_transition_receipt_ref, "state_transition_receipt_ref")
    if uses_actual_recall_v2 and state_ref != root_transition.transition_id:
        raise ValueError("state transition receipt reference is forged")
    p4_ref = _identifier(p4_receipt_ref, "p4_receipt_ref")
    if not isinstance(parent_bindings, Mapping):
        raise ValueError("parent_bindings must be a mapping")

    item_ids = tuple(item.event_id for item in impact_batch.items)
    binding_ids = tuple(str(key) for key in parent_bindings)
    if len(set(binding_ids)) != len(binding_ids) or set(binding_ids) != set(item_ids):
        raise ValueError("parent binding set must exactly match P4 impact IDs")

    requested_delta = dict(impact_batch.delta_x_memory)
    applied_delta = (
        dict(root_transition.actual_delta_x_memory)
        if uses_actual_recall_v2 else requested_delta)
    boundary_ratios = {}
    for axis in ROOT_ORDER:
        requested = float(requested_delta[axis])
        applied = float(applied_delta[axis])
        if math.isclose(requested, 0.0, rel_tol=0.0, abs_tol=1e-12):
            if not math.isclose(applied, 0.0, rel_tol=0.0, abs_tol=1e-12):
                raise ValueError("actual root delta cannot exist without requested delta")
            boundary_ratios[axis] = 1.0
        else:
            boundary_ratios[axis] = applied / requested

    edges, traces = [], []
    for item in sorted(impact_batch.items, key=lambda row: row.event_id):
        binding = parent_bindings[item.event_id]
        if not isinstance(binding, Mapping):
            raise ValueError("parent binding must be a mapping")
        required = {
            "edge_id", "parent_live_readout_ref", "propagation_binding_ref",
            "receipt_refs",
        }
        if set(binding) != required:
            raise ValueError("parent binding schema is unsupported")
        pre_boundary_delta = {
            axis: float(item.impact_vector[axis]) / impact_batch.projection_factor
            for axis in ROOT_ORDER
        }
        actual_delta = {
            axis: pre_boundary_delta[axis] * boundary_ratios[axis]
            for axis in ROOT_ORDER
        }
        edge = MemoryInfluenceEdge(
            edge_id=_identifier(binding["edge_id"], "edge_id"),
            parent_node_id=item.event_id,
            actual_delta=actual_delta,
            parent_live_readout_ref=_identifier(
                binding["parent_live_readout_ref"], "parent_live_readout_ref"),
            p4_receipt_ref=p4_ref,
            propagation_binding_ref=_identifier(
                binding["propagation_binding_ref"], "propagation_binding_ref"),
            formula_version=version,
            receipt_refs=tuple(binding["receipt_refs"] or ()),
        )
        edges.append(edge)
        traces.append({
            "parent_event_id": item.event_id,
            "edge_id": edge.edge_id,
            "raw_impact_vector": dict(item.impact_vector),
            "global_projection_factor": impact_batch.projection_factor,
            "pre_root_boundary_delta": pre_boundary_delta,
            "root_boundary_attribution_ratio": boundary_ratios,
            "actual_edge_delta": actual_delta,
            "parent_live_readout_ref": edge.parent_live_readout_ref,
            "propagation_binding_ref": edge.propagation_binding_ref,
        })

    transaction = MemoryInfluenceTransaction(
        transaction_id=transaction_id,
        child_anchor_id=child_anchor_id,
        child_anchor_kind=child_kind,
        committed_at_ms=committed_at,
        state_transition_receipt_ref=state_ref,
        formula_version=version,
        edges=tuple(edges),
        receipt_refs=(p4_ref,),
    )
    receipt = {
        "operator_version": version,
        "input_p4_operator_version": impact_batch.operator_version,
        "input_p4_parameter_version": impact_batch.parameter_version,
        "transaction_id": transaction_id,
        "child_anchor_id": child_anchor_id,
        "child_anchor_kind": child_kind,
        "committed_at_ms": committed_at,
        "state_transition_receipt_ref": state_ref,
        "p4_receipt_ref": p4_ref,
        "parent_event_ids": tuple(item.event_id for item in impact_batch.items),
        "global_projection_factor": impact_batch.projection_factor,
        "global_projection_applied_once": True,
        "requested_delta_x_memory": requested_delta,
        "delta_x_memory": applied_delta,
        "edge_traces": tuple(traces),
        "edge_sum_equals_applied_delta": True,
        "edge_sum_equals_actual_root_delta": uses_actual_recall_v2,
        "root_transition_version": (
            root_transition.operator_version if uses_actual_recall_v2 else None),
        "root_domain_attribution_only": uses_actual_recall_v2,
        "post_selection_rescale_performed": False,
        "current_evaluation_visible": False,
        "future_evaluation_eligible": True,
        "parent_mutated": False,
        "attention_read": False,
        "content_read": False,
        "state_write_status": "not_performed",
        "journal_write_status": "not_performed",
        "child_content_hydration_eligible": child_kind == "interaction",
    }
    return MemoryInfluenceCommitPlan(
        transaction=transaction,
        delta_x_memory=applied_delta,
        formula_version=version,
        receipt=receipt,
    )
