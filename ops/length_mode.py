# ops/length_mode.py
"""长文模式判定与历史观测（C/X/墙钟/已交付记忆 → 小作文概率）。

- p_essay = sigmoid(b + C·b_C + (C^T A_length)·(X-X_rest)
  + gap_term + delivered_memory_term)。已交付的记忆卡可连续增加材料密度；
  ``recent_S_max`` 只保留在本地历史观测中，不再越线触发写作模式；
- 历史观测 6:4（用于长度概率和本地审计，不直接进入 responder prompt）：
  最近 6 条原文（进行中的话题）+ 高权重历史事件 4 条；
  事件权重 = (0.6·S + 0.4·R) × e^(−龄/7天半衰期)——情感浓度、上下文变化量
  （R = 强度×相对个人基线的变化）、距离现在的时间。这里的 R 只作为被动的
  记忆显著性线索，不解释为正向体验，也不更新任何参数；三要素均来自落盘数据，
  零新测量、确定性可审计。
- 决策纯数理（seed 化 rng），一切按概率；小作文是 RAG/词元库的主用武之地。
"""
import math
import time

from ops.fixture_provenance import is_non_dialogue_seed

from core.params import LENGTH_MODE
from temporal.proactive import CONSTANT_ORDER, ROOT_ORDER, centered_root_vector, constant_vector


def _sigmoid(value):
    return 1.0 / (1.0 + math.exp(-max(-60.0, min(60.0, float(value)))))


def _dot(left, right):
    return sum(float(a) * float(b) for a, b in zip(left, right))


def _matrix_row(matrix, constants):
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CONSTANT_ORDER):
        raise ValueError("LENGTH_MODE.state_matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(ROOT_ORDER):
            raise ValueError("LENGTH_MODE.state_matrix 必须是 7×6")
        rows.append(tuple(float(value) for value in row))
    return tuple(sum(constants[i] * rows[i][j] for i in range(len(CONSTANT_ORDER)))
                 for j in range(len(ROOT_ORDER)))


def essay_readout(gap_hours, constants, variables=None, memory_delivery_count=0,
                 recent_S_max=None, params=None):
    """F17 的可回放读数；``recent_S_max`` 仅保留旧调用兼容，明确不参与公式。"""
    p = params or LENGTH_MODE
    del recent_S_max
    c = constant_vector(constants)
    dx = centered_root_vector(variables)
    interp = max(0.0, min(1.0, (gap_hours - p["gap_short_h"])
                            / (p["gap_long_h"] - p["gap_short_h"])))
    weights = _matrix_row(p["state_matrix"], c)
    state_terms = tuple(weight * value for weight, value in zip(weights, dx))
    n_memory = max(0, int(memory_delivery_count or 0))
    memory_term = float(p["memory_delivery_gain"]) * (1.0 - math.exp(-n_memory))
    gap_term = float(p["gap_logit_gain"]) * (2.0 * interp - 1.0)
    constant_term = _dot(c, tuple(float(value) for value in p["constant_bias"]))
    logit = float(p["logit_bias"]) + constant_term + _dot(weights, dx) + gap_term + memory_term
    probability = min(float(p["p_essay_cap"]), _sigmoid(logit))
    return {
        "version": str(p.get("version") or "length-root-vector-v2"),
        "probability": round(max(0.0, probability), 6),
        "gap_interp": round(interp, 6),
        "gap_term": round(gap_term, 6),
        "memory_delivery_count": n_memory,
        "memory_delivery_term": round(memory_term, 6),
        "constant_term": round(constant_term, 6),
        "state_terms": dict(zip(ROOT_ORDER, (round(value, 6) for value in state_terms))),
        "root_delta": dict(zip(ROOT_ORDER, (round(value, 6) for value in dx))),
        "weights": dict(zip(ROOT_ORDER, (round(value, 6) for value in weights))),
        "logit": round(logit, 6),
    }


def essay_probability(gap_hours, constants, recent_S_max=None, *, variables=None,
                      memory_delivery_count=0, params=None):
    """兼容标量入口；真正公式见 :func:`essay_readout`。"""
    return essay_readout(gap_hours, constants, variables,
                         memory_delivery_count, recent_S_max, params)["probability"]


def length_mode(gap_hours, constants, rng, recent_S_max=None, *, variables=None,
                memory_delivery_count=0, params=None):
    """→ "essay" / "short"（seed 化随机，回放一致）。"""
    if rng.random() < essay_probability(
            gap_hours, constants, recent_S_max=recent_S_max, variables=variables,
            memory_delivery_count=memory_delivery_count, params=params):
        return "essay"
    return "short"


def past_event_weight(rec, now_ms, params=None):
    """历史事件显著性：w = (w_s·S + w_r·R) × e^(−龄/半衰期)，不作价值判断。"""
    p = params or LENGTH_MODE
    S = float(rec.get("S", 0.0) or 0.0)
    R = float(rec.get("R", 0.0) or 0.0)
    age_days = max(0.0, (now_ms - float(rec.get("ts", now_ms))) / 86_400_000.0)
    return (p["past_w_s"] * S + p["past_w_r"] * R) \
        * math.exp(-age_days / p["past_half_life_days"])


def memory_supplement(store, now_ms=None, params=None):
    """→ {"recent": [...], "past": [...], "recent_S_max": float}。

    最近 6 条原文（时间序）+ 高权重历史事件 4 条（权重序，排除最近 6 条）。原文
    只供本地观测；普通与长文回复的跨回合事实均须通过 turn_recall 事件卡进入 prompt。
    """
    p = params or LENGTH_MODE
    now = int(now_ms if now_ms is not None else time.time() * 1000)
    user_msgs = [m for m in store.read_messages()
                 if m.get("role") == "user" and not is_non_dialogue_seed(m)]
    recent = [str(m.get("text", "")).strip()[:p["memory_supplement_cut"]]
              for m in user_msgs[-p["supplement_recent_n"]:]
              if str(m.get("text", "")).strip()]
    recent_S_max = max([float(m.get("S", 0.0) or 0.0)
                        for m in user_msgs[-p["supplement_recent_n"]:]]
                       or [0.0])
    past_pool = [m for m in user_msgs[:-p["supplement_recent_n"]]
                 if str(m.get("text", "")).strip()]
    weighted = sorted(
        [(past_event_weight(m, now, p), m) for m in past_pool],
        key=lambda x: -x[0])
    past = [str(m.get("text", "")).strip()[:p["memory_supplement_cut"]]
            for w, m in weighted[:p["supplement_past_n"]] if w > 0]
    return {"recent": recent, "past": past, "recent_S_max": round(recent_S_max, 4)}
