"""当前输入的召回查询词元装配。

时间场 Top-3 是墙钟消费者，不能反过来裁掉已确认的当前语义锚。这里仅把本轮
`semantic_projector` 已给出的显式词元并入 recall 查询；它不读取资源全账本、不改变
时间场/表达候选，也不建立另一套 RAG 排序。
"""


def assemble_recall_query_tokens(field_tokens, semantic_tokens=()):
    """保留场词元顺序，追加未重复的当前语义锚；相同锚只提高既有强度。"""
    rows = []
    index = {}
    for token in list(field_tokens or ()) + list(semantic_tokens or ()):
        if not isinstance(token, dict):
            continue
        text = str(token.get("text") or "").strip()
        if not text:
            continue
        item = dict(token)
        item["text"] = text
        try:
            item["strength"] = float(item.get("strength", 1.0) or 1.0)
        except (TypeError, ValueError):
            item["strength"] = 1.0
        if text not in index:
            index[text] = len(rows)
            rows.append(item)
        else:
            prior = rows[index[text]]
            prior["strength"] = max(float(prior.get("strength", 0.0)), item["strength"])
    return rows
