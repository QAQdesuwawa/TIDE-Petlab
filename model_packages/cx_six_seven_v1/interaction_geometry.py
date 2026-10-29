"""互动树调度专用的关系几何（P1，尚未接入运行时）。

这个模块的职责很窄：把已结算的六维根状态 ``X`` 与同一事件前后的变化，展开成
可回溯的关系几何 receipt。它不读取角色常量、三驱动力、词元、记忆候选或 UI，也不
写状态，更不裁定开口。

``core.drives.relationship_geometry`` 仍服务既有 M.A.N.E. 表现驱动力；其 ``R_mag``
兼容定义不能被误当成互动树调度的关系几何。本模块是 P1 的影子纯层，在 P3 切换权威
开口公式前不得被 Actor/Runner 消费。
"""
from __future__ import annotations

import math
from copy import deepcopy
from typing import Mapping

from .role_baseline import calculate_baseline


ROOT_ORDER = (
    "E_env", "P_need", "V_attr", "V_rep", "L_self", "L_opponent",
)
GEOMETRY_ORDER = ("S", "T", "M", "R_net", "R_amb")
VERSION = "interaction-geometry-v1-shadow"

# P1 的候选参数只服务纯函数影子对照，尚未注册为运行时 F14/F15 参数。
# 权重首先表达“哪些项更重要”：P_need 与杠杆优先，E_env 只做辅助；实际数值须由
# 冻结互动样本校准后才可能进入权威开口路径。
SCHEDULE_VECTOR_CANDIDATE_V2 = {
    "version": "schedule-vector-candidate-v2-shadow",
    "exchange_weights": {
        "need_level": 0.38,
        "leverage_capacity": 0.18,
        "mutual_goodwill": 0.24,
        "need_rise": 0.14,
        "net_approach": 0.06,
    },
    "withhold_weights": {
        "reputation_pressure": 0.40,
        "leverage_vulnerability": 0.24,
        "environment_support": 0.08,
        "reputation_rise": 0.16,
        "leverage_loss": 0.12,
    },
    "tension_level_weights": {
        # T 是双方总暴露：不是 S 或 M 的别名。它作为总暴露包络；M/S 只能
        # 以该包络内的共同／不对称比例出现，禁止把 T、M、|S|当独立量叠加。
        "exposure_envelope": 0.15,
        "mutual_need": 0.18,
        "asymmetric_friction": 0.20,
        "ambivalence": 0.20,
        "environment_support": 0.05,
        # P_need 是快需求；同幅慢好感变化不得在即时张力上压过它。
        "need_rise": 0.22,
    },
    "tension_shock_weight": 0.20,
    # 各项以可能的单事件变化范围归一，而不是让 T 的量程天然压过其余几何。
    "shock_scales": {"S": 2.0, "T": 2.0, "M": 1.0, "R_net": 2.0, "R_amb": 1.0},
}

# P3 候选：三项只读同一个 Z，区别只在表达推进、克制和观察密度的投影方向。
# 它不是场景开关；特别是 tension 会提高表达冲动并降低门槛，但不等于直接发送。
OPENING_CANDIDATE_V1 = {
    "version": "opening-from-schedule-vector-v2-shadow",
    # 作者已裁定：同一 Z 上，主动读“交换 + 张力”的固定单位方向；门槛只读收缩方向。
    # 这两条基不是角色/场景权重；角色差异早已在 B ⊙ R 的唯一入口进入 Z。
    "impulse_basis": {
        "exchange": 1.0 / math.sqrt(2.0), "withhold": 0.0,
        "tension": 1.0 / math.sqrt(2.0),
    },
    "threshold_basis": {"exchange": 0.0, "withhold": 1.0, "tension": 0.0},
    # shadow 的零偏置只表示原点中性；任何未来非零版本必须通过定标 receipt 引入。
    "projection_gain": 1.0,
    "impulse_bias": 0.0,
    "threshold_bias": 0.0,
}

# ADR-58：P3 进入正式消费者迁移前的首版冻结包。它复用已经以纯函数和方向性回归
# 验证过的三维条目，但不改变任何数值；区别在于 runtime 只能显式传入这个版本，不能
# 再从“candidate/shadow”默认值悄悄取得一套行为参数。后续校准必须新建版本包，不能
# 原地改写本包或让其与旧 7×6 开口矩阵共同裁定一次发送。
SCHEDULE_VECTOR_AUTHORITY_V1 = {
    **SCHEDULE_VECTOR_CANDIDATE_V2,
    "version": "schedule-vector-authority-v1",
}

# AUD-053：v1 的 tension level 仅把 V_rep 放进 V_attr*V_rep 的“又爱又恨”项。
# 当 V_attr/杠杆为零时，已定义为评价／拒绝压力的 V_rep 会从关系张力水平消失，进而
# 被 Q_tick 误读成稀疏观察。v2 保留 v1 供历史 receipt 回放，显式补回该根量的一次
# 前向读取；角色差异仍只经 B⊙R 进入一次。
SCHEDULE_VECTOR_CANDIDATE_V3 = {
    **SCHEDULE_VECTOR_CANDIDATE_V2,
    "version": "schedule-vector-candidate-v3-shadow",
    "tension_level_weights": {
        "exposure_envelope": 0.13,
        "mutual_need": 0.16,
        "asymmetric_friction": 0.17,
        "ambivalence": 0.15,
        "environment_support": 0.04,
        "need_rise": 0.15,
        "reputation_pressure": 0.20,
    },
}
SCHEDULE_VECTOR_AUTHORITY_V2 = {
    **SCHEDULE_VECTOR_CANDIDATE_V3,
    "version": "schedule-vector-authority-v2",
}
OPENING_AUTHORITY_V1 = {
    **OPENING_CANDIDATE_V1,
    "version": "opening-from-schedule-vector-authority-v1",
}

# ADR-59：v1 将 ``V_open=[exchange, 0, tension]`` 与
# ``V_hold=[0, withhold, 0]`` 冻结成稀疏投影。它虽然形式上来自同一 Z，实际却把
# 另外两个已结算方向静默置零；高张力/高交换无法自然降低当下门槛，S3 的正式 tick
# 因而整段保持在线下。作者的同源约束要求 I/Theta/Q 读取同一完整三维事实，只能
# 以不同、可回放的投影方向表达“推进”与“克制”。v1 继续只供历史 receipt/replay
# 读取；新会话权威公式必须显式使用 v2，不能原地改写 v1。
OPENING_CANDIDATE_V2 = {
    "version": "opening-from-schedule-vector-v3-shadow",
    # 交换与张力是主动表达的主要方向；收缩仍可轻度抵消冲动。三个系数均非零，
    # 使该投影真的读取完整 Z，而不是以“同源”之名忽略一部分根链。
    "impulse_basis": {
        "exchange": 0.70, "withhold": -0.10, "tension": 0.70,
    },
    # 门槛仍以收缩为正向主项，但交换/张力的提升会连续降低“还需多少理由才开口”。
    # 它不是场景开关、冷却或计数器；只是一条同 Z 的相反读数方向。
    "threshold_basis": {
        "exchange": -0.55, "withhold": 0.60, "tension": -0.55,
    },
    "projection_gain": 1.0,
    "impulse_bias": 0.0,
    "threshold_bias": 0.0,
}
OPENING_AUTHORITY_V2 = {
    **OPENING_CANDIDATE_V2,
    "version": "opening-from-schedule-vector-authority-v2",
}

# AUD-053：公式图谱中的 Q_tick 是 ||Z||；v2 之前实现为 RMS，静默把长度除以
# sqrt(3)。新包将 norm convention 显式版本化，历史 v1/v2 receipt 仍可按原读数回放。
OPENING_CANDIDATE_V3 = {
    **OPENING_CANDIDATE_V2,
    "version": "opening-from-schedule-vector-v4-shadow",
    "q_norm": "l2",
}
OPENING_AUTHORITY_V3 = {
    **OPENING_CANDIDATE_V3,
    "version": "opening-from-schedule-vector-authority-v3",
}

# DIR-161 / AUD-054：Q_tick 不能再以无方向的 ||Z|| 与 I/Theta 并列。三者都读取
# 同一份完整 Z，但 Q 是观察密度而非“总能量”，因此需要自己的静态方向。这里以已冻结
# 的 V_open - V_hold 前向构造 V_tick：它不读取本轮 I、Theta 或 margin，只复用两条
# 同版基向量的代数差，并在包内明确保存为可回放的三维投影。
_TICK_BASIS_NORM = math.sqrt(1.25 ** 2 + (-0.70) ** 2 + 1.25 ** 2)
OPENING_CANDIDATE_V4 = {
    **OPENING_CANDIDATE_V2,
    "version": "opening-from-schedule-vector-v5-shadow",
    "tick_basis": {
        "exchange": 1.25 / _TICK_BASIS_NORM,
        "withhold": -0.70 / _TICK_BASIS_NORM,
        "tension": 1.25 / _TICK_BASIS_NORM,
    },
    "tick_projection_gain": 1.0,
    # 零偏置保持 Q/I/Theta 同一中性投影尺度。若未来需要调整 idle 的资源基线，必须
    # 新建版本化 calibration receipt，不能在事件、Actor 或 UI 内临时加偏置。
    "tick_bias": 0.0,
    "q_projection": "sigmoid-static-tick-basis-v1",
}
OPENING_AUTHORITY_V4 = {
    **OPENING_CANDIDATE_V4,
    "version": "opening-from-schedule-vector-authority-v4",
}

# DIR-162：Q 已在与 I/Theta 相同的 sigmoid 尺度；继续用线性 2–300 秒会把
# Q≈0.52 读成约 144 秒。幂曲线仍只读取 Q，连续且单调；不是 hot/warm/cold
# 档位，也不含 margin/F16/文本等运行时条件。它是 v4 之后的新包，不能改写
# 已落盘 v4 receipt 的 H 语义。
OPENING_CANDIDATE_V5 = {
    **OPENING_CANDIDATE_V4,
    "version": "opening-from-schedule-vector-v6-shadow",
    "interval_curve": {
        "version": "q-tick-power-interval-v2",
        "exponent": 3.0,
    },
}
OPENING_AUTHORITY_V5 = {
    **OPENING_CANDIDATE_V5,
    "version": "opening-from-schedule-vector-authority-v5",
}


def _number(source: Mapping[str, object], axis: str) -> float:
    try:
        return float(source.get(axis, 0.0) or 0.0)
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"{axis} 必须是可解析数值") from exc


def root_snapshot(variables: Mapping[str, object]) -> dict[str, float]:
    """复制六条根轴，拒绝缺轴，避免零值悄悄掩盖接线遗漏。"""
    if not isinstance(variables, Mapping):
        raise ValueError("variables 必须是六维根状态映射")
    missing = [axis for axis in ROOT_ORDER if axis not in variables]
    if missing:
        raise ValueError("缺少根轴: " + ", ".join(missing))
    return {axis: _number(variables, axis) for axis in ROOT_ORDER}


def relationship_geometry(variables: Mapping[str, object]) -> dict[str, object]:
    """从单个已结算 ``X`` 读取调度关系几何。

    ``T`` 在互动树中是双方总暴露，故不沿用旧表现层的 capped threat 定义；
    ``M`` 作为共同暴露由 ``min(L_self, L_opponent)`` 直接得到。它们都只是临时
    查询，调用方不得把结果回写成第七个关系状态。
    """
    roots = root_snapshot(variables)
    l_self = roots["L_self"]
    l_opponent = roots["L_opponent"]
    v_attr = roots["V_attr"]
    v_rep = roots["V_rep"]
    values = {
        "S": l_self - l_opponent,
        "T": l_self + l_opponent,
        "M": min(l_self, l_opponent),
        "R_net": v_attr - v_rep,
        "R_amb": v_attr * v_rep,
    }
    return {
        "version": VERSION,
        "roots": roots,
        "values": values,
        "derived_only": True,
    }


def relationship_transition(before: Mapping[str, object],
                            after: Mapping[str, object]) -> dict[str, object]:
    """生成同一已提交事件的 ``X_before → X_after → ΔG`` receipt。

    这是调度层唯一允许读取变化量的入口；事件、记忆或墙钟分别怎样得到 ``after``
由上游负责，本函数不猜测来源，也不把累计时间的远距离差当作最终张力公式。
    """
    before_receipt = relationship_geometry(before)
    after_receipt = relationship_geometry(after)
    root_delta = {
        axis: after_receipt["roots"][axis] - before_receipt["roots"][axis]
        for axis in ROOT_ORDER
    }
    geometry_delta = {
        axis: after_receipt["values"][axis] - before_receipt["values"][axis]
        for axis in GEOMETRY_ORDER
    }
    return {
        "version": VERSION,
        "before": before_receipt,
        "after": after_receipt,
        "root_delta": root_delta,
        "geometry_delta": geometry_delta,
        "derived_only": True,
    }


def direction_components(transition: Mapping[str, object]) -> dict[str, object]:
    """列出三维 ``R`` 的合法根量成分，但尚不选择权重或饱和函数。

    这一步刻意不输出 ``R_exchange/R_withhold/R_tension``：把未经定标的系数藏成
    默认值，会重新制造“拍脑袋公式”。输出项是下一阶段唯一允许加权的前向成分；
    每一项都可回溯至当前 ``X`` 或同一结算的 ``ΔG``。
    """
    if not isinstance(transition, Mapping):
        raise ValueError("transition 必须是关系变化 receipt")
    before = transition.get("before")
    after = transition.get("after")
    root_delta = transition.get("root_delta")
    geometry_delta = transition.get("geometry_delta")
    if not all(isinstance(item, Mapping) for item in
               (before, after, root_delta, geometry_delta)):
        raise ValueError("transition 缺少 before/after/root_delta/geometry_delta")
    roots = after.get("roots")
    geometry = after.get("values")
    if not isinstance(roots, Mapping) or not isinstance(geometry, Mapping):
        raise ValueError("transition 的 after 几何无效")

    def positive(value: object) -> float:
        try:
            return max(0.0, float(value or 0.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("方向成分必须是数值") from exc

    s = float(geometry["S"])
    t = positive(geometry["T"])
    m = positive(geometry["M"])
    p_need = positive(roots["P_need"])
    v_attr = positive(roots["V_attr"])
    v_rep = positive(roots["V_rep"])
    e_env = positive(roots["E_env"])
    # 根轴均在 0~1 时，T 的自然量程为 0~2。以下把 T 变为总暴露包络，
    # M 与 |S|仅说明包络的组成比例：T=2M+|S| 不会在同一张力水平被重复加总。
    exposure_envelope = _saturate(t / 2.0)
    if t > 0.0:
        mutual_share = _saturate((2.0 * m) / t)
        asymmetry_share = _saturate(abs(s) / t)
    else:
        mutual_share = 0.0
        asymmetry_share = 0.0
    return {
        "version": "interaction-direction-components-v1-shadow",
        "source_geometry_version": str(transition.get("version") or ""),
        "exchange": {
            # 当前需要、优势筹码和双方已互相袒露时的好感承接。
            "need_level": p_need,
            "leverage_capacity": positive(s),
            "mutual_goodwill": m * v_attr,
            # 带符号变化保留为单独项，不能把突然靠近和稳定高好感混为一谈。
            "need_rise": positive(root_delta.get("P_need")),
            "net_approach": positive(geometry_delta.get("R_net")),
        },
        "withhold": {
            # 收缩读取被评价压力、杠杆下位与少量环境修正。
            "reputation_pressure": v_rep,
            "leverage_vulnerability": positive(-s),
            "environment_support": e_env,
            "reputation_rise": positive(root_delta.get("V_rep")),
            "leverage_loss": positive(-float(geometry_delta.get("S", 0.0) or 0.0)),
        },
        "tension": {
            # 水平项保留关系余波。T 是总暴露包络，M/S 只表明它的内部结构；
            # shock 只提供原始 ΔG，下一阶段统一尺度后再取模长。
            "exposure_envelope": exposure_envelope,
            "mutual_need": mutual_share * p_need,
            "asymmetric_friction": asymmetry_share * v_rep,
            "ambivalence": v_attr * v_rep,
            "environment_support": e_env,
            "need_rise": positive(root_delta.get("P_need")),
            # V_rep 是“被评价、拒绝或不愿交付”的压力。它与 V_attr*V_rep 的
            # ambivalence 不同：后者只表达双高拉扯，不能在 V_attr 很低时吞掉
            # 已存在的关系张力。
            "reputation_pressure": v_rep,
            "shock_geometry_delta": {
                axis: float(geometry_delta[axis]) for axis in GEOMETRY_ORDER
            },
        },
        "derived_only": True,
    }


def _saturate(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _sigmoid(value: float) -> float:
    return 1.0 / (1.0 + math.exp(-float(value)))


def _weighted_terms(values: Mapping[str, object], weights: Mapping[str, object], *, label: str) -> dict[str, float]:
    if set(values) != set(weights):
        raise ValueError(f"{label} 的权重与成分必须一一对应")
    terms: dict[str, float] = {}
    for key, value in values.items():
        try:
            terms[key] = float(value) * float(weights[key])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}.{key} 必须为数值") from exc
    return terms


def normalized_shock(geometry_delta: Mapping[str, object], scales: Mapping[str, object]) -> dict[str, object]:
    """计算只反映“关系变化幅度”的标准化 ΔG 模长。

    符号被保留在 ``components`` 内供交换／收缩方向使用；第三方向只关心变化有多剧烈，
    所以 ``d`` 与 ``-d`` 必须得到相同的 ``norm``。
    """
    if set(geometry_delta) != set(GEOMETRY_ORDER) or set(scales) != set(GEOMETRY_ORDER):
        raise ValueError("shock 必须完整覆盖 S/T/M/R_net/R_amb")
    normalized = {}
    for axis in GEOMETRY_ORDER:
        scale = float(scales[axis])
        if scale <= 0.0:
            raise ValueError(f"shock scale {axis} 必须为正")
        normalized[axis] = float(geometry_delta[axis]) / scale
    # 除以维度平方根，让“每个标准化方向同样移动”的读数仍在 0~1 附近。
    norm = math.sqrt(sum(value * value for value in normalized.values()) / len(normalized))
    return {"normalized": normalized, "norm": _saturate(norm)}


def schedule_vector(transition: Mapping[str, object], constants: Mapping[str, object], *,
                    params: Mapping[str, object] | None = None) -> dict[str, object]:
    """P1 候选：``X/ΔX → R``、``C → B``、``B⊙R → Z`` 的纯前向 receipt。

    这是影子候选，不是开口函数：没有 ``eligible``、没有时间计划，亦不读取或写入
    Actor/Runner 状态。角色常量只通过 ``B`` 进入一次，绝不参与 ``R`` 的计算。
    """
    cfg = deepcopy(SCHEDULE_VECTOR_CANDIDATE_V3 if params is None else dict(params))
    components_receipt = direction_components(transition)
    exchange_terms = _weighted_terms(
        components_receipt["exchange"], cfg["exchange_weights"], label="exchange")
    withhold_terms = _weighted_terms(
        components_receipt["withhold"], cfg["withhold_weights"], label="withhold")
    tension_level_terms = _weighted_terms(
        {key: components_receipt["tension"][key]
         for key in cfg["tension_level_weights"]},
        cfg["tension_level_weights"], label="tension_level")
    shock = normalized_shock(components_receipt["tension"]["shock_geometry_delta"],
                             cfg["shock_scales"])
    shock_term = float(cfg["tension_shock_weight"]) * float(shock["norm"])
    relationship = {
        "exchange": _saturate(sum(exchange_terms.values())),
        "withhold": _saturate(sum(withhold_terms.values())),
        "tension": _saturate(sum(tension_level_terms.values()) + shock_term),
    }
    baseline_raw = calculate_baseline(dict(constants))
    baseline = {
        "exchange": float(baseline_raw["E_aggro"]),
        "withhold": float(baseline_raw["E_defend"]),
        "tension": float(baseline_raw["E_chaos"]),
    }
    vector = {axis: baseline[axis] * relationship[axis] for axis in relationship}
    return {
        "version": str(cfg["version"]),
        "components": components_receipt,
        "relationship_direction": relationship,
        "relationship_terms": {
            "exchange": exchange_terms,
            "withhold": withhold_terms,
            "tension_level": tension_level_terms,
            "tension_shock": shock_term,
        },
        "shock": shock,
        "baseline": baseline,
        "schedule_vector": vector,
        "derived_only": True,
    }


def opening_readout(schedule_receipt: Mapping[str, object], *,
                    params: Mapping[str, object] | None = None) -> dict[str, object]:
    """P3 shadow：由同一个 ``Z`` 读取 I、Theta、margin 与 Q_tick。

    这不是 F15 提交器：即使 margin 为正，只有未来正式 dynamic tick 的权威公式才可
    创建输出事务。该函数不返回 ``eligible``，避免被调用方误作绕过 tick 的发送入口。
    """
    if not isinstance(schedule_receipt, Mapping):
        raise ValueError("schedule_receipt 必须是 Z receipt")
    z = schedule_receipt.get("schedule_vector")
    if not isinstance(z, Mapping) or set(z) != {"exchange", "withhold", "tension"}:
        raise ValueError("schedule_receipt 缺少完整三维 Z")
    cfg = deepcopy(OPENING_CANDIDATE_V5 if params is None else dict(params))
    impulse_basis = cfg.get("impulse_basis")
    threshold_basis = cfg.get("threshold_basis")
    if not isinstance(impulse_basis, Mapping) or not isinstance(threshold_basis, Mapping):
        raise ValueError("opening 参数必须包含两条三维投影基")
    if set(impulse_basis) != set(z) or set(threshold_basis) != set(z):
        raise ValueError("opening 投影基必须完整覆盖 Z")
    gain = float(cfg.get("projection_gain", 1.0))
    impulse_terms = {axis: float(z[axis]) * float(impulse_basis[axis]) for axis in z}
    threshold_terms = {axis: float(z[axis]) * float(threshold_basis[axis]) for axis in z}
    impulse_logit = gain * sum(impulse_terms.values()) + float(cfg.get("impulse_bias", 0.0))
    threshold_logit = gain * sum(threshold_terms.values()) + float(cfg["threshold_bias"])
    impulse = _sigmoid(impulse_logit)
    threshold = _sigmoid(threshold_logit)
    # AUD-054：新包让 Q 同样是同一 Z 的固定 sigmoid 投影。它刻意不读已经计算好的
    # I/Theta/margin；V_tick 只是冻结 V_open/V_hold 的前向代数差。没有 tick_basis 的
    # 历史包仍严格按其原 norm 定义回放，不能被新版语义重算。
    tick_basis = cfg.get("tick_basis")
    if tick_basis is not None:
        if not isinstance(tick_basis, Mapping) or set(tick_basis) != set(z):
            raise ValueError("tick 投影基必须完整覆盖 Z")
        tick_gain = float(cfg.get("tick_projection_gain", gain))
        tick_bias = float(cfg.get("tick_bias", 0.0))
        tick_terms = {axis: float(z[axis]) * float(tick_basis[axis]) for axis in z}
        tick_logit = tick_gain * sum(tick_terms.values()) + tick_bias
        q_tick = _sigmoid(tick_logit)
        q_projection = str(cfg.get("q_projection") or "sigmoid-static-tick-basis-v1")
        q_detail = {
            "method": q_projection,
            "basis": {axis: float(tick_basis[axis]) for axis in z},
            "terms": tick_terms,
            "logit": tick_logit,
            "gain": tick_gain,
            "bias": tick_bias,
        }
    else:
        q_norm = str(cfg.get("q_norm") or "rms-legacy")
        squared_sum = sum(float(value) ** 2 for value in z.values())
        if q_norm == "l2":
            q_tick = _saturate(math.sqrt(squared_sum))
        elif q_norm == "rms-legacy":
            q_tick = _saturate(math.sqrt(squared_sum / len(z)))
        else:
            raise ValueError("q_norm 必须是 l2 或 rms-legacy")
        q_projection = "norm:" + q_norm
        q_detail = {"method": q_projection, "basis": None, "terms": None,
                    "logit": None, "gain": None, "bias": None}
    return {
        "version": str(cfg["version"]),
        "schedule_vector": {axis: float(z[axis]) for axis in z},
        "impulse": impulse,
        "threshold": threshold,
        "margin": impulse - threshold,
        "q_tick": q_tick,
        "q_tick_projection": q_detail,
        "interval_curve": deepcopy(cfg.get("interval_curve") or {
            "version": "q-tick-linear-interval-v1-shadow", "exponent": 1.0}),
        "impulse_terms": impulse_terms,
        "threshold_terms": threshold_terms,
        "impulse_logit": impulse_logit,
        "threshold_logit": threshold_logit,
        "projection_gain": gain,
        "derived_only": True,
    }


def next_observation_interval(opening_receipt: Mapping[str, object], *,
                              min_interval_ms: int, max_interval_ms: int) -> dict[str, object]:
    """将同一 ``Z`` 的 ``Q_tick`` 连续投影为下一次观察间隔（P3 shadow）。

    这里的上下界仅是 Actor 的队列公平／资源安全边界；它们不表示人格档位。函数
    只读取已经由 ``Z`` 取得的观察密度，刻意不读取 ``I_open``、``Theta_open``、余量、
    消息数、事件锚或 UI 时间，故“是否想说”不能反过来决定“多久再观察”。

    ``Q_tick`` 已归一到 0~1。历史 receipt 默认使用线性映射；新版 authority 可在
    版本化包中给出连续幂指数。无论哪种映射都只读 Q 和工程资源边界，高密度更短、
    低密度更长；它不决定本次是否发言。
    """
    if not isinstance(opening_receipt, Mapping):
        raise ValueError("opening_receipt 必须是开口 shadow receipt")
    try:
        q_tick = float(opening_receipt["q_tick"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("opening_receipt 必须包含可解析的 q_tick") from exc
    if not math.isfinite(q_tick) or q_tick < 0.0 or q_tick > 1.0:
        raise ValueError("q_tick 必须在 0 到 1 之间")
    try:
        low = int(min_interval_ms)
        high = int(max_interval_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("观察间隔边界必须是整数毫秒") from exc
    if low <= 0 or high < low:
        raise ValueError("观察间隔必须满足 0 < min_interval_ms <= max_interval_ms")
    raw_curve = opening_receipt.get("interval_curve")
    curve = (dict(raw_curve) if isinstance(raw_curve, Mapping) else {
        "version": "q-tick-linear-interval-v1-shadow", "exponent": 1.0})
    curve_version = str(curve.get("version") or "")
    try:
        exponent = float(curve.get("exponent", 1.0))
    except (TypeError, ValueError) as exc:
        raise ValueError("观察间隔 exponent 必须是数值") from exc
    if curve_version not in {"q-tick-linear-interval-v1-shadow", "q-tick-power-interval-v2"}:
        raise ValueError("未知观察间隔曲线版本")
    if not math.isfinite(exponent) or exponent <= 0.0:
        raise ValueError("观察间隔 exponent 必须大于零")
    interval = int(round(low + (high - low) * ((1.0 - q_tick) ** exponent)))
    return {
        "version": curve_version,
        "q_tick": q_tick,
        "h_next_ms": interval,
        "bounds_ms": {"min": low, "max": high},
        "exponent": exponent,
        "derived_only": True,
    }


def opening_authority_receipt(
        before: Mapping[str, object], after: Mapping[str, object],
        constants: Mapping[str, object], *, constants_version: str,
        event_kind: str, event_id: str, observed_at_ms: int,
        min_interval_ms: int, max_interval_ms: int,
        schedule_params: Mapping[str, object] | None = None,
        opening_params: Mapping[str, object] | None = None) -> dict[str, object]:
    """封装 P3 的唯一单向候选链，供正式接管前逐事件核验。

    这不是新的状态、更不是一次新的计算来源：它只把同一已提交事件前后的根量，按
    ``X/ΔX → ΔG → R → B⊙R=Z → I/Θ/Q → h`` 逐段留成 receipt。调用者必须在
    *事件已经提交后* 传入 ``event_id`` 与常量版本；函数既不判定 ``eligible``，也不
    创建 clock plan。因此后续 Actor 接管时只能消费同一份 receipt，而不能把旧 F14、
    旧 tick 或 UI 重算混进来。
    """
    binding = str(constants_version or "").strip()
    kind = str(event_kind or "").strip()
    identifier = str(event_id or "").strip()
    if not binding:
        raise ValueError("constants_version 不能为空")
    if not kind:
        raise ValueError("event_kind 不能为空")
    if not identifier:
        raise ValueError("event_id 不能为空")
    try:
        observed_ms = int(observed_at_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("observed_at_ms 必须是整数毫秒") from exc

    transition = relationship_transition(before, after)
    schedule = schedule_vector(transition, constants, params=schedule_params)
    opening = opening_readout(schedule, params=opening_params)
    interval = next_observation_interval(
        opening, min_interval_ms=min_interval_ms, max_interval_ms=max_interval_ms)
    return {
        "version": "opening-authority-receipt-v1-shadow",
        "event": {"kind": kind, "id": identifier, "observed_at_ms": observed_ms},
        "effective_constants_version": binding,
        "X_before": dict(transition["before"]["roots"]),
        "X_after": dict(transition["after"]["roots"]),
        "Delta_X": dict(transition["root_delta"]),
        "Delta_G": dict(transition["geometry_delta"]),
        "B": dict(schedule["baseline"]),
        "R": dict(schedule["relationship_direction"]),
        "Z": dict(schedule["schedule_vector"]),
        "I_open": float(opening["impulse"]),
        "Theta_open": float(opening["threshold"]),
        "margin": float(opening["margin"]),
        "Q_tick": float(opening["q_tick"]),
        "Q_tick_projection": deepcopy(opening["q_tick_projection"]),
        "H_projection": {
            "version": str(interval["version"]),
            "exponent": float(interval["exponent"]),
            "bounds_ms": dict(interval["bounds_ms"]),
        },
        "h_next_ms": int(interval["h_next_ms"]),
        "formula_versions": {
            "relationship_geometry": str(transition["version"]),
            "schedule_vector": str(schedule["version"]),
            "opening_projection": str(opening["version"]),
            "observation_interval": str(interval["version"]),
        },
        "derived_only": True,
    }


def require_single_authority(*, active_formula_version: str,
                             shadow_formula_versions: tuple[str, ...] = ()) -> dict[str, object]:
    """P0 护栏：影子公式可以记录，不能与权威发送公式并列裁定。

    Actor/Runner 接入新开口公式前，可把旧、新读数都留在 receipt；真正做出
    ``eligible`` 决定时必须只标记一个非空权威版本。这个纯校验不读取任何行为数据。
    """
    active = str(active_formula_version or "").strip()
    if not active:
        raise ValueError("自主发送必须声明唯一权威公式版本")
    shadows = tuple(str(version or "").strip() for version in shadow_formula_versions)
    if any(not version for version in shadows):
        raise ValueError("影子公式版本不能为空")
    if active in shadows:
        raise ValueError("权威公式不能同时登记为影子公式")
    if len(set(shadows)) != len(shadows):
        raise ValueError("影子公式版本不得重复")
    return {
        "active_formula_version": active,
        "shadow_formula_versions": list(shadows),
        "single_authority": True,
    }
