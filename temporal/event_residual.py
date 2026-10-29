"""已提交用户事件的只读墙钟残量。

这不是开口判定，也不产生命令、计划或投递。它只把冻结的 ``opening_anchor``
在一个明确的真实墙钟时刻投影成 ``e_user(t)``，供 F06 输出后效应读取。把这项
读数从旧 ``opening_margin`` 中拆出，避免正式输出为了取得事件残量而误触旧的
开口资格计算。
"""
from __future__ import annotations

import math

from temporal.proactive import ROOT_ORDER
from temporal.root_time import RECOVERABLE_AXES


HOUR_MS = 3_600_000.0


def opening_anchor(state):
    """读取冻结的已提交用户事件身份；没有锚就没有事件残量。"""
    anchor = (state or {}).get("opening_anchor")
    if not isinstance(anchor, dict) or anchor.get("ts") is None:
        return None
    try:
        at = int(anchor["ts"])
    except (TypeError, ValueError):
        return None
    return {
        "ts": at,
        "turn_id": str(anchor.get("turn_id") or ""),
        "delta": {axis: float((anchor.get("delta") or {}).get(axis, 0.0) or 0.0)
                  for axis in ROOT_ORDER},
    }


def user_event_residual(state, now_ms, *, tau_hours=None, axis_rates_per_hour=None):
    """在 ``now_ms`` 读取不可变用户锚的指数残量，并返回完整审计收据。

    调用方必须明确提供旧兼容的 ``tau_hours``，或正式 F_time 的
    ``axis_rates_per_hour``。本模块不读取人格、状态、开口门限或 tick，从而不能
    成为隐形调度器。
    """
    has_tau = tau_hours is not None
    has_rates = axis_rates_per_hour is not None
    if has_tau == has_rates:
        raise ValueError("事件残量必须且只能提供 tau_hours 或 axis_rates_per_hour")
    if has_rates:
        rates = axis_rates_per_hour if isinstance(axis_rates_per_hour, dict) else {}
        missing = set(RECOVERABLE_AXES) - set(rates)
        if missing:
            raise ValueError("事件残量分轴速率缺少：" + ", ".join(sorted(missing)))
        parsed_rates = {axis: max(0.0, float(rates[axis])) for axis in RECOVERABLE_AXES}
    else:
        tau = max(1e-6, float(tau_hours or 0.0))
    anchor = opening_anchor(state)
    if anchor is None:
        receipt = {
            "version": ("user-event-residual-v2" if has_rates else "user-event-residual-v1"),
            "anchor_present": False,
            "anchor_ts": None, "anchor_turn_id": "", "elapsed_hours": 0.0,
            "delta": {axis: 0.0 for axis in ROOT_ORDER},
            "delta_original": {axis: 0.0 for axis in ROOT_ORDER},
        }
        if has_rates:
            receipt["axis_rates_per_hour"] = dict(parsed_rates)
            receipt["decay_by_axis"] = {axis: 0.0 for axis in ROOT_ORDER}
        else:
            receipt["tau_hours"] = tau
            receipt["decay"] = 0.0
        return receipt
    try:
        at = int(now_ms)
    except (TypeError, ValueError):
        at = int(anchor["ts"])
    elapsed_h = max(0.0, (at - int(anchor["ts"])) / HOUR_MS)
    original = dict(anchor["delta"])
    if has_rates:
        decay_by_axis = {
            axis: math.exp(-float(parsed_rates[axis]) * elapsed_h)
            if axis in RECOVERABLE_AXES else 1.0
            for axis in ROOT_ORDER
        }
        return {
            "version": "user-event-residual-v2", "anchor_present": True,
            "anchor_ts": int(anchor["ts"]), "anchor_turn_id": anchor["turn_id"],
            "elapsed_hours": round(elapsed_h, 6),
            "axis_rates_per_hour": {axis: round(value, 9) for axis, value in parsed_rates.items()},
            "decay_by_axis": {axis: round(value, 9) for axis, value in decay_by_axis.items()},
            "delta": {axis: round(float(original[axis]) * decay_by_axis[axis], 6)
                      for axis in ROOT_ORDER},
            "delta_original": {axis: round(float(original[axis]), 6) for axis in ROOT_ORDER},
        }
    decay = math.exp(-elapsed_h / tau)
    return {
        "version": "user-event-residual-v1", "anchor_present": True,
        "anchor_ts": int(anchor["ts"]), "anchor_turn_id": anchor["turn_id"],
        "elapsed_hours": round(elapsed_h, 6), "tau_hours": round(tau, 6),
        "decay": round(decay, 6),
        "delta": {axis: round(float(original[axis]) * decay, 6) for axis in ROOT_ORDER},
        "delta_original": {axis: round(float(original[axis]), 6) for axis in ROOT_ORDER},
    }
