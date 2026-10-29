"""Alpha 0.4: actual recall 后、MemoryCard 水合前的注意力占位。

该模块只消费已经完成动态记忆唤起后发布的 ``MemoryAttentionWitness``。
它不重新判断是否 actual recall，不读取正文、根量、常量、RAG、词元、P4 或 writer，
也不修改任何历史事实。输出只回答：本轮哪些已唤起 exact IDs 获得正文水合资格。

V1 使用泄漏式竞争累加器的确定性平衡解。每项只有自己的 raw drive 提供正输入；
共享注意力池只产生非负侧向压力。正 activation 自然形成可多项存活集合，不设 Top-N、
recent-N、固定容量或保底数量。
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from ops.memory_attention import MEMORY_ATTENTION_VERSION, MemoryAttentionWitness
from ops.turn_recall import ACTUAL_RECALL_RECEIPT_VERSION


MEMORY_CARD_ATTENTION_VERSION = "memory-card-attention-lca-equilibrium-v1"
MEMORY_CARD_ATTENTION_ADAPTER_VERSION = "actual-recall-to-memory-card-attention-v1"
MEMORY_CARD_ATTENTION_V1 = {
    "version": MEMORY_CARD_ATTENTION_VERSION,
    "input_witness_version": MEMORY_ATTENTION_VERSION,
    # da_i/dt = drive_i - leak*a_i - inhibition*sum(a_j, j != i)
    # leak 归一为 1；0.5 是冻结强弱／等强样本可行域内的简单内点。
    # 后续任何数值标定都必须升版本，不能静默改写 v1。
    "leak": 1.0,
    "lateral_inhibition": 0.5,
    "solver_tolerance": 1e-12,
}


def _identifier(value, field):
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    return result


def _finite(value, field):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
    return result


def _freeze(value, field="value"):
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain only finite serializable values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _parameter_package(params):
    if not isinstance(params, Mapping):
        raise ValueError("an explicit versioned memory-card attention package is required")
    required = {
        "version", "input_witness_version", "leak",
        "lateral_inhibition", "solver_tolerance",
    }
    missing = required - set(params)
    if missing:
        raise ValueError("memory-card attention package is missing: "
                         + ", ".join(sorted(missing)))
    version = _identifier(params.get("version"), "params.version")
    if version != MEMORY_CARD_ATTENTION_VERSION:
        raise ValueError("unknown memory-card-attention version")
    witness_version = _identifier(
        params.get("input_witness_version"), "params.input_witness_version")
    leak = _finite(params.get("leak"), "params.leak")
    inhibition = _finite(
        params.get("lateral_inhibition"), "params.lateral_inhibition")
    tolerance = _finite(params.get("solver_tolerance"), "params.solver_tolerance")
    if witness_version != MEMORY_ATTENTION_VERSION:
        raise ValueError("memory-card attention input witness version is unregistered")
    if leak <= 0.0 or inhibition < 0.0 or inhibition >= leak:
        raise ValueError("params require leak > lateral_inhibition >= 0")
    if tolerance <= 0.0:
        raise ValueError("params.solver_tolerance must be positive")
    registered = MEMORY_CARD_ATTENTION_V1
    for key, value in (("leak", leak), ("lateral_inhibition", inhibition),
                       ("solver_tolerance", tolerance)):
        if not math.isclose(value, float(registered[key]), rel_tol=0.0, abs_tol=1e-15):
            raise ValueError("memory-card attention package does not match its registered version")
    return version, witness_version, leak, inhibition, tolerance


@dataclass(frozen=True)
class MemoryCardAttentionOccupation:
    trigger_event_id: str
    evaluation_time_ms: int
    formula_version: str
    recalled_event_ids: tuple
    selected_for_hydration_ids: tuple
    receipt: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "trigger_event_id", _identifier(
            self.trigger_event_id, "trigger_event_id"))
        if isinstance(self.evaluation_time_ms, bool):
            raise ValueError("evaluation_time_ms must be a non-negative integer")
        try:
            at_ms = int(self.evaluation_time_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("evaluation_time_ms must be a non-negative integer") from exc
        if at_ms < 0 or at_ms != self.evaluation_time_ms:
            raise ValueError("evaluation_time_ms must be a non-negative integer")
        object.__setattr__(self, "evaluation_time_ms", at_ms)
        object.__setattr__(self, "formula_version", _identifier(
            self.formula_version, "formula_version"))
        recalled = tuple(_identifier(item, "recalled_event_id")
                         for item in self.recalled_event_ids)
        selected = tuple(_identifier(item, "selected_for_hydration_id")
                         for item in self.selected_for_hydration_ids)
        if len(set(recalled)) != len(recalled):
            raise ValueError("recalled_event_ids must be unique")
        if not set(selected).issubset(set(recalled)):
            raise ValueError("selected_for_hydration_ids must be recalled event IDs")
        object.__setattr__(self, "recalled_event_ids", recalled)
        object.__setattr__(self, "selected_for_hydration_ids", selected)
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self):
        return {
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "formula_version": self.formula_version,
            "recalled_event_ids": list(self.recalled_event_ids),
            "selected_for_hydration_ids": list(self.selected_for_hydration_ids),
            "receipt": _thaw(self.receipt),
        }


def _settle_active_set(rows, *, leak, inhibition, tolerance):
    """求非负 LCA 平衡；返回 active IDs、逐项 activation 与共享平衡读数。"""
    active = [row for row in rows if row["raw_drive"] > tolerance]
    passes = 0
    total_activation = 0.0
    shared_cutoff = 0.0
    while active:
        passes += 1
        denominator = leak + inhibition * (len(active) - 1)
        total_activation = sum(row["raw_drive"] for row in active) / denominator
        shared_cutoff = inhibition * total_activation
        survivors = [row for row in active
                     if row["raw_drive"] - shared_cutoff > tolerance]
        if len(survivors) == len(active):
            break
        active = survivors
    if not active:
        return set(), {}, 0.0, 0.0, passes
    denominator = leak + inhibition * (len(active) - 1)
    total_activation = sum(row["raw_drive"] for row in active) / denominator
    shared_cutoff = inhibition * total_activation
    activations = {
        row["event_id"]: max(
            0.0, (row["raw_drive"] - shared_cutoff) / (leak - inhibition))
        for row in active
    }
    return set(activations), activations, total_activation, shared_cutoff, passes


def settle_memory_card_attention(
        *, witnesses, params: Mapping[str, object]) -> MemoryCardAttentionOccupation:
    """在 actual recall 之后决定哪些 exact IDs 获得正文水合资格。"""
    version, witness_version, leak, inhibition, tolerance = _parameter_package(params)
    rows = []
    trigger_event_id = None
    evaluation_time_ms = None
    seen = set()
    for item in tuple(witnesses or ()):
        if not isinstance(item, MemoryAttentionWitness):
            raise ValueError("witnesses must contain MemoryAttentionWitness values")
        if item.formula_version != witness_version:
            raise ValueError("memory attention witness version does not match package")
        if item.target_event_id in seen:
            raise ValueError("duplicate target_event_id in memory-card attention input")
        seen.add(item.target_event_id)
        if trigger_event_id is None:
            trigger_event_id = item.trigger_event_id
            evaluation_time_ms = item.evaluation_time_ms
        elif (item.trigger_event_id != trigger_event_id
              or item.evaluation_time_ms != evaluation_time_ms):
            raise ValueError("all witnesses must share the same trigger and evaluation time")
        rows.append({"event_id": item.target_event_id,
                     "raw_drive": float(item.raw_drive),
                     "witness_formula_version": item.formula_version})
    if not rows:
        raise ValueError("at least one actual-recall witness is required")
    rows.sort(key=lambda row: row["event_id"])
    active, activations, total_activation, cutoff, passes = _settle_active_set(
        rows, leak=leak, inhibition=inhibition, tolerance=tolerance)

    decisions = []
    max_residual = 0.0
    for row in rows:
        event_id, drive = row["event_id"], row["raw_drive"]
        activation = activations.get(event_id, 0.0)
        occupied = event_id in active
        other_activation = max(0.0, total_activation - activation)
        leak_pressure = leak * activation
        lateral_pressure = inhibition * other_activation
        residual = drive - leak_pressure - lateral_pressure
        if occupied:
            max_residual = max(max_residual, abs(residual))
            reason = "positive_equilibrium_activation"
        elif drive <= tolerance:
            reason = "zero_drive"
        else:
            reason = "attention_occupied_by_stronger_recalled_events"
        decisions.append({
            "event_id": event_id,
            "input_witness_version": row["witness_formula_version"],
            "raw_drive": drive,
            "settled_activation": activation,
            "leak_pressure": leak_pressure,
            "lateral_pressure": lateral_pressure,
            "equilibrium_residual": residual,
            "occupied": occupied,
            "hydration_eligible": occupied,
            "reason": reason,
            "actual_recall_preserved": True,
            "p4_authority_changed": False,
        })

    recalled_ids = tuple(row["event_id"] for row in rows)
    selected_ids = tuple(event_id for event_id in recalled_ids if event_id in active)
    receipt = {
        "operator_version": version,
        "parameter_version": version,
        "input_witness_version": witness_version,
        "scope": "writer_memory_card_hydration_only",
        "trigger_event_id": trigger_event_id,
        "evaluation_time_ms": evaluation_time_ms,
        "parameters": {
            "leak": leak,
            "lateral_inhibition": inhibition,
            "solver_tolerance": tolerance,
        },
        "equilibrium": {
            "active_count": len(active),
            "total_activation": total_activation,
            "shared_cutoff": cutoff,
            "active_set_passes": passes,
            "max_active_residual": max_residual,
        },
        "recalled_event_ids": recalled_ids,
        "selected_for_hydration_ids": selected_ids,
        "decisions": tuple(decisions),
        "fixed_count_used": False,
        "content_hydrated": False,
        "actual_recall_mutated": False,
        "p4_input_mutated": False,
        "writer_feedback_read": False,
    }
    return MemoryCardAttentionOccupation(
        trigger_event_id=trigger_event_id,
        evaluation_time_ms=evaluation_time_ms,
        formula_version=version,
        recalled_event_ids=recalled_ids,
        selected_for_hydration_ids=selected_ids,
        receipt=receipt,
    )


def build_memory_card_attention_plan(
        *, actual_recall_receipt: Mapping[str, object], witnesses,
        params: Mapping[str, object]) -> MemoryCardAttentionOccupation:
    """把已完成 actual recall 与同批 C3-A witnesses 严格适配为水合计划。

    该边界只验证身份、触发事件、评价时点和集合完全一致。它不从旧 relevance、
    正文、P4、RAG 或 writer 重新推导 drive，也不改变 actual recall 集合。
    """
    version, witness_version, leak, inhibition, tolerance = _parameter_package(params)
    if not isinstance(actual_recall_receipt, Mapping):
        raise ValueError("actual_recall_receipt must be a mapping")
    receipt_version = _identifier(
        actual_recall_receipt.get("version"), "actual_recall_receipt.version")
    if receipt_version != ACTUAL_RECALL_RECEIPT_VERSION:
        raise ValueError("actual recall receipt version is unregistered")
    if actual_recall_receipt.get("selection_completed") is not True:
        raise ValueError("actual recall selection must be completed")
    if actual_recall_receipt.get("content_included") is not False:
        raise ValueError("actual recall receipt must not include content")
    trigger_event_id = _identifier(
        actual_recall_receipt.get("trigger_event_id"),
        "actual_recall_receipt.trigger_event_id")
    evaluation_time_ms = actual_recall_receipt.get("evaluation_time_ms")
    if isinstance(evaluation_time_ms, bool):
        raise ValueError("actual recall evaluation time must be a non-negative integer")
    try:
        evaluation_time_ms = int(evaluation_time_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("actual recall evaluation time must be a non-negative integer") from exc
    if evaluation_time_ms < 0:
        raise ValueError("actual recall evaluation time must be a non-negative integer")

    recalled_ids = tuple(_identifier(item, "recalled_event_id")
                         for item in actual_recall_receipt.get("recalled_event_ids", ()))
    if len(set(recalled_ids)) != len(recalled_ids):
        raise ValueError("actual recall receipt contains duplicate event IDs")
    references = tuple(actual_recall_receipt.get("references") or ())
    reference_ids = []
    for reference in references:
        if not isinstance(reference, Mapping):
            raise ValueError("actual recall references must be mappings")
        if reference.get("identity_kind") != "exact_committed_event":
            raise ValueError("actual recall adapter requires exact committed identity")
        reference_ids.append(_identifier(reference.get("event_id"), "reference.event_id"))
        if "text" in reference:
            raise ValueError("actual recall receipt must not contain text")
    if tuple(reference_ids) != recalled_ids:
        raise ValueError("actual recall references must match recalled_event_ids")

    witness_rows = tuple(witnesses or ())
    witness_ids = []
    for witness in witness_rows:
        if not isinstance(witness, MemoryAttentionWitness):
            raise ValueError("witnesses must contain MemoryAttentionWitness values")
        witness_ids.append(witness.target_event_id)
        if witness.trigger_event_id != trigger_event_id:
            raise ValueError("memory attention witness trigger event does not match actual recall")
        if witness.evaluation_time_ms != evaluation_time_ms:
            raise ValueError("memory attention witness evaluation time does not match actual recall")
        if witness.formula_version != witness_version:
            raise ValueError("memory attention witness version does not match package")
    if len(set(witness_ids)) != len(witness_ids) or set(witness_ids) != set(recalled_ids):
        raise ValueError("witness set must exactly match actual recalled event IDs")

    adapter_receipt = {
        "version": MEMORY_CARD_ATTENTION_ADAPTER_VERSION,
        "actual_recall_receipt_version": receipt_version,
        "trigger_event_id": trigger_event_id,
        "evaluation_time_ms": evaluation_time_ms,
        "actual_recalled_event_ids": recalled_ids,
        "exact_set_match": True,
        "selection_recomputed": False,
        "content_read": False,
        "p4_read": False,
    }
    if witness_rows:
        settled = settle_memory_card_attention(witnesses=witness_rows, params=params)
        receipt = _thaw(settled.receipt)
        receipt["adapter"] = adapter_receipt
        return MemoryCardAttentionOccupation(
            trigger_event_id=settled.trigger_event_id,
            evaluation_time_ms=settled.evaluation_time_ms,
            formula_version=settled.formula_version,
            recalled_event_ids=settled.recalled_event_ids,
            selected_for_hydration_ids=settled.selected_for_hydration_ids,
            receipt=receipt,
        )

    receipt = {
        "operator_version": version,
        "parameter_version": version,
        "input_witness_version": witness_version,
        "scope": "writer_memory_card_hydration_only",
        "trigger_event_id": trigger_event_id,
        "evaluation_time_ms": evaluation_time_ms,
        "parameters": {
            "leak": leak,
            "lateral_inhibition": inhibition,
            "solver_tolerance": tolerance,
        },
        "equilibrium": {
            "active_count": 0,
            "total_activation": 0.0,
            "shared_cutoff": 0.0,
            "active_set_passes": 0,
            "max_active_residual": 0.0,
        },
        "recalled_event_ids": (),
        "selected_for_hydration_ids": (),
        "decisions": (),
        "fixed_count_used": False,
        "content_hydrated": False,
        "actual_recall_mutated": False,
        "p4_input_mutated": False,
        "writer_feedback_read": False,
        "adapter": adapter_receipt,
    }
    return MemoryCardAttentionOccupation(
        trigger_event_id=trigger_event_id,
        evaluation_time_ms=evaluation_time_ms,
        formula_version=version,
        recalled_event_ids=(),
        selected_for_hydration_ids=(),
        receipt=receipt,
    )
