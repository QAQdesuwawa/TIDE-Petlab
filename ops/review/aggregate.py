"""EvidenceRow → AssessmentBatch 的纯聚合。

本模块只报告条件化证据向量，不把 R、回应率或文本风格压成全局奖励，也不产生 Proposal。
"""
import copy
from collections import Counter

from ops.review.spec import field_value, require_valid_spec


ASSESSMENT_BATCH_VERSION = 1


def _as_rows(rows):
    return [copy.deepcopy(row) for row in (rows or []) if isinstance(row, dict)]


def _direct_high_feedback(row):
    return [item for item in ((row.get("observations") or {}).get("explicit_feedback") or [])
            if item.get("link") == "direct"
            and item.get("attribution_confidence") == "high"]


def _independent_values(row, dimension):
    values = []
    for item in ((row.get("observations") or {}).get("independent_labels") or []):
        if not isinstance(item, dict) or item.get("dimension") != dimension:
            continue
        value = item.get("value")
        if value is not None:
            values.append(str(value))
    return values


def _outcome_value(row):
    return ((row.get("observations") or {}).get("outcome") or {})


def _eligible(row, policy):
    eligibility = row.get("eligibility") or {}
    reasons = []
    if policy.get("require_high_attribution") and not eligibility.get("feedback_action_level"):
        reasons.append("high_feedback_attribution_required")
    if policy.get("require_high_confidence_outcome") and not eligibility.get("outcome_action_level"):
        reasons.append("high_confidence_outcome_required")
    if policy.get("exclude_open_outcome") and _outcome_value(row).get("status") == "open":
        reasons.append("open_outcome_excluded")
    allowed = policy.get("allowed_params_sha")
    if allowed is not None and row.get("params_sha") not in set(allowed):
        reasons.append("params_sha_not_in_spec")
    return not reasons, reasons


def _new_vector(targets):
    vector = {}
    for target in targets:
        if target == "explicit_fit":
            vector[target] = {"n": 0, "labels": {}}
        elif target == "pacing_cost":
            vector[target] = {"n": 0, "status": {}, "confidence": {}}
        else:
            vector[target] = {"n": 0, "labels": {}}
    return vector


def _add_row(vector, row, targets):
    for target in targets:
        if target == "explicit_fit":
            feedback = _direct_high_feedback(row)
            bucket = vector[target]
            bucket["n"] += len(feedback)
            labels = Counter(bucket["labels"])
            labels.update(str(item.get("feedback", "__unknown__")) for item in feedback)
            bucket["labels"] = dict(sorted(labels.items()))
        elif target == "pacing_cost":
            outcome = _outcome_value(row)
            if outcome:
                bucket = vector[target]
                bucket["n"] += 1
                status = Counter(bucket["status"])
                status[str(outcome.get("status", "__unknown__"))] += 1
                bucket["status"] = dict(sorted(status.items()))
                confidence = Counter(bucket["confidence"])
                confidence[str((outcome.get("outcome_vector") or {}).get(
                    "confidence", "__unknown__"))] += 1
                bucket["confidence"] = dict(sorted(confidence.items()))
        else:
            values = _independent_values(row, target)
            bucket = vector[target]
            bucket["n"] += len(values)
            labels = Counter(bucket["labels"])
            labels.update(values)
            bucket["labels"] = dict(sorted(labels.items()))


def build_assessment_batch(evidence_rows, spec):
    """生成不可决策的阶段评审数据。

    所有 bucket 强制按 ``params_sha`` 分开，即使 AssessmentSpec 漏写该条件也不能跨版本
    混合。输出永久标明 `verdict=no-decision`，Proposal 属于下一职责。
    """
    spec = require_valid_spec(spec)
    rows = _as_rows(evidence_rows)
    policy = dict(spec.get("eligibility") or {})
    # 协议顶层的版本白名单优先于运行数据；它与 condition 分组共同防止跨版本结论。
    if "source_params_sha" in spec:
        policy["allowed_params_sha"] = list(spec["source_params_sha"])
    target_vector = list(spec["target_vector"])
    groups, excluded = {}, Counter()

    for row in rows:
        allowed, reasons = _eligible(row, policy)
        if not allowed:
            excluded.update(reasons)
            continue
        condition = {"params_sha": row.get("params_sha", "__unknown__")}
        for key in spec.get("condition_keys", []):
            condition[str(key)] = field_value(row, key)
        group_key = tuple(sorted(condition.items()))
        group = groups.get(group_key)
        if group is None:
            group = {
                "condition": condition,
                "n_evidence": 0,
                "action_ids": [],
                "score_vector": _new_vector(target_vector),
                "source_refs": [],
            }
            groups[group_key] = group
        group["n_evidence"] += 1
        group["action_ids"].append(row.get("action_id"))
        group["source_refs"].append(copy.deepcopy(row.get("source_refs") or {}))
        _add_row(group["score_vector"], row, target_vector)

    ordered_groups = [groups[key] for key in sorted(groups, key=lambda item: repr(item))]
    return {
        "assessment_batch_version": ASSESSMENT_BATCH_VERSION,
        "assessment_id": spec["assessment_id"],
        "assessment_spec": spec,
        "input_evidence_count": len(rows),
        "eligible_evidence_count": sum(group["n_evidence"] for group in ordered_groups),
        "excluded": dict(sorted(excluded.items())),
        "groups": ordered_groups,
        "verdict": "no-decision",
        "verdict_reason": "AssessmentBatch 只汇总条件化证据；ProposalBuilder 尚未运行",
    }
