# ops/budget.py
"""预算硬闸门 + 装配降级（承 MANE ops/budget.py 思路，聊天版）。

softmax 对抗的硬形态：每轮同步 prompt 有字符预算上限——
超限时从最低优先级块开始裁，核心区（纪律/身份/直觉）永不被裁。
估算 = 字符数（4 字/token 先验启发式，零 LLM）。
预算上限收编进 core.params.DEFAULTS（2026-08 审计：散落常量进标定区）。
"""
from core.params import DEFAULTS

PROMPT_BUDGET_CHARS = DEFAULTS["prompt_budget_chars"]   # 标定区唯一来源


def estimate_chars(*texts):
    """零 LLM 字符估算（多段求和）。"""
    return sum(len(t or "") for t in texts)


def fit_entries(entries, core_chars, max_chars=None):
    """entries = [(priority, text)] 已按优先级降序 → 保留在预算内的文本列表。

    核心区（core_chars）不参与裁减，只算占用；块按优先级从低到高丢弃。
    """
    cap = max_chars if max_chars is not None else PROMPT_BUDGET_CHARS
    used = core_chars
    kept = []
    for _prio, text in entries:
        cost = len(text or "") + 3
        if used + cost > cap:
            continue
        kept.append(text)
        used += cost
    return kept
