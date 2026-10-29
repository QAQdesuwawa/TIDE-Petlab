"""CX67 numeric Memory DTOs and validated influence graph contracts.

These contracts contain six-root numeric state and no product content, RAG,
Store, Runner, Writer, tick, route, send, or UI authority.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from collections.abc import Mapping
from typing import Optional

from .preference_parameters import CONSTANT_ORDER
from .interaction_geometry import ROOT_ORDER


MEMORY_EVENT_REF_SCHEMA = 1
NEUTRAL_MEMORY_READOUT_SCHEMA = 2
MEMORY_RECALL_EVENT_SCHEMA = 2
MEMORY_INFLUENCE_EDGE_SCHEMA = 1
MEMORY_INFLUENCE_TRANSACTION_SCHEMA = 1

def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} 不能为空")
    return normalized


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是非负整数时间")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是非负整数时间") from exc
    if parsed < 0 or parsed != value:
        raise ValueError(f"{field} 必须是非负整数时间")
    return parsed


def _number(value, field):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是有限数值") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} 必须是有限数值")
    return parsed


def _root_vector(value, field):
    if not isinstance(value, Mapping) or set(value) != set(ROOT_ORDER):
        raise ValueError(f"{field} 必须包含且仅包含六个根量")
    return MappingProxyType({
        axis: _number(value[axis], f"{field}.{axis}") for axis in ROOT_ORDER})


def _freeze_receipt(value, field="receipt"):
    if type(value) in (tuple, list) and all(type(item) is str for item in value):
        return tuple(value)
    if value is None or type(value) in (str, bool, int): return value
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_receipt(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_receipt(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} 必须可序列化且数值有限")
        return value
    raise ValueError(f"{field} 必须是可序列化的只读 receipt")


def _thaw_receipt(value):
    if type(value) is tuple and all(type(item) is str for item in value):
        return list(value)
    if value is None or type(value) in (str, bool, int): return value
    if isinstance(value, Mapping):
        return {str(key): _thaw_receipt(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_receipt(item) for item in value]
    return value


def _record(value, schema, required):
    if not isinstance(value, Mapping):
        raise ValueError("memory contract record 必须是映射")
    allowed = {"schema_version", *required}
    if set(value) - allowed:
        raise ValueError("memory contract record 含未知字段")
    missing = set(required) - set(value)
    if missing:
        raise ValueError("memory contract record 缺少字段：" + ", ".join(sorted(missing)))
    if value.get("schema_version") != schema:
        raise ValueError("memory contract schema_version 不受支持")
    return value


def _recall_propagation_binding(value):
    if value is None:
        return None
    if not isinstance(value, Mapping):
        raise ValueError("propagation_binding 必须是映射或空")
    required = {
        "version", "constant_binding", "root_time_operator_version",
        "root_time_parameter_version",
    }
    if not required <= set(value):
        raise ValueError("propagation_binding 缺少版本化 C/Phi 绑定")
    for field in ("version", "root_time_operator_version", "root_time_parameter_version"):
        _identifier(value.get(field), f"propagation_binding.{field}")
    constants_binding = value.get("constant_binding")
    if not isinstance(constants_binding, Mapping):
        raise ValueError("propagation_binding.constant_binding 必须是映射")
    constant_required = {
        "version", "base_constants_version", "b0_version", "b0_mapping_version",
        "correction_version", "effective_constants_version", "effective_constants",
    }
    if not constant_required <= set(constants_binding):
        raise ValueError("propagation constant binding 缺少版本谱系")
    for field in ("version", "base_constants_version", "b0_version",
                  "effective_constants_version"):
        _identifier(constants_binding.get(field), f"constant_binding.{field}")
    for field in ("b0_mapping_version", "correction_version"):
        if constants_binding.get(field) is not None:
            _identifier(constants_binding.get(field), f"constant_binding.{field}")
    constants = constants_binding.get("effective_constants")
    if not isinstance(constants, Mapping) or set(constants) != set(CONSTANT_ORDER):
        raise ValueError("propagation binding 必须完整冻结七常量")
    for axis in CONSTANT_ORDER:
        number = _number(constants[axis], f"effective_constants.{axis}")
        if not 0.0 <= number <= 1.0:
            raise ValueError("propagation binding 七常量必须在 0 到 1 之间")
    return _freeze_receipt(value, "propagation_binding")


@dataclass(frozen=True)
class MemoryEventRef:
    event_id: str
    committed_at_ms: int

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "committed_at_ms", _timestamp(
            self.committed_at_ms, "committed_at_ms"))

    def to_record(self):
        return {"schema_version": MEMORY_EVENT_REF_SCHEMA,
                "event_id": self.event_id, "committed_at_ms": self.committed_at_ms}

    @classmethod
    def from_record(cls, record):
        row = _record(record, MEMORY_EVENT_REF_SCHEMA, {"event_id", "committed_at_ms"})
        return cls(row["event_id"], row["committed_at_ms"])


@dataclass(frozen=True)
class NeutralMemoryReadout:
    event: MemoryEventRef
    as_of_ms: int
    live_vector: Mapping[str, float]
    magnitude: float
    formula_version: str
    lineage: tuple = ()
    receipt: Optional[Mapping[str, object]] = None

    def __post_init__(self):
        if not isinstance(self.event, MemoryEventRef):
            raise ValueError("neutral readout.event 必须是 MemoryEventRef")
        as_of = _timestamp(self.as_of_ms, "as_of_ms")
        if as_of < self.event.committed_at_ms:
            raise ValueError("neutral readout 不能早于 memory event")
        object.__setattr__(self, "as_of_ms", as_of)
        object.__setattr__(self, "live_vector", _root_vector(self.live_vector, "live_vector"))
        object.__setattr__(self, "magnitude", _number(self.magnitude, "magnitude"))
        object.__setattr__(self, "formula_version", _identifier(
            self.formula_version, "formula_version"))
        object.__setattr__(self, "lineage", tuple(
            _identifier(item, "lineage item") for item in self.lineage))
        receipt = {} if self.receipt is None else self.receipt
        if not isinstance(receipt, Mapping):
            raise ValueError("neutral readout.receipt 必须是映射")
        object.__setattr__(self, "receipt", _freeze_receipt(receipt))

    def to_record(self):
        return {"schema_version": NEUTRAL_MEMORY_READOUT_SCHEMA,
                "event": self.event.to_record(), "as_of_ms": self.as_of_ms,
                "live_vector": dict(self.live_vector), "magnitude": self.magnitude,
                "formula_version": self.formula_version, "lineage": list(self.lineage),
                "receipt": _thaw_receipt(self.receipt)}

    @classmethod
    def from_record(cls, record):
        row = _record(record, NEUTRAL_MEMORY_READOUT_SCHEMA, {
            "event", "as_of_ms", "live_vector", "magnitude", "formula_version",
            "lineage", "receipt"})
        return cls(MemoryEventRef.from_record(row["event"]), row["as_of_ms"],
                   row["live_vector"], row["magnitude"], row["formula_version"],
                   tuple(row["lineage"]), row["receipt"])


@dataclass(frozen=True)
class MemoryRecallEvent:
    recall_event_id: str
    committed_at_ms: int
    trigger_event_id: str
    target_event_id: str
    parent_recall_event_ids: tuple
    pre_recall_live_vector: Mapping[str, float]
    current_trigger_vector: Mapping[str, float]
    state_impact: Mapping[str, float]
    reconsolidation_delta: Mapping[str, float]
    formula_version: str
    receipt_refs: tuple = ()
    propagation_binding: Optional[Mapping[str, object]] = None

    def __post_init__(self):
        for field in ("recall_event_id", "trigger_event_id", "target_event_id"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        if self.trigger_event_id == self.target_event_id:
            raise ValueError("memory recall edge 不得自环")
        object.__setattr__(self, "committed_at_ms", _timestamp(
            self.committed_at_ms, "committed_at_ms"))
        parents = tuple(_identifier(item, "parent_recall_event_id")
                        for item in self.parent_recall_event_ids)
        if len(set(parents)) != len(parents) or self.recall_event_id in parents:
            raise ValueError("memory recall parent edge 重复或自环")
        object.__setattr__(self, "parent_recall_event_ids", parents)
        for field in ("pre_recall_live_vector", "current_trigger_vector",
                      "state_impact", "reconsolidation_delta"):
            object.__setattr__(self, field, _root_vector(getattr(self, field), field))
        object.__setattr__(self, "formula_version", _identifier(
            self.formula_version, "formula_version"))
        object.__setattr__(self, "receipt_refs", tuple(
            _identifier(item, "receipt_ref") for item in self.receipt_refs))
        object.__setattr__(self, "propagation_binding",
                           _recall_propagation_binding(self.propagation_binding))

    def to_record(self):
        return {"schema_version": MEMORY_RECALL_EVENT_SCHEMA,
                "recall_event_id": self.recall_event_id,
                "committed_at_ms": self.committed_at_ms,
                "trigger_event_id": self.trigger_event_id,
                "target_event_id": self.target_event_id,
                "parent_recall_event_ids": list(self.parent_recall_event_ids),
                "pre_recall_live_vector": dict(self.pre_recall_live_vector),
                "current_trigger_vector": dict(self.current_trigger_vector),
                "state_impact": dict(self.state_impact),
                "reconsolidation_delta": dict(self.reconsolidation_delta),
                "formula_version": self.formula_version,
                "receipt_refs": list(self.receipt_refs),
                "propagation_binding": _thaw_receipt(self.propagation_binding)}

    @classmethod
    def from_record(cls, record):
        common = {"recall_event_id", "committed_at_ms", "trigger_event_id",
                  "target_event_id", "parent_recall_event_ids",
                  "pre_recall_live_vector", "current_trigger_vector", "state_impact",
                  "reconsolidation_delta", "formula_version", "receipt_refs"}
        if not isinstance(record, Mapping):
            raise ValueError("memory contract record 必须是映射")
        if record.get("schema_version") == 1:
            row = _record(record, 1, common)
            propagation_binding = None
        else:
            row = _record(record, MEMORY_RECALL_EVENT_SCHEMA,
                          {*common, "propagation_binding"})
            propagation_binding = row["propagation_binding"]
        return cls(
            recall_event_id=row["recall_event_id"],
            committed_at_ms=row["committed_at_ms"],
            trigger_event_id=row["trigger_event_id"],
            target_event_id=row["target_event_id"],
            parent_recall_event_ids=tuple(row["parent_recall_event_ids"]),
            pre_recall_live_vector=row["pre_recall_live_vector"],
            current_trigger_vector=row["current_trigger_vector"],
            state_impact=row["state_impact"],
            reconsolidation_delta=row["reconsolidation_delta"],
            formula_version=row["formula_version"],
            receipt_refs=tuple(row["receipt_refs"]),
            propagation_binding=propagation_binding,
        )


@dataclass(frozen=True)
class MemoryInfluenceEdge:
    """One immutable earlier-to-later numeric influence edge.

    The edge stores no text. Its parent resolves to a canonical interaction event or
    a committed silent-recall anchor. actual_delta is the six-root contribution that
    P4 really applied for this parent after projection.
    """

    edge_id: str
    parent_node_id: str
    actual_delta: Mapping[str, float]
    parent_live_readout_ref: str
    p4_receipt_ref: str
    propagation_binding_ref: str
    formula_version: str
    receipt_refs: tuple = ()

    def __post_init__(self):
        for field in (
            "edge_id", "parent_node_id", "parent_live_readout_ref",
            "p4_receipt_ref", "propagation_binding_ref", "formula_version",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "actual_delta", _root_vector(
            self.actual_delta, "actual_delta"))
        object.__setattr__(self, "receipt_refs", tuple(
            _identifier(item, "receipt_ref") for item in self.receipt_refs))

    def to_record(self):
        return {
            "schema_version": MEMORY_INFLUENCE_EDGE_SCHEMA,
            "edge_id": self.edge_id,
            "parent_node_id": self.parent_node_id,
            "actual_delta": dict(self.actual_delta),
            "parent_live_readout_ref": self.parent_live_readout_ref,
            "p4_receipt_ref": self.p4_receipt_ref,
            "propagation_binding_ref": self.propagation_binding_ref,
            "formula_version": self.formula_version,
            "receipt_refs": list(self.receipt_refs),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "edge_id", "parent_node_id", "actual_delta",
            "parent_live_readout_ref", "p4_receipt_ref",
            "propagation_binding_ref", "formula_version", "receipt_refs",
        }
        row = _record(record, MEMORY_INFLUENCE_EDGE_SCHEMA, required)
        return cls(
            edge_id=row["edge_id"],
            parent_node_id=row["parent_node_id"],
            actual_delta=row["actual_delta"],
            parent_live_readout_ref=row["parent_live_readout_ref"],
            p4_receipt_ref=row["p4_receipt_ref"],
            propagation_binding_ref=row["propagation_binding_ref"],
            formula_version=row["formula_version"],
            receipt_refs=tuple(row["receipt_refs"]),
        )


@dataclass(frozen=True)
class MemoryInfluenceTransaction:
    """Atomic child anchor plus every parent contribution applied at P4."""

    transaction_id: str
    child_anchor_id: str
    child_anchor_kind: str
    committed_at_ms: int
    state_transition_receipt_ref: str
    formula_version: str
    edges: tuple
    receipt_refs: tuple = ()

    def __post_init__(self):
        for field in (
            "transaction_id", "child_anchor_id", "state_transition_receipt_ref",
            "formula_version",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        kind = str(self.child_anchor_kind or "").strip()
        if kind not in {"interaction", "silent_recall"}:
            raise ValueError("child_anchor_kind 不受支持")
        object.__setattr__(self, "child_anchor_kind", kind)
        object.__setattr__(self, "committed_at_ms", _timestamp(
            self.committed_at_ms, "committed_at_ms"))
        edges = tuple(self.edges)
        if not edges or not all(isinstance(edge, MemoryInfluenceEdge) for edge in edges):
            raise ValueError("memory influence transaction 至少需要一条有效 edge")
        edge_ids = tuple(edge.edge_id for edge in edges)
        parent_ids = tuple(edge.parent_node_id for edge in edges)
        if len(set(edge_ids)) != len(edge_ids):
            raise ValueError("同一 transaction 内 edge identity 重复")
        if len(set(parent_ids)) != len(parent_ids):
            raise ValueError("同一 transaction 内 parent-child edge 重复")
        object.__setattr__(self, "edges", edges)
        object.__setattr__(self, "receipt_refs", tuple(
            _identifier(item, "receipt_ref") for item in self.receipt_refs))

    def to_record(self):
        return {
            "schema_version": MEMORY_INFLUENCE_TRANSACTION_SCHEMA,
            "record_kind": "memory_influence_transaction",
            "transaction_id": self.transaction_id,
            "child_anchor_id": self.child_anchor_id,
            "child_anchor_kind": self.child_anchor_kind,
            "committed_at_ms": self.committed_at_ms,
            "state_transition_receipt_ref": self.state_transition_receipt_ref,
            "formula_version": self.formula_version,
            "edges": [edge.to_record() for edge in self.edges],
            "receipt_refs": list(self.receipt_refs),
        }

    @classmethod
    def from_record(cls, record):
        required = {
            "record_kind", "transaction_id", "child_anchor_id",
            "child_anchor_kind", "committed_at_ms",
            "state_transition_receipt_ref", "formula_version", "edges",
            "receipt_refs",
        }
        row = _record(record, MEMORY_INFLUENCE_TRANSACTION_SCHEMA, required)
        if row["record_kind"] != "memory_influence_transaction":
            raise ValueError("memory influence record_kind 不受支持")
        if not isinstance(row["edges"], (tuple, list)):
            raise ValueError("memory influence edges 必须是列表")
        return cls(
            transaction_id=row["transaction_id"],
            child_anchor_id=row["child_anchor_id"],
            child_anchor_kind=row["child_anchor_kind"],
            committed_at_ms=row["committed_at_ms"],
            state_transition_receipt_ref=row["state_transition_receipt_ref"],
            formula_version=row["formula_version"],
            edges=tuple(MemoryInfluenceEdge.from_record(item)
                        for item in row["edges"]),
            receipt_refs=tuple(row["receipt_refs"]),
        )


class MemoryInfluenceGraphIndex:
    """Validated adjacency index over canonical events and silent recall anchors."""

    def __init__(self, *, canonical_node_times, transactions=(), canonical_node_ordinals=None):
        if not isinstance(canonical_node_times, Mapping):
            raise ValueError("canonical_node_times 必须是映射")
        node_times = {}
        node_kinds = {}
        for raw_node_id, raw_time in canonical_node_times.items():
            node_id = _identifier(raw_node_id, "canonical node ID")
            if node_id in node_times:
                raise ValueError("canonical node identity 重复")
            node_times[node_id] = _timestamp(raw_time, "canonical node time")
            node_kinds[node_id] = "interaction"

        from .memory_runtime_order import validate_node_ordinals
        node_ordinals = (None if canonical_node_ordinals is None else
                         validate_node_ordinals(node_times, canonical_node_ordinals))
        transactions = tuple(transactions)
        if not all(isinstance(tx, MemoryInfluenceTransaction) for tx in transactions):
            raise ValueError("graph index 只接受 MemoryInfluenceTransaction")

        transaction_ids = set()
        child_ids = set()
        edge_ids = set()
        parent_child_edges = set()
        incoming = {}
        outgoing = {}

        for tx in transactions:
            if tx.transaction_id in transaction_ids:
                raise ValueError("memory influence transaction identity 重复")
            if tx.child_anchor_id in child_ids:
                raise ValueError("一个 child anchor 只能提交一笔 influence transaction")
            transaction_ids.add(tx.transaction_id)
            child_ids.add(tx.child_anchor_id)

            if tx.child_anchor_kind == "interaction":
                child_time = node_times.get(tx.child_anchor_id)
                if child_time is None:
                    raise ValueError("interaction child 是未知 canonical event ID")
                if child_time != tx.committed_at_ms:
                    raise ValueError("interaction child 的提交时间与 canonical event 冲突")
            else:
                if node_ordinals is not None:
                    raise ValueError("silent recall needs its own formal ordinal evidence")
                if tx.child_anchor_id in node_times:
                    raise ValueError("silent recall anchor 不得覆盖已有 node identity")
                child_time = tx.committed_at_ms
                node_times[tx.child_anchor_id] = child_time
                node_kinds[tx.child_anchor_id] = "silent_recall"

            for edge in tx.edges:
                if edge.edge_id in edge_ids:
                    raise ValueError("memory influence edge identity 重复")
                edge_ids.add(edge.edge_id)
                parent_time = node_times.get(edge.parent_node_id)
                if parent_time is None:
                    raise ValueError("memory influence parent 是未知或尚未提交的 node ID")
                ordered = parent_time < child_time
                if node_ordinals is not None:
                    ordered = (parent_time <= child_time and
                        edge.parent_node_id in node_ordinals and tx.child_anchor_id in node_ordinals and
                        node_ordinals[edge.parent_node_id] < node_ordinals[tx.child_anchor_id])
                if not ordered:
                    raise ValueError("memory influence edge 必须严格由过去指向未来")
                identity = (edge.parent_node_id, tx.child_anchor_id)
                if identity in parent_child_edges:
                    raise ValueError("memory influence parent-child edge 重复")
                parent_child_edges.add(identity)
                incoming.setdefault(tx.child_anchor_id, []).append(edge)
                outgoing.setdefault(edge.parent_node_id, []).append(edge)

        self._node_times = MappingProxyType(dict(node_times))
        self._node_kinds = MappingProxyType(dict(node_kinds))
        self._incoming = MappingProxyType({
            node_id: tuple(sorted(edges, key=lambda edge: edge.edge_id))
            for node_id, edges in incoming.items()
        })
        self._outgoing = MappingProxyType({
            node_id: tuple(sorted(edges, key=lambda edge: edge.edge_id))
            for node_id, edges in outgoing.items()
        })

    def incoming(self, child_anchor_id):
        return self._incoming.get(str(child_anchor_id), ())

    def outgoing(self, parent_node_id):
        return self._outgoing.get(str(parent_node_id), ())

    def node_kind(self, node_id):
        normalized = _identifier(node_id, "node_id")
        if normalized not in self._node_kinds:
            raise KeyError(normalized)
        return self._node_kinds[normalized]

    def content_hydration_eligible(self, node_id):
        return self.node_kind(node_id) == "interaction"
