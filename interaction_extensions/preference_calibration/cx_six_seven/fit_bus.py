# ops/fit_bus.py
"""偏好拟合总线 · 期①：双寄存器 + 差距/浓度读数的纯函数（零行为变化）。

结构（承 docs/基准锚定与调动监测.md v3.2）：
- B₀ 基准锚（首因）= 当前 prior（声明权重，G1 裁定冻结；无 prior 回落均匀）；
- D(t) 瞬时状态（近因）= 词典粗算+精修的行为拟合（coarse_fit 输出）；
- d(t) 差距线 = cos(D(t), B₀) 连续相似度 ∈ [0,1]；均匀声明守卫——
  B₀ 纯度 H ≤ user_dominant_min_w（0.3）时不产 d(t)（没基准方向不做偏差判断）；
- 浓度读数分属不同消费者：场词元浓度 ``conc_token`` 只服务表达；用户偏好
  证据浓度 ``user_evidence_conc`` 只从 D(t) 读取，供漂移门判断。两者不可互换。

本模块 = 纯函数层；接线在 runner（期①：只算只落盘，消费端不变）。
判定零 LLM、零新测量（全部读数来自现有报告/状态）。
"""
import math

from model_packages.cx_six_seven_v1.preference_calibration_parameters import CONCENTRATION, FITTING

PROTOTYPES = ("Counter_Phobic", "Mirroring", "Avoidance", "Trauma_Recon", "Pragmatism")


def _norm(w):
    total = sum(float(w.get(k, 0.0)) for k in PROTOTYPES)
    if total <= 0:
        return {k: 0.2 for k in PROTOTYPES}
    return {k: float(w.get(k, 0.0)) / total for k in PROTOTYPES}


def purity(weights):
    """H = Σwᵢ²：权重集中度（E_user 的置信度口径，承架构文档 §9.2）。"""
    w = _norm(weights)
    return round(sum(v * v for v in w.values()), 6)


def dominant(weights):
    """主导原型（无平局裁决：均匀/平局时按最大键稳定返回——调用方自行守卫）。"""
    w = _norm(weights)
    return max(w, key=lambda k: (w[k], k))


def uniform_guard(b0, params=None):
    """均匀声明守卫：纯度不足（没选方向）→ True（不产 d(t)）。"""
    p = params or FITTING
    return purity(b0) <= float(p.get("user_dominant_min_w", 0.3))


def cos_similarity(a, b):
    """五原型空间连续相似度 ∈ [0,1]（非负归一向量，1 = 完全一致）。"""
    wa, wb = _norm(a), _norm(b)
    dot = sum(wa[k] * wb[k] for k in PROTOTYPES)
    na = math.sqrt(sum(v * v for v in wa.values()))
    nb = math.sqrt(sum(v * v for v in wb.values()))
    if na <= 0 or nb <= 0:
        return 0.0
    return round(dot / (na * nb), 6)


def fit_gap(b0, d_t, params=None):
    """d(t) = cos(D(t), B₀)；均匀声明守卫不产值 → None。"""
    if uniform_guard(b0, params):
        return None
    return cos_similarity(d_t, b0)


def concentration_readings(p_now, p_prev, p_need):
    """浓度线双读数（期①只落盘）：
    ① P / P_need 绝对值（场热度，冻结区口径）
    ② ΔP = 本轮场增量（含时间推进与事件两部分——"纯用户事件"细分留期②）。
    """
    return {
        "P": round(float(p_now), 4),
        "dP": round(float(p_now) - float(p_prev), 4),
        "P_need": round(float(p_need), 4),
    }


def user_evidence_concentration(d_t):
    """用户侧偏好证据浓度 = D(t) 的非实用质量。

    ``coarse_fit`` 已把 Pragmatism 定义为平静/信息交换的基线，且 D(t) 是归一化
    五维向量；因此 ``1 - D_pragmatism`` 恰好是四种非日常交互方向的总质量。它是
    对既有向量的纯投影，不新增心理状态、词典或计数器。只允许漂移门读取它，不能
    由 NPC 的墙钟、人格或状态词元替代。
    """
    return round(max(0.0, 1.0 - _norm(d_t)["Pragmatism"]), 4)


# =====================================================================
# 浓度卡（期②-1）：情感浓度 → 状态词元（消费者卡：只读词元流，写自己的输出）
# =====================================================================
def conc_token_strength(ranked_tokens, params=None):
    """浓度读数：Σ 词元强度（全族开放，仅排除第一公民 compat/nature 污染），
    clamp [0,1]×gain。ranked_tokens = report["ranked"]["all"]（rank 后 Top-N）。
    """
    p = params or CONCENTRATION
    excl = set(p["exclude_categories"])
    s = sum(float(t.get("strength", 0.0) or 0.0)
            for t in (ranked_tokens or [])
            if t.get("category", "other") not in excl)
    return round(min(1.0, s * float(p.get("gain", 1.0))), 4)


def emit_concentration_token(ranked_tokens, params=None):
    """浓度 → 状态词元（喂写作直觉共情面）：
    rank 同款沉底线（< min_emit 不产）——浓度低时词元不存在，天然仲裁；
    产出词元 category=phase（tie-break 最低档、无常驻特权），family=none
    （不进漂移场、不进召回证据——零污染）。返回 None 或 token dict。
    """
    p = params or CONCENTRATION
    s = conc_token_strength(ranked_tokens, p)
    if s < float(p.get("min_emit", 0.2)):
        return None
    return {"text": str(p.get("text", "[对方情绪很浓]")), "layer": "表",
            "category": "phase", "strength": s}


def fit_row(b0, d_t, p_now, p_prev, p_need, ranked_tokens=None, params=None):
    """期①~② 每轮落盘行（breakpoints extra 的字段源，纯函数可测）。"""
    row = {
        "b0_dominant": dominant(b0),
        "b0_purity": purity(b0),
        "D_dominant": dominant(d_t),
        "D": {k: round(v, 4) for k, v in _norm(d_t).items()},
        "d_sim": fit_gap(b0, d_t, params),
        "conc": concentration_readings(p_now, p_prev, p_need),
        "user_evidence_conc": user_evidence_concentration(d_t),
        "conc_token": conc_token_strength(ranked_tokens),
    }
    return row
