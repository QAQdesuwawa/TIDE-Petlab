"""离线校准样本与数学 trace 的隐私化对账。

这个模块只回答一个很窄的问题：一条已经关闭的 ``calibration_observed``
是否能追到同参数版本、同一次已提交输出的数学观测链。它不解释结果好坏，
不把自然沉默翻译成偏好，也绝不写运行时状态或映射参数。

数学 trace 是可选观测流，因此 ``trace_missing_or_disabled`` 是诚实的覆盖
状态，不是让线上事务失败的错误。报告只输出不可逆的 action 指纹，防止开发
工具把本地 action ID、消息正文或 prompt 带出存档边界。
"""
from __future__ import annotations

from collections import Counter, defaultdict
from hashlib import sha256

from interaction_extensions.preference_calibration.cx_six_seven.vector import (
    calibration_sample_key,
)


SCHEMA_VERSION = 1
_REQUIRED_COMMIT = "F16"


def _fingerprint(value):
    """稳定的本地展示引用；不要将真实 action ID 放进审计产物。"""
    return sha256(str(value or "").encode("utf-8")).hexdigest()[:16]


def _action_refs(row):
    """从 trace 根引用和 span 父引用提取受控 action ID。"""
    refs = set()
    root = row.get("root_refs") if isinstance(row.get("root_refs"), dict) else {}
    for key in ("action_id", "action_ids"):
        raw = root.get(key)
        if isinstance(raw, (list, tuple)):
            refs.update(str(item) for item in raw if str(item))
        elif raw is not None and str(raw):
            refs.add(str(raw))
    parent = row.get("parent_ref")
    if parent is not None and str(parent):
        refs.add(str(parent))
    return refs


def _trace_index(records):
    """构造 ``action -> trace`` 只读索引，忽略非 span / 不完整旧记录。"""
    indexed = defaultdict(lambda: defaultdict(list))
    trace_rows = defaultdict(list)
    for raw in records or ():
        row = raw if isinstance(raw, dict) else {}
        trace_id = str(row.get("trace_id") or "")
        params_sha = str(row.get("params_sha") or "")
        formula = str(row.get("formula_id") or "")
        if not trace_id or not params_sha or not formula:
            continue
        # 同一 trace 是一次既有事务的因果容器。只有 F16/F06 会直接带 action
        # parent_ref 并不代表同 trace 的 F10/F14/F15 与该已提交输出无关。
        trace_rows[trace_id].append(row)
        for action_id in _action_refs(row):
            indexed[action_id][trace_id].append(row)
    return indexed, trace_rows


def audit_calibration_trace_links(observations, math_traces):
    """审计 append-only 观测与可选 trace 的关联完整性。

    ``observations`` 仅消费 ``calibration_observed``。每个 outcome revision 单独
    计数，但同一个 action 只要有一条同参数 trace 即可被关联。结果永远标记为
    ``manual_review_only``，不可直接交给 ``propose_shadow_delta`` 当作目标。
    """
    trace_by_action, trace_rows = _trace_index(math_traces)
    rows, invalid = [], 0
    coverage = Counter()
    for raw in observations or ():
        observed = raw if isinstance(raw, dict) else {}
        if observed.get("kind") != "calibration_observed":
            continue
        action_id = str(observed.get("action_id") or "")
        params_sha = str(observed.get("params_sha") or "")
        if not action_id or not params_sha:
            invalid += 1
            continue
        all_traces = trace_by_action.get(action_id, {})
        same_version = {
            trace_id: [span for span in trace_rows.get(trace_id, ())
                       if str(span.get("params_sha") or "") == params_sha]
            for trace_id, action_spans in all_traces.items()
            if any(str(span.get("params_sha") or "") == params_sha for span in action_spans)
        }
        if not all_traces:
            status = "trace_missing_or_disabled"
            linked, formula_ids, trace_refs = False, (), ()
        elif not same_version:
            status = "params_version_mismatch"
            linked, formula_ids, trace_refs = False, (), ()
        else:
            trace_refs = tuple(sorted(same_version))
            formula_ids = tuple(sorted({str(span.get("formula_id"))
                                        for spans in same_version.values() for span in spans}))
            committed = any(
                str(span.get("formula_id")) == _REQUIRED_COMMIT
                and str(span.get("phase")) == "output_committed"
                for spans in same_version.values() for span in spans)
            linked = bool(committed)
            status = "linked" if committed else "output_commit_span_missing"
        coverage[status] += 1
        rows.append({
            # 与 calibration_vector 的 sample_key 同形，但审计本身仍不暴露 action ID。
            "sample_key": calibration_sample_key(observed),
            "action_ref": _fingerprint(action_id),
            "outcome_revision": max(0, int(observed.get("outcome_revision") or 0)),
            "params_ref": _fingerprint(params_sha),
            "status": status,
            "linked": linked,
            "trace_count": len(trace_refs),
            "formula_ids": formula_ids,
        })

    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "calibration_trace_audit",
        "trace_optional": True,
        "decision": "manual_review_only",
        "observed_revision_count": len(rows),
        "linked_revision_count": sum(1 for row in rows if row["linked"]),
        "unlinked_revision_count": sum(1 for row in rows if not row["linked"]),
        "invalid_observation_count": invalid,
        "coverage_by_status": dict(sorted(coverage.items())),
        "rows": rows,
        "privacy": "no_prompt_no_message_body_no_raw_action_id_no_raw_params_sha",
    }


_FAMILY_FORMULAS = {
    # W_time 的四行只投影既有 F14/F15 调度，不借 F06/F08 的状态读数臆测方向。
    "time": frozenset(("F14", "F15")),
    "form": frozenset(("F16",)),
    "recall": frozenset(("F10",)),
}


def gate_frozen_targets_by_trace(targets, audit_report):
    """只保留有同源公式 receipt 的冻结残差目标。

    这是 ``propose_shadow_delta`` 前的证据闸，而非另一个优化器。调用者仍须提供
    人工／冻结实验目标和显式学习率。未知 family、未关联 trace 或缺少对应公式时，
    目标不会被猜测转投给其他参数族。
    """
    report = audit_report if isinstance(audit_report, dict) else {}
    rows = report.get("rows") if isinstance(report.get("rows"), list) else []
    indexed = {str(row.get("sample_key") or ""): row for row in rows
               if isinstance(row, dict) and row.get("sample_key")}
    accepted, rejected = [], []
    for raw in targets or ():
        target = raw if isinstance(raw, dict) else {}
        key = str(target.get("sample_key") or "")
        audit_row = indexed.get(key)
        residuals = target.get("residuals") if isinstance(target.get("residuals"), dict) else {}
        if audit_row is None:
            rejected.append({"sample_key": key, "reason": "sample_not_in_trace_audit"})
            continue
        if not bool(audit_row.get("linked", False)):
            rejected.append({"sample_key": key, "reason": str(audit_row.get("status") or "trace_unlinked")})
            continue
        seen = set(audit_row.get("formula_ids") or ())
        kept, blocked = {}, []
        for family, family_residuals in residuals.items():
            required = _FAMILY_FORMULAS.get(str(family))
            if required is None:
                blocked.append({"family": str(family), "reason": "unknown_parameter_family"})
            elif not isinstance(family_residuals, dict):
                blocked.append({"family": str(family), "reason": "invalid_residual_contract"})
            elif required.isdisjoint(seen):
                blocked.append({"family": str(family), "reason": "formula_receipt_missing"})
            else:
                kept[str(family)] = dict(family_residuals)
        if kept:
            item = dict(target)
            item["residuals"] = kept
            accepted.append(item)
        if blocked:
            rejected.append({"sample_key": key, "reason": "family_not_evidenced", "blocked": blocked})
    return {"schema_version": SCHEMA_VERSION, "kind": "frozen_target_trace_gate",
            "apply": False, "accepted_targets": accepted, "rejected_targets": rejected,
            "decision": "manual_review_only"}
