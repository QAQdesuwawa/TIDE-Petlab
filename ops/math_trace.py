"""Alpha 0.2 R0.5：事务边界的只读数学观测合同。

本模块既不是事件总线，也不是参数更新器。``MathTraceCollector`` 只在一次既有
Runner/Actor 事务内暂存紧凑数值 span；调用方在原事务已经提交后，才可把 records
追加到 store。它不导入 Runner、Actor、temporal 或 responder，因而不会改变调度、
状态、RNG 或模型调用。

正文、prompt、草稿、私密 ammo 与模型中间推理不得进入该流。原始事实仍留在既有
messages/archive/action receipt；本流只用 ID 引用和有界数值摘要将公式因果串起来。
"""
from __future__ import annotations

from copy import deepcopy
import re
from typing import Any, Mapping

from core.formula_registry import get


MATH_TRACE_SCHEMA_VERSION = 1
_PHASES = frozenset({
    "input_observed", "state_settled", "memory_resolved", "schedule_evaluated",
    "output_committed", "cancelled", "outcome_closed",
})
_MODES = frozenset({"active", "shadow"})
_FORBIDDEN_KEYS = frozenset({
    "text", "raw", "content", "prompt", "system", "user", "message", "messages",
    "reply", "draft", "ammo", "reasoning", "completion",
})
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_.:-]{1,96}$")
_REF_KEY = re.compile(r"^[A-Za-z][A-Za-z0-9_]*(?:_id|_ids|_ts|_at|_kind|_source)$")
_MAX_DEPTH = 4
_MAX_ITEMS = 24


def make_trace_id(root_kind: str, root_id: str) -> str:
    """从既有 turn/action 根引用生成稳定 trace ID，不引入随机数。"""
    kind = str(root_kind or "").strip()
    identifier = str(root_id or "").strip()
    if not _IDENTIFIER.fullmatch(kind) or not identifier or len(identifier) > 160:
        raise ValueError("math trace 根引用非法")
    return "math:" + kind + ":" + identifier


def _safe_identifier(value: Any, label: str) -> str:
    item = str(value or "").strip()
    if not _IDENTIFIER.fullmatch(item):
        raise ValueError(label + " 必须是受控标识符")
    return item


def _safe_summary(value: Any, *, depth: int = 0) -> Any:
    """仅接受短小、JSON 原生的数值/类别摘要，拒绝正文形字段。"""
    if depth > _MAX_DEPTH:
        raise ValueError("math trace 摘要层级过深")
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        # 类别/版本/哈希可保留；自然语言正文不属于本流。
        return _safe_identifier(value, "math trace 字符串摘要")
    if isinstance(value, (list, tuple)):
        if len(value) > _MAX_ITEMS:
            raise ValueError("math trace 摘要项过多")
        return [_safe_summary(item, depth=depth + 1) for item in value]
    if isinstance(value, Mapping):
        if len(value) > _MAX_ITEMS:
            raise ValueError("math trace 摘要字段过多")
        row = {}
        for key, item in value.items():
            name = _safe_identifier(key, "math trace 摘要键")
            if name.lower() in _FORBIDDEN_KEYS:
                raise ValueError("math trace 禁止记录正文或 prompt 字段: " + name)
            row[name] = _safe_summary(item, depth=depth + 1)
        return row
    raise TypeError("math trace 摘要只能包含 JSON 原生数值、类别或容器")


def _safe_refs(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping) or not value:
        raise ValueError("math trace 必须具有 root_refs")
    if len(value) > _MAX_ITEMS:
        raise ValueError("math trace 引用字段过多")
    refs: dict[str, Any] = {}
    for key, item in value.items():
        name = str(key)
        if not _REF_KEY.fullmatch(name):
            raise ValueError("math trace 引用键必须显式标明 ID/时间/来源: " + name)
        if isinstance(item, (list, tuple)):
            if len(item) > _MAX_ITEMS:
                raise ValueError("math trace 引用项过多")
            refs[name] = [_safe_identifier(part, "math trace 引用") for part in item]
        elif isinstance(item, (str, int)):
            refs[name] = _safe_identifier(item, "math trace 引用")
        else:
            raise TypeError("math trace 引用只能是 ID、时间或其列表")
    return refs


def build_span(*, trace_id: str, span_id: int, phase: str, formula_id: str,
               params_sha: str, logical_ts: int, root_refs: Mapping[str, Any],
               input_summary: Mapping[str, Any] | None = None,
               output_summary: Mapping[str, Any] | None = None,
               decision_reason: str = "observed",
               active_or_shadow: str = "active", parent_ref: str | None = None,
               formula_params: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """构造并验证单个 span；不写文件、不读状态、不调用任何运行公式。"""
    if str(phase) not in _PHASES:
        raise ValueError("未知 math trace phase: " + str(phase))
    if str(active_or_shadow) not in _MODES:
        raise ValueError("math trace mode 必须为 active 或 shadow")
    if int(span_id) < 1:
        raise ValueError("span_id 必须从 1 开始")
    spec = get(str(formula_id))
    record = {
        "schema_version": MATH_TRACE_SCHEMA_VERSION,
        "trace_id": _safe_identifier(trace_id, "trace_id"),
        "span_id": int(span_id),
        "phase": str(phase),
        "formula_id": spec.formula_id,
        "formula_version": spec.formula_version,
        "params_sha": _safe_identifier(params_sha, "params_sha"),
        "logical_ts": int(logical_ts),
        "root_refs": _safe_refs(root_refs),
        "input_summary": _safe_summary(dict(input_summary or {})),
        "output_summary": _safe_summary(dict(output_summary or {})),
        "decision_reason": _safe_identifier(decision_reason, "decision_reason"),
        "active_or_shadow": str(active_or_shadow),
    }
    if parent_ref is not None:
        record["parent_ref"] = _safe_identifier(parent_ref, "parent_ref")
    if formula_params is not None:
        record["formula_params"] = _safe_summary(dict(formula_params))
    return record


class MathTraceCollector:
    """一次既有事务的短生命周期 collector；调用方决定何时提交或丢弃。"""

    def __init__(self, *, root_kind: str, root_id: str, params_sha: str,
                 logical_ts: int, root_refs: Mapping[str, Any]):
        self.trace_id = make_trace_id(root_kind, root_id)
        self.params_sha = _safe_identifier(params_sha, "params_sha")
        self.logical_ts = int(logical_ts)
        self.root_refs = _safe_refs(root_refs)
        self._records: list[dict[str, Any]] = []

    def record(self, *, phase: str, formula_id: str,
               input_summary: Mapping[str, Any] | None = None,
               output_summary: Mapping[str, Any] | None = None,
               decision_reason: str = "observed", active_or_shadow: str = "active",
               parent_ref: str | None = None,
               formula_params: Mapping[str, Any] | None = None,
               logical_ts: int | None = None) -> dict[str, Any]:
        span = build_span(
            trace_id=self.trace_id,
            span_id=len(self._records) + 1,
            phase=phase,
            formula_id=formula_id,
            params_sha=self.params_sha,
            logical_ts=self.logical_ts if logical_ts is None else int(logical_ts),
            root_refs=self.root_refs,
            input_summary=input_summary,
            output_summary=output_summary,
            decision_reason=decision_reason,
            active_or_shadow=active_or_shadow,
            parent_ref=parent_ref,
            formula_params=formula_params,
        )
        self._records.append(span)
        return deepcopy(span)

    def records(self) -> tuple[dict[str, Any], ...]:
        """返回不可反向修改 collector 的深拷贝快照。"""
        return tuple(deepcopy(self._records))


def explain_trace(records, *, trace_id: str | None = None) -> dict[str, Any]:
    """将一个 trace 压缩成适合终端/测试阅读的无正文因果时间线。"""
    rows = [dict(row) for row in (records or ()) if isinstance(row, Mapping)]
    available = sorted({str(row.get("trace_id")) for row in rows if row.get("trace_id")})
    if trace_id is None:
        if len(available) != 1:
            raise ValueError("请显式提供 trace_id")
        trace_id = available[0]
    wanted = _safe_identifier(trace_id, "trace_id")
    selected = sorted((row for row in rows if row.get("trace_id") == wanted),
                      key=lambda row: int(row.get("span_id", 0)))
    if not selected:
        raise KeyError("math trace 不存在: " + wanted)
    steps = []
    for row in selected:
        steps.append({key: deepcopy(row.get(key)) for key in (
            "span_id", "phase", "formula_id", "formula_version", "params_sha",
            "logical_ts", "root_refs", "input_summary", "output_summary",
            "formula_params", "decision_reason", "active_or_shadow", "parent_ref",
        ) if key in row})
    return {"schema_version": MATH_TRACE_SCHEMA_VERSION, "trace_id": wanted,
            "span_count": len(steps), "steps": steps}


def first_trace_difference(left_records, right_records, *, left_trace_id: str | None = None,
                           right_trace_id: str | None = None) -> dict[str, Any] | None:
    """比较两条冻结 trace 的首个公式/参数/决策差异，不比较根 ID 与时间引用。"""
    left = explain_trace(left_records, trace_id=left_trace_id)["steps"]
    right = explain_trace(right_records, trace_id=right_trace_id)["steps"]
    compared = ("phase", "formula_id", "formula_version", "params_sha", "input_summary",
                "output_summary", "formula_params", "decision_reason", "active_or_shadow")
    for index, (a, b) in enumerate(zip(left, right), start=1):
        for field in compared:
            if a.get(field) != b.get(field):
                return {"span_index": index, "field": field,
                        "left": deepcopy(a.get(field)), "right": deepcopy(b.get(field))}
    if len(left) != len(right):
        return {"span_index": min(len(left), len(right)) + 1,
                "field": "span_count", "left": len(left), "right": len(right)}
    return None
