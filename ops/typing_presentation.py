"""把正式数学回执投影为“正在输入”的纯展示节律。

这层没有资格查看 ``P``、R、词元、记忆、旧 cadence/score/line 或回复正文。
它只消费同一轮已结算的 F03 ``Delta_X_event`` 与可用的 F14/F15/F16 receipt：因此
UI 的快慢既不改变总线，也不会让历史影子读数偷偷决定用户看见的行为。
"""
from __future__ import annotations

import math


ROOT_ORDER = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")


def _unit(value, default=0.0):
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return float(default)


def _clamp(value, low, high):
    return max(float(low), min(float(high), float(value)))


def _mapping(value):
    return value if isinstance(value, dict) else {}


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _event_magnitude(event_delta):
    """已提交 F03 事件向量的有界展示读数，不写回状态。"""
    event = _mapping(event_delta)
    l2 = math.sqrt(sum(max(-1.0, min(1.0, _number(event.get(axis, 0.0)))) ** 2
                       for axis in ROOT_ORDER))
    return _unit(l2 / math.sqrt(len(ROOT_ORDER)))


def _receipt_from(value):
    """只接受显式 presentation receipt；拒绝从旧 turn 字段回填。"""
    raw = _mapping(value)
    receipt = raw.get("presentation_receipt")
    return _mapping(receipt if receipt is not None else raw)


def typing_presentation_plan(turn_result=None):
    """从正式回执构造纯 UI 时间倍率。

    回执可以是 responder 前的 ``{event_delta}``，也可以追加已计算的 F14/F15/F16。
    缺任何字段只降低这一个展示分量，绝不回退去读 P/R/文本等旧旁路。
    """
    receipt = _receipt_from(turn_result)
    event = _event_magnitude(receipt.get("event_delta"))
    f14 = _mapping(receipt.get("f14"))
    f15 = _mapping(receipt.get("f15"))
    f16 = _mapping(receipt.get("f16"))
    f14_impulse = _unit(f14.get("impulse"))
    f14_margin = _clamp(_number(f14.get("margin", 0.0)), -1.0, 1.0)
    f15_impulse = _unit(f15.get("impulse"))
    f15_margin = _clamp(_number(f15.get("margin", 0.0)), -1.0, 1.0)
    f16_urgency = _unit(f16.get("urgency"))

    # 同一正式 receipt 的只读连续投影。事件描述“刚发生多少变化”，F14/F16
    # 分别描述已结算的续话／主动开口张力与当前回复节律；均不是新的决策量。
    engagement = _clamp(0.50 * event + 0.20 * f14_impulse + 0.15 * f15_impulse
                         + 0.15 * f16_urgency,
                         0.0, 1.0)
    tempo = _clamp(1.0 + 0.60 * engagement, 0.45, 1.60)
    return {
        "version": "typing_presentation_v2",
        "event_magnitude": round(event, 6),
        "f14_impulse": round(f14_impulse, 6),
        "f14_margin": round(f14_margin, 6),
        "f15_impulse": round(f15_impulse, 6),
        "f15_margin": round(f15_margin, 6),
        "f16_urgency": round(f16_urgency, 6),
        "engagement": round(engagement, 6),
        "initial_multiplier": round(_clamp(1.0 - 0.30 * engagement, 0.55, 1.20), 6),
        "visible_multiplier": round(_clamp(1.0 + 0.18 * engagement, 0.75, 1.18), 6),
        "hidden_multiplier": round(_clamp(1.0 - 0.30 * engagement, 0.60, 1.42), 6),
        "dot_multiplier": round(_clamp(1.0 / tempo, 0.62, 1.55), 6),
        "dwell_multiplier": round(_clamp(1.0 + 0.12 * engagement, 0.85, 1.12), 6),
        "reveal_multiplier": round(_clamp(1.0 - 0.22 * engagement, 0.72, 1.30), 6),
    }


def presentation_snapshot(value):
    """收窄 UI 反馈可携带的展示证据；它不构成新的运行时状态。"""
    plan = value if isinstance(value, dict) else {}
    numeric = (
        "event_magnitude", "f14_impulse", "f14_margin", "f15_impulse", "f15_margin",
        "f16_urgency", "engagement",
        "initial_multiplier", "visible_multiplier", "hidden_multiplier", "dot_multiplier",
        "dwell_multiplier", "reveal_multiplier",
    )
    result = {"version": str(plan.get("version") or "typing_presentation_v2")}
    for key in numeric:
        try:
            result[key] = round(float(plan[key]), 6)
        except (KeyError, TypeError, ValueError):
            continue
    return result
