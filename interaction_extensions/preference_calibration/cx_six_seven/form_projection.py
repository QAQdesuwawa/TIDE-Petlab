"""连续状态到表达形式词元的本地编译器（默认影子）。

它不规定台词或字数。B₀/D(t) 只提供三维形式方向；C/X/e 的 activity 投影决定
该方向是否进入本轮作者注意力。少量固定语义只承担“数值→语言接口”，不是
“某类人格/场景必须如何说话”。
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from model_packages.cx_six_seven_v1.preference_calibration_parameters import CADENCE, FORM
from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import (
    PROTOTYPES,
    cos_similarity,
    uniform_guard,
)
from model_packages.cx_six_seven_v1.expression_projection_primitives import (
    CONSTANT_ORDER,
    ROOT_ORDER,
    centered_root_vector,
    constant_vector,
)


_ROWS = ("expansion", "fragmentation", "open_ended")
_SEMANTICS = {
    "expansion": ("稍微展开一点", "收住，不必说满"),
    "fragmentation": ("允许断续地补一句", "尽量一口气说清"),
    "open_ended": ("留下自然的余波", "自然收束这次表达"),
}


def _clamp(value, low, high):
    return max(float(low), min(float(high), float(value)))


def _weights(value):
    raw = value if isinstance(value, dict) else {}
    values = {key: max(0.0, float(raw.get(key, 0.0) or 0.0)) for key in PROTOTYPES}
    total = sum(values.values())
    if total <= 0:
        return {key: 1.0 / len(PROTOTYPES) for key in PROTOTYPES}
    return {key: item / total for key, item in values.items()}


def _direction(params, row):
    rows = params.get("directions") or {}
    candidate = rows.get(row) if isinstance(rows, dict) else None
    raw = candidate if isinstance(candidate, dict) else CADENCE["direction"]
    return {key: _clamp(raw.get(key, 0.0), -1.0, 1.0) for key in PROTOTYPES}


def _level(value, maximum, params):
    if maximum <= 0:
        return "弱"
    ratio = abs(float(value)) / maximum
    return ("强" if ratio >= params["level_strong_ratio"] else
            "中" if ratio >= params["level_mid_ratio"] else "弱")


def _sigmoid(value):
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, float(value)))))


def _dot(left, right):
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _matrix_row(matrix, constants):
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CONSTANT_ORDER):
        raise ValueError("FORM.activity_matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(ROOT_ORDER):
            raise ValueError("FORM.activity_matrix 必须是 7×6")
        rows.append(tuple(float(value) for value in row))
    return tuple(sum(constants[i] * rows[i][j] for i in range(len(CONSTANT_ORDER)))
                 for j in range(len(ROOT_ORDER)))


def _event_magnitude(event_delta):
    values = event_delta if isinstance(event_delta, dict) else {}
    return min(1.0, math.sqrt(sum(float(values.get(axis, 0.0) or 0.0) ** 2
                              for axis in ROOT_ORDER)))


@dataclass(frozen=True)
class FormProjection:
    version: str
    active: bool
    guard: str | None
    shadow: dict
    controls: dict
    lines: tuple
    receipt: tuple
    activity: dict | None = None

    def as_record(self):
        return {
            "version": self.version,
            "active": self.active,
            "guard": self.guard,
            "shadow": dict(self.shadow),
            "controls": dict(self.controls),
            "tokens": list(self.receipt),
            "activity": dict(self.activity or {}),
        }


def compile_form_projection(*, baseline, dynamic, constants=None, variables=None,
                            event_delta=None, params=None):
    """以 B0/D 的写作方向和 C/X/e 的连续 gate 生成形式词元。

    B0/D 仍是用户偏好层的形式方向；行为强度只来自本轮根状态和已结算事件。
    缓存 ``P``、未回应账本及任何消息数量都不得进入这条公式。
    """
    p = params or FORM
    b0, dt = _weights(baseline), _weights(dynamic)
    c = constant_vector(constants)
    dx = centered_root_vector(variables)
    weights = _matrix_row(p["activity_matrix"], c)
    event_magnitude = _event_magnitude(event_delta)
    activity_logit = (float(p["activity_bias"]) + _dot(weights, dx)
                      + float(p["activity_event_gain"]) * event_magnitude)
    activity = _sigmoid(activity_logit)
    if uniform_guard(b0):
        alignment, gate, guard = 0.0, 0.0, "uniform_baseline"
    else:
        alignment = _clamp(cos_similarity(b0, dt), 0.0, 1.0)
        gate = activity
        guard = None
    shadow = {
        row: round(_clamp(sum(b0[key] * _direction(p, row)[key] for key in PROTOTYPES)
                                * alignment * gate, -1.0, 1.0), 6)
        for row in _ROWS
    }
    active = bool(p.get("enabled", False)) and guard is None
    controls = dict(shadow) if active else {row: 0.0 for row in _ROWS}
    maximum = max((abs(value) for value in controls.values()), default=0.0)
    lines, receipt = [], []
    for row in _ROWS:
        value = controls[row]
        if abs(value) <= 0.0:
            continue
        level = _level(value, maximum, p)
        text = _SEMANTICS[row][0 if value > 0 else 1]
        lines.append("- 【表达形式·%s】这是写法上的注意力，不是行动命令；%s。" %
                     (level, text))
        receipt.append({"axis": row, "direction": "positive" if value > 0 else "negative",
                        "level": level})
    activity_receipt = {
        "gate": round(gate, 6), "logit": round(activity_logit, 6),
        "event_magnitude": round(event_magnitude, 6),
        "root_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in dx))),
        "state_terms": dict(zip(ROOT_ORDER, (round(weight * value, 6)
                                               for weight, value in zip(weights, dx)))),
    }
    return FormProjection(str(p.get("version") or FORM["version"]), active, guard,
                          shadow, controls, tuple(lines), tuple(receipt), activity_receipt)
