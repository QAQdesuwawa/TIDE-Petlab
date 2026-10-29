"""局部 ΔW 的冻结数学回放，不调用 Runner/LLM，也不写任何状态。"""
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


def _clamp(value, low=-1.0, high=1.0):
    return max(low, min(high, float(value)))


def _weights(raw):
    values = {axis: max(0.0, _number((raw or {}).get(axis))) for axis in PROTOTYPES}
    total = sum(values.values())
    if total <= 0.0:
        return {axis: 1.0 / len(PROTOTYPES) for axis in PROTOTYPES}
    return {axis: value / total for axis, value in values.items()}


def _row(raw):
    return {axis: _clamp((raw or {}).get(axis, 0.0)) for axis in PROTOTYPES}


def _shadow(b0, direction, gate, alignment):
    return round(_clamp(sum(b0[axis] * direction[axis] for axis in PROTOTYPES)
                        * alignment * gate), 6)


def _form_gate(state):
    return _clamp(_number((state or {}).get("form_activity")), 0.0, 1.0)


def _recall_gate(sample):
    selection = sample.get("recall_selection") if isinstance(sample.get("recall_selection"), dict) else {}
    if not bool(selection.get("available", 0)) or int(selection.get("candidate_count", 0) or 0) <= 0:
        return 0.0
    return max(0.0, min(1.0, _number(selection.get("max_relevance", 0.0))))


def _proposal_rows(proposal, family, rows):
    family_data = proposal.get(family) if isinstance(proposal.get(family), dict) else {}
    raw = family_data.get("rows") if isinstance(family_data.get("rows"), dict) else {}
    return {row: _row(raw.get(row)) for row in rows}


def replay_shadow_proposal(samples, proposal):
    """冻结 sample + `apply=false` 提案 → 每个局部映射行的前后 shadow。

    action snapshot 未冻结 mappings 的旧样本会被明确跳过；不能退回当前 `params.py`
    补算，否则“回放”会悄悄变成当前版本的假设。
    """
    candidate = proposal if isinstance(proposal, dict) else {}
    if candidate.get("kind") != "calibration_delta_proposal" or candidate.get("apply") is not False:
        raise ValueError("只接受 apply=false 的 calibration_delta_proposal")
    expected_versions = {str(item) for item in candidate.get("params_versions") or ()}
    time_delta = _proposal_rows(candidate, "time", TIME_ROWS)
    form_delta = _proposal_rows(candidate, "form", FORM_ROWS)
    recall_delta = _proposal_rows(candidate, "recall", RECALL_ROWS)
    rows, skipped = [], []
    for sample in samples or ():
        item = sample if isinstance(sample, dict) else {}
        if item.get("kind") != "calibration_vector_sample":
            continue
        key = str(item.get("sample_key") or "")
        version = str(item.get("params_sha") or "")
        if expected_versions and version not in expected_versions:
            skipped.append({"sample_key": key, "reason": "params_version_mismatch"})
            continue
        mappings = item.get("mappings") if isinstance(item.get("mappings"), dict) else {}
        cadence = mappings.get("cadence") if isinstance(mappings.get("cadence"), dict) else {}
        form = mappings.get("form") if isinstance(mappings.get("form"), dict) else {}
        recall = mappings.get("recall") if isinstance(mappings.get("recall"), dict) else {}
        if not cadence.get("rows") or not form.get("rows") or not recall.get("rows"):
            skipped.append({"sample_key": key, "reason": "legacy_missing_mapping_snapshot"})
            continue
        state = item.get("state") if isinstance(item.get("state"), dict) else {}
        b0, dynamic = _weights(state.get("B0")), _weights(state.get("D"))
        if uniform_guard(b0):
            skipped.append({"sample_key": key, "reason": "uniform_baseline"})
            continue
        alignment = max(0.0, min(1.0, cos_similarity(b0, dynamic)))
        time_gate = 1.0
        form_gate = _form_gate(item)

        def replay_family(base, delta, names, gate):
            result = {}
            for name in names:
                before_direction = _row((base.get("rows") or {}).get(name))
                after_direction = {axis: _clamp(before_direction[axis] + delta[name][axis])
                                   for axis in PROTOTYPES}
                before = _shadow(b0, before_direction, gate, alignment)
                after = _shadow(b0, after_direction, gate, alignment)
                result[name] = {"before": before, "after": after,
                                "delta": round(after - before, 6)}
            return result

        rows.append({
            "sample_key": key,
            "params_sha": version,
            "alignment": round(alignment, 6),
            "time": replay_family(cadence, time_delta, TIME_ROWS, time_gate),
            "form": replay_family(form, form_delta, FORM_ROWS, form_gate),
            "recall": replay_family(recall, recall_delta, RECALL_ROWS, _recall_gate(item)),
        })
    changed_time = sum(1 for row in rows for value in row["time"].values() if value["delta"] != 0.0)
    changed_form = sum(1 for row in rows for value in row["form"].values() if value["delta"] != 0.0)
    changed_recall = sum(1 for row in rows for value in row["recall"].values() if value["delta"] != 0.0)
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "calibration_shadow_replay",
        "apply": False,
        "replayed_count": len(rows),
        "changed_time_row_count": changed_time,
        "changed_form_row_count": changed_form,
        "changed_recall_row_count": changed_recall,
        "rows": rows,
        "skipped": skipped,
        "reason": "mathematical_shadow_only; no_runner_no_llm_no_state_write",
    }
