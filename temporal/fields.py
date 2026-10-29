# temporal/fields.py
"""连续动力学：四场 + 词元漂移场原语（全部闭式解，回放逐位一致）。

方程（架构文档 §11.1）：
    dx/ds = F_curve + F_fatigue − F_recovery − F_rebound
    x = (E_env, P_need, V_attr, V_rep)；杠杆 L 不在场内（事件性）

实现（每段 gap 单次结算，闭式）：
    curve  : F(s) 自场启动点 p0 的绝对解析值（s = 挂钟小时）
    fatigue: 主导驱动力支撑轴 x *= e^(−η·dur_h)
    recovery: x += (rest−x)·(1−e^(−ρ·Δt))
    rebound : 爆后回落，速率 ρ_r，窗口 τ_h 内生效（回弹原语 = 全局场，非词元原语）

结算顺序：curve → fatigue → recovery → rebound → clamp（测试锁死）。
"""
import math

from core.params import TIME
from temporal.lexicon import match_rule

AXES = ("E_env", "P_need", "V_attr", "V_rep")

# 主导驱动力 → 支撑变量（疲劳场作用轴，架构文档 §8.2）
SUPPORT = {
    "D_aggro": ("E_env", "V_rep"),
    "D_defend": ("E_env", "P_need"),
    "D_chaos": ("E_env", "V_attr"),
}


def _clamp01(x):
    return min(1.0, max(0.0, x))


# ---------------------------------------------------------------------
# 原语闭式（冻结区函数形式）
# ---------------------------------------------------------------------
def exp_approach(p0, target, s, rate):
    """指数逼近：f(s) = target + (p0 − target)·e^(−rate·s)。"""
    return target + (p0 - target) * math.exp(-rate * s)


def linear_climb(p0, target, s, rate):
    """线性爬升/回落：f = p0 + rate·s·sign(target−p0)，钳制于 target。"""
    if target >= p0:
        return min(target, p0 + rate * s)
    return max(target, p0 - rate * s)


def pulse(p0, peak, rest, s, t1, decay_rate):
    """脉冲：线性升至峰值（t1 小时），后指数回落至 rest。"""
    if s <= t1:
        return p0 + (peak - p0) * (s / t1)
    return rest + (peak - rest) * math.exp(-decay_rate * (s - t1))


# ---------------------------------------------------------------------
# 场编译与求值
# ---------------------------------------------------------------------
def field_targets(token_field, p0, params):
    """按 target_mode 计算每轴目标值。"""
    mode = token_field["target_mode"]
    direction = token_field["direction"]
    rest = params["rest_point"]
    targets = {}
    for ax in AXES:
        if mode == "delta":
            sign = direction.get(ax, 0)
            targets[ax] = _clamp01(
                p0[ax] + params["field_delta_target_step"] * sign
            )
        elif mode == "rest":
            targets[ax] = rest[ax]
        elif mode == "boom":
            targets[ax] = _clamp01(
                p0[ax] + params["field_boom_target_step"]
                * max(p0[ax], params["field_boom_floor"])
            )
        else:  # none：本词元对该轴无漂移
            targets[ax] = p0[ax]
    return targets


def token_axis_value(token_field, ax, p0_ax, s, params):
    """单词元单轴的场值（绝对，自 p0）。"""
    prim = token_field["primitive"]
    target = token_field["targets"][ax]
    if prim == "exp_approach":
        return exp_approach(p0_ax, target, s, params["curve_rate_exp"])
    if prim == "linear_climb":
        return linear_climb(p0_ax, target, s, params["curve_rate_linear"])
    if prim == "pulse":
        rest = params["rest_point"][ax]
        return pulse(p0_ax, target, rest, s,
                     params["pulse_peak_hours"], params["pulse_decay_rate"])
    return p0_ax  # none：不动


def compile_field(ranked_tokens, p0, regime, params=None):
    """ranked_tokens（Top-3 结束词元）→ 编译后的场（可 JSON 序列化）。

    regime: "blend"（P_end < 0.5，加权混合）/"dominate"（P_end ≥ 0.5，赢家通吃）。
    全部词元无漂移能力时返回 None（场不存在）。
    """
    p = params or TIME
    compiled = []
    for t in ranked_tokens:
        rule = match_rule(t)
        tf = {
            "text": t.get("text", ""),
            "category": t.get("category", "other"),
            "w": max(p["field_token_weight_floor"], float(t.get("strength", 1.0))),
            "family": rule["family"],
            "direction": rule["direction"],
            "primitive": rule["primitive"],
            "target_mode": rule["target_mode"],
        }
        tf["targets"] = field_targets(tf, p0, p)
        compiled.append(tf)
    active = [c for c in compiled
              if c["target_mode"] != "none" or any(c["direction"].values())]
    if not active:
        return None
    return {"tokens": compiled, "active": active, "p0": dict(p0), "regime": regime}


def evaluate_field(field, s_hours, params=None):
    """场在 s 小时处的逐轴值（绝对，自场启动点 p0）。"""
    p = params or TIME
    p0 = field["p0"]
    tokens = field["active"]
    total_w = sum(t["w"] for t in tokens) or 1.0
    out = {}
    for ax in AXES:
        if field["regime"] == "dominate":
            top = max(tokens, key=lambda t: t["w"])
            out[ax] = token_axis_value(top, ax, p0[ax], s_hours, p)
        else:
            acc = sum(t["w"] * token_axis_value(t, ax, p0[ax], s_hours, p)
                      for t in tokens)
            out[ax] = acc / total_w
    return out
