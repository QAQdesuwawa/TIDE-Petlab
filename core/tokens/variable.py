# core/tokens/variable.py
"""变量空间词元层（单 NPC 版）：相空间 + 痛苦度 + 信息平面 + 相熟度。

自 M.A.N.E. core/tokens/variable.py 改造（实现设计 §3.2 / §7.1）：
- 双角色循环 → 单 NPC（角色 B 被用户伪向量顶替，见 core/tokens/behavior.py）；
- 关系平面重定义：情感/吸引/排斥/定向/认知错位族（依赖双角色变量）删除；
  信息平面按 NPC 的 L_self/L_opponent 分档保留，新增相熟度 F 词元；
- 相空间 / 痛苦度公式逐字符继承；全部确定性几何，零学习参数。
"""
import math

# ---------------------------------------------------------------------
# 阈值（可标定旋钮口径，全部确定性）
# ---------------------------------------------------------------------
STATE_HIGH = 0.7
STATE_LOW = 0.3
DELTA_EPS = 0.1          # |Δ| ≤ 0.1 视为持稳
DELTA_STRONG = 0.25      # |Δ| ≥ 0.25 时词元归属"势"层
PAIN_LIGHT = 0.08        # 痛苦度分级：轻
PAIN_HEAVY = 0.2         # 痛苦度分级：重

LEVEL_ZH = {"high": "高位", "mid": "中位", "low": "低位"}
TREND_ZH = {"rising": "攀升", "stable": "持稳", "falling": "回落"}

# 六维变量的静态归属：表=明面，里=内心（势由变化率动态归属）
# L 语义（T.I.D.E. 重定义，架构文档 §7.2）：
#   L_self = 用户交出的私密筹码（NPC 握着的牌）
#   L_opponent = NPC 坦白的筹码（用户握着的牌）
VAR_META = {
    "E_env":      ("环境压强", "表"),
    "L_self":     ("我方筹码", "表"),
    "L_opponent": ("对方明牌", "表"),
    "P_need":     ("心理饥渴", "里"),
    "V_attr":     ("吸引", "里"),
    "V_rep":      ("排斥", "里"),
}


def _level(x):
    if x >= STATE_HIGH:
        return "high"
    if x <= STATE_LOW:
        return "low"
    return "mid"


def _trend(dx):
    if dx > DELTA_EPS:
        return "rising"
    if dx < -DELTA_EPS:
        return "falling"
    return "stable"


# ---------------------------------------------------------------------
# 相空间词元：每变量一张 3(状态) × 3(变化率) 相图，共 9 格/变量
# ---------------------------------------------------------------------
def phase_token(name, value, delta):
    """返回该变量当前所处的相空间词元（带出生层）。"""
    level = _level(value)
    trend = _trend(delta)
    label, static_layer = VAR_META[name]
    layer = "势" if abs(delta) >= DELTA_STRONG else static_layer
    text = f"[{label}·{LEVEL_ZH[level]}·{TREND_ZH[trend]}]"
    return {"text": text, "layer": layer, "var": name, "value": round(value, 3),
            "delta": round(delta, 3), "cell": (level, trend), "category": "phase"}


# ---------------------------------------------------------------------
# 痛苦度 / 危险度（损失厌恶：跌幅 × 存量 = 痛苦）——逐字符继承
# ---------------------------------------------------------------------
def _loss(prev, cur):
    return max(0.0, prev - cur)


def pain_tokens(prev_vars, vars_):
    """四个情绪重量指标：爱崩塌 / 渴求崩塌 / 麻木(在乎总量崩塌) / 恨意攀升。"""
    prev_vars = prev_vars or {}
    out = []
    metrics = []

    prev_attr, attr = prev_vars.get("V_attr", 0.0), vars_.get("V_attr", 0.0)
    metrics.append(("爱意崩塌", _loss(prev_attr, attr) * prev_attr, "里"))

    prev_need, need = prev_vars.get("P_need", 0.0), vars_.get("P_need", 0.0)
    metrics.append(("万念俱灰", _loss(prev_need, need) * prev_need, "里"))

    prev_sum = prev_attr + prev_vars.get("V_rep", 0.0)
    cur_sum = attr + vars_.get("V_rep", 0.0)
    metrics.append(("麻木(在乎崩塌)", _loss(prev_sum, cur_sum) * prev_sum / 2.0, "里"))

    prev_rep, rep = prev_vars.get("V_rep", 0.0), vars_.get("V_rep", 0.0)
    metrics.append(("恨意攀升", max(0.0, rep - prev_rep) * rep, "势"))

    for label, value, layer in metrics:
        if value >= PAIN_HEAVY:
            grade = "重"
        elif value >= PAIN_LIGHT:
            grade = "轻"
        else:
            continue  # 无感不产词元
        out.append({"text": f"[{label}·{grade}]", "layer": layer,
                    "value": round(value, 3), "category": "pain"})
    return out


# ---------------------------------------------------------------------
# 信息平面 + 相熟度（单 NPC 重定义，实现设计 §7.1）
# ---------------------------------------------------------------------
def relation_tokens(vars_):
    """信息平面（S 分档）+ 相熟度（F 分档）。

    S = L_self − L_opponent（权力倾斜度，正 = NPC 上位）
    F = min(L_self, L_opponent)（相熟度）
    """
    v = vars_ or {}
    ls = float(v.get("L_self", 0.0))
    lo = float(v.get("L_opponent", 0.0))
    s = ls - lo
    f = min(ls, lo)
    out = []

    # 1. 信息平面（NPC 主视角）
    if ls >= STATE_HIGH and lo >= STATE_HIGH:
        out.append({"text": "[双向核威慑]", "layer": "表", "category": "relation"})
    elif ls >= STATE_HIGH and lo <= STATE_LOW:
        out.append({"text": "[单方绝对上位]", "layer": "表", "category": "relation"})
    elif ls <= STATE_LOW and lo >= STATE_HIGH:
        out.append({"text": "[单方绝对受制]", "layer": "表", "category": "relation"})
    elif ls <= STATE_LOW and lo <= STATE_LOW:
        out.append({"text": "[无筹码清谈]", "layer": "表", "category": "relation"})
    if abs(s) <= 0.15:
        out.append({"text": "[势均力敌]", "layer": "表", "category": "relation"})

    # 2. 相熟度 F
    if f >= STATE_HIGH:
        out.append({"text": "[相熟·熟稔]", "layer": "表", "category": "relation"})
    elif f <= STATE_LOW:
        out.append({"text": "[相熟·生疏]", "layer": "表", "category": "relation"})
    else:
        out.append({"text": "[相熟·试探]", "layer": "表", "category": "relation"})

    return out


# ---------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------
def compute_variable_tokens(vars_, prev_vars=None):
    """每轮产出变量空间词元包：{"npc": [tokens], "relation": [tokens]}。

    首回合（无 prev）时：趋势按持稳、痛苦度为零（无基线不做损失计算）。
    """
    prev = prev_vars or {}
    npc = []
    for name in VAR_META:
        cur = float(vars_.get(name, 0.0))
        past = float(prev.get(name, cur))  # 无基线时按持稳
        npc.append(phase_token(name, cur, cur - past))
    if prev:
        npc.extend(pain_tokens(prev, vars_))
    return {"npc": npc, "relation": relation_tokens(vars_)}


def count_token_space():
    """词元空间容量统计（单 NPC 口径，纯静态计数）。

    原子词元种类 = 相空间格 + 痛苦档位 + 信息平面/相熟度分支。
    """
    phase_cells = len(VAR_META) * 9                     # 6 变量 × 3×3 相格 = 54
    pain_types = 4 * 2                                 # 4 指标 × (轻/重) = 8
    relation_types = (4 + 1 + 3)                       # 信息平面 4 + 势均力敌 1 + 相熟 3 = 8
    atomic_types = phase_cells + pain_types + relation_types
    raw_configs = (9 ** len(VAR_META)) * (3 ** 4) * relation_types
    return {
        "phase_cells": phase_cells,
        "pain_types": pain_types,
        "relation_types": relation_types,
        "atomic_token_types": atomic_types,
        "raw_config_space": raw_configs,
    }
