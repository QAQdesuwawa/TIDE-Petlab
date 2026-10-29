"""B₀/D(t) 的离线校准投影（不再拥有运行时调度写权限）。

这里不定义“黏人角色”“冷淡角色”或任何场景规则。输入只有已经存在的五维 B₀、
D(t)；输出是可回放的影子系数，供人工评审提出针对 F14/F15/rhythm/F17 参数族的
版本化候选。它不写状态、不生成任务、不决定台词，也不再复制 FOLLOWUP 门线、
主动耐火期或分享概率。

无论旧配置中的 ``enabled`` 是何值，运行时都只输出 shadow；“启用”只能发生在
离线评审批准某个新参数版本后，不能在 Runner 内现场改变行为。
"""
from __future__ import annotations

from model_packages.cx_six_seven_v1.preference_calibration_parameters import CADENCE
from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import (
    PROTOTYPES,
    cos_similarity,
    uniform_guard,
)


def _weights(value):
    raw = value if isinstance(value, dict) else {}
    values = {key: max(0.0, float(raw.get(key, 0.0) or 0.0))
              for key in PROTOTYPES}
    total = sum(values.values())
    if total <= 0.0:
        return {key: 1.0 / len(PROTOTYPES) for key in PROTOTYPES}
    return {key: value / total for key, value in values.items()}


def _clamp(value, low, high):
    return max(float(low), min(float(high), float(value)))


_CONTROL_ROWS = ("f14_impulse", "f14_gate", "f15_impulse", "f15_gate",
                 "f17_length")


def _control_direction(params, row):
    """读取 W_time 的一行；旧配置无行时严格回退至 v1 direction。"""
    rows = params.get("control_directions") or {}
    candidate = rows.get(row) if isinstance(rows, dict) else None
    raw = candidate if isinstance(candidate, dict) else (params.get("direction") or {})
    return {key: _clamp(raw.get(key, 0.0), -1.0, 1.0) for key in PROTOTYPES}


def cadence_projection(*, baseline, dynamic, field_pressure=None, params=None):
    """返回 B₀/D(t) 校准 shadow；``field_pressure`` 是弃用兼容参数。

    ``baseline`` 是用户明确声明的 B₀，``dynamic`` 是本轮/最近一次 D(t)。
    只有 B₀ 非均匀且 D(t) 与其同向时，才会产生影子系数。它不读取 P；``direction``
    是待标定的五维连续向量，不是词典或台词表。
    """
    p = params or CADENCE
    b0 = _weights(baseline)
    dt = _weights(dynamic)
    del field_pressure
    if uniform_guard(b0):
        alignment = 0.0
        control_shadow = {row: 0.0 for row in _CONTROL_ROWS}
        guard = "uniform_baseline"
    else:
        alignment = _clamp(cos_similarity(b0, dt), 0.0, 1.0)
        # 每个既有控制参数各有一行五维方向；初始缺省行回退旧 direction，等候影子
        # 标定后才允许出现分化。B₀ 是单纯形，故点积仍在 [-1, 1] 包络内。
        control_shadow = {
            row: _clamp(sum(b0[key] * _control_direction(p, row)[key]
                            for key in PROTOTYPES) * alignment, -1.0, 1.0)
            for row in _CONTROL_ROWS
        }
        guard = None

    # 旧 ``enabled`` 不再被允许改任何正式参数；真实反向标定只能产出新版本
    # 的 F14/F15/rhythm/F17 矩阵快照，再经显式部署进入产品。
    active = False
    controls = {row: 0.0 for row in _CONTROL_ROWS}
    # 兼容标量只代表第一个校准目标族的影子方向；它绝非万能调度轴。
    shadow = control_shadow["f14_impulse"]
    coefficient = controls["f14_impulse"]

    return {
        "version": str(p["version"]),
        "active": active,
        "coefficient": round(coefficient, 6),
        "shadow_coefficient": round(shadow, 6),
        "alignment": round(alignment, 6),
        "preference_axis": round(shadow, 6),
        "guard": guard,
        "mode": "calibration_shadow_only",
        "target_parameter_families": list(_CONTROL_ROWS),
        "control_coefficients": {
            "active": {row: round(controls[row], 6) for row in _CONTROL_ROWS},
            "shadow": {row: round(control_shadow[row], 6) for row in _CONTROL_ROWS},
        },
    }
