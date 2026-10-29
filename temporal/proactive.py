"""F15 主动开口：根向量双投影，纯数理、零 LLM。

F15 不再把 X 压成一个 ``activation`` 后扇出，也不读取 P、驱动力、词元、记忆、
writer 或未提交草稿。它只读 C、六轴 X 与真实经过时间：
``margin = impulse(C, X, dt) - gate(C, X)``。
"""
from __future__ import annotations

import copy
import math

from core.params import ACTOR, FALLBACK_VARS, PROACTIVE, TIME

MIN_MS = 60_000.0
HOUR_MS = 3_600_000.0
CONSTANT_ORDER = ("a1", "a2", "b1", "b2", "b3", "g1", "g2")
ROOT_ORDER = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")


def _unit(value, default=0.0):
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def _sigmoid(value):
    value = max(-60.0, min(60.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _dot(left, right):
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _matrix_row(matrix, constants):
    """计算 ``C^T A``，并拒绝静默截断错误矩阵。"""
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CONSTANT_ORDER):
        raise ValueError("F15 projection matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(ROOT_ORDER):
            raise ValueError("F15 projection matrix 必须是 7×6")
        rows.append(tuple(float(item) for item in row))
    return tuple(sum(constants[i] * rows[i][j] for i in range(len(CONSTANT_ORDER)))
                 for j in range(len(ROOT_ORDER)))


def root_reference():
    """F15 的固定 X0；不是上轮投影或可变缓存。"""
    rest = TIME["rest_point"]
    return {
        "E_env": max(float(FALLBACK_VARS["E_env"]), float(rest["E_env"])),
        "L_self": 0.0,
        "L_opponent": 0.0,
        "P_need": max(float(FALLBACK_VARS["P_need"]), float(rest["P_need"])),
        "V_attr": max(float(FALLBACK_VARS["V_attr"]), float(rest["V_attr"])),
        "V_rep": max(float(FALLBACK_VARS["V_rep"]), float(rest["V_rep"])),
    }


def root_vector(variables=None):
    values = variables if isinstance(variables, dict) else {}
    return tuple(_unit(values.get(axis, FALLBACK_VARS.get(axis, 0.0)),
                       FALLBACK_VARS.get(axis, 0.0)) for axis in ROOT_ORDER)


def constant_vector(constants=None):
    values = constants if isinstance(constants, dict) else {}
    return tuple(_unit(values.get(axis, 0.0)) for axis in CONSTANT_ORDER)


def centered_root_vector(variables=None):
    reference = root_reference()
    values = root_vector(variables)
    return tuple(values[index] - float(reference[axis])
                 for index, axis in enumerate(ROOT_ORDER))


def _cosine(left, right):
    left_norm = math.sqrt(_dot(left, left))
    right_norm = math.sqrt(_dot(right, right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return 0.0
    return _dot(left, right) / (left_norm * right_norm)


def proactive_params(constants, cadence=None, params=None, variables=None):
    """返回 F15 的时间包络与两条**独立**根向量投影 receipt。

    ``cadence`` 保留为兼容参数但故意不读取：B0/偏好校准不进入 NPC 的 F15 核心。
    """
    del cadence
    # ``params`` 仅接受显式 F15 参数快照；在线 schedule/Actor 的动态 tick 不会
    # 将 TIME 或 cadence 作为 F15 的隐式输入。F15 矩阵始终属于 PROACTIVE。
    p = params if isinstance(params, dict) and "impulse_matrix" in params else PROACTIVE
    c = constant_vector(constants)
    x_delta = centered_root_vector(variables)
    impulse_weights = _matrix_row(p["impulse_matrix"], c)
    gate_weights = _matrix_row(p["gate_matrix"], c)
    time_weights = _matrix_row(p.get("time_compression_matrix",
                                  ((0.0,) * len(ROOT_ORDER),) * len(CONSTANT_ORDER)), c)
    impulse_terms = tuple(weight * value for weight, value in zip(impulse_weights, x_delta))
    gate_terms = tuple(weight * value for weight, value in zip(gate_weights, x_delta))
    time_terms = tuple(weight * value for weight, value in zip(time_weights, x_delta))
    bias = _dot(c, tuple(float(value) for value in p["gate_bias"]))
    g2 = c[-1]
    amp = min(float(p["amp_cap"]), float(p["amp_base"]) + float(p["amp_g2"]) * g2)
    # 时间尺度也只能从 C/X 单向投影得来。这里没有“耐火期”、消息条数或
    # 场景分支：热态根向量让峰值更早到达，冷却后的根向量则自然恢复较慢节律。
    time_compression_h = _dot(time_weights, x_delta)
    tau_h = max(float(p["tau_floor_hours"]), float(p["tau_base_hours"]) -
                float(p["tau_g2"]) * g2 - time_compression_h)
    cosine = _cosine(impulse_weights, gate_weights)
    return {
        "version": str(p.get("version") or "f15-root-vector-v1"),
        "amp": amp, "tau_h": tau_h,
        # 统一开口函数读取同一 C/X 时间尺度的事件残留比例；它不是另一套
        # 调度阈值，只让一次已提交用户事件比“长期沉默”更快淡出。
        "opening_event_tau_ratio": float(p.get("opening_event_tau_ratio", 0.075)),
        "k": float(p["shape_base"]) + float(p["shape_g2"]) * g2,
        "constant_vector": dict(zip(CONSTANT_ORDER, (round(value, 6) for value in c))),
        "root_reference": {key: round(value, 6) for key, value in root_reference().items()},
        "root_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in x_delta))),
        "impulse_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in impulse_weights))),
        "gate_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in gate_weights))),
        "impulse_logit": _dot(impulse_weights, x_delta),
        "gate_logit": bias + _dot(gate_weights, x_delta),
        "impulse_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in impulse_terms))),
        "gate_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in gate_terms))),
        "time_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in time_terms))),
        "time_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in time_weights))),
        "time_compression_h": round(time_compression_h, 6),
        "gate_bias": round(bias, 6),
        "direction_cosine": round(cosine, 6),
        "direction_warn": abs(cosine) >= float(p["direction_cosine_warn"]),
    }


def urgency(t_hours, pc, cold_n=None):
    """时间包络 ``u(C, dt)``，没有旧门线、P 门或耐火期。

    ``cold_n`` 只保留给历史调用的兼容签名，刻意不参与计算；已提交输出会先以
    ``e_out`` 回写根状态，不能再通过消息计数二次衰减。
    """
    del cold_n
    if t_hours <= 0.0:
        return 0.0
    ratio = float(t_hours) / float(pc["tau_h"])
    base = float(pc["amp"]) * (ratio ** float(pc["k"])) * math.exp(
        float(pc["k"]) * (1.0 - ratio))
    return base


def f15_margin(constants, variables, elapsed_hours, unanswered_n=None, params=None):
    """一次可回放 F15 读数：冲动、动态门限、余量及逐根轴贡献。"""
    pc = proactive_params(constants, params=params, variables=variables)
    del unanswered_n
    envelope = urgency(elapsed_hours, pc)
    impulse_state = _sigmoid(pc["impulse_logit"])
    gate = _sigmoid(pc["gate_logit"])
    impulse = envelope * impulse_state
    return {
        "version": pc["version"],
        "elapsed_hours": round(max(0.0, float(elapsed_hours)), 6),
        "envelope": round(envelope, 6),
        "impulse_state": round(impulse_state, 6),
        "impulse": round(impulse, 6),
        "gate": round(gate, 6),
        "margin": round(impulse - gate, 6),
        "root_delta": pc["root_delta"],
        "impulse_terms": pc["impulse_terms"], "gate_terms": pc["gate_terms"],
        "impulse_weights": pc["impulse_weights"], "gate_weights": pc["gate_weights"],
        "gate_bias": pc["gate_bias"], "direction_cosine": pc["direction_cosine"],
        "direction_warn": pc["direction_warn"], "params": pc,
    }


def proactive_root_projection(variables=None, params=None):
    """弃用兼容入口：不再提供可回灌的 activation。"""
    del params
    return {"deprecated": True, "root_delta": dict(zip(ROOT_ORDER, centered_root_vector(variables)))}


def _observation_step_ms(authority_receipt):
    """从当前正式 P3 receipt 读取下一离线观察间隔。"""
    receipt = authority_receipt if isinstance(authority_receipt, dict) else {}
    try:
        return max(1, int(receipt["h_next_ms"])), {
            "version": "tick-from-opening-authority-v1",
            "density": float(receipt["Q_tick"]),
            "interval_ms": int(receipt["h_next_ms"]),
            "authority_receipt_version": str(receipt.get("version") or ""),
            "authority_event": dict(receipt.get("event") or {}),
        }
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("离线 replay 必须消费完整 P3 authority receipt") from exc


def first_urgency_crossing_ms(*_args, **_kwargs):
    """旧冻结未来求根入口已移除；调度必须在 tick 后读真实 margin。"""
    return None


def replay_tick_lifecycle(state, constants, now_ms, params=None, cap=None, cadence=None, *,
                          effective_constants=None, effective_constants_version=None,
                          effective_time_params=None):
    """离线回放按观察 tick 推进 F08，再读取统一开口 margin。

    该函数保留旧名称仅为兼容调用方；它不再另算 F15。每个观察点使用最近用户
    event anchor，因此离线恢复与在线 reply/tick 的资格数学相同。
    """
    del cadence
    from temporal.bus import advance, update_hidden

    if (effective_constants is None) != (effective_constants_version is None):
        raise ValueError("离线 tick 的 C_effective 与版本必须成对提供")
    # 正式调用方会把同一会话绑定同时传给 F08、F14/F15 和 observation density。
    # 未传的窄纯函数夹具仍以显式 ``constants`` 自测；这里绝不猜测 profile/prior。
    calculation_constants = (effective_constants
                             if effective_constants is not None else constants)
    del params
    from core.interaction_geometry import (OPENING_AUTHORITY_V2,
                                           SCHEDULE_VECTOR_AUTHORITY_V1,
                                           opening_authority_receipt)
    from temporal.opening import opening_anchor
    if state.get("last_ts") is None or not isinstance(state.get("opening_anchor"), dict):
        return {"points": [], "decision": {"rule": "no_opening_anchor", "candidate_count": 0}}
    diagnostic_cap = None if cap is None else max(0, int(cap))
    now = int(now_ms)
    origin = copy.deepcopy(state)
    simulated = copy.deepcopy(state)
    reference_ts = int(origin["last_ts"])
    observed_from = max(reference_ts, int(simulated.get("last_advance_ms") or reference_ts))
    observed_intervals = []
    points, last_margin = [], None
    stop = {"rule": "observation_window_complete"}
    t = observed_from
    while t <= now:
        before = copy.deepcopy(simulated)
        if t > observed_from:
            advance(simulated, t,
                    effective_constants=effective_constants,
                    effective_constants_version=effective_constants_version,
                    effective_time_params=effective_time_params)
            update_hidden(simulated, calculation_constants or {}, None, [], t)
        tick_id = "replay-tick:%d:%d" % (int(t), int(before.get("last_advance_ms") or 0))
        readout = opening_authority_receipt(
            (before.get("vars") or {}), (simulated.get("vars") or {}),
            calculation_constants or {},
            constants_version=str(effective_constants_version or "explicit-constants-v1"),
            event_kind="tick", event_id=tick_id, observed_at_ms=int(t),
            min_interval_ms=ACTOR["tick_min_ms"], max_interval_ms=ACTOR["tick_max_ms"],
            schedule_params=SCHEDULE_VECTOR_AUTHORITY_V1,
            opening_params=OPENING_AUTHORITY_V2)
        margin = float(readout["margin"])
        # anchor 只是自主资格的离散归属，不能参与 P3 数学或以高根态补造开口。
        crossed = (opening_anchor(simulated) is not None and margin >= 0.0
                   and (last_margin is None or last_margin < 0.0))
        # 起点是已经提交的用户/输出事件，不是一次自主 F15 观察；不能把它的
        # 正余量预先标成 held-above。下一次真正 replay tick 才有资格消费 crossing。
        if t <= observed_from and observed_from <= reference_ts:
            last_margin = None
        elif crossed and t > reference_ts:
            points.append({"ts": int(t), "state": copy.deepcopy(simulated), "margin": readout})
            reference_ts = int(t)
            # 这条纯扫描不伪造 F06 输出；保持 above 直到真实提交状态改变/下一段
            # replay 重新开始，避免同一 held-high receipt 在每个 tick 重复成点。
            last_margin = margin
            if diagnostic_cap is not None and len(points) >= diagnostic_cap:
                stop = {"rule": "diagnostic_limit"}
                break
        else:
            last_margin = margin
        step_ms, tick = _observation_step_ms(readout)
        observed_intervals.append(int(step_ms))
        t += step_ms
    return {"points": points, "decision": {**stop, "candidate_count": len(points),
            "observation_interval_source": "opening_authority_receipt", "observed_intervals_ms": observed_intervals,
            "last_margin": (None if last_margin is None
            else round(last_margin, 6)), "formula_version": "opening-authority-receipt-v1"}}


def speak_points(state, constants, now_ms, params=None, cap=None, cadence=None, *,
                 effective_constants=None, effective_constants_version=None,
                 effective_time_params=None):
    return replay_tick_lifecycle(state, constants, now_ms, params=params, cap=cap,
                            cadence=cadence, effective_constants=effective_constants,
                            effective_constants_version=effective_constants_version,
                            effective_time_params=effective_time_params)["points"]


def speak_point_plan(*args, **kwargs):
    """旧诊断别名；正式 Runner 必须调用 replay_tick_lifecycle。"""
    return replay_tick_lifecycle(*args, **kwargs)
