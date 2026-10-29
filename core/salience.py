# core/salience.py
"""显著性分数 S（架构文档 §12.3）：这一轮有多值得被想起。

S = w1·|ΔV_attr| + w2·|ΔV_rep| + w3·痛苦度 + w4·信息熵 + w5·P_need
S 不决定"存不存"，只决定"触发权重"。纯函数，零 LLM。
"""
import math

from core.params import SALIENCE, MEMORY


def _entropy_proxy(text, scale_chars=None):
    """信息熵代理（[拍脑袋待标定]）：长度对数归一，长内容信息量大。"""
    scale = scale_chars or MEMORY["entropy_scale_chars"]
    n = len(text or "")
    if n <= 0:
        return 0.0
    return min(1.0, math.log2(1.0 + n) / math.log2(1.0 + scale))


def compute_salience_parts(prev_vars, vars_, pain=0.0, tier=None, text="", params=None):
    """计算本轮显著性分数 S + 各分量（供调参器收集逐轮样本）。

    pain：异步提取 Agent 的痛苦度（可能滞后）；tier：三档分级兜底——
    创伤档即使 pain 未到，也按档位权重抬高痛苦分量（创伤档强制高显著性）。
    """
    p = params or SALIENCE
    prev = prev_vars or {}
    v = vars_ or {}
    dv_attr = abs(float(v.get("V_attr", 0.0)) - float(prev.get("V_attr", 0.0)))
    dv_rep = abs(float(v.get("V_rep", 0.0)) - float(prev.get("V_rep", 0.0)))
    pain_comp = float(pain)
    if tier:
        pain_comp = max(pain_comp, float(p.get("tier_pain_weight", {}).get(tier, 0.0)))
    p_need = float(v.get("P_need", 0.0))
    entropy = _entropy_proxy(text)
    parts = {"dv_attr": dv_attr, "dv_rep": dv_rep, "pain": pain_comp,
             "entropy": entropy, "p_need": p_need}
    s = (p["w_dv_attr"] * dv_attr + p["w_dv_rep"] * dv_rep
         + p["w_pain"] * pain_comp
         + p["w_entropy"] * entropy
         + p["w_pneed"] * p_need)
    return round(min(1.0, max(0.0, s)), 4), parts


def compute_salience(prev_vars, vars_, pain=0.0, tier=None, text="", params=None):
    """计算本轮显著性分数 S ∈ [0,1]。"""
    return compute_salience_parts(prev_vars, vars_, pain=pain, tier=tier,
                                  text=text, params=params)[0]
