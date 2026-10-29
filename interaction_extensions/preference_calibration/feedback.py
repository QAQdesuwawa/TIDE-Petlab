"""可选偏好扩展中用户明确反馈的纯归因规则。

本模块不从自然语言猜测感受，也不改状态/参数；它只把调用方已确认的标签归因到
墙钟窗口内的前序 NPC 行动，供 Runner 追加审计事件。
"""

FEEDBACK_TYPES = frozenset({"hurt", "helpful", "too_much", "too_little"})
CADENCE_FEEDBACK_TYPES = frozenset({"too_fast", "balanced", "too_slow"})

# 这是反馈标签的输入语义定义，不是“某类用户应如何回应”的行为矩阵。表达层只把
# 已确认的语义交给现有 responder 自行落实，既不改记忆选择，也不写回任何参数。
FEEDBACK_MEANINGS = {
    "hurt": "上一轮的表达让对方受伤了",
    "helpful": "上一轮的表达对对方有帮助",
    "too_much": "上一轮的表达或推进让对方觉得过强",
    "too_little": "上一轮的表达或回应让对方觉得不足",
}


def normalize_feedback(value):
    label = str(value or "").strip().lower()
    if label not in FEEDBACK_TYPES:
        raise ValueError("feedback 必须是 hurt/helpful/too_much/too_little")
    return label


def normalize_cadence_feedback(value):
    """节律标签只供人工标定读取，绝不进入下一轮写作语境。"""
    label = str(value or "").strip().lower()
    if label not in CADENCE_FEEDBACK_TYPES:
        raise ValueError("cadence_feedback 必须是 too_fast/balanced/too_slow")
    return label


def attribute_feedback(receipts, ts, horizon_ms):
    """仅在窗口内唯一前序行动时给 high 归因；多个候选绝不强行分摊。"""
    now = int(ts)
    horizon = max(0, int(horizon_ms))
    candidates = [dict(row) for row in (receipts or [])
                  if int(row.get("ts", now)) <= now
                  and now - int(row.get("ts", now)) <= horizon]
    candidates.sort(key=lambda row: int(row.get("ts", 0)), reverse=True)
    ids = [str(row["action_id"]) for row in candidates if row.get("action_id")]
    if len(ids) == 1:
        action = candidates[0]
        return {
            "action_id": ids[0], "attribution_confidence": "high",
            "candidate_action_ids": ids,
            "action_signature": {
                "source": action.get("source"),
                "recall_mode": action.get("recall_mode"),
                "recall_refs": list(action.get("recall_refs") or []),
            },
        }
    return {
        "action_id": None,
        "attribution_confidence": "low" if ids else "none",
        "candidate_action_ids": ids,
        "action_signature": {},
    }


def expression_feedback_context(record):
    """把唯一高归因的用户反馈投影成一次性表达校准语境。

    只读取刚归档的 append-only `user_feedback` 事实；低归因、未知标签和无 action
    的记录一律不进入 prompt。返回值不含用户原文、不保存新状态，也不携带或修改
    任何 recall 事件，故它不能成为记忆层旁路。
    """
    if not isinstance(record, dict) or record.get("kind") != "user_feedback":
        return None
    if record.get("attribution_confidence") != "high" or not record.get("action_id"):
        return None
    try:
        label = normalize_feedback(record.get("feedback"))
    except ValueError:
        return None
    return {
        "label": label,
        "meaning": FEEDBACK_MEANINGS[label],
        "source": str(record.get("source") or "explicit"),
        "attribution": "unique_prior_action",
    }
