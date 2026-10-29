"""阶段评审的行动级只读证据关联。

不读当前 Runner/profile 状态，不调用模型，也不写入用户目录。缺少行动时快照时返回
unknown，而不是用会话末尾状态倒推过去。
"""
import copy
import json


EVIDENCE_VERSION = 1


def _as_list(rows):
    return [dict(row) for row in (rows or []) if isinstance(row, dict)]


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _latest_outcomes(outcomes):
    """每个 action 取最高 revision；同 revision 取 append 顺序靠后的事实行。"""
    latest = {}
    for index, row in enumerate(outcomes):
        action_id = row.get("action_id")
        if not action_id:
            continue
        revision = int(row.get("revision", 0) or 0)
        previous = latest.get(str(action_id))
        if previous is None or revision >= previous[0]:
            latest[str(action_id)] = (revision, index, row)
    return {key: dict(value[2]) for key, value in latest.items()}


def _feedback_for(action_id, events):
    rows = []
    for index, event in enumerate(events):
        if event.get("kind") != "user_feedback":
            continue
        direct = event.get("action_id") == action_id
        candidate = action_id in set(event.get("candidate_action_ids") or [])
        if not direct and not candidate:
            continue
        rows.append({
            "event_index": index,
            "ts": event.get("ts"),
            "feedback": event.get("feedback"),
            "attribution_confidence": event.get("attribution_confidence", "none"),
            "link": "direct" if direct else "candidate_only",
            "b0": copy.deepcopy(event.get("b0")) if isinstance(event.get("b0"), dict) else None,
            "params_sha": event.get("params_sha"),
        })
    return rows


def _feedback_b0(feedback_rows):
    """只接受唯一高置信直连反馈中留存的 B₀ 快照。"""
    snapshots = [row["b0"] for row in feedback_rows
                 if row["link"] == "direct"
                 and row["attribution_confidence"] == "high"
                 and row.get("b0")]
    unique = {_canonical(value): value for value in snapshots}
    if len(unique) == 1:
        return copy.deepcopy(next(iter(unique.values()))), "feedback_snapshot", []
    if len(unique) > 1:
        return None, "unknown", ["conflicting_feedback_b0"]
    return None, "unknown", ["b0_snapshot_missing"]


def _breakpoint_fit(receipt, breakpoints):
    """仅匹配同一时间与同一 params_sha 的唯一断点，避免事后状态穿越。"""
    matches = []
    for index, row in enumerate(breakpoints):
        extra = row.get("extra") or {}
        if (row.get("ts") == receipt.get("ts")
                and extra.get("params_sha") == receipt.get("params_sha")):
            matches.append((index, extra.get("fit")))
    if len(matches) == 1 and isinstance(matches[0][1], dict):
        return copy.deepcopy(matches[0][1]), {"breakpoint_index": matches[0][0]}, []
    if not matches:
        return None, {}, ["fit_snapshot_missing"]
    return None, {}, ["fit_snapshot_ambiguous"]


def _input_for(receipt, messages):
    turn_id = receipt.get("turn_id")
    if turn_id is None:
        return None, {}, ["input_turn_missing"]
    matches = [row for row in messages
               if row.get("role") == "user" and row.get("turn_id") == turn_id]
    if len(matches) == 1:
        message = matches[0]
        return {
            "R": message.get("R"),
            "S": message.get("S"),
            "S_parts": copy.deepcopy(message.get("S_parts")),
            "tokens": copy.deepcopy(message.get("tokens")),
        }, {"input_turn_id": turn_id}, []
    return None, {}, ["input_turn_ambiguous" if matches else "input_turn_not_found"]


def _outcome_eligibility(outcome):
    if not outcome:
        return False, ["outcome_missing"]
    status = outcome.get("status")
    confidence = (outcome.get("outcome_vector") or {}).get("confidence")
    if status == "open":
        return False, ["outcome_open"]
    if status == "orphan":
        return False, ["outcome_orphan"]
    if confidence != "high":
        return False, ["outcome_low_confidence"]
    return True, []


def build_evidence_rows(receipts, messages, events, outcomes, breakpoints=None,
                        independent_labels=None, study_context=None):
    """关联 append-only 事实流为按 action_id 一行的 EvidenceRow。

    ``independent_labels`` 是未来盲评/人工标签导入的 action_id → list 映射；本函数
    原样带出，绝不解释标签。所有输入只读，返回对象与输入不共享可变嵌套结构。
    """
    receipts = _as_list(receipts)
    messages = _as_list(messages)
    events = _as_list(events)
    outcomes = _as_list(outcomes)
    breakpoints = _as_list(breakpoints)
    if study_context is not None and not isinstance(study_context, dict):
        raise ValueError("study_context 必须是 dict 或 None")
    labels = independent_labels or {}
    if not isinstance(labels, dict):
        raise ValueError("independent_labels 必须是按 action_id 索引的 dict")
    seen_ids = set()
    latest_outcome = _latest_outcomes(outcomes)
    rows = []

    for receipt_index, receipt in enumerate(receipts):
        action_id = receipt.get("action_id")
        if not action_id:
            raise ValueError("ActionReceipt 缺少 action_id")
        action_id = str(action_id)
        if action_id in seen_ids:
            raise ValueError(f"ActionReceipt action_id 重复: {action_id}")
        seen_ids.add(action_id)

        feedback = _feedback_for(action_id, events)
        b0, b0_source, b0_reasons = _feedback_b0(feedback)
        fit, fit_refs, fit_reasons = _breakpoint_fit(receipt, breakpoints)
        input_features, input_refs, input_reasons = _input_for(receipt, messages)
        outcome = copy.deepcopy(latest_outcome.get(action_id))
        outcome_eligible, outcome_reasons = _outcome_eligibility(outcome)
        direct_high = any(row["link"] == "direct"
                          and row["attribution_confidence"] == "high"
                          for row in feedback)

        recall_refs = copy.deepcopy(receipt.get("recall_refs") or [])
        policy_signature = {
            "response_source": receipt.get("source"),
            "recall_mode": receipt.get("recall_mode"),
            "recall_strategy": receipt.get("recall_strategy"),
            "recall_sources": sorted({str(ref.get("source")) for ref in recall_refs
                                      if isinstance(ref, dict) and ref.get("source")} ),
            "trigger_reason_codes": [],
        }
        row = {
            "evidence_version": EVIDENCE_VERSION,
            "action_id": action_id,
            "turn_id": receipt.get("turn_id"),
            "params_sha": receipt.get("params_sha"),
            "study_context": copy.deepcopy(study_context),
            "policy_signature": policy_signature,
            "context": {
                "b0_snapshot": b0,
                "b0_source": b0_source,
                "declared_style_constraints": None,
                "boundary_constraints": None,
                "fit_snapshot": fit,
                "input_features": input_features,
            },
            "observations": {
                "outcome": outcome,
                "response_intensity_r": (input_features or {}).get("R"),
                "explicit_feedback": feedback,
                "independent_labels": copy.deepcopy(labels.get(action_id) or []),
            },
            "eligibility": {
                "action_level": True,
                "feedback_action_level": direct_high,
                "outcome_action_level": outcome_eligible,
                "reasons": b0_reasons + fit_reasons + input_reasons + outcome_reasons,
            },
            "source_refs": {
                "receipt_index": receipt_index,
                **fit_refs,
                **input_refs,
                "feedback_event_indexes": [row["event_index"] for row in feedback],
                "outcome_action_id": action_id if outcome is not None else None,
            },
        }
        rows.append(row)
    return rows
