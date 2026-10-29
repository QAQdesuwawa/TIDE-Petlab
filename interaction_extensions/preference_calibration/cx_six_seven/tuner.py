# ops/tuner.py
"""无梯度调参器（承 MANE 调参器：无梯度下的"反向传播"，聊天版）。

前向 = 会话（参数 → 回复 → 用户响应强度 R）；分组 = 高/低响应强度分诊带。
反向 = 对显著性权重 w1~w5：只有 R 已通过独立反馈/盲评验证闸时，才计算 AUC 建议；
       建议 = 当前值 + 0.5×(高响应组中位 − 低响应组中位)，归一化；
       每侧样本 < min_side → 只报告不建议（宁缺毋滥，MANE 同款）。
口径与 report_calibration 验收同一条线（一把尺子量到底）。
R 未验真时只允许观察报告；写入 profile 还需要显式的已验证反馈凭据。
"""
import os

from model_packages.cx_six_seven_v1.preference_calibration_parameters import PERSONA, SALIENCE
from model_packages.cx_six_seven_v1.calibration_statistics import median, spearman
from .response_intensity import label_of

# 权重键 ↔ 分量键
WEIGHT_KEYS = ("w_dv_attr", "w_dv_rep", "w_pain", "w_entropy", "w_pneed")
WEIGHT_TO_COMP = {"w_dv_attr": "dv_attr", "w_dv_rep": "dv_rep",
                  "w_pain": "pain", "w_entropy": "entropy", "w_pneed": "p_need"}
COMPONENT_KEYS = ("dv_attr", "dv_rep", "pain", "entropy", "p_need")


def collect_samples(sessions):
    """会话 → 调参样本：{label, 分量值…}（只取落在分诊带内的轮次）。"""
    out = []
    for s in sessions or []:
        for m in s.get("messages") or []:
            if m.get("role") != "user" or "S_parts" not in m or "R" not in m:
                continue
            label = label_of(float(m["R"]))
            if label is None:
                continue
            parts = m.get("S_parts") or {}
            sample = {"label": label}
            for c in COMPONENT_KEYS:
                sample[c] = float(parts.get(c, 0.0))
            out.append(sample)
    return out


def tune_salience(samples, weights=None, min_side=3, auc_line=0.7, r_validated=False):
    """→ (建议权重 dict, 报告 list)。建议为空 = 全部宁缺毋滥。

    auc_line 默认 0.7（比 MANE 的 0.6 更严）：隐式分组（R 分诊带）噪声高于人工
    标签，5 个权重 × 随机样本在 0.6 线下容易出假阳性建议——门严一点，人工裁决少一点。
    """
    if not r_validated:
        return {}, [{"verdict": "R 未经独立反馈/盲评验证；只观察，不建议调参",
                     "n": len(samples or [])}]
    w = dict(weights or {k: float(SALIENCE[k]) for k in WEIGHT_KEYS})
    report = []
    suggestions = {}
    for wk in WEIGHT_KEYS:
        comp = WEIGHT_TO_COMP[wk]
        labeled = [s for s in samples if s["label"] in (0, 1)]
        strong = [s[comp] for s in labeled if s["label"] == 1]
        weak = [s[comp] for s in labeled if s["label"] == 0]
        if len(strong) < min_side or len(weak) < min_side:
            report.append({"weight": wk, "verdict": "样本不足",
                           "n_strong": len(strong), "n_weak": len(weak)})
            continue
        rho = spearman([s[comp] for s in labeled], [s["label"] for s in labeled])
        if rho is None:
            report.append({"weight": wk, "verdict": "零方差"})
            continue
        auc = (rho + 1.0) / 2.0
        if 1.0 - auc_line < auc < auc_line:
            report.append({"weight": wk, "verdict": "AUC 不过线", "auc": round(auc, 4)})
            continue
        med_s, med_w = median(strong), median(weak)
        delta = 0.5 * (med_s - med_w)
        suggestions[wk] = max(0.0, w[wk] + delta)
        report.append({"weight": wk, "verdict": "建议调整",
                       "auc": round(auc, 4), "delta": round(delta, 4),
                       "med_strong": round(med_s, 4), "med_weak": round(med_w, 4)})
    if suggestions:
        total = sum(suggestions.values())
        if total > 0:
            suggestions = {k: round(v / total, 4) for k, v in suggestions.items()}
    return suggestions, report


def apply_salience_override(profile, weights, validated_feedback=False):
    """写入已验证的调参建议；R-only 建议不得进入运行时 profile。"""
    if not validated_feedback:
        raise ValueError("R 未经独立反馈/盲评验证，禁止写入显著性调参")
    profile = dict(profile or {})
    tune = dict(profile.get("tune") or {})
    tune["salience_weights"] = dict(weights)
    tune["evidence"] = "validated_feedback"
    profile["tune"] = tune
    return profile


def tune_variant_thresholds(records, min_side=5, r_validated=False):
    """口癖换挡阈值建议。

    样本 = (V_attr, R) 分诊带内的轮次。R 只表示响应强度；只有它已被独立反馈或
    盲评验证为与目标有关时，才可用强/弱响应组提出阈值建议。建议永不自动生效。
    """
    labeled = [(float(r.get("extra", {}).get("V_attr", 0.5)), float(r["R"]))
               for r in records if isinstance(r.get("extra"), dict)]
    strong = [v for v, r in labeled if r >= 0.6]
    weak = [v for v, r in labeled if r <= 0.3]
    report = {"n": len(labeled), "n_strong": len(strong), "n_weak": len(weak),
              "suggestions": {}}
    report["r_validated"] = bool(r_validated)
    if not r_validated:
        report["verdict"] = "R 未经独立反馈/盲评验证；只观察，不建议调参"
        return report
    if len(strong) >= min_side and len(weak) >= min_side:
        med_s, med_w = median(strong), median(weak)
        cur_close = float(PERSONA["variant_close_v"])
        cur_far = float(PERSONA["variant_far_v"])
        close_new = max(0.0, min(1.0, cur_close + 0.5 * (med_s - cur_close)))
        far_new = max(0.0, min(1.0, cur_far + 0.5 * (med_w - cur_far)))
        if abs(close_new - cur_close) > 0.01:
            report["suggestions"]["variant_close_v"] = round(close_new, 4)
        if abs(far_new - cur_far) > 0.01:
            report["suggestions"]["variant_far_v"] = round(far_new, 4)
        report["med_strong"] = round(med_s, 4)
        report["med_weak"] = round(med_w, 4)
    return report


def load_tuned_weights(profile):
    tune = (profile or {}).get("tune") or {}
    if tune.get("evidence") != "validated_feedback":
        return None
    return tune.get("salience_weights")


# =====================================================================
# 连续旋钮无梯度标定（承 tuner 纪律：算法选值、宁缺毋滥、只出建议）
# =====================================================================
def tune_continuous_knob(eval_fn, lo, hi, tol=1.0, max_iter=12):
    """连续旋钮标定：数值走算法，不拍脑袋（harness 只产样本，选值在算法层）。

    前向 = eval_fn(value) → {"pass": bool, "metric": float|None}——样本由
    自测试线 harness 产出（多种子中位抗噪）；本函数只做无梯度搜索：
    **三分探点 + 二分搜索最小过线值**（标定区单调假设：metric 随旋钮单调上升）。

    宁缺毋滥（MANE 同款）：
    - 上界不过线 → 不出建议；
    - 下界已过线 → 报告"边界低于扫描域"（扩域重扫），不硬给值；
    - 建议 = 数学定义的边界值（最小改动即最保守），不是网格 cherry-pick。
    不自动生效：人工确认后改 params.py（承 apply_salience_override 纪律）。
    """
    history = []

    def _ev(v):
        r = eval_fn(v) or {}
        history.append({"value": round(v, 4), "pass": bool(r.get("pass")),
                        "metric": r.get("metric")})
        return bool(r.get("pass"))

    if not _ev(hi):
        return {"verdict": "不过线（上界未过线，宁缺毋滥不出建议）",
                "boundary": None, "history": history}
    if _ev(lo):
        return {"verdict": "下界已过线（边界低于扫描域，扩低下界重扫）",
                "boundary": lo, "history": history}
    mid = (lo + hi) / 2.0
    a, b = (lo, mid) if _ev(mid) else (mid, hi)
    for _ in range(max_iter):
        if b - a <= tol:
            break
        m = (a + b) / 2.0
        if _ev(m):
            b = m
        else:
            a = m
    boundary = round(b, 4)
    return {"verdict": "过线", "boundary": boundary,
            "recommendation": boundary,
            "history": sorted(history, key=lambda x: x["value"])}
