"""已注入事件与回复之间的本地候选观察。

此模块只为人工审阅建立索引：不检索记忆、不调用模型、不参与奖励或参数更新。词面锚的缺失
不能说明模型没有使用事件，锚的出现也不能说明回复自然或用户喜欢。
"""
import hashlib


OBSERVATION_VERSION = 1
DEFAULT_NGRAM_WIDTH = 2
MAX_ANCHORS = 3


def _compact(text):
    """保留字母数字字符，避免空白/标点造成伪重合；适用于 CJK 与拉丁文本。"""
    return "".join(char.lower() for char in str(text or "") if char.isalnum())


def _anchors(left, right, width=DEFAULT_NGRAM_WIDTH):
    width = max(1, int(width))
    a, b = _compact(left), _compact(right)
    if len(a) < width or len(b) < width:
        return []
    shared = {a[index:index + width] for index in range(len(a) - width + 1)} & {
        b[index:index + width] for index in range(len(b) - width + 1)}
    return sorted(shared, key=lambda value: (-len(value), value))[:MAX_ANCHORS]


def _reply_quotes(reply, anchors):
    """候选锚在当前 reply 中的短片段；reply 本身已在消息流归档。"""
    source = str(reply or "")
    lower = source.lower()
    quotes = []
    for anchor in anchors:
        index = lower.find(anchor.lower())
        if index < 0:
            continue
        start, end = max(0, index - 16), min(len(source), index + len(anchor) + 24)
        quotes.append(source[start:end])
    return list(dict.fromkeys(quotes))


def observe_recall_expression(events, reply, *, ngram_width=DEFAULT_NGRAM_WIDTH):
    """返回每个**已注入**事件的可反驳词面观察，不输出质量分数。"""
    rows = []
    for event in events or ():
        event_id = str(getattr(event, "event_id", "") or "")
        text = str(getattr(event, "text", "") or "").strip()
        if not event_id or not text:
            continue
        anchors = _anchors(text, reply, ngram_width)
        rows.append({
            "event_id": event_id,
            "source": str(getattr(event, "source", "") or ""),
            "record_ts": int(getattr(event, "record_ts", 0) or 0),
            "event_text_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
            "kind": "lexical_candidate" if anchors else "no_lexical_signal",
            "anchors": anchors,
            "reply_quotes": _reply_quotes(reply, anchors),
        })
    return {"observer_version": OBSERVATION_VERSION,
            "ngram_width": max(1, int(ngram_width)),
            "events": rows,
            "non_judgment": "词面候选仅供审阅，不代表表达成功、相关或用户偏好"}
