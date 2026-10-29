"""旧 root-vector opening 的显式兼容读数。

本模块只计算、绝不调度。P3 已接管正式 dynamic tick：自主开口必须读取
``opening_authority_receipt`` 中同源的 ``I_open/Theta_open/Q_tick``，不能调用本模块。
这里仅保留给旧档、fixture 和 peer 兼容对照；其 ``e_user(t)`` 读数也由独立的
``temporal.event_residual`` 统一提供。
"""
from __future__ import annotations

from core.params import FOLLOWUP
from ops.followup_root_vector import _event_vector, _matrix_weights
from ops.followup_root_vector import f14_margin
from temporal.proactive import (CONSTANT_ORDER, ROOT_ORDER, _dot, _sigmoid,
                                constant_vector, proactive_params, urgency)
from temporal.event_residual import (HOUR_MS, opening_anchor,
                                     user_event_residual)



def _bounded_elapsed_hours(value):
    try:
        return max(0.0, float(value))
    except (TypeError, ValueError):
        return 0.0


def elapsed_from_anchor(state, now_ms):
    anchor = opening_anchor(state)
    if anchor is None:
        return None, None
    try:
        now = int(now_ms)
    except (TypeError, ValueError):
        now = anchor["ts"]
    return anchor, max(0.0, (now - anchor["ts"]) / HOUR_MS)


def opening_margin(state, constants, now_ms, *, anchor=None, params=None):
    """计算唯一的 ``I_open - Theta_open``。

    状态/时间项沿用既有 F15 C×X 投影；用户事件项沿用既有 F14 C×e 投影。
    两者在本函数内同一次结算，而非按“追发/主动”切换公式。事件的指数淡出使用
    同一 C/X 时间尺度，故既没有固定耐火期，也不会由 NPC 输出重置时钟。
    """
    anchor = anchor or opening_anchor(state)
    if anchor is None:
        return {"version": "opening-root-vector-v2", "eligible": False,
                "rule": "no_user_anchor", "impulse": 0.0, "gate": 1.0,
                "margin": -1.0, "elapsed_hours": 0.0}
    try:
        at = int(now_ms)
    except (TypeError, ValueError):
        at = int(anchor["ts"])
    elapsed_h = max(0.0, (at - int(anchor["ts"])) / HOUR_MS)
    pc = proactive_params(constants or {}, params=params,
                          variables=(state or {}).get("vars") or {})
    c = constant_vector(constants or {})
    raw_event = _event_vector(anchor.get("delta") or {})
    # e_user 是输入事实，不能被 NPC 输出重置；但它也不能按“沉默后主动性”的
    # 小时包络原样存活。两者仍由同一 C/X tau_h 推出，只用版本化比例得到事件的
    # 更快残留时间尺度。没有未回应次数、耐火期或条数上限参与。
    ratio = float((params or {}).get("opening_event_tau_ratio",
                                     pc.get("opening_event_tau_ratio", 0.075)) or 0.075)
    event_tau_h = max(1e-6, float(pc["tau_h"]) * max(1e-6, ratio))
    event_receipt = user_event_residual(state, at, tau_hours=event_tau_h)
    event_decay = float(event_receipt["decay"])
    event = tuple(float((event_receipt["delta"] or {}).get(axis, 0.0) or 0.0)
                  for axis in ROOT_ORDER)
    p = FOLLOWUP
    impulse_event_weights = _matrix_weights(p["impulse_event_matrix"], c)
    gate_event_weights = _matrix_weights(p["gate_event_matrix"], c)
    event_impulse_logit = _dot(impulse_event_weights, event)
    event_gate_logit = _dot(gate_event_weights, event)
    # 同一根量门里有两项连续冲动：
    # - near：C/X/e_user 的即时投影，解释“刚说完还想补一句”；
    # - far：C/X 与墙钟包络，解释“沉默后又重新起念”。
    #
    # ``f14_margin`` 在此只是已版本化的 C/X/e 根向量投影算子，绝不单独决定
    # 是否发送；两项最后只与下面唯一的动态门限比较。e=0 时不凭高状态伪造一条
    # 近程事件拉力。
    near_readout = f14_margin(state or {}, constants or {}, anchor.get("delta") or {})
    event_present = any(abs(value) > 1e-12 for value in raw_event)
    near_impulse = event_decay * float(near_readout["impulse"]) if event_present else 0.0
    state_impulse = _sigmoid(pc["impulse_logit"])
    time_impulse = urgency(elapsed_h, pc) * state_impulse
    impulse = min(1.0, max(0.0, time_impulse + near_impulse))
    gate_logit = float(pc["gate_logit"]) + event_gate_logit
    gate = _sigmoid(gate_logit)
    margin = impulse - gate
    return {
        "version": "opening-root-vector-v2",
        "rule": "margin_crossed" if margin >= 0.0 else "margin_below_zero",
        "eligible": bool(margin >= 0.0),
        "anchor_ts": int(anchor["ts"]), "anchor_turn_id": str(anchor.get("turn_id") or ""),
        "elapsed_hours": round(elapsed_h, 6), "event_tau_hours": round(event_tau_h, 6),
        "event_decay": round(event_decay, 6),
        "time_envelope": round(urgency(elapsed_h, pc), 6),
        "impulse_state": round(state_impulse, 6),
        "impulse_time": round(time_impulse, 6), "impulse_event": round(near_impulse, 6),
        "near_impulse": round(near_impulse, 6),
        "near_root_projection": {
            "version": near_readout.get("version"),
            "impulse": near_readout.get("impulse"),
            "impulse_logit": near_readout.get("impulse_logit"),
            "state_terms": near_readout.get("impulse_state_terms"),
            "event_terms": near_readout.get("impulse_event_terms"),
        },
        "impulse": round(impulse, 6), "gate": round(gate, 6),
        "margin": round(margin, 6), "root_delta": pc["root_delta"],
        "event_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in event))),
        "event_delta_original": dict(zip(ROOT_ORDER, (round(value, 6) for value in raw_event))),
        "impulse_terms": pc["impulse_terms"], "gate_terms": pc["gate_terms"],
        "event_impulse_logit": round(event_impulse_logit, 6),
        "event_gate_logit": round(event_gate_logit, 6),
        "params": pc,
    }
