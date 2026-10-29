"""F16 节律延迟：从根量作一次只读呈现投影。

它不决定是否主动发言，也不改墙钟／状态。输入只允许是角色常量 C、已结算六轴
关系状态 X、本轮已经准入的 F03 ``Delta_X_event``、用户节奏基线和本地随机流。
词元、D(t)、回复草稿、文本长度和模型耗时不在此公式中。
"""
import math
import random

from core.params import DELAY
from temporal.proactive import (CONSTANT_ORDER, ROOT_ORDER, centered_root_vector,
                                constant_vector)


_AXES = ROOT_ORDER


def _unit(value):
    return max(0.0, min(1.0, float(value or 0.0)))


def _sigmoid(value):
    value = max(-60.0, min(60.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _dot(left, right):
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _matrix_row(matrix, constants):
    """计算 ``C^T A``，拒绝静默截断的 F16 参数快照。"""
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CONSTANT_ORDER):
        raise ValueError("F16 projection matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(ROOT_ORDER):
            raise ValueError("F16 projection matrix 必须是 7×6")
        rows.append(tuple(float(item) for item in row))
    return tuple(sum(constants[i] * rows[i][j] for i in range(len(CONSTANT_ORDER)))
                 for j in range(len(ROOT_ORDER)))


def _root_cadence_readout(constants, variables, event_deltas, params):
    """F16: ``C, X-X0, Delta_X_event -> urgency``；纯函数、无回灌。"""
    c = constant_vector(constants)
    x_delta = centered_root_vector(variables)
    event = tuple(max(-1.0, min(1.0, float((event_deltas or {}).get(axis, 0.0) or 0.0)))
                  for axis in ROOT_ORDER)
    state_weights = _matrix_row(params["state_matrix"], c)
    event_weights = _matrix_row(params["event_matrix"], c)
    state_terms = tuple(weight * value for weight, value in zip(state_weights, x_delta))
    event_terms = tuple(weight * value for weight, value in zip(event_weights, event))
    bias = _dot(c, tuple(float(value) for value in params["bias"]))
    logit = bias + sum(state_terms) + sum(event_terms)
    return {
        "c": c,
        "x": {axis: _unit((variables or {}).get(axis, 0.0)) for axis in ROOT_ORDER},
        "root_delta": x_delta,
        "event_delta": event,
        "state_weights": state_weights,
        "event_weights": event_weights,
        "state_terms": state_terms,
        "event_terms": event_terms,
        "bias": bias,
        "logit": logit,
        "urgency": _sigmoid(logit),
    }


def decide_delay_seconds(constants, variables, event_deltas, baseline=None, rng=None,
                         params=None, include_receipt=False):
    """返回 ``int`` 秒，或 ``(seconds, receipt)``；同 seed／根量必定同结果。

    防御常量决定基础的端着／犹豫时间；C/X/e 得出的连续 urgency 只按比例压缩
    该时间。不存在 ``P >= line`` 的突变秒回支路。
    """
    p = params or DELAY
    c = dict(constants or {})
    readout = _root_cadence_readout(c, variables, event_deltas, p)
    hold = (float(p["persona_base"])
            + float(p["persona_w_b2"]) * _unit(c.get("b2"))
            + float(p["persona_w_b1"]) * _unit(c.get("b1"))
            + float(p["persona_w_b3"]) * _unit(c.get("b3")))
    pace = 0.0
    if baseline and not baseline.get("cold", True):
        median = baseline.get("median_ms")
        if median:
            pace = min(float(p["pace_cap_sec"]),
                       float(median) / 1000.0 * float(p["pace_mirror"]))
    r = rng if rng is not None else random.Random()
    lo, hi = p["jitter"]
    jitter = r.uniform(float(lo), float(hi))
    # urgency=0 保持端着基线，urgency=1 把它连续压到 remain 比例；边界 clamp
    # 只是时间合法性，不是人格或追发上限。
    delay = hold * (1.0 - float(p["urgency_hold_reduction"]) * readout["urgency"])
    delay += pace + jitter
    result = max(int(p["min_sec"]), min(int(p["max_sec"]), int(round(delay))))
    if not include_receipt:
        return result
    return result, {
        "inputs": {
            "C": {key: round(value, 6) for key, value in zip(CONSTANT_ORDER, readout["c"])},
            "X": {key: round(value, 6) for key, value in readout["x"].items()},
            "Delta_X_event": {key: round(float((event_deltas or {}).get(key, 0.0) or 0.0), 6)
                              for key in _AXES},
            "baseline_cold": bool(not baseline or baseline.get("cold", True)),
            "baseline_median_ms": (round(float((baseline or {}).get("median_ms", 0.0) or 0.0), 3)
                                   if baseline and not baseline.get("cold", True) else None),
        },
        "readout": {
            "version": str(p.get("version") or "f16-root-vector-v1"),
            "root_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in readout["root_delta"]))),
            "event_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in readout["event_delta"]))),
            "state_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in readout["state_weights"]))),
            "event_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in readout["event_weights"]))),
            "state_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in readout["state_terms"]))),
            "event_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in readout["event_terms"]))),
            "bias": round(readout["bias"], 6),
            "logit": round(readout["logit"], 6),
            "urgency": round(readout["urgency"], 6),
        },
        "components": {"hold_sec": round(hold, 6), "pace_sec": round(pace, 6),
                       "jitter_sec": round(jitter, 6)},
        "delay_sec": result,
    }
