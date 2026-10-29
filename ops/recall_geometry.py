# ops/recall_geometry.py
"""词元几何召回判定观察卡（记忆与反应向量化提案 §三 · M2）。

MANE CARD_DEFS 纪律：本卡有自己独立的算法与语义（家族几何 + drift 对位），
目录共享、语义不共享。它只消费词元流与归档行，产出**只读观测**：

- ``fam_cos``  ：cos(当前状态词元方向, 事件词元方向) = 情绪同向度
  （家族 δ 几何；方向来自冻结区 lexicon ``match_rule`` 的四轴 δ）
- ``drift_dot``：dot(D_now, D_ev) = 场方向一致度
  （归档 ``drift`` 读回——此前检索层从不读取的死向量）

M2 等价锁：本卡**不接选择器**。它不改变候选排序、不消耗随机数、不写唤起
账本；只追加一条 ``recall_geometry`` 审计行（不含任何历史文本）。压制、证实
等消费是 M4/M5 的事，届时仍走"先观察、后人工确认"纪律。

方向空间：AXES 四轴（E_env/P_need/V_attr/V_rep）。本卡内部的 cos/dot 是卡内
投影，不写共享状态（承提案 §三：若某算法内部需要投影，它在自己内部投影）。

drift 读回口径（诚实标注缺失，不硬造）：
- ``drift.dir``（M2 起 runner 归档的场方向，四轴）优先；
- 否则 ``drift`` 顶层四轴键（历史夹具/合成种子形状，如 ``{"E_env": 0.3}``）；
- 都无（如旧生产行 ``{"P","E"}``）→ 测度 = None，观察行如实留空。
"""
import math

from core.params import RECALL_GEOMETRY
from temporal.lexicon import AXES, match_rule

KIND = "recall_geometry"


def _token_text(token):
    if isinstance(token, dict):
        return token.get("text", "")
    return str(token or "")


def _strength(token):
    """词元权重：与 compile_field 同款 w = max(weight_floor, strength)。"""
    if not isinstance(token, dict):
        return 1.0
    try:
        strength = float(token.get("strength", 1.0))
    except (TypeError, ValueError):
        strength = 1.0
    if strength <= 0:
        return 0.0
    return max(float(RECALL_GEOMETRY["token_weight_floor"]), strength)


def token_direction(tokens):
    """词元 → 四轴家族方向（强度加权平均 δ）。

    只计家族命中的词元（family != none）；全部无命中 → None（不硬造方向）。
    family 命中的零 δ 词元（如疲惫族）计入权重但方向贡献为零——方向为零
    与"无方向"不同，前者如实返回全零向量。
    """
    acc = {ax: 0.0 for ax in AXES}
    total_w = 0.0
    for token in tokens or []:
        rule = match_rule({"text": _token_text(token)})
        if rule["family"] == "none":
            continue
        w = _strength(token)
        if w <= 0:
            continue
        for ax in AXES:
            acc[ax] += w * float(rule["direction"].get(ax, 0.0))
        total_w += w
    if total_w <= 0:
        return None
    return {ax: round(acc[ax] / total_w, 4) for ax in AXES}


def axis_drift(record):
    """归档行 → 四轴 drift（读回；无四轴数据 → None）。"""
    drift = (record or {}).get("drift")
    if not isinstance(drift, dict):
        return None
    nested = drift.get("dir")
    source = nested if (isinstance(nested, dict)
                        and any(ax in nested for ax in AXES)) else drift
    if not any(ax in source for ax in AXES):
        return None
    out = {}
    for ax in AXES:
        try:
            out[ax] = float(source.get(ax, 0.0))
        except (TypeError, ValueError):
            out[ax] = 0.0
    return out


def _cos(a, b):
    """四轴余弦（卡内投影；零模长 → 0.0，承 ops/memory.cosine 防护约定）。"""
    return directional_cosine(a, b)


def directional_cosine(a, b):
    """四轴方向余弦（公共：家族几何与否认压制共用同一投影语义）。"""
    dot = sum(a[ax] * b[ax] for ax in AXES)
    na = math.sqrt(sum(v * v for v in a.values()))
    nb = math.sqrt(sum(v * v for v in b.values()))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return dot / (na * nb)


def _dot(a, b):
    """四轴点积（带模长：幅度 = 方向一致的证据量）。"""
    return sum(a[ax] * b[ax] for ax in AXES)


def family_alignment(dir_now, record):
    """cos(当前状态词元方向, 事件词元方向)；任一侧无家族方向 → None。"""
    if not dir_now:
        return None
    dir_ev = token_direction((record or {}).get("tokens") or [])
    if not dir_ev:
        return None
    return round(_cos(dir_now, dir_ev), 4)


def drift_alignment(dir_now, record):
    """dot(D_now, D_ev)：当前场方向与事件归档 drift 的一致度；无四轴 drift → None。"""
    if not dir_now:
        return None
    d_ev = axis_drift(record)
    if d_ev is None:
        return None
    return round(_dot(dir_now, d_ev), 4)


def geometry_observations(dir_now, candidates):
    """候选 → 公开观测行（不含正文与内部记录；双测度皆空的行不产出）。"""
    rows = []
    for candidate in candidates or []:
        if not isinstance(candidate, dict):
            continue
        record = candidate.get("_record")
        if not isinstance(record, dict):
            record = candidate
        fam = family_alignment(dir_now, record)
        drift = drift_alignment(dir_now, record)
        if fam is None and drift is None:
            continue
        rows.append({
            "source": str(candidate.get("source", "")),
            "record_ts": int(record.get("ts") or 0),
            "fam_cos": fam,
            "drift_dot": drift,
        })
    return rows


def geometry_event(*, ts, turn_id, strategy, dir_now, observations):
    """审计行：只含观测数值，绝不复制历史文本（承 recall_shadow 纪律）。"""
    event = {"ts": int(ts), "kind": KIND, "strategy": str(strategy),
             "direction": dir_now, "observations": list(observations)}
    if turn_id is not None:
        event["turn_id"] = str(turn_id)
    return event
