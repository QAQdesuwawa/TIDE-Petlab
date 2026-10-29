# ops/ammo.py
"""坦白弹药账本 · 关键词抓取：常驻秘密被戳中时的多参数连锁（架构文档 §7.3 对称三档）。

秘密在创建时随角色卡/背景入账（常驻），每条带关键词表：
- 未曝光时：用户消息命中任意关键词 → 死穴被碰；
- 曝光后：不再重复触发（这是 NPC 被触及过的内部事件标记，不等于用户已拿到明牌）。
纯确定性函数，零 LLM。
关键词命中 = CJK 词边界语义（ops.lexmatch：单字边界词防误撞，2026-08 审计）。
对能在秘密原文中定位的多字关键词，额外保持局部肯否极性："很喜欢"不能被用户的
"不太喜欢"误撞；这只比较同一关键词附近的否定，不做用户心理或语义分类。
"""
import re

from ops.lexmatch import any_hit


_NEGATED_PREFIX = re.compile(r"(?:不|没|无|未|别)(?:太|很|再|怎么|那么)?$")
_AFFIRMING_MODIFIER = re.compile(r"(?:很|太|真|挺|好|非常|特别)$")


def _is_negated_at(text, start):
    """关键词起点前的短窗口是否为中文否定；只作字面局部判断。"""
    return bool(_NEGATED_PREFIX.search(str(text or "")[max(0, start - 5):start]))


def _polarity_aware_hit(keyword, text, secret):
    """对“很喜欢”式肯定偏好关键词保持局部肯否极性。

    否定并非永远表示反义：“别丢下我”反而精确命中“怕被丢下”。因此只有秘密
    原文在关键词前带肯定强度修饰（很/太/非常…）时，才把“不太喜欢”视作相反。
    其他秘密和自定义关键词保留既有 lexmatch 行为，避免把事件/恐惧类命中漏掉。
    """
    word = str(keyword or "").strip()
    source = str(secret or "")
    if len(word) < 2 or word not in source:
        return any_hit([word], text)
    source_polarities = set()
    guarded = False
    pos = source.find(word)
    while pos >= 0:
        source_polarities.add(_is_negated_at(source, pos))
        guarded = guarded or bool(_AFFIRMING_MODIFIER.search(source[max(0, pos - 5):pos]))
        pos = source.find(word, pos + len(word))
    if not guarded:
        return any_hit([word], text)
    # 同一秘密若同时含正反用法，则没有稳定可比较的目标极性，保留原匹配。
    if len(source_polarities) != 1:
        return any_hit([word], text)
    wanted = next(iter(source_polarities))
    target = str(text or "")
    pos = target.find(word)
    while pos >= 0:
        if _is_negated_at(target, pos) == wanted:
            return True
        pos = target.find(word, pos + len(word))
    return False


def scan_ammo(text, ammo):
    """扫描用户消息：返回命中的未曝光弹药条目列表。

    keywords 为空 → 该条目不参与自动抓取（无关键词的秘密只能靠提取 Agent 二期待补）。
    """
    t = text or ""
    hits = []
    for entry in ammo or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("exposed"):
            continue
        kws = entry.get("keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        if any(_polarity_aware_hit(keyword, t, entry.get("text")) for keyword in kws):
            hits.append(entry)
    return hits
