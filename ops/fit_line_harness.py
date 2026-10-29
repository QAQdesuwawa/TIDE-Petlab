# ops/fit_line_harness.py
"""自测试线 · 动态层 harness（零 LLM · 种子化可回放）。

B 集合 × 分段随机扰动 × 两条验收曲线（承 docs/基准锚定与调动监测.md §六）：
- B 的回复 = 反应特征合成器（Level 1）：真值权重 → 各原型特征模板加权混合
  （产品前向映射"喜好→行为"的确定性形态）；文本用词典词实现密度，延迟/长度
  直接合成——LLM 角色对调版留待文本质量层（本 harness 只测拟合数学，
  隔离 LLM 方差；循环性边界见设计文档 §六）；
- 扰动 = 分段随机（每段 2~6 轮同档、段间跳变——心情有惯性），注入四通道
  （情绪词密度/延迟/长度/暴增），绝不碰声明（声明=真值直接设 prior）；
- 曲线① = D(t) 跟随注入扰动（相关 + bias/variance 误差分布，偏差定位到原型
  → 喂 T4 补词）；
- 曲线② = 相似度长期均值（锚住了：无扰动下 D(t) 应贴近 B₀）。

注意：合成器与拟合器共用同一词典 → 本 harness 测的是"混合/归一化/窗口/加速"
的估计质量，**词典覆盖率的系统性偏差须由 T4 的"未收录词变体"另行测**。
"""
import random

from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import fit_gap
from interaction_extensions.preference_calibration.cx_six_seven.fitting import PROTOTYPES
from ops.metrics import spearman

# 每原型特征模板（[拍脑袋待标定] 测试线资产，非产品参数）：
# 关键词数按通道（attack/approval/avoid/emotion），delay_ms 基准延迟，length 基准长度
TEMPLATES = {
    "Counter_Phobic": {"attack": 4, "approval": 0, "avoid": 0, "emotion": 0,
                       "delay_ms": 800, "length": 18},
    "Mirroring":      {"attack": 0, "approval": 4, "avoid": 0, "emotion": 0,
                       "delay_ms": 1200, "length": 20},
    "Avoidance":      {"attack": 0, "approval": 0, "avoid": 4, "emotion": 0,
                       "delay_ms": 3000, "length": 10},
    "Trauma_Recon":   {"attack": 0, "approval": 0, "avoid": 0, "emotion": 4,
                       "delay_ms": 900, "length": 22},
    "Pragmatism":     {"attack": 0, "approval": 0, "avoid": 0, "emotion": 0,
                       "delay_ms": 1500, "length": 15},
}

# 扰动四档（[拍脑袋待标定]）：情绪**密度**增量（随长度缩放，保证密度单调）/
# 延迟倍率 / 长度倍率 / 暴增概率
PERTURB = {
    "0":    {"emo_density": 0.0, "delay_mult": 1.0, "len_mult": 1.0, "burst_p": 0.0},
    "low":  {"emo_density": 0.05, "delay_mult": 1.3, "len_mult": 1.2, "burst_p": 0.0},
    "mid":  {"emo_density": 0.10, "delay_mult": 1.8, "len_mult": 1.5, "burst_p": 0.3},
    "high": {"emo_density": 0.15, "delay_mult": 2.5, "len_mult": 2.0, "burst_p": 0.6},
}

_LEVEL_ORDER = ("0", "low", "mid", "high")
_CHANNELS = ("attack", "approval", "avoid", "emotion")

# 词典外同义词表（[拍脑袋待标定] 测试线资产）：覆盖率偏差对照组用——
# 合成器默认与拟合器共享词典 = 循环（参数会拟合到"合成器的分布"上）；
# 对照组改用词典外同义词，拟合掉多少 = 词典覆盖率的量化偏差。
# 纪律：每个词不得包含任何词典词为子串（防对照组自身撞词典）。
SYNONYMS = {
    "attack": ["发火", "凶巴巴", "冷嘲", "翻脸", "斥责", "咄咄逼人"],
    "approval": ["求表扬", "快看我", "认可一下", "给我个赞", "表扬我"],
    "avoid": ["别管我", "爱答不理", "懒得说", "敷衍你", "随你便"],
    "emotion": ["抓狂", "心如刀绞", "委屈巴巴", "火冒三丈", "泪崩", "憋屈"],
}


def _load_lexicon():
    from interaction_extensions.preference_calibration.cx_six_seven.fitting import load_lexicon
    return load_lexicon()


def synth_message(true_weights, level, rng, lexicon=None, outlex=False):
    """B 的一条消息：真值权重 × 模板加权 → (text, delay_ms, length)。

    先定长度（扰动缩放），再按**密度**定词数——保证每通道密度随档位单调
    （密度 = Σ w_k·(t_k[ch]/t_k[length])，情绪通道再加扰动密度）；
    文本 = 词 + 中性填充字；暴增 = 延迟减半 + 长度翻倍。
    outlex=True → 词池改用 SYNONYMS（词典外同义，覆盖率偏差对照组）。
    """
    lex = lexicon or _load_lexicon()
    pools = SYNONYMS if outlex else lex
    total = sum(true_weights.get(k, 0.0) for k in PROTOTYPES) or 1.0
    w = {k: true_weights.get(k, 0.0) / total for k in PROTOTYPES}
    delay = sum(w[k] * TEMPLATES[k]["delay_ms"] for k in PROTOTYPES)
    base_len = sum(w[k] * TEMPLATES[k]["length"] for k in PROTOTYPES)

    p = PERTURB[level]
    length = base_len * p["len_mult"]
    if rng.random() < p["burst_p"]:
        delay *= 0.5
        length *= 2.0
    delay *= p["delay_mult"]

    counts = {}
    for ch in _CHANNELS:
        dens = sum(w[k] * TEMPLATES[k][ch] / max(1.0, TEMPLATES[k]["length"])
                   for k in PROTOTYPES)
        counts[ch] = int(round(dens * length))
    counts["emotion"] += int(round(p["emo_density"] * length))

    words = []
    for ch in _CHANNELS:
        pool = pools.get(ch, [])
        if pool and counts[ch] > 0:
            words.extend(rng.choice(pool) for _ in range(counts[ch]))
    rng.shuffle(words)
    # 词间用非 CJK 分隔符——真实文本词间有标点；连续拼接会让单字边界词
    # （滚/嗯/哦）两侧都是 CJK 而失效（与 ops/lexmatch 边界语义对齐）
    base = "，".join(words)
    need = max(0, int(length) - len(base))
    # 填充字集刻意避开词典全部词（avoid 词典含单字"嗯/哦"——填充字撞词典 =
    # 特征污染，合成器资产纪律；产品侧同族风险见 recall_gate 排除清单教训）
    filler = "".join(rng.choice("的了在想吗啊") for _ in range(need))
    text = (base + filler)[: max(1, int(length))]
    return text, int(delay), int(length)


def segment_plan(turns, max_level, rng):
    """分段随机计划：[(level, 段长)]，段长 2~6，档位 ∈ 0..max_level 均匀采样。"""
    plan = []
    used = 0
    allow = _LEVEL_ORDER[:_LEVEL_ORDER.index(max_level) + 1]
    while used < turns:
        seg = min(rng.randint(2, 6), turns - used)
        plan.append((rng.choice(allow), seg))
        used += seg
    return plan


def simulate_session(runner, store, true_weights, turns=30, seed=1,
                     max_level="high", now_start=1_000_000, step_ms=60_000):
    """跑一个 B：返回 records（每轮 fit 行 + level 标注）。声明 = 真值
    （直接设 prior）；A 侧回复由注入的 stub responder 承担（harness 不管文本）。
    额外记录 compat_token（心向词元是否装配进 Top-3）——C4 卡装配率样本。
    """
    runner.prior = {k: float(true_weights.get(k, 0.0)) for k in PROTOTYPES}
    rng = random.Random(seed)
    records = []
    now = now_start
    for level, seg_len in segment_plan(turns, max_level, rng):
        for _ in range(seg_len):
            text, delay, _len = synth_message(true_weights, level, rng)
            runner.on_user_message(text, now_ms=now, delay_ms=delay)
            now += step_ms
            fits = store.read_breakpoints()
            fit = fits[-1]["extra"]["fit"] if fits else {}
            extra = fits[-1]["extra"] if fits else {}
            records.append({"turn": len(records) + 1, "level": level,
                            "d_sim": fit.get("d_sim"),
                            "D": dict(fit.get("D") or {}),
                            "conc_token": fit.get("conc_token"),
                            "compat_token": bool(extra.get("compat_token"))})
    return records


def assembly_rate(records):
    """心向词元装配率 = compat_token=True 的轮占比（C4 卡度量，零 LLM）。"""
    if not records:
        return 0.0
    n = sum(1 for r in records if r.get("compat_token"))
    return round(n / len(records), 4)


# =====================================================================
# 两条验收曲线
# =====================================================================
def curve1_follow(records, proto="Trauma_Recon"):
    """曲线①：D(t) 对注入档位的跟随（档位序 vs 该原型权重的 Spearman）。"""
    xs = [_LEVEL_ORDER.index(r["level"]) for r in records]
    ys = [float((r.get("D") or {}).get(proto, 0.0)) for r in records]
    return spearman(xs, ys)


def error_stats(records, true_weights):
    """估计误差：bias（逐原型均值误差，定位系统性偏差）+ variance（L1 标准差）。"""
    n = len(records)
    bias = {k: round(sum(float(r["D"].get(k, 0.0)) - float(true_weights.get(k, 0.0))
                         for r in records) / n, 4) for k in PROTOTYPES}
    l1s = [sum(abs(float(r["D"].get(k, 0.0)) - float(true_weights.get(k, 0.0)))
               for k in PROTOTYPES) for r in records]
    mean = sum(l1s) / n
    var = round((sum((x - mean) ** 2 for x in l1s) / n) ** 0.5, 4)
    return {"bias": bias, "l1_mean": round(mean, 4), "l1_std": var}


def curve2_anchor(records):
    """曲线②：相似度长期均值与方差（均匀守卫轮次 d_sim=None 已排除）。"""
    sims = [r["d_sim"] for r in records if r["d_sim"] is not None]
    if not sims:
        return {"n": 0, "mean": None, "std": None}
    mean = sum(sims) / len(sims)
    std = (sum((s - mean) ** 2 for s in sims) / len(sims)) ** 0.5
    return {"n": len(sims), "mean": round(mean, 4), "std": round(std, 4)}


# =====================================================================
# 参数敏感性扫描（可复用口径：同一 harness、同一通过线，扫共享旋钮）
# =====================================================================
def scan_session(true_weights, turns, seed, max_level, fit_params, outlex=False):
    """纯拟合层会话：合成消息 → coarse_fit（带旋钮参数）+ 个人基线更新 → D(t)。

    不经过 runner（测量隔离：测 coarse_fit 旋钮就不引入总线/融合的干扰）。
    outlex=True → 词典外同义词（覆盖率偏差对照组）。
    返回 records（结构与 simulate_session 同构：turn/level/d_sim/D）。
    """
    from interaction_extensions.preference_calibration.cx_six_seven.fitting import (
        coarse_fit,
        update_baseline,
    )
    rng = random.Random(seed)
    msgs = []
    baseline = {}
    records = []
    for level, seg_len in segment_plan(turns, max_level, rng):
        for _ in range(seg_len):
            text, delay, _len = synth_message(true_weights, level, rng,
                                              outlex=outlex)
            msgs.append({"ts": len(msgs) * 60_000, "text": text,
                         "delay_ms": delay})
            d_t = coarse_fit(msgs, baseline=baseline, params=fit_params)
            baseline = update_baseline(baseline, delay, params=fit_params,
                                       length=len(text))
            records.append({"turn": len(records) + 1, "level": level,
                            "d_sim": fit_gap(true_weights, d_t, params=fit_params),
                            "D": dict(d_t), "conc_token": 0.0})
    return records


def scan_grid(true_weights, turns, seeds, grid, max_level="high"):
    """旋钮网格 → {combo: curve② 多种子中位}（无扰动专场做锚住检验）。"""
    out = {}
    for combo in grid:
        anchors = []
        for seed in seeds:
            recs = scan_session(true_weights, turns, seed, "0", combo)
            a = curve2_anchor(recs)
            if a["mean"] is not None:
                anchors.append(a["mean"])
        out[tuple(sorted(combo.items()))] = (sorted(anchors)[len(anchors) // 2]
                                             if anchors else None)
    return out
