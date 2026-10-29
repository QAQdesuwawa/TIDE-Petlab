"""冻结评审目标 → 局部 ΔW 影子提案（绝不写入运行时参数）。

自然时间轨迹负责保留“当时发生了什么”；它并不等于用户喜好。只有冻结实验、盲评或
用户自愿纠错提供了一个明确的 *局部方向残差* 后，本模块才对对应映射行计算
``η · mean(e ⊗ B₀)``。这类似外环反向标定，而不是训练 LLM，也不会把候选提案
应用到 ``CADENCE``、``FORM`` 或召回参数。
"""
from __future__ import annotations

import math

from interaction_extensions.preference_calibration.cx_six_seven.vector import (
    FORM_ROWS,
    RECALL_ROWS,
    TIME_ROWS,
)
from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import (
    PROTOTYPES,
    cos_similarity,
    uniform_guard,
)


SCHEMA_VERSION = 1


def _number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _residual(value):
    value = _number(value, float("nan"))
    return value if math.isfinite(value) and -1.0 <= value <= 1.0 else None


def _confidence(value):
    value = _number(value, float("nan"))
    return value if math.isfinite(value) and 0.0 < value <= 1.0 else None


def _empty_rows(rows):
    return {row: {axis: 0.0 for axis in PROTOTYPES} for row in rows}


def _target_index(targets):
    """验证目标绑定；同一 sample_key 的双份目标不猜测覆盖顺序。"""
    indexed, rejected, seen = {}, [], set()
    for raw in targets or ():
        target = raw if isinstance(raw, dict) else {}
        key = str(target.get("sample_key") or "")
        if not key:
            rejected.append({"reason": "missing_sample_key"})
            continue
        if key in seen:
            rejected.append({"sample_key": key, "reason": "duplicate_sample_key"})
            indexed.pop(key, None)
            continue
        seen.add(key)
        origin = str(target.get("origin") or "")
        confidence = _confidence(target.get("confidence"))
        residuals = target.get("residuals") if isinstance(target.get("residuals"), dict) else {}
        if not origin or confidence is None or not residuals:
            rejected.append({"sample_key": key, "reason": "invalid_target_contract"})
            continue
        indexed[key] = {"origin": origin, "confidence": confidence, "residuals": residuals}
    return indexed, rejected


def _form_gate(sample):
    # 使用 action 当时冻结的正式 form C/X/e gate；不能从 P 或未回应账本重建。
    return max(0.0, min(1.0, _number((sample or {}).get("form_activity"))))


def _recall_gate(sample):
    selection = sample.get("recall_selection") if isinstance(sample.get("recall_selection"), dict) else {}
    if not bool(selection.get("available", 0)) or int(selection.get("candidate_count", 0) or 0) <= 0:
        return 0.0
    return max(0.0, min(1.0, _number(selection.get("max_relevance", 0.0))))


def propose_shadow_delta(samples, targets, *, learning_rate, params_sha=None):
    """对冻结、同参数版本样本提出 ``W_time/W_recall/W_form`` 的局部候选。

    ``targets`` 是脱离正常会话的评审记录，例如::

        {"sample_key": "…", "origin": "frozen_review", "confidence": 0.8,
         "residuals": {"time": {"f15_gate": -0.5},
                       "form": {"open_ended": 0.25}}}

    residual 正值只代表“本映射的该行应上调”，负值代表下调；它不是情绪、人格或
    台词规则。``learning_rate`` 必须显式传入，禁止藏一个默认经验值。
    """
    eta = _number(learning_rate, float("nan"))
    if not math.isfinite(eta) or eta <= 0.0:
        raise ValueError("learning_rate 必须是正的有限数")
    all_samples = [row for row in (samples or ()) if isinstance(row, dict)
                   and row.get("kind") == "calibration_vector_sample"]
    versions = {str(row.get("params_sha") or "") for row in all_samples}
    selected_version = str(params_sha) if params_sha is not None else None
    if selected_version is not None:
        all_samples = [row for row in all_samples if str(row.get("params_sha") or "") == selected_version]
        versions = {selected_version}
    elif len(versions) > 1:
        return {
            "schema_version": SCHEMA_VERSION,
            "kind": "calibration_delta_proposal",
            "apply": False,
            "eligible_sample_count": 0,
            "reason": "mixed_parameter_versions",
            "params_versions": sorted(versions),
            "time": {"rows": _empty_rows(TIME_ROWS), "n_by_row": {row: 0 for row in TIME_ROWS}},
            "form": {"rows": _empty_rows(FORM_ROWS), "n_by_row": {row: 0 for row in FORM_ROWS}},
            "recall": {"rows": _empty_rows(RECALL_ROWS), "n_by_row": {row: 0 for row in RECALL_ROWS}},
            "unsupported": ("W_delivery",),
        }

    indexed, rejected = _target_index(targets)
    total = {"time": _empty_rows(TIME_ROWS), "form": _empty_rows(FORM_ROWS),
             "recall": _empty_rows(RECALL_ROWS)}
    weight_sum = {"time": {row: 0.0 for row in TIME_ROWS},
                  "form": {row: 0.0 for row in FORM_ROWS},
                  "recall": {row: 0.0 for row in RECALL_ROWS}}
    row_counts = {"time": {row: 0 for row in TIME_ROWS},
                  "form": {row: 0 for row in FORM_ROWS},
                  "recall": {row: 0 for row in RECALL_ROWS}}
    used, skipped = 0, []
    for sample in all_samples:
        key = str(sample.get("sample_key") or "")
        target = indexed.get(key)
        if target is None:
            continue
        baseline = (sample.get("state") or {}).get("B0") or {}
        if uniform_guard(baseline):
            skipped.append({"sample_key": key, "reason": "uniform_baseline"})
            continue
        dynamic = (sample.get("state") or {}).get("D") or {}
        if cos_similarity(baseline, dynamic) <= 0.0:
            # 原映射含 alignment 乘子；完全正交时该 action 对任一 W 行都没有
            # 可辨识的局部影响，不能把评审方向硬投到 B₀ 上。
            skipped.append({"sample_key": key, "reason": "zero_alignment"})
            continue
        used_this_sample = False
        gates = {"time": 1.0,
                 "form": _form_gate(sample), "recall": _recall_gate(sample)}
        for family, allowed_rows in (("time", TIME_ROWS), ("form", FORM_ROWS),
                                     ("recall", RECALL_ROWS)):
            requested = target["residuals"].get(family)
            if not isinstance(requested, dict):
                continue
            if gates[family] <= 0.0:
                skipped.append({"sample_key": key, "family": family, "reason": "zero_projection_gate"})
                continue
            for row, raw_residual in requested.items():
                if row not in allowed_rows:
                    skipped.append({"sample_key": key, "family": family, "row": str(row),
                                    "reason": "unknown_mapping_row"})
                    continue
                residual = _residual(raw_residual)
                if residual is None:
                    skipped.append({"sample_key": key, "family": family, "row": row,
                                    "reason": "invalid_residual"})
                    continue
                # e ⊗ B₀：只沿用户已声明的五维方向提出局部映射变化；gate 作为
                # 可辨识性闸，不拿它凭空放大单条样本的幅度。
                for axis in PROTOTYPES:
                    total[family][row][axis] += target["confidence"] * residual * float(baseline.get(axis, 0.0))
                weight_sum[family][row] += target["confidence"]
                row_counts[family][row] += 1
                used_this_sample = True
        if used_this_sample:
            used += 1

    def finalize(family, rows):
        values, counts = {}, {}
        for row in rows:
            divisor = weight_sum[family][row]
            counts[row] = row_counts[family][row]
            values[row] = {
                axis: round(0.0 if divisor <= 0.0 else eta * total[family][row][axis] / divisor, 8)
                for axis in PROTOTYPES
            }
        return {"rows": values, "n_by_row": counts}

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "calibration_delta_proposal",
        "apply": False,
        "learning_rate": eta,
        "params_versions": sorted(versions),
        "eligible_sample_count": used,
        "time": finalize("time", TIME_ROWS),
        "form": finalize("form", FORM_ROWS),
        "recall": finalize("recall", RECALL_ROWS),
        "rejected_targets": rejected,
        "skipped": skipped,
        "unsupported": ("W_delivery",),
        "reason": ("shadow_proposal_only; requires frozen review target and separate replay before any parameter write"),
    }
