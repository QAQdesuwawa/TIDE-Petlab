"""AssessmentBatch → 参数/触发候选建议（只读）。

v1 只评估既有的 recall strategy feature flag 是否值得进入下一轮 A/B。它不产生数值
调参、不写 profile，也不把 R 或未经定义的文本风格当成功标签。
"""
import copy


PROPOSAL_VERSION = 1


def _positive_rate(group, target, positive_label):
    vector = (group.get("score_vector") or {}).get(target) or {}
    n = int(vector.get("n", 0) or 0)
    labels = vector.get("labels") or {}
    return n, (float(labels.get(positive_label, 0)) / n if n else None)


def _policy(spec):
    return dict(spec.get("decision_policy") or {})


def _base_result(batch):
    return {
        "proposal_version": PROPOSAL_VERSION,
        "assessment_id": batch.get("assessment_id"),
        "assessment_batch_version": batch.get("assessment_batch_version"),
        "verdict": "no-decision",
        "proposals": [],
        "diagnostics": [],
        "note": "建议只用于创建后续对照实验；不得直接写入运行时参数或 profile。",
    }


def _pair_groups(groups, compare_key, control, candidate):
    """按除策略字段外的全部条件配对，避免跨 B₀/边界条件比较。"""
    pairs = {}
    for group in groups:
        condition = group.get("condition") or {}
        value = condition.get(compare_key)
        if value not in {control, candidate}:
            continue
        context = tuple(sorted((key, value) for key, value in condition.items()
                               if key != compare_key))
        pairs.setdefault(context, {})[value] = group
    return pairs


def _aggregate_paired_cells(result, pairs, control, candidate, positive, min_samples,
                            min_effect, min_cells):
    """预注册地汇总多个已配对场景；每格仍保留，避免跨场景直接混样。"""
    complete = [(context, pair) for context, pair in pairs.items()
                if control in pair and candidate in pair]
    if len(complete) < min_cells:
        result["diagnostics"].append({
            "reason": "paired_cells_insufficient", "paired_cells": len(complete),
            "min_paired_cells": min_cells,
        })
        return
    cells, control_n_total, candidate_n_total = [], 0, 0
    control_positive, candidate_positive = 0.0, 0.0
    for context, pair in sorted(complete, key=lambda item: repr(item[0])):
        control_n, control_rate = _positive_rate(pair[control], "recall_relevance", positive)
        candidate_n, candidate_rate = _positive_rate(pair[candidate], "recall_relevance", positive)
        control_n_total += control_n
        candidate_n_total += candidate_n
        control_positive += control_n * (control_rate or 0.0)
        candidate_positive += candidate_n * (candidate_rate or 0.0)
        cells.append({"condition": dict(context), "control_n": control_n,
                      "candidate_n": candidate_n, "effect": round(candidate_rate - control_rate, 6)})
    if control_n_total < min_samples or candidate_n_total < min_samples:
        result["diagnostics"].append({
            "reason": "sample_insufficient", "scope": "paired_cells",
            "control_n": control_n_total, "candidate_n": candidate_n_total,
            "min_samples_per_cell": min_samples,
        })
        return
    if any(cell["effect"] < 0.0 for cell in cells):
        result["diagnostics"].append({
            "reason": "candidate_direction_inconsistent", "cells": cells,
        })
        return
    control_rate = control_positive / control_n_total
    candidate_rate = candidate_positive / candidate_n_total
    effect = candidate_rate - control_rate
    if effect < float(min_effect):
        result["diagnostics"].append({
            "reason": "candidate_not_better_than_control", "scope": "paired_cells",
            "control_rate": round(control_rate, 6), "candidate_rate": round(candidate_rate, 6),
            "effect": round(effect, 6), "min_effect": float(min_effect),
        })
        return
    result["proposals"].append({
        "proposal_kind": "recall_strategy_experiment", "knob": "recall_strategy",
        "control": control, "candidate": candidate, "condition_scope": "paired_cells",
        "paired_conditions": [cell["condition"] for cell in cells],
        "target": "recall_relevance", "positive_label": positive,
        "control_n": control_n_total, "candidate_n": candidate_n_total,
        "control_rate": round(control_rate, 6), "candidate_rate": round(candidate_rate, 6),
        "effect": round(effect, 6), "per_cell_effects": cells,
        "required_next_step": "人工确认后，以相同 AssessmentSpec 运行独立 A/B 复验",
    })


def build_proposals(batch):
    """从预注册的 batch 建立 recall strategy 候选；不足证据必须 no-decision。

    需要 AssessmentSpec 显式提供：
    - comparison.control/candidate；
    - decision_policy.comparison_field；
    - decision_policy.positive_label_by_target.recall_relevance；
    - decision_policy.min_samples_per_cell（正整数）与 min_effect（非负数）。
    """
    result = _base_result(batch)
    if not isinstance(batch, dict) or batch.get("verdict") != "no-decision":
        result["diagnostics"].append("assessment_batch_invalid_or_not_final")
        return result
    spec = batch.get("assessment_spec") or {}
    comparison = spec.get("comparison") or {}
    policy = _policy(spec)
    control, candidate = comparison.get("control"), comparison.get("candidate")
    compare_key = policy.get("comparison_field")
    positive = (policy.get("positive_label_by_target") or {}).get("recall_relevance")
    min_samples = policy.get("min_samples_per_cell")
    min_effect = policy.get("min_effect")
    aggregate_cells = policy.get("aggregate_paired_cells") is True
    min_cells = policy.get("min_paired_cells", 1)

    missing = []
    if not control or not candidate:
        missing.append("comparison_control_candidate_missing")
    if not isinstance(compare_key, str) or not compare_key:
        missing.append("comparison_field_missing")
    if not isinstance(positive, str) or not positive:
        missing.append("independent_positive_label_missing")
    if not isinstance(min_samples, int) or min_samples < 1:
        missing.append("min_samples_per_cell_missing")
    if not isinstance(min_effect, (int, float)) or float(min_effect) < 0:
        missing.append("min_effect_missing")
    if aggregate_cells and (not isinstance(min_cells, int) or min_cells < 1):
        missing.append("min_paired_cells_missing")
    if "recall_relevance" not in (spec.get("target_vector") or []):
        missing.append("recall_relevance_not_in_target_vector")
    if missing:
        result["diagnostics"].extend(missing)
        return result

    pairs = _pair_groups(batch.get("groups") or [], compare_key, control, candidate)
    if not pairs:
        result["diagnostics"].append("no_comparable_condition_cells")
        return result
    if aggregate_cells:
        _aggregate_paired_cells(result, pairs, control, candidate, positive, min_samples,
                                min_effect, min_cells)
        if result["proposals"]:
            result["verdict"] = "candidate-proposals"
        return result
    for context, pair in sorted(pairs.items(), key=lambda item: repr(item[0])):
        if control not in pair or candidate not in pair:
            result["diagnostics"].append({
                "reason": "counterpart_missing", "condition": dict(context),
                "present": sorted(pair),
            })
            continue
        control_n, control_rate = _positive_rate(pair[control], "recall_relevance", positive)
        candidate_n, candidate_rate = _positive_rate(pair[candidate], "recall_relevance", positive)
        if control_n < min_samples or candidate_n < min_samples:
            result["diagnostics"].append({
                "reason": "sample_insufficient", "condition": dict(context),
                "control_n": control_n, "candidate_n": candidate_n,
                "min_samples_per_cell": min_samples,
            })
            continue
        effect = candidate_rate - control_rate
        if effect < float(min_effect):
            result["diagnostics"].append({
                "reason": "candidate_not_better_than_control", "condition": dict(context),
                "control_rate": round(control_rate, 6),
                "candidate_rate": round(candidate_rate, 6),
                "effect": round(effect, 6), "min_effect": float(min_effect),
            })
            continue
        result["proposals"].append({
            "proposal_kind": "recall_strategy_experiment",
            "knob": "recall_strategy",
            "control": control,
            "candidate": candidate,
            "condition": dict(context),
            "target": "recall_relevance",
            "positive_label": positive,
            "control_n": control_n,
            "candidate_n": candidate_n,
            "control_rate": round(control_rate, 6),
            "candidate_rate": round(candidate_rate, 6),
            "effect": round(effect, 6),
            "required_next_step": "人工确认后，以相同 AssessmentSpec 运行独立 A/B 复验",
        })
    if result["proposals"]:
        result["verdict"] = "candidate-proposals"
    elif not result["diagnostics"]:
        result["diagnostics"].append("no_candidate_proposals")
    return copy.deepcopy(result)
