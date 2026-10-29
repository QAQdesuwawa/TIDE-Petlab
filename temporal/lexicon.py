# temporal/lexicon.py
"""结束词元 → 漂移场映射（冻结区规则表，架构文档 §11.2）。

匹配 = 有序关键词规则（MANE _BURST_TREND_KEYS 同款确定性匹配，零 LLM）。
方向 δ：AXES 上的 {−1, 0, +1}；原语：exp_approach / linear_climb / pulse。
target_mode：delta（p0±0.3 步长）/ rest（静息点）/ boom（四维同向放大）。

设计要点（P1 实证）：常驻关系词元必须自带时间动力学——
否则"无筹码清谈"类词元吃满 Top-3，时间场永远为空（MANE G6f 截流事故的变体）。
信息平面/相熟度词元的沉默语义就是最自然的曲线来源。
"""

AXES = ("E_env", "P_need", "V_attr", "V_rep")

_NO = {"E_env": 0, "P_need": 0, "V_attr": 0, "V_rep": 0}
_AGGR = {"E_env": +1, "P_need": 0, "V_attr": 0, "V_rep": +1}
_LOVE = {"E_env": 0, "P_need": +1, "V_attr": +1, "V_rep": 0}
_GUARD = {"E_env": +1, "P_need": 0, "V_attr": -1, "V_rep": 0}
_BOOM = {"E_env": +1, "P_need": +1, "V_attr": +1, "V_rep": +1}

# 顺序即优先级：桥 → 崩溃 → 疲惫 → 信息平面/相熟 → 爱意 → 侵略 → 无
_RULES = [
    ("bridge_calm", ("沉默后如常", "沉默后微变"),
     dict(_NO), "exp_approach", "rest"),
    ("bridge_restless", ("沉默后·躁", "沉默后巨变"),
     dict(_AGGR), "linear_climb", "delta"),
    ("bridge_guard", ("沉默后·防备",),
     dict(_GUARD), "exp_approach", "delta"),
    ("bridge_chaos", ("沉默后·失控倾向",),
     {"E_env": 0, "P_need": +1, "V_attr": 0, "V_rep": +1}, "pulse", "boom"),
    ("chaos", ("崩溃", "爆发", "失控", "濒临", "雪崩", "加剧"),
     dict(_BOOM), "pulse", "boom"),
    ("fatigue", ("崩塌", "万念俱灰", "麻木", "疲惫", "衰退", "缓解"),
     dict(_NO), "exp_approach", "rest"),
    ("info_upper", ("单方绝对上位", "双向核威慑"),
     dict(_AGGR), "linear_climb", "delta"),
    ("info_lower", ("单方绝对受制",),
     dict(_GUARD), "exp_approach", "delta"),
    ("fam_close", ("相熟·熟稔",),
     dict(_LOVE), "exp_approach", "delta"),
    ("info_calm", ("无筹码清谈", "势均力敌", "相熟·生疏", "相熟·试探"),
     dict(_NO), "exp_approach", "rest"),
    ("love", ("爱意", "深爱", "渴望", "吸引", "满足", "顺应", "甜蜜", "想念"),
     dict(_LOVE), "exp_approach", "delta"),
    ("aggro", ("施压", "对抗", "攻击", "侵略", "躁", "防备", "恨", "威胁",
               "碾压", "博弈", "占优", "逆"),
     dict(_AGGR), "linear_climb", "delta"),
]


def match_rule(token):
    """token → 规则（族名/方向/原语/target_mode）；无命中 → none。"""
    text = token.get("text", "")
    for name, keys, direction, primitive, target_mode in _RULES:
        if any(k in text for k in keys):
            return {"family": name, "direction": dict(direction),
                    "primitive": primitive, "target_mode": target_mode}
    return {"family": "none", "direction": dict(_NO),
            "primitive": "none", "target_mode": "none"}
