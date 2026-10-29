# core/tokens/priority.py
"""词元优先级合成（T.I.D.E. 版）：priority = base(类别, 能量带) × 强度 + 新鲜度。

自 M.A.N.E. core/tokens/priority.py 改造（实现设计 §3.2）：
- Top-N 默认 3（时间场合成带宽，架构文档 §10.3）；
- 删除家族保底位（persona/style/framing/gameplay 文风族不进入 T.I.D.E.）；
- 非常驻词元强度 < 0.2 沉底（无新闻，架构文档 §10.3）。
纯确定性计算，零参数学习（矩阵值只是可调旋钮，不是拟合量）。
"""
from core.params import TOKEN_PRIORITY
from core.tokens.variable import PAIN_HEAVY

# 爆发级趋势词元（常驻规则用）：每回合都必须看到的前兆
_BURST_TREND_KEYS = ("突变", "爆发", "濒临", "崩溃加剧", "崩塌", "压抑极值")

# 类别 tie-break（同分时优先）：compat（偏好通道）> pain > trend > tactics >
# relation > nature > gameplay > phase > atomic > other。确定性排序，回放一致。
_CATEGORY_PRECEDENCE = {
    "compat": 0, "pain": 1, "trend": 2, "tactics": 3, "relation": 4,
    "nature": 5, "gameplay": 6, "phase": 7, "atomic": 8, "other": 9,
}


def _order_key(entry):
    return (-entry["score"], _CATEGORY_PRECEDENCE.get(entry.get("category", "other"), 9))


def energy_band(potential, priority_config=None):
    """场景能量带：low(平静) / mid(升温) / high(爆发)。"""
    config = priority_config or TOKEN_PRIORITY
    thresholds = config.get("energy_band_thresholds", {})
    for threshold, band in (
        (thresholds.get("low_lt", 0.45), "low"),
        (thresholds.get("mid_lt", 0.65), "mid"),
    ):
        if potential < threshold:
            return band
    return "high"


def is_resident(token, priority_config=None):
    """常驻规则：信息平面 / 重档痛苦 / 爆发级趋势 / 战术 3D 词元。"""
    config = priority_config or TOKEN_PRIORITY
    rules = config.get("resident_rules", {})
    category = token.get("category", "")
    if category in rules.get("categories", ()):
        return True
    if rules.get("pain_heavy") and category == "pain" and token.get("strength", 0) >= PAIN_HEAVY:
        return True
    if rules.get("trend_burst") and category == "trend":
        text = token.get("text", "")
        if any(k in text for k in _BURST_TREND_KEYS):
            return True
    return False


def priority_score(token, band, seen_counts, priority_config=None):
    """priority = base(类别, 能量带) × 强度放大 + 新鲜度奖励。

    seen_counts: {text: 出现次数}，用于新鲜度（首次 +0.15，复读衰减）。
    """
    config = priority_config or TOKEN_PRIORITY
    matrix = config.get("base_matrix", {})
    base = matrix.get(token.get("category", "other"), {}).get(band, 0.5)
    strength = token.get("strength", 1.0) or 1.0
    count = seen_counts.get(token.get("text", ""), 0)
    novelty = (config.get("novelty_first_bonus", 0.15) if count == 0 else
               -config.get("novelty_repeat_penalty", 0.05)
               * min(count - 1, config.get("novelty_repeat_cap", 3)))
    clipped_strength = min(
        config.get("strength_ceiling", 2.0),
        max(config.get("strength_floor", 0.2), strength),
    )
    return base * clipped_strength + novelty


def score_token_resources(candidates, potential, seen_counts=None, priority_config=None,
                          provenance=None):
    """对本轮资源做全量、确定性的排序，尚不施加时间场的 Top-N 带宽。

    这是同一笔总线事务中的资源账本，而不是新的状态或第二条词元流：
    ``all`` 只包含通过既有 ``WEAK_SINK`` 的候选，按既有 priority/newness 公式排序，
    并与 ``source_receipt`` 一一对应。它让不同消费者在同一份已结算资源上做各自投影，
    避免时间场 Top-3 被误用为语言模型的全部表达素材。
    """
    config = priority_config or TOKEN_PRIORITY
    seen = dict(seen_counts or {})
    band = energy_band(potential, config)
    source_rows = list(provenance or ())
    emitted = []
    for candidate_index, token in enumerate(candidates):
        text = token.get("text", "")
        # 修正 MANE 原版"先增后算"顺序 bug：先按当前历史计数评分（兑现首现 +0.15），
        # 再记入本轮计数——跨轮持久化 seen_counts 后，静态常驻词元按 −0.05/复读衰减，
        # 新鲜"新闻"词元获得首现奖励，防止关系词元吃满 Top-N（G6f 截流同族问题）。
        score = priority_score(token, band, seen, config)
        seen[text] = seen.get(text, 0) + 1
        entry = {"text": text, "layer": token.get("layer", "表"),
                 "category": token.get("category", "other"),
                 "strength": token.get("strength", 1.0), "score": round(score, 3),
                 # 旁路来源只在本次 rank 的返回收据中出现；绝不写入 token 本体、
                 # 状态或归档。这让相同 text 的不同计算方向也可追溯。
                 "_source": (dict(source_rows[candidate_index])
                             if candidate_index < len(source_rows)
                             and isinstance(source_rows[candidate_index], dict) else None),
                 "_candidate_index": candidate_index}
        if (not is_resident(token, config)
                and entry["strength"] < config.get("weak_sink", 0.2)):
            continue  # 无新闻沉底：任何通道都不送达
        emitted.append(entry)

    emitted.sort(key=_order_key)
    resources = []
    source_receipt = []
    for entry in emitted:
        resources.append({key: value for key, value in entry.items()
                          if key not in {"_source", "_candidate_index"}})
        source_receipt.append({
            "candidate_index": entry["_candidate_index"],
            "source": entry["_source"],
        })
    return {
        "all": resources,
        "band": band,
        # 与全量、已排序资源一一对应的临时来源收据；不进入状态或归档。
        "source_receipt": source_receipt,
    }


def _split_bands(chosen, source_receipt, band):
    """把已经选中的时间场资源分层，并保持来源回执的并行契约。"""
    n = len(chosen)
    core_n = max(1, n // 3)
    important_n = max(1, n // 3)
    return {
        "core": chosen[:core_n],
        "important": chosen[core_n:core_n + important_n],
        "background": chosen[core_n + important_n:],
        "all": chosen,
        "band": band,
        "source_receipt": source_receipt,
    }


def select_field_tokens(resources, potential, priority_config=None):
    """从同轮全量资源账本投影原有 Top-N 时间场带宽。

    仅此函数消费 resident/family quota/Top-N；因此时间场规则与语言表达的资源选择
    不再共用同一份截断结果。输入为 ``score_token_resources`` 的返回值。
    """
    config = priority_config or TOKEN_PRIORITY
    top_n = config.get("top_n", 3)
    family_quota = config.get("family_quota") or {}
    all_resources = list((resources or {}).get("all", ()))
    source_rows = list((resources or {}).get("source_receipt", ()))

    rows = []
    for index, token in enumerate(all_resources):
        entry = dict(token)
        source_row = (dict(source_rows[index]) if index < len(source_rows)
                      and isinstance(source_rows[index], dict) else
                      {"candidate_index": index, "source": None})
        rows.append((entry, source_row))

    residents = [row for row in rows if is_resident(row[0], config)]
    scored = [row for row in rows if not is_resident(row[0], config)]

    # G6f 家族保底位（承 MANE core/tokens/priority.py）：常驻词元吃满带宽时，
    # 有发射的新族观测读数仍必须送达——先算本回合有发射（强度≥0.2 新闻读数）
    # 的配额族数，为其预留席位，常驻词元只填"剩余带宽"；保底位占 Top-N
    # 带宽、不扩带宽。TIDE 的配额族 = compat（相性/心向词元——贴合通道第一
    # 公民，高压轮不被 relation/pain 常驻截流）。
    fired_families = {entry["category"] for entry, _source in scored
                      if entry["category"] in family_quota and entry["strength"] >= 0.2}
    quota_needed = min(len(fired_families), top_n)
    resident_cap = top_n - quota_needed

    # 常驻优先 + 去重，再按分数补足（同分按类别 tie-break）
    chosen_rows = []
    chosen_texts = set()
    for entry, source in sorted(residents, key=lambda row: _order_key(row[0])):
        if len(chosen_rows) >= resident_cap:
            break
        if entry["text"] not in chosen_texts:
            chosen_rows.append((entry, source))
            chosen_texts.add(entry["text"])
    # 保底位：每族取分最高的一条新闻读数
    for fam, q in family_quota.items():
        if len(chosen_rows) >= top_n:
            break
        pool = [(entry, source) for entry, source in scored
                if entry["category"] == fam and entry["strength"] >= 0.2
                and entry["text"] not in chosen_texts]
        for entry, source in sorted(pool, key=lambda row: _order_key(row[0]))[:int(q)]:
            if len(chosen_rows) >= top_n:
                break
            chosen_rows.append((entry, source))
            chosen_texts.add(entry["text"])
    for entry, source in sorted(scored, key=lambda row: _order_key(row[0])):
        if len(chosen_rows) >= top_n:
            break
        # 保底族弱读数（强度<0.2，贴合类）走"无新闻沉底"：任何通道都不送达
        if entry["category"] in family_quota and entry["strength"] < 0.2:
            continue
        if entry["text"] not in chosen_texts:
            chosen_rows.append((entry, source))
            chosen_texts.add(entry["text"])

    chosen_rows.sort(key=lambda row: _order_key(row[0]))
    chosen = [entry for entry, _source in chosen_rows]
    selected_sources = [source for _entry, source in chosen_rows]
    return _split_bands(chosen, selected_sources,
                        (resources or {}).get("band", energy_band(potential, config)))


def rank_tokens(candidates, potential, seen_counts=None, priority_config=None,
                provenance=None):
    """兼容入口：全量资源排序后投影既有 Top-N 时间场结果。"""
    resources = score_token_resources(
        candidates, potential, seen_counts=seen_counts,
        priority_config=priority_config, provenance=provenance)
    return select_field_tokens(resources, potential, priority_config=priority_config)
