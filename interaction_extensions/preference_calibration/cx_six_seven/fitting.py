# ops/fitting.py
"""用户图式拟合（架构文档 §13）：词典粗算（同步）+ LLM 精修（异步）+ 个人基线。

- 词典粗算：configs/lexicon.json 四类密度 × density_scale + 实用基线 → 5 原型权重（零 LLM 零延迟）
- 瞬时加速度：回复快于个人基线中位一半 且 字数超窗口均值 2 倍 → 逆恐/重构加权
  （变化量优于绝对值，哲学三；个人基线只跟自己比，零群体绝对值）
- 个人基线：滚动中位响应延迟；冷启动期（样本 < cold_start_n）不强产信号
- LLM 精修：每 refine_every_n 轮校正一次（契约见 prompts/refine.md，接线在 P6）
- 禁侧写：只描述"最近这些消息里的互动状态"，不做人格诊断
"""
import json
import os
import re

from model_packages.cx_six_seven_v1.preference_calibration_parameters import FITTING
from model_packages.cx_six_seven_v1.lexical_matching import word_hit_count
from model_packages.cx_six_seven_v1.schema_matrix import SchemaMatrix

PROTOTYPES = ("Counter_Phobic", "Mirroring", "Avoidance", "Trauma_Recon", "Pragmatism")


def _repository_root():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))


def load_lexicon():
    path = os.path.join(_repository_root(), "configs", "lexicon.json")
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def dict_tier_hint(text, lexicon=None):
    """词典三档粗分级（同步路径；extractor 精修滞后时的先行信号）。"""
    lex = lexicon or load_lexicon()
    t = text or ""
    if any(w in t for w in lex.get("trauma", [])):
        return "创伤"
    if any(w in t for w in lex.get("private", [])):
        return "私密"
    return None


def extract_features(text, lexicon=None):
    """四类词典密度（命中词数 / 文本长度，防长文天然高分）。
    命中 = 本六七包版本化的 CJK 词边界语义（单字边界词防误撞）。
    """
    lex = lexicon or load_lexicon()
    t = text or ""
    n = max(1.0, float(len(t)))
    feats = {}
    for key in ("attack", "approval", "avoid", "emotion"):
        hits = sum(word_hit_count(w, t) for w in lex.get(key, []))
        feats[key] = hits / n
    return feats


def _norm(w):
    total = sum(w.values())
    if total <= 0:
        return {k: 1.0 / len(PROTOTYPES) for k in PROTOTYPES}
    return {k: round(v / total, 4) for k, v in w.items()}


def coarse_fit(messages, baseline=None, params=None):
    """窗口内消息 → 5 原型权重（词典粗算，确定性）。

    messages: [{"ts": ms, "text": str, "delay_ms": int|None}]
    窗口 = 最近 window_n 条 ∩ window_hours 小时。
    raw = 逆恐:attack, 镜映:approval, 逃避:avoid, 重构:emotion（密度均值 × density_scale）
          实用:1.0（基线——平静/信息交换默认落实用）
    """
    p = params or FITTING
    lex = load_lexicon()
    msgs = list(messages or [])
    if not msgs:
        return {k: 0.2 for k in PROTOTYPES}
    if len(msgs) > p["window_n"]:
        msgs = msgs[-p["window_n"]:]
    now = msgs[-1]["ts"]
    horizon = now - p["window_hours"] * 3_600_000.0
    msgs = [m for m in msgs if m.get("ts", now) >= horizon]
    if not msgs:
        return {k: 0.2 for k in PROTOTYPES}

    agg = {"attack": 0.0, "approval": 0.0, "avoid": 0.0, "emotion": 0.0}
    for m in msgs:
        f = extract_features(m.get("text", ""), lex)
        for k in agg:
            agg[k] += f[k]
    n = max(1.0, float(len(msgs)))
    scale = p["density_scale"]
    raw = {
        "Counter_Phobic": agg["attack"] * scale / n,
        "Mirroring": agg["approval"] * scale / n,
        "Avoidance": agg["avoid"] * scale / n,
        "Trauma_Recon": agg["emotion"] * scale / n,
        "Pragmatism": 1.0,
    }

    # 瞬时加速度（个人基线：变化量非绝对值；冷启动期不产强信号）
    if baseline is not None and not baseline.get("cold", True):
        med = baseline.get("median_ms")
        delays = [m.get("delay_ms") for m in msgs if m.get("delay_ms") is not None]
        lengths = [len(m.get("text", "")) for m in msgs]
        if med and delays and lengths:
            avg_len = sum(lengths) / max(1, len(lengths))
            fast = any(d < med * 0.5 for d in delays)
            long_msg = len(msgs[-1].get("text", "")) > avg_len * 2
            if fast and long_msg:
                boost = p["burst_boost"]
                raw["Counter_Phobic"] *= boost
                raw["Trauma_Recon"] *= boost
    return _norm(raw)


def update_baseline(baseline, delay_ms, params=None, length=None):
    """个人基线：滚动中位响应延迟 + 滚动中位字数（样本上限 100）+ 冷启动标记。"""
    p = params or FITTING
    b = dict(baseline or {})
    samples = list(b.get("samples", []))
    samples.append(float(delay_ms))
    samples = samples[-100:]
    srt = sorted(samples)
    mid = len(srt) // 2
    median = srt[mid] if len(srt) % 2 else (srt[mid - 1] + srt[mid]) / 2.0
    b["samples"] = samples
    b["median_ms"] = round(median, 2)
    if length is not None:
        len_samples = list(b.get("len_samples", []))
        len_samples.append(float(length))
        len_samples = len_samples[-100:]
        lsrt = sorted(len_samples)
        lmid = len(lsrt) // 2
        b["len_samples"] = len_samples
        b["median_len"] = lsrt[lmid] if len(lsrt) % 2 else (lsrt[lmid - 1] + lsrt[lmid]) / 2.0
    b["n"] = len(samples)
    b["cold"] = len(samples) < p["cold_start_n"]
    return b


# ---------------------------------------------------------------------
# LLM 精修（异步）
# ---------------------------------------------------------------------
def _load_refine_prompt():
    path = os.path.join(_repository_root(), "prompts", "refine.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_refine_messages(messages):
    system = _load_refine_prompt()
    tail = messages[-50:] if messages else []
    user = "\n".join(f"用户：{m.get('text', '')}" for m in tail)
    return system, user


def _strip_fences(raw):
    t = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    return m.group(1) if m else t


def parse_refine(raw):
    """精修输出解析（契约见 prompts/refine.md；兜底 = 均匀先验）。"""
    data = None
    try:
        data = json.loads(_strip_fences(raw))
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}
    weights = data.get("weights") or {}
    if not isinstance(weights, dict):
        weights = {}
    w = {}
    for k in PROTOTYPES:
        try:
            w[k] = float(weights.get(k, 0.0))
        except (TypeError, ValueError):
            w[k] = 0.0
    return _norm(w)
