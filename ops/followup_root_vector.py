"""F14 即时续话的根向量双投影（M9 迁移纯函数）。

它不调度、不写状态、也不读取 writer/词元/记忆/B0。输入严格是 ``C, X, e``；
Runner 完成接线前，此模块只作为可回放数学基准与 trace 生产者。
"""
from __future__ import annotations

import math

from core.params import FOLLOWUP, PEER_REACTIVE
from temporal.proactive import (CONSTANT_ORDER, ROOT_ORDER, centered_root_vector,
                                constant_vector)


def _sigmoid(value):
    value = max(-60.0, min(60.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _dot(left, right):
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _matrix_weights(matrix, constants):
    """C^T A；严格校验 7×6，避免静默截断导致 trace 不可回放。"""
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CONSTANT_ORDER):
        raise ValueError("F14 projection matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(ROOT_ORDER):
            raise ValueError("F14 projection matrix 必须是 7×6")
        rows.append(tuple(float(value) for value in row))
    return tuple(sum(constants[i] * rows[i][j] for i in range(len(CONSTANT_ORDER)))
                 for j in range(len(ROOT_ORDER)))


def _event_vector(variable_deltas=None):
    event = variable_deltas if isinstance(variable_deltas, dict) else {}
    values = []
    for axis in ROOT_ORDER:
        try:
            values.append(max(-1.0, min(1.0, float(event.get(axis, 0.0) or 0.0))))
        except (TypeError, ValueError):
            values.append(0.0)
    return tuple(values)


def _cosine(left, right):
    left_norm = math.sqrt(_dot(left, left))
    right_norm = math.sqrt(_dot(right, right))
    if left_norm <= 1e-12 or right_norm <= 1e-12:
        return 0.0
    return _dot(left, right) / (left_norm * right_norm)


def root_vector_margin(state, constants, variable_deltas=None, *, params, fallback,
                       receipt_attenuation=False, require_external_event=False,
                       formula_label="root-vector"):
    """计算一条即时调度路径的 ``I-Theta``，不写状态也不调度。

    这是共享的矩阵算子，不是新的状态层：调用方必须显式给出自己的版本化参数、
    外部事件边界。已提交输出的冻结 ``e_out`` 已回写根状态，故不得通过 receipt
    次数再次进入公式；peer reactive 只接受本轮非零外部 ``e``，不能从旧状态自行生成来信。
    """
    p = params if isinstance(params, dict) and "impulse_state_matrix" in params else fallback
    c = constant_vector(constants)
    dx = centered_root_vector((state or {}).get("vars") or {})
    event = _event_vector(variable_deltas)
    impulse_state_weights = _matrix_weights(p["impulse_state_matrix"], c)
    impulse_event_weights = _matrix_weights(p["impulse_event_matrix"], c)
    gate_state_weights = _matrix_weights(p["gate_state_matrix"], c)
    gate_event_weights = _matrix_weights(p["gate_event_matrix"], c)
    impulse_state_terms = tuple(w * x for w, x in zip(impulse_state_weights, dx))
    impulse_event_terms = tuple(w * e for w, e in zip(impulse_event_weights, event))
    gate_state_terms = tuple(w * x for w, x in zip(gate_state_weights, dx))
    gate_event_terms = tuple(w * e for w, e in zip(gate_event_weights, event))
    bias = _dot(c, tuple(float(value) for value in p["gate_bias"]))
    # 兼容形参在 P0 迁移期间刻意失效。输出后效只以 e_out -> X 的单向方式
    # 影响后续 F14/F15，绝不经未回应计数或第二条衰减标量旁路。
    del receipt_attenuation
    attenuation = 1.0
    impulse_logit = _dot(impulse_state_weights, dx) + _dot(impulse_event_weights, event)
    gate_logit = bias + _dot(gate_state_weights, dx) + _dot(gate_event_weights, event)
    impulse_state = _sigmoid(impulse_logit)
    gate = _sigmoid(gate_logit)
    impulse = attenuation * impulse_state
    event_present = any(abs(value) > 1e-12 for value in event)
    eligible = bool(impulse >= gate and (event_present or not require_external_event))
    return {
        "version": str(p.get("version") or formula_label),
        "impulse": round(impulse, 6),
        "impulse_state": round(impulse_state, 6),
        "gate": round(gate, 6),
        "margin": round(impulse - gate, 6),
        "eligible": eligible,
        "attenuation": round(attenuation, 6),
        "event_present": bool(event_present),
        "constant_vector": dict(zip(CONSTANT_ORDER, (round(value, 6) for value in c))),
        "root_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in dx))),
        "event_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in event))),
        "impulse_state_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in impulse_state_terms))),
        "impulse_event_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in impulse_event_terms))),
        "gate_state_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in gate_state_terms))),
        "gate_event_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in gate_event_terms))),
        "impulse_logit": round(impulse_logit, 6),
        "gate_logit": round(gate_logit, 6),
        "gate_bias": round(bias, 6),
        "direction_cosine": round(_cosine(
            tuple(a + b for a, b in zip(impulse_state_weights, impulse_event_weights)),
            tuple(a + b for a, b in zip(gate_state_weights, gate_event_weights))), 6),
        "direction_warn": abs(_cosine(
            tuple(a + b for a, b in zip(impulse_state_weights, impulse_event_weights)),
            tuple(a + b for a, b in zip(gate_state_weights, gate_event_weights))))
            >= float(p["direction_cosine_warn"]),
    }


def f14_margin(state, constants, variable_deltas=None, params=None):
    """返回 F14 自主续话的 ``I14, Theta14, margin`` 与逐根轴 receipt。"""
    return root_vector_margin(
        state, constants, variable_deltas, params=params, fallback=FOLLOWUP,
        receipt_attenuation=False, require_external_event=False,
        formula_label="f14-root-vector-v1")


def peer_reactive_margin(state, constants, variable_deltas=None, params=None):
    """返回 peer 来信回应的独立 ``I_R, Theta_R, margin``。

    它和 F14 共用矩阵算子：本轮是否回应只由 C/X/e 决定，
    且必须真实存在一项已提交外部事件增量，防止旧状态自激。
    """
    return root_vector_margin(
        state, constants, variable_deltas, params=params, fallback=PEER_REACTIVE,
        receipt_attenuation=False, require_external_event=True,
        formula_label="peer-reactive-root-vector-v1")
