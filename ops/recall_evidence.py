"""重复提及的事件级召回证据：纯函数与 append-only 记录契约。

本模块不检索正文、不调用模型、不写 prompt，也不修改墙钟或现有召回结果。它只将一次
已经由上游相似度组件给出的无文本探测分数，累计为某个既有事件的重复提及证据。运行时
接线前可独立回放，避免把“第二次更可能想起”写成不可审计的隐式状态。
"""
from __future__ import annotations

import math

from core.params import RECALL_EVIDENCE


SCHEMA_VERSION = 1
KIND = "recall_evidence"
_ALLOWED_KEYS = frozenset({
    "schema_version", "kind", "ts", "turn_id", "candidate", "probe_score", "stage",
    "policy_version", "evidence_before", "evidence_after",
})


def _number(value, name, *, minimum=0.0, maximum=1.0):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(name + " 必须是数值")
    number = float(value)
    if not minimum <= number <= maximum:
        raise ValueError(name + " 超出允许范围")
    return number


def candidate_ref(source, record_ts):
    """候选的稳定、无正文身份。"""
    source = str(source or "").strip()
    if not source:
        raise ValueError("candidate.source 不能为空")
    if isinstance(record_ts, bool) or not isinstance(record_ts, int):
        raise ValueError("candidate.record_ts 必须是整数")
    return {"source": source, "record_ts": int(record_ts)}


def build_evidence_record(*, ts, turn_id, source, record_ts, probe_score,
                          stage="weak_probe", policy_version=None,
                          evidence_before=None, evidence_after=None):
    """构造可持久化的无正文证据行。

    ``probe_score`` 只描述候选与当前输入的通用相似度，不能由情绪奖励、用户偏好或
    模型自报替代。``stage`` 目前仅用于审计；尚不驱动产品行为。
    """
    if isinstance(ts, bool) or not isinstance(ts, int):
        raise ValueError("ts 必须是整数")
    if stage not in {"weak_probe", "strong_probe"}:
        raise ValueError("stage 无效")
    record = {
        "schema_version": SCHEMA_VERSION, "kind": KIND, "ts": int(ts),
        "candidate": candidate_ref(source, record_ts),
        "probe_score": round(_number(probe_score, "probe_score"), 6),
        "stage": stage,
        "policy_version": str(policy_version or RECALL_EVIDENCE["version"]),
    }
    if turn_id is not None:
        record["turn_id"] = str(turn_id)
    if evidence_before is not None:
        record["evidence_before"] = round(_number(evidence_before, "evidence_before"), 6)
    if evidence_after is not None:
        record["evidence_after"] = round(_number(evidence_after, "evidence_after"), 6)
    return record


def validate_evidence_record(record):
    """验证读取到的证据行；拒绝正文与未来版本，防止审计流成为旁路记忆库。"""
    if not isinstance(record, dict) or record.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("不支持的 recall evidence 记录")
    if record.get("kind") != KIND or not set(record) <= _ALLOWED_KEYS:
        raise ValueError("recall evidence 记录无效或含正文")
    ref = record.get("candidate")
    if not isinstance(ref, dict):
        raise ValueError("candidate 缺失")
    candidate_ref(ref.get("source"), ref.get("record_ts"))
    build_evidence_record(
        ts=record.get("ts"), turn_id=record.get("turn_id"),
        source=ref.get("source"), record_ts=ref.get("record_ts"),
        probe_score=record.get("probe_score"), stage=record.get("stage"),
        policy_version=record.get("policy_version"),
        evidence_before=record.get("evidence_before"),
        evidence_after=record.get("evidence_after"))
    return True


def _matching_records(records, *, source, record_ts, now_ms):
    ref = candidate_ref(source, record_ts)
    selected = []
    for record in records or ():
        try:
            validate_evidence_record(record)
        except ValueError:
            continue
        candidate = record["candidate"]
        if candidate == ref and int(record["ts"]) <= int(now_ms):
            selected.append(record)
    return selected


def evidence_snapshot(records, *, source, record_ts, now_ms, params=None):
    """重建某候选截至 ``now_ms`` 的衰减累计，返回无文本观测值。"""
    p = params or RECALL_EVIDENCE
    decay_hours = float(p["decay_hours"])
    if decay_hours <= 0:
        raise ValueError("decay_hours 必须大于零")
    scale = float(p["saturation_mass"])
    if scale <= 0:
        raise ValueError("saturation_mass 必须大于零")
    rows = _matching_records(records, source=source, record_ts=record_ts, now_ms=now_ms)
    # 一个用户回合最多计最高一条探测，避免同一输入因多种表征被重复放大。
    per_turn = {}
    for record in rows:
        key = record.get("turn_id") or "ts:%d" % int(record["ts"])
        age_hours = max(0.0, (int(now_ms) - int(record["ts"])) / 3_600_000.0)
        weighted = float(record["probe_score"]) * math.exp(-age_hours / decay_hours)
        per_turn[key] = max(per_turn.get(key, 0.0), weighted)
    mass = sum(per_turn.values())
    exposure = 1.0 - math.exp(-mass / scale)
    active_min = float(p.get("active_turn_weight_min", 0.0))
    if active_min < 0:
        raise ValueError("active_turn_weight_min 不能为负")
    # 质量衰减与回合计数使用同一权重；否则一次远古的微小尾迹会永久满足
    # min_distinct_turns，令一次新的高相似探测过早资格化。
    active_turns = sum(weight >= active_min for weight in per_turn.values())
    return {"candidate": candidate_ref(source, record_ts), "mass": round(mass, 6),
            "exposure": round(exposure, 6), "distinct_turns": active_turns}


def observe_evidence(records, *, ts, turn_id, source, record_ts, probe_score,
                     stage="weak_probe", params=None):
    """为一个探测生成审计行；调用方把它 append 后才成为下一轮的历史证据。"""
    p = params or RECALL_EVIDENCE
    before = evidence_snapshot(records, source=source, record_ts=record_ts,
                               now_ms=ts, params=p)
    provisional = build_evidence_record(
        ts=ts, turn_id=turn_id, source=source, record_ts=record_ts,
        probe_score=probe_score, stage=stage, policy_version=p["version"])
    after = evidence_snapshot(list(records or ()) + [provisional], source=source,
                              record_ts=record_ts, now_ms=ts, params=p)
    return build_evidence_record(
        ts=ts, turn_id=turn_id, source=source, record_ts=record_ts,
        probe_score=probe_score, stage=stage, policy_version=p["version"],
        evidence_before=before["exposure"], evidence_after=after["exposure"])


def weak_text_probe(query_text, candidate_text):
    """基于字符集合与二元组的通用弱相关探测，供 shadow-only 阶段使用。

    它不声称两个文本语义相同，也不直接赋予召回资格。要求至少一个连续二元组与两个
    共同字符，避免只有单一常驻字符时形成事件证据。分数只用于重复累计，正文不落盘。
    """
    query = "".join(str(query_text or "").split())
    candidate = "".join(str(candidate_text or "").split())
    if len(query) < 2 or len(candidate) < 2:
        return 0.0
    query_bigrams = {query[index:index + 2] for index in range(len(query) - 1)}
    candidate_bigrams = {candidate[index:index + 2]
                          for index in range(len(candidate) - 1)}
    common_bigrams = query_bigrams & candidate_bigrams
    common_chars = set(query) & set(candidate)
    if not common_bigrams or len(common_chars) < 2:
        return 0.0
    bigram_part = min(1.0, len(common_bigrams) / 2.0)
    char_part = len(common_chars) / max(1, min(len(set(query)), len(set(candidate))))
    return round(min(1.0, 0.65 * bigram_part + 0.35 * char_part), 6)


def qualifies_for_recall(*, strong_relevance, weak_relevance, snapshot, params=None):
    """资格门：强相关直接通过；弱相关必须有近期、跨回合的累计证据。"""
    p = params or RECALL_EVIDENCE
    strong = _number(strong_relevance, "strong_relevance")
    weak = _number(weak_relevance, "weak_relevance")
    if strong >= float(p["strong_relevance_min"]):
        return {"eligible": True, "reason": "strong_relevance"}
    if (weak >= float(p["weak_probe_min"])
            and float(snapshot.get("exposure", 0.0)) >= float(p["eligibility_exposure_min"])
            and int(snapshot.get("distinct_turns", 0)) >= int(p["min_distinct_turns"])):
        return {"eligible": True, "reason": "repeated_weak_evidence"}
    return {"eligible": False, "reason": "insufficient_evidence"}


def recall_propensity(*, salience, relevance, exposure, params=None):
    """计算未来 unified_evidence_v2 的概率倾向；本函数不抽样、不提交召回。"""
    p = params or RECALL_EVIDENCE
    value = (float(p["propensity_base"])
             + float(p["propensity_salience_weight"]) * _number(salience, "salience")
             + float(p["propensity_relevance_weight"]) * _number(relevance, "relevance")
             + float(p["propensity_exposure_weight"]) * _number(exposure, "exposure"))
    return round(max(float(p["propensity_min"]),
                     min(float(p["propensity_max"]), value)), 6)
