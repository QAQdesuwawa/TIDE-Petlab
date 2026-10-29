# ops/recall_gate.py
"""召回裁决层（状态相关记忆）：裁决证据来自词元系统，不另起状态炉灶。

- 词元是系统的标准状态语言（场编译/卡包触发/写作直觉/台账盖章都在消费它），
  裁决层同样消费：ranked 词元的家族（lexicon 规则表映射）+ 桥词元的情绪语义；
- 证据 = Σ strength × 家族极性，只信语义可靠类别（pain/trend/phase + 桥），
  排除 compat/nature 第一公民的子串误撞污染；
- 支配判定承"混合/支配"双机制同款口径：|证据| ≥ dominate → grudge/nostalgia，
  否则 mixed；概率随证据**连续**调节（p = clamp(基础 + 斜率×证据)）——
  替代手搓变量线性公式与固定 ±档；
- 家族极性 = 语义常量（lexicon 设计蕴含：攻击/失控/疲惫/受制为负，
  爱意/熟稔/平静为正），判定全数理、零 LLM。
"""
from core.params import RECALL_GATE
from temporal.lexicon import match_rule

# 家族极性（语义常量，承 lexicon 规则表语义）
_POLARITY = {
    "aggro": -1.0, "chaos": -1.0, "fatigue": -1.0, "info_lower": -1.0,
    "bridge_restless": -0.8, "bridge_guard": -0.6, "bridge_chaos": -1.0,
    "love": 1.0, "fam_close": 1.0, "info_calm": 0.5,
    "info_upper": 0.3, "bridge_calm": 0.5,
}

_NEGATIVE = {"aggro", "info_lower", "fatigue", "chaos"}
_POSITIVE = {"love", "fam_close", "info_calm"}


def token_evidence(ranked_tokens, bridge_tokens=None, params=None):
    """→ 词元证据 ∈ [−1, 1]（正 = 怀旧倾向，负 = 翻旧账倾向）。"""
    p = params or RECALL_GATE
    allowed = set(p["evidence_categories"])
    ev = 0.0
    for t in (ranked_tokens or []):
        if str(t.get("category", "")) not in allowed:
            continue
        fam = match_rule(t).get("family", "none")
        pol = _POLARITY.get(fam, 0.0)
        if pol:
            ev += pol * float(t.get("strength", 1.0) or 1.0)
    for t in (bridge_tokens or []):
        fam = match_rule(t).get("family", "none")
        pol = _POLARITY.get(fam, 0.0)
        if pol:
            ev += pol * float(t.get("strength", 1.0) or 1.0) * p["bridge_weight"]
    return round(max(-1.0, min(1.0, ev)), 4)


def memory_query_tokens(ranked_tokens, params=None, allow_resident=False):
    """按结构化来源筛出可作情绪档案相似度锚的词元。

    相性/天性会跨话题常驻；若参与记忆重合计算，会因“同一人格”而虚假命中旧事。
    这里不依赖具体业务词或用户偏好，只消费词元已有的类别语义。
    """
    p = params or RECALL_GATE
    excluded = set() if allow_resident else set(p.get("memory_exclude_categories") or ())
    return [dict(token) for token in (ranked_tokens or [])
            if str(token.get("category", "")) not in excluded]


def recall_mode(ranked_tokens, bridge_tokens=None, params=None):
    """词元证据支配 → "grudge" / "nostalgia" / "mixed"（确定性）。"""
    p = params or RECALL_GATE
    ev = token_evidence(ranked_tokens, bridge_tokens, p)
    if ev <= -p["dominate"]:
        return "grudge"
    if ev >= p["dominate"]:
        return "nostalgia"
    return "mixed"


def gate_probability(mode, base_p, evidence, params=None):
    """概率随词元证据连续调节（clamp 留混沌两端）。"""
    p = params or RECALL_GATE
    return round(max(p["p_min"], min(p["p_max"], base_p + p["p_slope"] * evidence)), 4)


def preferred_families(mode):
    """家庭偏好：翻旧账要负面族、怀旧要正面族、混合不过滤。"""
    if mode == "grudge":
        return _NEGATIVE
    if mode == "nostalgia":
        return _POSITIVE
    return None


def hint_for(mode, family):
    """呈现包装：模式前缀 + 家庭短语（家庭短语表在 memory 层，函数内取防环）。"""
    from ops.memory import RECALL_PHRASES
    base = RECALL_PHRASES.get(family, RECALL_PHRASES["info_calm"])
    if mode == "grudge":
        return "你突然想起上次也有过这样的一幕，火又上来了——" + base
    if mode == "nostalgia":
        return "你忽然有点怀念过去——" + base
    return base
