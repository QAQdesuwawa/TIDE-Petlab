# ops/constraints/checks.py
"""输出侧检查卡目录（T1 v1 · 承 MANE ops/constraints/checks.py 纪律）：
每张卡 = check_fn 式独立纯函数，判定零 LLM、只读 ctx、不写任何共享状态。

TIDE 四卡（全部 observe 默认、repair/inject 关——先实测触发率再谈注入，
承 MANE G1 实证纪律）：
- dedup（复读卡）：回复与近期对话尾巴的 4-gram 重叠率——4-gram 防打转的
  输出侧形态（MANE spinning_guard 同族）；
- persona_anchor（口癖密度卡）：口癖小节提取的关键词在回复中的命中数——
  "平静热局口癖一点不剩"的输出侧警报；
- taboo（禁区卡）：禁区小节提取的短语出现在回复中 = 禁区违反；
- ledger_conflict（台账矛盾卡）：回复含否定词且命中近期台账陈述的引用键
  （【】/「」标记的关键词）——"承诺周五陪你去 → 周五没空"类改口的保守检测。

关键词提取：块文本里【X】/「X」/"X" 的显式标记词——无标记词则卡休眠
（pass），不靠语义猜测（判定全数理纪律）。
"""
from core.params import CHECKS

# 否定词（台账矛盾卡用；语义常量，保守只认这几个）
_NEGATIONS = ("没", "不", "别", "没有", "没空", "不去", "取消")


def _ngrams(text, n=4):
    t = text or ""
    if len(t) < n:
        return set()
    return {t[i:i + n] for i in range(len(t) - n + 1)}


def _extract_marked(text):
    """块文本 → 显式标记词列表（【X】/「X」/“X”/"X"）；无标记 → []（卡休眠）。"""
    import re
    out = []
    for pat in (r"【([^】]+)】", r"「([^」]+)」", r"[“\"]([^”\"]+)[”\"]"):
        out.extend(m.strip() for m in re.findall(pat, text or "") if m.strip())
    return out


def check_dedup(ctx, params=None):
    """复读卡：回复 4-gram 与近期尾巴的重叠率（短回复豁免）。"""
    p = params or CHECKS
    reply = str(ctx.get("reply") or "")
    if len(reply) < int(p["dedup_min_len"]):
        return {"pass": True, "detail": {"reason": "short", "len": len(reply)}}
    rg = _ngrams(reply)
    if not rg:
        return {"pass": True, "detail": {"reason": "short"}}
    tail = "".join(str(t) for t in (ctx.get("tail") or []))
    shared = len(rg & _ngrams(tail))
    ratio = shared / len(rg)
    return {"pass": ratio < float(p["dedup_ratio"]),
            "detail": {"ratio": round(ratio, 4), "shared": shared}}


def check_persona_anchor(ctx, params=None):
    """口癖密度卡：口癖关键词命中数 < 下限 → 口癖丢失警报；无关键词 → 休眠。"""
    p = params or CHECKS
    block = (ctx.get("persona") or {}).get("口癖") or ""
    kws = _extract_marked(block)
    if not kws:
        return {"pass": True, "detail": {"reason": "no_keywords"}}
    reply = str(ctx.get("reply") or "")
    hits = sum(reply.count(k) for k in kws)
    return {"pass": hits >= int(p["anchor_min_hits"]),
            "detail": {"hits": hits, "keywords": kws[:6]}}


def check_taboo(ctx, params=None):
    """禁区卡：禁区标记短语出现在回复中 = 禁区违反；无短语 → 休眠。"""
    block = (ctx.get("persona") or {}).get("禁区") or ""
    phrases = _extract_marked(block)
    if not phrases:
        return {"pass": True, "detail": {"reason": "no_phrases"}}
    reply = str(ctx.get("reply") or "")
    hits = [ph for ph in phrases if ph in reply]
    return {"pass": not hits, "detail": {"hits": hits}}


def check_ledger_conflict(ctx, params=None):
    """台账矛盾卡（保守）：回复含否定词且命中近期陈述的引用键。

    引用键 = 陈述里【X】/「X」标记词 + 标记词的 2 字 CJK 子串（"周五陪你去"
    → 含"周五"——承诺里的时间/主题项也能对上）；无键 → 休眠。
    只查否定词 + 键共现——语义级矛盾留给二期（金标对账后收紧）。
    observe 模式容忍假阳性（先实测触发率，承 MANE G1 纪律）。
    """
    import re
    reply = str(ctx.get("reply") or "")
    if not any(neg in reply for neg in _NEGATIONS):
        return {"pass": True, "detail": {"reason": "no_negation"}}
    keys = set()
    for st in (ctx.get("ledger") or [])[:5]:
        # prompt 注入侧 Ledger.top_recent() 返回文本列表；检索/测试侧可能传完整
        # record。检查卡是只读 observe 层，必须兼容两种公开形状，不能因一条模型
        # 生成的台账文本把用户这轮回复中断。
        text = st.get("text") if isinstance(st, dict) else st
        for phrase in _extract_marked(str(text or "")):
            keys.add(phrase)
            if len(phrase) >= 2:
                for i in range(len(phrase) - 1):
                    seg = phrase[i:i + 2]
                    if re.fullmatch(r"[\u3400-\u4dbf\u4e00-\u9fff]{2}", seg):
                        keys.add(seg)
    if not keys:
        return {"pass": True, "detail": {"reason": "no_keys"}}
    hits = [k for k in keys if k in reply]
    return {"pass": not hits, "detail": {"hits": hits}}


# 卡目录（MANE CARD_DEFS 式：目录共享、语义本地化；v1 全部 on_fail = log）
CARD_DEFS = {
    "dedup": {
        "kind": "check",
        "when": None,
        "check_fn": check_dedup,
        "on_fail": {"action": "log", "instruction": ""},
    },
    "persona_anchor": {
        "kind": "check",
        "when": None,
        "check_fn": check_persona_anchor,
        "on_fail": {"action": "log", "instruction": ""},
    },
    "taboo": {
        "kind": "check",
        "when": None,
        "check_fn": check_taboo,
        "on_fail": {"action": "log", "instruction": ""},
    },
    "ledger_conflict": {
        "kind": "check",
        "when": None,
        "check_fn": check_ledger_conflict,
        "on_fail": {"action": "log", "instruction": ""},
    },
}

CARD_IDS = tuple(CARD_DEFS)
