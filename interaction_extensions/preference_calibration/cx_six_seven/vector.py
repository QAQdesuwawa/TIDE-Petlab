"""把已关闭 action 观察编译为可回放的局部向量样本。

这是反向标定的“尺子”，不是第二套决策器：
- 只消费 append-only 的 ``calibration_observed``；
- 向量分成状态、时间、记忆交付、表达形式四个局部族，拒绝拼成万能心理分数；
- timeout、字数、回复延迟都是事实，不被翻译成用户喜欢／不喜欢；
- 不产生 ΔW，不写参数，也不参与线上调度。

后续冻结评审或盲评可在同一 sample 上附加外部目标，再选择性地对某一 W 行提出
候选更新。没有目标时，本模块只导出样本和完整性报告。
"""
from __future__ import annotations

import hashlib

from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import PROTOTYPES


SCHEMA_VERSION = 1
TIME_ROWS = ("f14_impulse", "f14_gate", "f15_impulse", "f15_gate",
             "f17_length")
FORM_ROWS = ("expansion", "fragmentation", "open_ended")
RECALL_ROWS = ("propensity_base", "propensity_salience_weight",
               "propensity_relevance_weight", "propensity_exposure_weight")
FORM_FACTS = ("chars", "segments", "line_breaks", "question_marks", "ellipsis_marks")
RECALL_SELECTION_FACTS = ("available", "candidate_count", "selected_count", "rejected_count",
                          "max_relevance", "max_salience", "max_propensity", "max_utility")


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _unit(value):
    return max(0.0, min(1.0, _number(value)))


def _bounded(value):
    return max(-1.0, min(1.0, _number(value)))


def _weights(raw):
    values = {key: max(0.0, _number((raw or {}).get(key))) for key in PROTOTYPES}
    total = sum(values.values())
    if total <= 0.0:
        return {key: round(1.0 / len(PROTOTYPES), 6) for key in PROTOTYPES}
    return {key: round(value / total, 6) for key, value in values.items()}


def calibration_sample_key(row):
    """本地 action revision 的不可读引用键；不是输出中的原 action ID。"""
    material = "|".join((
        str(row.get("action_id") or ""),
        str(row.get("outcome_revision") or 0),
        str(row.get("params_sha") or ""),
    ))
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def vector_schema():
    """稳定维度约定；分类事实保留在 metadata，绝不伪造欧氏距离。"""
    return {
        "state": tuple(["B0." + key for key in PROTOTYPES] +
                       ["D." + key for key in PROTOTYPES] + ["P", "unanswered_n"]),
        "time_shadow": tuple(TIME_ROWS),
        "recall_delivery": ("delivered_count", "recalled_count", "suppressed_count"),
        "recall_selection": tuple(RECALL_SELECTION_FACTS),
        "recall_shadow": tuple(RECALL_ROWS),
        "form_shadow": tuple(FORM_ROWS),
        "output_form": tuple(FORM_FACTS),
        "outcome": ("answered", "delay_ms", "next_R", "npc_actions_before_response"),
    }


def calibration_vector_sample(observation):
    """单条 ``calibration_observed`` → 无正文、分族向量样本。

    返回 ``None`` 表示记录不属于本合同或缺少 action snapshot；调用方不应猜测补值。
    ``answered`` / ``next_R`` 的 None 特意保留为缺失值，不能把尚未可判定的自然时间
    事实压成零。
    """
    row = observation if isinstance(observation, dict) else {}
    if row.get("kind") != "calibration_observed":
        return None
    snap = row.get("action_snapshot") if isinstance(row.get("action_snapshot"), dict) else None
    if not snap:
        return None
    state = snap.get("state") if isinstance(snap.get("state"), dict) else {}
    cadence = snap.get("cadence") if isinstance(snap.get("cadence"), dict) else {}
    controls = cadence.get("control_coefficients") if isinstance(cadence.get("control_coefficients"), dict) else {}
    shadow = controls.get("shadow") if isinstance(controls.get("shadow"), dict) else {}
    assembly = snap.get("prompt_assembly") if isinstance(snap.get("prompt_assembly"), dict) else {}
    recall = assembly.get("recall") if isinstance(assembly.get("recall"), dict) else {}
    recall_selection = recall.get("selection") if isinstance(recall.get("selection"), dict) else {}
    recall_projection = (assembly.get("recall_projection")
                         if isinstance(assembly.get("recall_projection"), dict) else {})
    recall_shadow = (recall_projection.get("shadow")
                     if isinstance(recall_projection.get("shadow"), dict) else {})
    suppressed = recall.get("suppressed_by_reason")
    suppressed_count = sum(max(0.0, _number(value)) for value in suppressed.values()) \
        if isinstance(suppressed, dict) else 0.0
    form_plan = assembly.get("form") if isinstance(assembly.get("form"), dict) else {}
    mappings = snap.get("mappings") if isinstance(snap.get("mappings"), dict) else {}
    form_shadow = form_plan.get("shadow") if isinstance(form_plan.get("shadow"), dict) else {}
    output_form = snap.get("output_form") if isinstance(snap.get("output_form"), dict) else {}
    result = row.get("observation") if isinstance(row.get("observation"), dict) else {}
    answered = result.get("answered")
    if answered not in (True, False, None):
        answered = None
    next_r = result.get("next_R")
    next_r = None if next_r is None else round(_unit(next_r), 6)
    delay = result.get("delay_ms")
    delay = None if delay is None else max(0, int(_number(delay)))

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "calibration_vector_sample",
        "sample_key": calibration_sample_key(row),
        "source": str(row.get("source") or "reply"),
        "outcome_status": str(row.get("outcome_status") or "unknown"),
        "params_sha": str(row.get("params_sha") or ""),
        "state": {
            "B0": _weights(state.get("B0")),
            "D": _weights(state.get("D")),
            "P": round(_unit(state.get("P")), 6),
            "unanswered_n": max(0, int(_number(state.get("unanswered_n")))),
        },
        "time_shadow": {key: round(_bounded(shadow.get(key)), 6) for key in TIME_ROWS},
        "recall_delivery": {
            "delivered_count": max(0, int(_number(snap.get("recall_delivery", {}).get("delivered_count")))),
            "recalled_count": max(0, int(_number(recall.get("recalled_count")))),
            "suppressed_count": round(suppressed_count, 6),
        },
        "recall_selection": {
            "available": int(bool(recall_selection.get("available", False))),
            "candidate_count": max(0, int(_number(recall_selection.get("candidate_count")))),
            "selected_count": max(0, int(_number(recall_selection.get("selected_count")))),
            "rejected_count": max(0, int(_number(recall_selection.get("rejected_count")))),
            "max_relevance": _unit((recall_selection.get("score_max") or {}).get("relevance")),
            "max_salience": _unit((recall_selection.get("score_max") or {}).get("salience")),
            "max_propensity": _unit((recall_selection.get("score_max") or {}).get("propensity")),
            "max_utility": _unit((recall_selection.get("score_max") or {}).get("utility")),
        },
        "recall_shadow": {key: round(_bounded(recall_shadow.get(key)), 6) for key in RECALL_ROWS},
        "form_shadow": {key: round(_bounded(form_shadow.get(key)), 6) for key in FORM_ROWS},
        # 这是 action 当时正式 form C/X/e gate 的冻结只读读数；反向标定不得再从
        # P 或未回应账本临时重建它。
        "form_activity": _unit(form_plan.get("activity_gate", 0.0)),
        "output_form": {key: max(0, int(_number(output_form.get(key)))) for key in FORM_FACTS},
        "outcome": {
            "answered": None if answered is None else int(answered),
            "delay_ms": delay,
            "next_R": next_r,
            "npc_actions_before_response": max(0, int(_number(
                result.get("npc_actions_before_response")))),
        },
        "metadata": {
            "cadence_version": str(cadence.get("version") or "unknown"),
            "form_active": bool(form_plan.get("active", False)),
            "reference_scope": str((assembly.get("reference") or {}).get("delivery_scope") or "none"),
            "length_mode": str(assembly.get("length_mode") or "unknown"),
        },
        # 这是 action 当时冻结的局部 W 行，不是当前全局 params 的引用。
        "mappings": mappings,
    }


def compile_calibration_samples(events):
    """append-only event 流 → 样本；保留同一 action 的每个 outcome revision。"""
    samples = []
    for event in events or ():
        sample = calibration_vector_sample(event)
        if sample is not None:
            samples.append(sample)
    return samples


def calibration_sample_report(events):
    """冻结回放的完整性报告，不构造目标函数或 ΔW。"""
    samples = compile_calibration_samples(events)
    by_status, missing = {}, 0
    for sample in samples:
        status = sample["outcome_status"]
        by_status[status] = by_status.get(status, 0) + 1
        if sample["outcome"]["answered"] is None:
            missing += 1
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "calibration_sample_report",
        "sample_count": len(samples),
        "by_outcome_status": dict(sorted(by_status.items())),
        "missing_natural_outcome_count": missing,
        "ready_for_delta_w": False,
        "reason": "需要外部评审目标或冻结实验目标；自然结果本身不等于偏好标签",
    }
