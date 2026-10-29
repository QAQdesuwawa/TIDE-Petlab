"""旧 root-vector tick 密度的显式兼容读数。

正式 Actor 从 P3 authority receipt 读取 ``Q_tick → h_next_ms``。本模块不产生消息、
不保存人格档位，也不看 F15 余量；只为缺 P3 receipt 的旧档／窄夹具保留可审计观察读数。
"""
from __future__ import annotations

import math
from typing import Mapping

from temporal.proactive import (CONSTANT_ORDER, ROOT_ORDER, centered_root_vector,
                                constant_vector)
from temporal.event_residual import opening_anchor, user_event_residual


HOUR_MS = 3_600_000.0


def _sigmoid(value):
    value = max(-60.0, min(60.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _compiled_weights(matrix, constants):
    """C^T A；按可用 C 归一，避免人格常数总量改变观察公式量程。"""
    c = constant_vector(constants)
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CONSTANT_ORDER):
        raise ValueError("tick projection matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(ROOT_ORDER):
            raise ValueError("tick projection matrix 必须是 7×6")
        rows.append(tuple(float(item) for item in row))
    scale = sum(c)
    # 缺 profile 的窄测试/首次启动仍得到中性的可观察节律；这不是人格默认分支。
    if scale <= 1e-12:
        c, scale = (1.0,) * len(CONSTANT_ORDER), float(len(CONSTANT_ORDER))
    return tuple(sum(c[i] * rows[i][j] for i in range(len(CONSTANT_ORDER))) / scale
                 for j in range(len(ROOT_ORDER)))


def _event_residual(state, now_ms, tau_h):
    receipt = user_event_residual(state, now_ms, tau_hours=tau_h)
    anchor = opening_anchor(state)
    return (tuple(float((receipt["delta"] or {}).get(axis, 0.0) or 0.0)
                  for axis in ROOT_ORDER), float(receipt["decay"]), anchor)


def tick_readout(state, constants, now_ms, params: Mapping[str, object]):
    """返回可追溯的连续 tick 密度与间隔。

    ``q_tick = sigmoid(b + <C^T A_x, X-X0> + <C^T A_e, e(t)> - Δt/tau)``
    ``interval = I_min + (I_max-I_min) * (1-q_tick)``

    I_min/I_max 是 CPU/队列公平边界；其间没有 hot/warm/cold 阈值、事件计数或
    消息计数。正式 NPC 输出的 F06 若降低根量，下一次这里自然变慢。
    """
    p = params or {}
    state = state if isinstance(state, dict) else {}
    constants = constants if isinstance(constants, dict) else {}
    now = int(now_ms)
    x_delta = centered_root_vector(state.get("vars") or {})
    state_weights = _compiled_weights(p["tick_state_matrix"], constants)
    event_weights = _compiled_weights(p["tick_event_matrix"], constants)
    event, event_decay, anchor = _event_residual(state, now, p["tick_event_tau_h"])
    state_terms = tuple(weight * value for weight, value in zip(state_weights, x_delta))
    event_terms = tuple(weight * value for weight, value in zip(event_weights, event))
    reference_ts = None
    for candidate in (state.get("last_ts"), anchor.get("ts") if anchor else None):
        try:
            reference_ts = max(int(candidate), int(reference_ts)) if reference_ts is not None else int(candidate)
        except (TypeError, ValueError):
            continue
    idle_h = max(0.0, (now - reference_ts) / HOUR_MS) if reference_ts is not None else 0.0
    logit = (float(p["tick_bias"]) + sum(state_terms) + sum(event_terms)
             - idle_h / max(1e-6, float(p["tick_idle_tau_h"])))
    density = _sigmoid(logit)
    low = int(p["tick_min_ms"])
    high = max(low, int(p["tick_max_ms"]))
    interval = int(round(low + (high - low) * (1.0 - density)))
    return {
        "version": str(p.get("tick_version") or "tick-root-vector-v1"),
        "density": round(density, 6), "interval_ms": interval,
        "idle_hours": round(idle_h, 6), "event_decay": round(event_decay, 6),
        "anchor_ts": anchor.get("ts") if anchor else None,
        "root_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in x_delta))),
        "event_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in event))),
        "state_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in state_weights))),
        "event_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in event_weights))),
        "state_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in state_terms))),
        "event_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in event_terms))),
        "logit": round(logit, 6),
        "bounds_ms": {"min": low, "max": high},
    }
