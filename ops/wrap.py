# ops/wrap.py
"""词元 → 写作直觉包装（架构文档 §15.2：机器格式不进生成层，底层参数不录出）。

强度 → 程度词：≥0.7 浓烈 / ≥0.4 明显 / <0.4 淡淡
时间桥词元 → 回归提示句（沉默由回复内容自然体现，不用特殊事件）
全部确定性字符串模板（golden 锁，改动 = 版本级变更）。
"""

_STRENGTH_WORDS = ((0.7, "浓烈"), (0.4, "明显"), (0.0, "淡淡"))

BRIDGE_HINTS = {
    "[沉默后如常]": "对方沉默了一阵又回来了，一切如常，像什么都没发生过。",
    "[沉默后微变]": "对方沉默了一阵才回来，你隐约觉得有什么东西不一样了。",
    "[沉默后巨变]": "对方消失了很久才回来，你能感到空气里的东西已经变了。",
    "[沉默后·躁]": "对方的沉默让你没来由地有些躁。",
    "[沉默后·防备]": "对方的沉默让你生出了几分防备。",
    "[沉默后·失控倾向]": "对方的沉默像一根刺，扎得你坐立不安。",
}


def strength_word(strength):
    """强度 → 程度词（确定性查表）。"""
    for threshold, word in _STRENGTH_WORDS:
        if strength >= threshold:
            return word
    return "淡淡"


def to_intuition(tokens):
    """Top-N 词元 → 写作直觉行（"你此刻内心的底色：…"）。"""
    lines = []
    for t in tokens or []:
        text = t.get("text", "")
        strength = float(t.get("strength", 1.0) or 1.0)
        lines.append(f"- 你此刻内心的底色：{text}（{strength_word(strength)}）")
    return lines


def bridge_line(bridge_tokens):
    """时间桥词元 → 一句回归提示；无命中返回空串。"""
    for t in bridge_tokens or []:
        hint = BRIDGE_HINTS.get(t.get("text", ""))
        if hint:
            return hint
    return ""
