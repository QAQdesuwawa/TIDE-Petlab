# ops/drift_gate.py
"""偏移卡（偏好拟合总线 期②-3）：B₀ 版本替换的候选门。

消费者卡（MANE CARD_DEFS 式）：消费差距卡和用户侧证据浓度（fit 行的
``d_sim / user_evidence_conc / D``），三条件**同时**成立才发漂移信号：

① 浓度资格：本轮 user_evidence_conc ≥ max(地板, 1.5×个人中位)——只有偏离
   平静实用基线的用户输入才采信。NPC 墙钟/状态词元不参与此判断；
② 持续：连续 persist_k 个高浓度背离轮（d_sim < sim_line）——不是一两天；
③ 幅度：窗口 D(t) 均值与 B₀ 的 L1 ≥ l1_line——值得动底层才动。

动作 = 生成一个候选 B₀ 版本内容（half_step × (D̄ − B₀)，归一化）+ 冷却
cooldown_turns。它不是运行期写入权限：单次乱写/一天心情差不能碰 B₀；即使
持续证据越线，调用方也只能落 proposal receipt，后续必须走显式审核/新版本绑定。
判定零 LLM；纯函数状态机（状态由调用方持有，测试可注入）。

“连续”是墙钟连续而非消息计数：若相邻用户证据相隔超过既有 D(t) 的
``FITTING.window_hours``，只清空持续链和 D 窗口；个人浓度历史仍保留。这样离散
用户输入不会把几周内互不相干的单次表达拼成一次偏好漂移。
"""
from model_packages.cx_six_seven_v1.preference_calibration_parameters import DRIFT, FITTING
from interaction_extensions.preference_calibration.cx_six_seven.fitting import PROTOTYPES


def new_state():
    return {"conc_hist": [], "streak": 0, "window": [], "cooldown": 0,
            "last_evidence_ts": None}


def _median(xs):
    s = sorted(xs)
    n = len(s)
    if n == 0:
        return 0.0
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2.0


def _l1(a, b):
    return sum(abs(float(a.get(k, 0.0)) - float(b.get(k, 0.0))) for k in PROTOTYPES)


def _norm(w):
    total = sum(float(w.get(k, 0.0)) for k in PROTOTYPES)
    if total <= 0:
        return {k: 0.2 for k in PROTOTYPES}
    return {k: round(float(w.get(k, 0.0)) / total, 4) for k in PROTOTYPES}


def drift_update(state, rec, b0, params=None):
    """每轮调用：消费 {user_evidence_conc, d_sim, D, evidence_ts?} → (state, event|None)。

    状态由调用方持有并传回（纯函数状态机）；返回的是候选事件 =
    {"b0_new": ..., "l1": ..., "streak": ...}。``b0_new`` 仅表示候选版本内容的
    向量，调用方只能记 proposal receipt，绝不能直接覆盖已绑定 B₀。
    """
    p = params or DRIFT
    st = dict(state or new_state())
    st["conc_hist"] = list(st.get("conc_hist") or [])
    st["window"] = [dict(w) for w in (st.get("window") or [])]
    # 均匀声明守卫：没基准方向，不产漂移（承差距卡同款纪律）
    if fit_purity(b0) <= float(FITTING.get("user_dominant_min_w", 0.3)):
        return st, None
    if int(st.get("cooldown", 0)) > 0:
        st["cooldown"] -= 1
        return st, None

    d_sim = rec.get("d_sim")
    if d_sim is None:
        return st, None
    evidence_ts = rec.get("evidence_ts")
    previous_ts = st.get("last_evidence_ts")
    if isinstance(evidence_ts, (int, float)):
        if isinstance(previous_ts, (int, float)):
            expiry_ms = float(FITTING["window_hours"]) * 3_600_000.0
            if float(evidence_ts) - float(previous_ts) > expiry_ms:
                # 只让“连续”尊重既有 D(t) 时间窗；这不是新心理量，也不影响
                # 个人浓度基线或 B₀。
                st["streak"] = 0
                st["window"] = []
        st["last_evidence_ts"] = int(evidence_ts)
    # ``conc_token`` 仅为旧回放收据的兼容回退。新 Runner 必须传用户向量投影，
    # 不能把 NPC 时间场的“表达浓度”误作用户偏好证据。
    conc = float(rec.get("user_evidence_conc", rec.get("conc_token", 0.0)) or 0.0)

    st["conc_hist"] = st["conc_hist"][-int(p["conc_hist_n"]):]
    st["conc_hist"].append(conc)
    base = _median(st["conc_hist"])
    qualified = conc >= max(float(p["conc_floor"]),
                            float(p["conc_qual_mult"]) * base)

    if qualified and d_sim < float(p["sim_line"]):
        st["streak"] = int(st.get("streak", 0)) + 1
        st["window"].append(dict(rec.get("D") or {}))
        st["window"] = st["window"][-int(p["persist_k"]):]
    else:
        st["streak"] = 0
        st["window"] = []

    if int(st.get("streak", 0)) >= int(p["persist_k"]) and st["window"]:
        d_mean = {k: sum(float(w.get(k, 0.0)) for w in st["window"])
                     / len(st["window"]) for k in PROTOTYPES}
        l1 = _l1(d_mean, b0)
        if l1 >= float(p["l1_line"]):
            hs = float(p["half_step"])
            b0_new = _norm({k: float(b0.get(k, 0.0))
                            + hs * (d_mean[k] - float(b0.get(k, 0.0)))
                            for k in PROTOTYPES})
            st["streak"] = 0
            st["window"] = []
            st["cooldown"] = int(p["cooldown_turns"])
            return st, {"b0_new": b0_new, "l1": round(l1, 4),
                        "streak": int(p["persist_k"])}
    return st, None


def fit_purity(w):
    return round(sum(float(w.get(k, 0.0)) ** 2 for k in PROTOTYPES), 6)
