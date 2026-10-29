"""提交后回合遥测的纯投影。

这个模块只读取已持久化的事实流和已经得出的回合结果，产物供 UI / 测试展示使用；
它不产生词元、不调用模型、不修改总线状态。``telemetry.jsonl`` 即使缺失也不影响
回复结果，因而只是一个可丢弃、可重建的观测缓存。
"""
from __future__ import annotations

from collections import Counter

# 观测层的固定显示顺序；不参与任何总线公式或调度。
ROOT_AXES = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")


SCHEMA_VERSION = 1


def _events_for_turn(events, turn_id, kind=None):
    return [event for event in events
            if event.get("turn_id") == turn_id and (kind is None or event.get("kind") == kind)]


def _safe_number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def project_state(state):
    """把 temporal 状态压成展示/回放可用的无文本摘要。"""
    current_state = state or {}
    accum = (current_state.get("accum") or {}).get("npc") or {}
    return {
        # P/E 和主导驱动力是 temporal 状态的顶层字段，不是 vars 中的用户输入变量。
        "P": _safe_number(current_state.get("P")),
        "E": _safe_number(current_state.get("E")),
        "dominant_drive": str(current_state.get("last_dominant") or ""),
        "inertia_turns": int(accum.get("inertia_streak", 0) or 0),
        # P/E 是根量的投影。保留六轴的只读数值快照，监测图才不会把一段连续
        # 状态误画成“回复后回到同一条基准线”。不存词元、正文或模型材料。
        "vars": {axis: _safe_number((current_state.get("vars") or {}).get(axis))
                 for axis in ROOT_AXES},
    }


def project_opening_transition(*, state, constants=None, ts, event, opening=None,
                               authority_receipt=None):
    """投影一次已经发生的交互状态转换，供演示轨迹只读追加。

    ``event`` 必须来自 Actor 已提交的 F03 输入、tick、计划或 output receipt。
    receipt 必须已经由 Runner 生成；这里不 advance state、不预测下一个点、更不以
    刷新时刻重新计算 I/Theta。``constants`` 仅为旧调用签名兼容，绝不参与投影。
    """
    del constants
    snapshot = state or {}
    at = int(ts or snapshot.get("last_advance_ms") or snapshot.get("last_ts") or 0)
    # ``opening`` 只保留为旧调用方显式交来的、已经冻结的 receipt 别名；没有
    # receipt 时明确 unavailable，不能由 state 补算一条假曲线。
    readout = (dict(authority_receipt) if isinstance(authority_receipt, dict)
               else dict(opening) if isinstance(opening, dict) else {})
    is_p3 = all(key in readout for key in ("I_open", "Theta_open", "margin"))
    is_explicit_legacy = all(key in readout for key in ("impulse", "gate", "margin"))
    if is_p3:
        opening_view = {
            "available": True, "impulse": _safe_number(readout.get("I_open")),
            "gate": _safe_number(readout.get("Theta_open")),
            "margin": _safe_number(readout.get("margin")),
            "elapsed_h": None, "q_tick": _safe_number(readout.get("Q_tick")),
            "formula_version": str((readout.get("formula_versions") or {}).get(
                "opening_projection") or ""),
        }
    elif is_explicit_legacy:
        # 旧存档能展示已存 receipt，但不准由 state 推导、补齐或升级它。
        opening_view = {
            "available": True, "impulse": _safe_number(readout.get("impulse")),
            "gate": _safe_number(readout.get("gate")),
            "margin": _safe_number(readout.get("margin")),
            "elapsed_h": _safe_number(readout.get("elapsed_hours")),
            "formula_version": str(readout.get("version") or "legacy-explicit-receipt"),
        }
    else:
        opening_view = {"available": False}
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "opening_state_transition",
        "event": str(event or "state"),
        "ts": at,
        "state": project_state(snapshot),
        "opening": opening_view,
    }


def _expression_ids(events):
    result = []
    for event in events:
        if event.get("kind") != "recall_expression_declared":
            continue
        local = (event.get("detail") or {}).get("local_observation") or {}
        for item in local.get("events") or []:
            event_id = item.get("event_id") if isinstance(item, dict) else item
            if event_id is not None:
                result.append(str(event_id))
    return list(dict.fromkeys(result))


def build_turn_telemetry(*, store, turn_result, state):
    """构造安全、无原文的单回合观测投影，不写入 ``store``。

    ``turn_result`` 来自已完成的 ``Runner.on_user_message``；调用方应只在该事务的
    状态、消息、事件都提交后调用本函数。
    """
    result = dict(turn_result or {})
    turn_id = str(result.get("turn_id") or "")
    events = store.read_events()
    turn_events = _events_for_turn(events, turn_id)
    input_event = next((event for event in turn_events
                        if event.get("kind") == "input_received"), {})
    shadow = next((event.get("detail") or {} for event in turn_events
                   if event.get("kind") == "recall_shadow"), {})
    selected = list(shadow.get("selected_bundles") or [])
    actual_refs = list(shadow.get("actual_refs") or [])
    sources = [str(item.get("source")) for item in actual_refs + selected
               if isinstance(item, dict) and item.get("source")]
    report = result.get("report") or {}
    messages = store.read_messages()
    reply = str(result.get("reply") or "")
    uncertainties = []
    if result.get("semantic_projection") is None:
        uncertainties.append("semantic_projection_unavailable")
    if not shadow:
        uncertainties.append("recall_audit_unavailable")
    if not result.get("action_id"):
        uncertainties.append("action_receipt_unavailable")

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "turn_telemetry",
        "turn_id": turn_id,
        "ts": int(input_event.get("ts") or 0),
        "provenance": {"params_sha": str(input_event.get("params_sha") or "")},
        "state": project_state(state),
        "recall": {
            "candidate_count": len(shadow.get("candidates") or []),
            "selected_sources": dict(sorted(Counter(sources).items())),
            "actual_reference_count": len(actual_refs),
            "suppressed_count": len(shadow.get("rejected_bundles") or []),
            "expression_event_ids": _expression_ids(turn_events),
        },
        "conversation": {
            "visible_user_turn_index": sum(1 for message in messages
                                             if message.get("role") == "user"),
            "reply_char_count": len(reply),
            "outcome_status": "pending" if result.get("action_id") else "unavailable",
        },
        "measurement": {
            "semantic_available": result.get("semantic_projection") is not None,
            "semantic_source": (str((result.get("semantic_projection") or {}).get("source") or "")
                                if isinstance(result.get("semantic_projection"), dict) else ""),
        },
        "uncertainties": uncertainties,
    }
