"""反向标定的已发送动作快照（纯函数、零行为）。

它把已经存在的墙钟状态、五维 D(t)、已交付召回和真实输出形式压缩为无正文 receipt。
这里不判断用户是否喜欢、不决定下一条消息，也不修改任何参数；它只是让后续的影子
回放能把一个结果窗口精确连回动作当时真正生效的连续状态。
"""
from __future__ import annotations

import re

from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import PROTOTYPES


_FORM_SPLIT = re.compile(r"[。！？!?；;\n]+")


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _unit(value):
    return max(0.0, min(1.0, _number(value)))


def _weights(raw):
    values = {key: max(0.0, _number((raw or {}).get(key, 0.0)))
              for key in PROTOTYPES}
    total = sum(values.values())
    if total <= 0.0:
        return {key: round(1.0 / len(PROTOTYPES), 6) for key in PROTOTYPES}
    return {key: round(value / total, 6) for key, value in values.items()}


def output_form_snapshot(text):
    """只从已发送文本读取形式事实，不做情绪或语义猜测。"""
    body = str(text or "")
    segments = [part.strip() for part in _FORM_SPLIT.split(body) if part.strip()]
    return {
        "chars": len(body),
        "segments": len(segments),
        "line_breaks": body.count("\n"),
        "question_marks": body.count("?") + body.count("？"),
        "ellipsis_marks": body.count("…") + body.count("...") + body.count("……"),
    }


def _cadence_snapshot(cadence):
    raw = cadence if isinstance(cadence, dict) else {}
    controls = raw.get("control_coefficients") if isinstance(raw.get("control_coefficients"), dict) else {}

    def control_row(name):
        row = controls.get(name) if isinstance(controls.get(name), dict) else {}
        return {str(key): round(max(-1.0, min(1.0, _number(value))), 6)
                for key, value in sorted(row.items())}

    return {
        "version": str(raw.get("version") or "unknown"),
        "active": bool(raw.get("active", False)),
        # guard/alignment 是 projection 已经使用过的连续解释，不含输入文本或
        # prompt。保留它们才能在回放中区分“影子为零”与“无方向基线被守卫”。
        "guard": str(raw.get("guard")) if raw.get("guard") is not None else None,
        "alignment": round(_unit(raw.get("alignment", 0.0)), 6),
        "coefficient": round(_number(raw.get("coefficient")), 6),
        "shadow_coefficient": round(_number(raw.get("shadow_coefficient")), 6),
        "mode": str(raw.get("mode") or "legacy_unknown"),
        "control_coefficients": {
            "active": control_row("active"),
            "shadow": control_row("shadow"),
        },
    }


def _direction(raw, fallback):
    """冻结五维映射行；不把运行时可变 dict 直接挂进历史 receipt。"""
    candidate = raw if isinstance(raw, dict) else fallback
    return {key: round(max(-1.0, min(1.0, _number(candidate.get(key, 0.0)))), 6)
            for key in PROTOTYPES}


def mapping_snapshot():
    """已发送 action 当时的 W 行最小快照，供未来冻结 replay 复原。

    只保留本地连续映射的版本/方向/启用态，不含 prompt、台词、用户原文或模型过程。
    """
    from model_packages.cx_six_seven_v1.preference_calibration_parameters import (
        CADENCE, FORM, RECALL_CALIBRATION,
    )
    cadence_rows = CADENCE.get("control_directions") or {}
    form_rows = FORM.get("directions") or {}
    recall_rows = RECALL_CALIBRATION.get("directions") or {}
    cadence_fallback = CADENCE.get("direction") or {}
    return {
        "cadence": {
            "version": str(CADENCE.get("version") or "unknown"),
            "enabled": bool(CADENCE.get("enabled", False)),
            "rows": {
                row: _direction(cadence_rows.get(row), cadence_fallback)
                for row in ("f14_impulse", "f14_gate", "f15_impulse", "f15_gate",
                            "f17_length")
            },
        },
        "form": {
            "version": str(FORM.get("version") or "unknown"),
            "enabled": bool(FORM.get("enabled", False)),
            "rows": {
                row: _direction(form_rows.get(row), cadence_fallback)
                for row in ("expansion", "fragmentation", "open_ended")
            },
        },
        "recall": {
            "version": str(RECALL_CALIBRATION.get("version") or "unknown"),
            "enabled": bool(RECALL_CALIBRATION.get("enabled", False)),
            "rows": {
                row: _direction(recall_rows.get(row), cadence_fallback)
                for row in ("propensity_base", "propensity_salience_weight",
                            "propensity_relevance_weight", "propensity_exposure_weight")
            },
        },
    }


def prompt_assembly_snapshot(receipt):
    """将本地 prompt receipt 压缩成无 ID、无正文的校准观测。"""
    raw = receipt if isinstance(receipt, dict) else {}
    delivery = raw.get("recall_delivery") if isinstance(raw.get("recall_delivery"), dict) else {}
    selection = raw.get("recall_selection") if isinstance(raw.get("recall_selection"), dict) else {}
    selected = raw.get("selected") if isinstance(raw.get("selected"), dict) else {}
    kept = raw.get("kept") if isinstance(raw.get("kept"), dict) else {}
    dropped = raw.get("dropped") if isinstance(raw.get("dropped"), dict) else {}
    reference = raw.get("reference_frame") if isinstance(raw.get("reference_frame"), dict) else {}
    form = raw.get("form_projection") if isinstance(raw.get("form_projection"), dict) else {}
    recall_projection = (raw.get("recall_projection")
                         if isinstance(raw.get("recall_projection"), dict) else {})
    reasons = {}
    for row in delivery.get("suppressed") or []:
        if not isinstance(row, dict):
            continue
        reason = str(row.get("reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1

    def count(group, name):
        value = group.get(name) if isinstance(group, dict) else ()
        return len(value) if isinstance(value, (list, tuple)) else 0

    return {
        "recall": {
            "recalled_count": count(delivery, "recalled_event_ids"),
            "delivered_count": count(delivery, "delivered_event_ids"),
            "suppressed_by_reason": dict(sorted(reasons.items())),
            "selection": {
                "available": bool(selection.get("available", False)),
                "candidate_count": max(0, int(_number(selection.get("candidate_count", 0)))),
                "selected_count": max(0, int(_number(selection.get("selected_count", 0)))),
                "rejected_count": max(0, int(_number(selection.get("rejected_count", 0)))),
                "candidate_sources": dict(sorted((selection.get("candidate_sources") or {}).items()))
                if isinstance(selection.get("candidate_sources"), dict) else {},
                "selected_sources": dict(sorted((selection.get("selected_sources") or {}).items()))
                if isinstance(selection.get("selected_sources"), dict) else {},
                "rejected_by_reason": dict(sorted((selection.get("rejected_by_reason") or {}).items()))
                if isinstance(selection.get("rejected_by_reason"), dict) else {},
                "score_max": {
                    key: (None if value is None else round(_number(value), 6))
                    for key, value in sorted((selection.get("score_max") or {}).items())
                } if isinstance(selection.get("score_max"), dict) else {},
            },
        },
        "cards": {
            "selected_persona": count(selected, "persona_block_ids"),
            "kept_persona": count(kept, "persona_block_ids"),
            "dropped_persona": count(dropped, "persona_block_ids"),
            "selected_response": count(selected, "response_block_ids"),
            "kept_response": count(kept, "response_block_ids"),
            "dropped_response": count(dropped, "response_block_ids"),
        },
        "reference": {
            "resolved": bool(reference.get("resolved", False)),
            "delivery_scope": str(reference.get("delivery_scope") or "none"),
            "writer_condition_included": bool(reference.get("writer_condition_included", False)),
        },
        "length_mode": str((raw.get("length_mode") or {}).get("selected")
                           if isinstance(raw.get("length_mode"), dict)
                           else raw.get("length_mode") or "unknown"),
        "form": {
            "active": bool(form.get("active", False)),
            "activity_gate": round(_unit((form.get("activity") or {}).get("gate", 0.0)), 6)
            if isinstance(form.get("activity"), dict) else 0.0,
            # 保留连续方向本身，而不是只有“有几个轴”。这仍然不暴露 writer
            # 正文／token 文本，却让后续冻结回放可判断实际是哪一条 W_form
            # 参与了该 action 的条件。
            "shadow": {
                str(key): round(max(-1.0, min(1.0, _number(value))), 6)
                for key, value in sorted((form.get("shadow") or {}).items())
            } if isinstance(form.get("shadow"), dict) else {},
            # 兼容既有 trace/UI 的摘要字段；新消费者应读取上面的实际三轴值。
            "shadow_axes": len(form.get("shadow") or {}) if isinstance(form.get("shadow"), dict) else 0,
            "active_controls": {
                str(key): round(max(-1.0, min(1.0, _number(value))), 6)
                for key, value in sorted((form.get("controls") or {}).items())
            } if isinstance(form.get("controls"), dict) else {},
            "writer_token_count": len(form.get("tokens") or []) if isinstance(form.get("tokens"), list) else 0,
        },
        "recall_projection": {
            "active": bool(recall_projection.get("active", False)),
            "guard": str(recall_projection.get("guard") or "none"),
            "candidate_gate": round(_unit(recall_projection.get("candidate_gate", 0.0)), 6),
            "alignment": round(_unit(recall_projection.get("alignment", 0.0)), 6),
            "shadow": {
                str(key): round(max(-1.0, min(1.0, _number(value))), 6)
                for key, value in sorted((recall_projection.get("shadow") or {}).items())
            } if isinstance(recall_projection.get("shadow"), dict) else {},
        },
    }


def action_calibration_snapshot(*, state, cadence, recall_refs, text, baseline=None,
                                assembly_receipt=None):
    """构造不含 prompt、正文或模型中间推理的动作快照。"""
    current = state if isinstance(state, dict) else {}
    refs = [row for row in (recall_refs or []) if isinstance(row, dict)]
    source_counts = {}
    for row in refs:
        source = str(row.get("source") or "unknown")
        source_counts[source] = source_counts.get(source, 0) + 1
    return {
        "schema_version": 1,
        "kind": "action_calibration_snapshot",
        "state": {
            "B0": _weights(baseline),
            "D": _weights(current.get("weights")),
            "P": round(_unit(current.get("P", 0.0)), 6),
            "unanswered_n": max(0, int(_number(current.get("unanswered_n", 0)))),
        },
        "cadence": _cadence_snapshot(cadence),
        "mappings": mapping_snapshot(),
        "recall_delivery": {
            "delivered_count": len(refs),
            "sources": dict(sorted(source_counts.items())),
        },
        "prompt_assembly": prompt_assembly_snapshot(assembly_receipt),
        "output_form": output_form_snapshot(text),
    }


def outcome_calibration_observation(*, action, outcome):
    """把已关闭结果窗口连回当时的动作快照。

    这是供未来影子回放消费的训练样本，不把 timeout、沉默或一次回复解释成喜欢／
    不喜欢；它只保存可观测的时间结果及其 action 归因。
    """
    receipt = action if isinstance(action, dict) else {}
    observed = outcome if isinstance(outcome, dict) else {}
    vector = observed.get("outcome_vector") if isinstance(observed.get("outcome_vector"), dict) else {}
    snapshot = receipt.get("calibration_snapshot")
    if not isinstance(snapshot, dict):
        return None
    answered = vector.get("answered")
    if answered not in (True, False, None):
        answered = None
    delay = vector.get("delay_ms")
    delay = None if delay is None else max(0, int(_number(delay)))
    next_r = vector.get("next_R")
    next_r = None if next_r is None else round(_unit(next_r), 6)
    return {
        "schema_version": 1,
        "kind": "calibration_observed",
        "action_id": str(receipt.get("action_id") or ""),
        "action_ts": int(_number(receipt.get("ts"))),
        "source": str(receipt.get("source") or "reply"),
        "params_sha": str(receipt.get("params_sha") or ""),
        "outcome_revision": max(0, int(_number(observed.get("revision")))),
        "outcome_status": str(observed.get("status") or "unknown"),
        "observation": {
            "answered": answered,
            "delay_ms": delay,
            "next_R": next_r,
            "npc_actions_before_response": max(0, int(_number(
                vector.get("npc_actions_before_response", 0)))),
            "confidence": str(vector.get("confidence") or "none"),
        },
        "action_snapshot": dict(snapshot),
    }
