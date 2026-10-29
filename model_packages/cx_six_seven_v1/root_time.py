"""互动树 F_time 的六轴墙钟推进纯函数。

该模块只做解析时间推进，不安排 tick、不生成事件、不读取表现层。v1 只比较分轴时间尺度；
v2 直接读取角色有效七常量以形成一次性的三维基准，并由正式 F08 入口使用；旧 v1 仅为
兼容/oracle helper 保留。两者都不读取三驱动力成品。
四条可回归根轴按各自速率靠近静息点；两条杠杆是已提交事实，墙钟不得把它们自行抹去。

长时间跨度的 ``root_delta`` 不能直接当成一次突发：receipt 同时给出结算末端的
``local_rate``，后续张力公式应读取该局部速率或经明确归一的量，而不是把 12 小时的
平缓回落伪装成一记冲击。
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Mapping

from .interaction_geometry import ROOT_ORDER, root_snapshot
from .preference_parameters import CONSTANT_ORDER
from .role_baseline import calculate_baseline


HOUR_MS = 3_600_000.0
RECOVERABLE_AXES = ("E_env", "P_need", "V_attr", "V_rep")
BASELINE_ORDER = ("exchange", "withhold", "tension")
ROOT_TIME_REST_POINT = {
    "E_env": 0.35,
    "P_need": 0.35,
    "V_attr": 0.5,
    "V_rep": 0.35,
}

# 候选只表达已裁定的时间尺度关系：P_need 最快、V_attr 最慢；具体数值尚不进入 live F08。
ROOT_TIME_CANDIDATE_V1 = {
    "version": "root-time-candidate-v1-shadow",
    "rest_point": deepcopy(ROOT_TIME_REST_POINT),
    "axis_rho_per_hour": {
        "E_env": 0.12,
        "P_need": 0.32,
        "V_attr": 0.035,
        "V_rep": 0.10,
    },
}

# ADR-2026-09-08-29：F_time v2 矩阵表达由 M.A.N.E. 七常量聚合而成的
# 三维角色基准，对“同一根轴偏离静息点后保留多久”的直接影响。负号表示保留更久；
# 每行用单位长度的稀疏方向，避免另塞未经来源支持的幅度。它不决定是否开口或何时 tick。
ROOT_TIME_VECTOR_V2 = {
    "version": "root-time-vector-v2",
    "rest_point": deepcopy(ROOT_TIME_REST_POINT),
    "axis_rho_per_hour": {
        "E_env": 0.12,
        "P_need": 0.32,
        "V_attr": 0.035,
        "V_rep": 0.10,
    },
    "time_matrix": {
        "E_env": {"exchange": 0.0, "withhold": -1.0 / math.sqrt(2.0),
                  "tension": -1.0 / math.sqrt(2.0)},
        "P_need": {"exchange": -1.0 / math.sqrt(3.0), "withhold": -1.0 / math.sqrt(3.0),
                   "tension": -1.0 / math.sqrt(3.0)},
        "V_attr": {"exchange": -1.0 / math.sqrt(2.0), "withhold": 0.0,
                   "tension": -1.0 / math.sqrt(2.0)},
        "V_rep": {"exchange": 0.0, "withhold": -1.0 / math.sqrt(2.0),
                  "tension": -1.0 / math.sqrt(2.0)},
    },
}


def _candidate(params: Mapping[str, object] | None) -> dict[str, object]:
    cfg = deepcopy(ROOT_TIME_CANDIDATE_V1 if params is None else dict(params))
    rest = cfg.get("rest_point")
    rates = cfg.get("axis_rho_per_hour")
    if not isinstance(rest, Mapping) or not isinstance(rates, Mapping):
        raise ValueError("F_time 参数必须包含 rest_point 与 axis_rho_per_hour")
    if set(rest) != set(RECOVERABLE_AXES) or set(rates) != set(RECOVERABLE_AXES):
        raise ValueError("F_time 参数必须完整覆盖四条可回归根轴")
    for axis in RECOVERABLE_AXES:
        if float(rates[axis]) <= 0.0:
            raise ValueError(f"F_time 速率 {axis} 必须为正")
    return cfg


def _vector_candidate(params: Mapping[str, object] | None) -> dict[str, object]:
    """校验 F_time v2 的版本化参数，不允许缺矩阵时静默退回某种人格。"""
    cfg = deepcopy(ROOT_TIME_VECTOR_V2 if params is None else dict(params))
    # 先复用四条根轴、静息点和速率的严格校验；time_matrix 是 v2 唯一新增参数族。
    _candidate(cfg)
    matrix = cfg.get("time_matrix")
    if not isinstance(matrix, Mapping) or set(matrix) != set(RECOVERABLE_AXES):
        raise ValueError("F_time time_matrix 必须完整覆盖四条可回归根轴")
    for root_axis in RECOVERABLE_AXES:
        row = matrix.get(root_axis)
        if not isinstance(row, Mapping) or set(row) != set(BASELINE_ORDER):
            raise ValueError("F_time time_matrix 每行必须完整覆盖三维角色基准")
        for direction in BASELINE_ORDER:
            try:
                float(row[direction])
            except (TypeError, ValueError) as exc:
                raise ValueError(f"F_time time_matrix.{root_axis}.{direction} 必须是数值") from exc
    return cfg


def _baseline_from_constants(constants: Mapping[str, object]) -> dict[str, float]:
    """直接复用既有七常量→三维角色基准映射；不读取 M.A.N.E. 驱动力成品。"""
    if not isinstance(constants, Mapping) or set(constants) != set(CONSTANT_ORDER):
        raise ValueError("F_time 必须接收完整七常量 C_effective")
    parsed: dict[str, float] = {}
    for key in CONSTANT_ORDER:
        try:
            parsed[key] = float(constants[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"F_time 七常量 {key} 必须是数值") from exc
    raw = calculate_baseline(parsed)
    return {
        "exchange": float(raw["E_aggro"]),
        "withhold": float(raw["E_defend"]),
        "tension": float(raw["E_chaos"]),
    }


def root_time_rate_receipt(constants: Mapping[str, object], *, constants_version: str,
                           params: Mapping[str, object] | None = None) -> dict[str, object]:
    """展开 F_time v2 的分轴墙钟速率，不推进状态也不创建调度读数。

    F06 的已送达输出可复用这份只读根量时间尺度来读取用户事件残量；这比借用
    legacy opening 的 ``tau`` 更符合“同一轴的回归语义只有一个版本化来源”。
    """
    constants_ref = str(constants_version or "").strip()
    if not constants_ref:
        raise ValueError("F_time 必须绑定可回放的 C_effective 常量版本")
    cfg = _vector_candidate(params)
    baseline = _baseline_from_constants(constants)
    base_rates = {axis: float(cfg["axis_rho_per_hour"][axis])
                  for axis in RECOVERABLE_AXES}
    rate_terms: dict[str, dict[str, float]] = {}
    effective_rate: dict[str, float] = {}
    for axis in RECOVERABLE_AXES:
        row = cfg["time_matrix"][axis]
        terms = {direction: float(row[direction]) * baseline[direction]
                 for direction in BASELINE_ORDER}
        rate_terms[axis] = terms
        effective_rate[axis] = base_rates[axis] * math.exp(sum(terms.values()))
    return {
        "version": str(cfg["version"]), "constants_version": constants_ref,
        "baseline": baseline, "rate_terms": rate_terms,
        "effective_rate_per_hour": effective_rate,
        "derived_only": True,
    }


def settle_root_time(variables: Mapping[str, object], elapsed_ms: int | float, *,
                     params: Mapping[str, object] | None = None) -> dict[str, object]:
    """解析推进 ``X(t)`` 并产出可回放 receipt，不改调用方状态。"""
    before = root_snapshot(variables)
    cfg = _candidate(params)
    elapsed_h = max(0.0, float(elapsed_ms) / HOUR_MS)
    rest = {axis: float(cfg["rest_point"][axis]) for axis in RECOVERABLE_AXES}
    rates = {axis: float(cfg["axis_rho_per_hour"][axis]) for axis in RECOVERABLE_AXES}
    after = dict(before)
    local_rate = {axis: 0.0 for axis in ROOT_ORDER}
    for axis in RECOVERABLE_AXES:
        after[axis] = rest[axis] + (before[axis] - rest[axis]) * math.exp(-rates[axis] * elapsed_h)
        # 终点局部导数：描述当下仍在怎样自然变化，而非把累计 gap 当作 shock。
        local_rate[axis] = -rates[axis] * (after[axis] - rest[axis])
    return {
        "version": str(cfg["version"]),
        "elapsed_ms": int(round(max(0.0, float(elapsed_ms)))),
        "elapsed_hours": elapsed_h,
        "before": before,
        "after": after,
        "root_delta": {axis: after[axis] - before[axis] for axis in ROOT_ORDER},
        "local_rate_per_hour": local_rate,
        "rest_point": rest,
        "axis_rho_per_hour": rates,
        "derived_only": True,
    }


def settle_root_time_vector(constants: Mapping[str, object], variables: Mapping[str, object],
                            elapsed_ms: int | float, *,
                            constants_version: str,
                            params: Mapping[str, object] | None = None) -> dict[str, object]:
    """F_time v2：由 ``C_effective + X_before + Delta_t`` 直接解析六维时间变化。

    此函数不调度 tick、不读开口/记忆/词元/UI、不写传入 state。``baseline`` 与逐轴
    ``rate_terms`` 只是本调用回执中的展开证据，绝不能持久化后喂给下一次运算。
    """
    constants_ref = str(constants_version or "").strip()
    if not constants_ref:
        raise ValueError("F_time 必须绑定可回放的 C_effective 常量版本")
    before = root_snapshot(variables)
    cfg = _vector_candidate(params)
    rate_receipt = root_time_rate_receipt(
        constants, constants_version=constants_ref, params=params)
    baseline = rate_receipt["baseline"]
    elapsed_h = max(0.0, float(elapsed_ms) / HOUR_MS)
    rest = {axis: float(cfg["rest_point"][axis]) for axis in RECOVERABLE_AXES}
    after = dict(before)
    local_rate = {axis: 0.0 for axis in ROOT_ORDER}
    rate_terms: dict[str, dict[str, float]] = {}
    effective_rate: dict[str, float] = {}
    for axis in RECOVERABLE_AXES:
        terms = dict(rate_receipt["rate_terms"][axis])
        rate_terms[axis] = terms
        effective_rate[axis] = float(rate_receipt["effective_rate_per_hour"][axis])
        after[axis] = rest[axis] + (before[axis] - rest[axis]) * math.exp(
            -effective_rate[axis] * elapsed_h
        )
        local_rate[axis] = -effective_rate[axis] * (after[axis] - rest[axis])

    return {
        "version": str(cfg["version"]),
        "constants_version": constants_ref,
        "inputs": {
            "constants": {key: float(constants[key]) for key in CONSTANT_ORDER},
            "variables": before,
            "elapsed_ms": int(round(max(0.0, float(elapsed_ms)))),
        },
        "elapsed_hours": elapsed_h,
        "before": before,
        "after": after,
        "root_delta": {axis: after[axis] - before[axis] for axis in ROOT_ORDER},
        "rest_point": rest,
        "baseline": baseline,
        "rate_terms": rate_terms,
        "effective_rate_per_hour": effective_rate,
        "local_rate_per_hour": local_rate,
        "derived_only": True,
    }
