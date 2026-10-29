# ops/user_line.py
"""用户线观察（双线架构 T7-3 · 运行时半）：沉默期流失观察——先观测后干预。

判定全数理、零 LLM；纯函数 + 事件 append-only（events.jsonl kind="user_gap"）。
语义：用户沉默时长跨入新分带（30min/2h/12h/24h）→ 追加一条观测事件；
用户回消息 → 观测带位清零。只观测不干预——调动失败/流失的运行时观测面，
与事后回算 `tools/report_churn.py` 互补（一个实时、一个批量）。
"""
from core.params import USER_LINE


def band_index(gap_min, params=None):
    """gap（分钟）→ 所在带位：-1 = 低于第一带；i = 跨入第 i 带。"""
    p = params or USER_LINE
    idx = -1
    for i, th in enumerate(p["gap_bands_min"]):
        if gap_min >= th:
            idx = i
        else:
            break
    return idx


def band_label(idx, params=None):
    p = params or USER_LINE
    labels = p["band_labels"]
    if idx < 0 or idx >= len(labels):
        return None
    return labels[idx]


def observe(last_band_idx, gap_min, now_ms, params=None):
    """→ (新带位, [观测事件])：每跨入一个新带产一条事件（升级轨迹完整）。"""
    p = params or USER_LINE
    idx = band_index(gap_min, p)
    if idx < 0:
        return -1, []                     # 未入带（或用户刚回）→ 无观测
    prev = last_band_idx if last_band_idx is not None else -1
    start = max(0, int(prev) + 1)
    if idx < start:
        return idx, []                    # 同带/回落：不重复记账
    events = []
    for i in range(start, idx + 1):
        label = band_label(i, p)
        events.append({"ts": int(now_ms), "kind": "user_gap",
                       "detail": {"band": label,
                                  "gap_min": round(float(gap_min), 1)}})
    return idx, events
