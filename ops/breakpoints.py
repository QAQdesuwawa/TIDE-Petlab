# ops/breakpoints.py
"""高响应签名观察层（二期 · 观察先行，零刺激）。

被动台账——每轮记录 (行为签名, 响应强度 R)，不主动制造任何刺激。聚合出的只是
"一贯高响应"签名，供回放和独立反馈核对；它不等于偏好、脆弱点或有效策略，不能
直接接入策略或参数更新。
"""
from ops.reward import label_of


def behavior_signature(resp_ids, persona_ids, P):
    """行为签名：响应块 × 人格细节块 × 能量带（确定性可聚合）。
    能量带阈值收编进 core.params.BREAKPOINTS（2026-08 审计）。
    """
    from core.params import BREAKPOINTS
    band = ("high" if P >= BREAKPOINTS["band_high"]
            else ("low" if P < BREAKPOINTS["band_low"] else "mid"))
    return {"resp": tuple(resp_ids or ()), "persona": tuple(persona_ids or ()),
            "band": band}


def signature_key(sig):
    return json_dumps(sig, sort_keys=True)


def record_breakpoint(store, ts, sig, R, extra=None):
    rec = {"ts": ts, "sig": sig, "R": float(R)}
    if extra:
        rec["extra"] = dict(extra)
    store.append_breakpoint(rec)
    return ts


def aggregate(records, min_n=3, baseline_margin=0.2, min_mean=0.6):
    """→ [(key, {n, mean_R, sig})] 候选高响应签名（按平均 R 降序）。

    候选 = 同签名样本数 ≥ min_n 且 平均 R ≥ max(基线+margin, min_mean)——
    只跟自己的历史基线比（个人基线，零群体绝对值）。
    """
    if not records:
        return []
    baseline = sum(float(r["R"]) for r in records) / len(records)
    groups = {}
    for r in records:
        key = signature_key(r.get("sig") or {})
        g = groups.setdefault(key, {"n": 0, "sum": 0.0, "sig": r.get("sig") or {}})
        g["n"] += 1
        g["sum"] += float(r["R"])
    out = []
    line = max(baseline + baseline_margin, min_mean)
    for key, g in groups.items():
        mean = g["sum"] / g["n"]
        if g["n"] >= min_n and mean >= line and mean > baseline:
            out.append((key, {"n": g["n"], "mean_R": round(mean, 4),
                              "baseline": round(baseline, 4), "sig": g["sig"]}))
    out.sort(key=lambda x: -x[1]["mean_R"])
    return out


def json_dumps(obj, sort_keys=True):
    import json
    return json.dumps(obj, ensure_ascii=False, sort_keys=sort_keys)
