# ops/anti_loop.py
"""防打转（承 MANE G1 打转检查卡 + 4-gram 围栏）：相似度检测 + 重写提示。

MANE 实证：模型会在相邻回合产出高相似度的台词（打转），也会用同一套僵硬句式
反复回话。TIDE 版：新回复与最近 k 条 NPC 台词做 4-gram 重合率比较，超阈值 →
带"换一种说法"指令重试 ≤1 次（承 MANE"重渲染 ≤2 轮防振荡"）。
决策全数理（ngram 计数），零 LLM。
"""
from core.params import ANTI_LOOP


def ngrams(text, n):
    text = "".join(ch for ch in text or "" if not ch.isspace())
    if len(text) < n:
        return set()
    return {text[i:i + n] for i in range(len(text) - n + 1)}


def ngram_ratio(a, b, n=None):
    """a 对 b 的 n-gram 重合率 = |ng(a) ∩ ng(b)| / |ng(a)|（防零分母）。"""
    n = n or ANTI_LOOP["n"]
    ga, gb = ngrams(a, n), ngrams(b, n)
    if not ga:
        return 0.0
    return round(len(ga & gb) / len(ga), 4)


def recent_overlap(text, history, k=None, n=None):
    """新回复 vs 最近 k 条 NPC 台词的 n-gram 重合率最大值。"""
    k = k or ANTI_LOOP["k"]
    n = n or ANTI_LOOP["n"]
    return max([ngram_ratio(text, h, n) for h in history[-k:]] or [0.0])


def stiff_hit(text):
    """刻板台词黑名单命中 → 命中短语列表（空 = 干净）。确定性纯函数。"""
    return [p for p in ANTI_LOOP["stiff_phrases"] if p in (text or "")]


def retry_note_for(text, overlap):
    """按触发原因生成重写指令：打转 → 换说法；刻板台词 → 删掉换行动表达。"""
    hits = stiff_hit(text)
    if hits:
        return (f"（检查卡：你的回复里出现了「{hits[0]}」这类自我标注的刻板台词——"
                f"删掉它，用行动、语气让对方感觉到，而不是自己说出口。保持你的口癖。）")
    return ("（防打转检查：你上一版回复和最近说过的话重合度太高——"
            "务必换一种说法、换一个角度，保持你的口癖与语气，不要复读自己。）")


RETRY_NOTE = retry_note_for("", 0.0)
