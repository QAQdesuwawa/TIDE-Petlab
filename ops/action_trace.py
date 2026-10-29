"""按 action 汇集本地已提交 receipt 的只读追溯视图。

它不是新的事件总线、调度器或状态投影。调用方只把一个已发送 NPC
``action_id`` 交给本模块；本模块只按已有的 action/turn/root 引用读取 Store，
把 manifest、公式 span、P4、表现装配与本机 prompt trace 放在同一份诊断资料中。

缺失不是推断的理由：不存在的根引用、数学 span 或 prompt trace 都会显式出现在
``diagnostics``，而不会按相邻时间戳拼接。默认结果不含消息正文或 prompt；只有
本机调用方明确传入 ``include_prompt=True`` 才能取得同一 action 的实际 prompt。
"""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from typing import Any, Iterable, Mapping

from ops.api_receipt_manifest import build_action_manifest


SCHEMA_VERSION = "action-trace-inspector-v1"

# 即便 action trace 仅在本机查看，默认摘要仍不能把用户输入、完整人格卡或 writer
# prompt 夹带进普通 receipt 视图。完整 prompt 另由明确的 include_prompt 入口交付。
_BODY_KEYS = frozenset({
    "text", "raw", "content", "prompt", "system", "user", "message", "messages",
    "reply", "draft", "ammo", "reasoning", "completion", "persona_card",
})
_TEXT_RECEIPT_KEYS = frozenset({
    "action_id", "turn_id", "trace_id", "tick_id", "event_id", "event_kind", "kind",
    "source", "lane", "axis", "token_key", "formula_id", "formula_version", "version",
    "params_sha", "effective_constants_version", "delivery", "decision_reason", "model",
    "active_or_shadow", "parent_ref", "phase", "delivery_scope", "reason",
})
_ACTION_EVENT_KINDS = frozenset({
    "input_measurement_committed",
    "output_semantic_measurement_committed",
    "opening_authority_receipt",
    "opening_dispatch_committed",
    "output_commit_prepared",
    "output_state_committed",
    "memory_state_batch_committed",
    "memory_state_recall_suppressed",
    "expression_emission_committed",
    "recall_expression_declared",
    "calibration_observed",
})


def _text(value: object) -> str:
    return str(value or "")


def _body_digest(value: object) -> dict[str, object]:
    raw = _text(value)
    return {"redacted": True, "length": len(raw),
            "sha256": sha256(raw.encode("utf-8")).hexdigest()}


def _safe_receipt(value: Any, *, key: str = "") -> Any:
    """保留 receipt 的结构/数值，剥离自由正文。

    这不是通用序列化器，而是本 inspector 的隐私边界。无法证明是受控 receipt
    标签的字符串一律以长度/hash 摘要表示，防止未来 event schema 新增正文键时被
    诊断 UI 静默泄漏。
    """
    name = str(key or "")
    if name.lower() in _BODY_KEYS:
        return _body_digest(value)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        if name in _TEXT_RECEIPT_KEYS or name.endswith(("_id", "_kind", "_source", "_version", "_sha")):
            return value[:200]
        return _body_digest(value)
    if isinstance(value, Mapping):
        return {str(item_key): _safe_receipt(item_value, key=str(item_key))
                for item_key, item_value in value.items()}
    if isinstance(value, (tuple, list)):
        return [_safe_receipt(item, key=name) for item in value]
    return _body_digest(repr(value))


def _action_event_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    """只保留 action 追溯需要的已提交 event 结构。"""
    result = {key: row.get(key) for key in ("kind", "ts", "turn_id", "action_id", "params_sha")
              if row.get(key) is not None}
    for key in ("detail", "timeline", "expression_binding", "expression_emission",
                "memory_state_receipt"):
        if key in row:
            result[key] = _safe_receipt(row.get(key), key=key)
    return result


def _action_receipt_projection(row: Mapping[str, Any]) -> dict[str, Any]:
    return _safe_receipt(dict(row), key="action_receipt")


def _trace_refs(row: Mapping[str, Any]) -> Iterable[str]:
    refs = row.get("root_refs")
    if not isinstance(refs, Mapping):
        return ()
    values: list[str] = []
    for value in refs.values():
        if isinstance(value, (tuple, list)):
            values.extend(_text(item) for item in value)
        else:
            values.append(_text(value))
    return tuple(item for item in values if item)


def _dispatch_tick_ids(events: Iterable[Mapping[str, Any]], action_id: str) -> tuple[str, ...]:
    """只从该 action 自己的 dispatch receipt 读取 P3 tick ID。"""
    ids: list[str] = []
    for row in events:
        if _text(row.get("kind")) != "opening_dispatch_committed":
            continue
        if _text(row.get("action_id")) != action_id:
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), Mapping) else {}
        authority = detail.get("authority") if isinstance(detail.get("authority"), Mapping) else {}
        event_id = _text(authority.get("event_id"))
        if event_id and event_id not in ids:
            ids.append(event_id)
    return tuple(ids)


def _matching_math_spans(rows: Iterable[Mapping[str, Any]], *, action_id: str,
                         turn_id: str, tick_ids: Iterable[str]) -> list[dict[str, Any]]:
    """按现存 root/parent 引用联结数学 span，绝不按时间距离补线。"""
    roots = {action_id}
    if turn_id:
        roots.add(turn_id)
    roots.update(str(item) for item in tick_ids if str(item))
    selected = []
    for row in rows:
        refs = set(_trace_refs(row))
        parent_ref = _text(row.get("parent_ref"))
        if not (roots & refs or parent_ref == action_id):
            continue
        selected.append({key: deepcopy(row.get(key)) for key in (
            "trace_id", "span_id", "phase", "formula_id", "formula_version", "params_sha",
            "logical_ts", "root_refs", "input_summary", "output_summary", "formula_params",
            "decision_reason", "active_or_shadow", "parent_ref",
        ) if key in row})
    return sorted(selected, key=lambda item: (
        int(item.get("logical_ts", 0) or 0), _text(item.get("trace_id")),
        int(item.get("span_id", 0) or 0)))


def _writer_projection(trace: Mapping[str, Any] | None, *, include_prompt: bool) -> dict[str, Any]:
    if not isinstance(trace, Mapping):
        return {"available": False, "prompt_included": False}
    result: dict[str, Any] = {
        "available": True,
        "prompt_included": bool(include_prompt),
        "turn_id": trace.get("turn_id"),
        "ts": trace.get("ts"),
        "source": trace.get("source"),
        "system_sha": trace.get("system_sha"),
        "user_sha": trace.get("user_sha"),
        "persona_block_ids": list(trace.get("persona_block_ids") or ()),
        "response_block_ids": list(trace.get("response_block_ids") or ()),
        "recall_event_ids": list(trace.get("recall_event_ids") or ()),
        "excluded_ledger_ts": list(trace.get("excluded_ledger_ts") or ()),
        "history_sources": list(trace.get("history_sources") or ()),
        "history_exclusions": list(trace.get("history_exclusions") or ()),
        "requested_temperature": trace.get("requested_temperature"),
        "effective_temperature": trace.get("effective_temperature"),
        "model": trace.get("model"),
        "length_mode": trace.get("length_mode"),
        "assembly_receipt": _safe_receipt(trace.get("assembly_receipt") or {},
                                            key="assembly_receipt"),
    }
    if include_prompt:
        # 仅由本地显式入口请求；此处不向 manifest/export 回传，也不写回 Store。
        result["prompt"] = {
            "system": _text(trace.get("system")),
            "user": _text(trace.get("user")),
        }
    return result


def build_action_trace(store: object, action_id: str, *, include_prompt: bool = False) -> dict[str, Any]:
    """构造一份 action 级、严格只读的本地追溯资料。

    返回中的 ``manifest`` 是现有无正文因果验收 verdict；``math`` 与 ``writer``
    分别说明公式观察和 prompt 装配是否实际存在。观察缺口不会使本函数自行推演
    新记录，也不会覆盖 manifest 的 fail-closed 结论。
    """
    wanted = _text(action_id).strip()
    if not wanted:
        raise ValueError("action_id 不能为空")

    messages = list(getattr(store, "read_messages")())
    events = list(getattr(store, "read_events")())
    action_rows = list(getattr(store, "read_action_receipts")())
    prompt_rows = list(getattr(store, "read_prompt_traces")())
    math_rows = list(getattr(store, "read_math_trace")())
    manifest = build_action_manifest(store, wanted)

    action_meta = dict(manifest.get("action") or {})
    turn_id = _text(action_meta.get("turn_id"))
    output_rows = [row for row in messages
                   if _text(row.get("role")) == "npc"
                   and _text(row.get("action_id")) == wanted]
    exact_action_rows = [row for row in action_rows if _text(row.get("action_id")) == wanted]
    relevant_events = [row for row in events
                       if _text(row.get("kind")) in _ACTION_EVENT_KINDS
                       and (_text(row.get("action_id")) == wanted
                            or (turn_id and _text(row.get("turn_id")) == turn_id))]
    tick_ids = _dispatch_tick_ids(events, wanted)
    spans = _matching_math_spans(math_rows, action_id=wanted, turn_id=turn_id,
                                 tick_ids=tick_ids)
    matching_prompts = [row for row in prompt_rows
                        if turn_id and _text(row.get("turn_id")) == turn_id]
    chosen_prompt = matching_prompts[-1] if matching_prompts else None

    diagnostics: list[dict[str, str]] = []
    if len(output_rows) != 1:
        diagnostics.append({"code": "output_message_not_exactly_one",
                            "detail": f"count={len(output_rows)}"})
    if len(exact_action_rows) != 1:
        diagnostics.append({"code": "f16_action_receipt_not_exactly_one",
                            "detail": f"count={len(exact_action_rows)}"})
    if not matching_prompts:
        diagnostics.append({"code": "prompt_trace_missing", "detail": "exact_turn_id_not_found"})
    elif len(matching_prompts) > 1:
        diagnostics.append({"code": "prompt_trace_multiple", "detail": "using_last_exact_turn_trace"})
    if not spans:
        diagnostics.append({"code": "math_trace_missing_or_disabled",
                            "detail": "no_exact_action_turn_or_dispatch_tick_root_ref"})

    formula_ids = tuple(sorted({_text(row.get("formula_id")) for row in spans
                                if _text(row.get("formula_id"))}))
    p4_events = [row for row in relevant_events
                 if _text(row.get("kind")) in {
                     "memory_state_batch_committed", "memory_state_recall_suppressed"}]
    expression_events = [row for row in relevant_events
                         if _text(row.get("kind")) in {
                             "output_commit_prepared", "expression_emission_committed"}]
    return {
        "schema_version": SCHEMA_VERSION,
        "read_only": True,
        "privacy": ("local_prompt_only_when_explicit" if include_prompt
                    else "no_message_body_no_prompt_body"),
        "action": action_meta,
        "manifest": manifest,
        "link_keys": {
            "action_id": wanted,
            "turn_id": turn_id or None,
            "dispatch_tick_ids": list(tick_ids),
            "join_policy": "exact_action_turn_or_existing_root_ref_only",
        },
        "scheduler_receipts": [_action_event_projection(row) for row in relevant_events],
        "f16_receipts": [_action_receipt_projection(row) for row in exact_action_rows],
        "memory_p4_receipts": [_action_event_projection(row) for row in p4_events],
        "expression_receipts": [_action_event_projection(row) for row in expression_events],
        "math": {
            "available": bool(spans),
            "trace_ids": sorted({_text(row.get("trace_id")) for row in spans
                                  if _text(row.get("trace_id"))}),
            "formula_ids": list(formula_ids),
            "spans": spans,
        },
        "writer": _writer_projection(chosen_prompt, include_prompt=bool(include_prompt)),
        "diagnostics": diagnostics,
    }
