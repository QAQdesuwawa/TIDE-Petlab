"""已提交 NPC 输出的统一根状态后效应。

输出后效应只在文本、action receipt 与发送时间均已成立时由 Runner 冻结进 payload。
它不解释台词，不决定是否输出，也不读取未回应次数；职责只是把“已经说出口”作为一条
可回放的根状态事件，使后续 F14/F15 从新的 X，而不是从计数型冷却读取连续性。
"""
from __future__ import annotations

import math

from core.params import OUTPUT_RECEIPT
from temporal.proactive import CONSTANT_ORDER, ROOT_ORDER, centered_root_vector, constant_vector


def _sigmoid(value):
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, float(value)))))


def _dot(left, right):
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _matrix_weights(matrix, constants):
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CONSTANT_ORDER):
        raise ValueError("OUTPUT_RECEIPT.release_matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(ROOT_ORDER):
            raise ValueError("OUTPUT_RECEIPT.release_matrix 必须是 7×6")
        rows.append(tuple(float(value) for value in row))
    return tuple(sum(constants[i] * rows[i][j] for i in range(len(CONSTANT_ORDER)))
                 for j in range(len(ROOT_ORDER)))


def _root_event_vector(event_delta):
    values = event_delta if isinstance(event_delta, dict) else {}
    return tuple(float(values.get(axis, 0.0) or 0.0) for axis in ROOT_ORDER)


def receipt_out_effect(state, constants, event_delta=None, params=None):
    """返回一次已提交输出的 ``e_out(C, X-X0)`` 与完整只读 receipt。

    ``event_delta`` 只能是 F15 在 ``t_sent`` 结算出的当前用户事件残量，不接受文本、
    source 或 unanswered_n。它让已送达的输出对同一因果事件真正产生状态后效，而不
    修改不可变输入锚；模型逐字自述造成的 ``e_self`` 仍由独立验证器在事务边界合并。
    """
    p = params if isinstance(params, dict) and "release_matrix" in params else OUTPUT_RECEIPT
    c = constant_vector(constants)
    dx = centered_root_vector((state or {}).get("vars") or {})
    weights = _matrix_weights(p["release_matrix"], c)
    event_weights = _matrix_weights(p.get("event_release_matrix", p["release_matrix"]), c)
    bias = _dot(c, tuple(float(value) for value in p["release_bias"]))
    terms = tuple(weight * value for weight, value in zip(weights, dx))
    activation = _sigmoid(bias + _dot(weights, dx))
    by_axis = dict(zip(ROOT_ORDER, dx))
    event = _root_event_vector(event_delta)
    event_by_axis = dict(zip(ROOT_ORDER, event))
    deltas = {axis: 0.0 for axis in ROOT_ORDER}
    # 通用“说出口后的释放”只收缩高于静息点的情绪/靠近轴；其余根轴是关系事实或
    # 评价，不应因一次发言被悄悄复位。
    deltas["E_env"] = -float(p["env_gain"]) * activation * max(0.0, by_axis["E_env"])
    deltas["P_need"] = -float(p["need_gain"]) * activation * max(0.0, by_axis["P_need"])
    # 已交付输出并不“消费”输入事件；F15 仍会按墙钟读取不可变 e_user(t)。它只把
    # e 与 X 同向的可恢复偏离投回静息点。这样下一次 F15 读取的是 X+ 与自然衰减
    # 的 e(t)，而非同一高压 X 与同一事件反复自激。L 轴的矩阵先验为 0。
    event_alignment = {}
    event_terms = {}
    for index, axis in enumerate(ROOT_ORDER):
        aligned = max(0.0, float(dx[index]) * float(event[index]))
        event_alignment[axis] = aligned
        if axis in ("L_self", "L_opponent") or aligned <= 0.0:
            event_terms[axis] = 0.0
            continue
        magnitude = (float(p.get("event_gain", 0.0)) * activation *
                     abs(float(event_weights[index])) * aligned)
        # 按 X-X0 的方向回投，永不越过静息点。
        magnitude = min(abs(float(dx[index])), magnitude)
        delta = -math.copysign(magnitude, float(dx[index])) if magnitude else 0.0
        deltas[axis] += delta
        event_terms[axis] = delta
    return {
        "version": str(p.get("version") or "receipt-out-root-vector-v1"),
        "activation": round(activation, 6),
        "constant_vector": dict(zip(CONSTANT_ORDER, (round(value, 6) for value in c))),
        "root_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in dx))),
        "release_weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in weights))),
        "event_residual": {key: round(float(value), 6) for key, value in event_by_axis.items()},
        "event_release_weights": dict(zip(ROOT_ORDER,
                                           (round(value, 6) for value in event_weights))),
        "event_alignment": {key: round(float(value), 6)
                            for key, value in event_alignment.items()},
        "event_terms": {key: round(float(value), 6) for key, value in event_terms.items()},
        "release_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in terms))),
        "release_bias": round(bias, 6),
        "deltas": {key: round(float(value), 6) for key, value in deltas.items()},
    }
