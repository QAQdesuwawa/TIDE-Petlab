"""P4 行动结果观察器（纯函数、零 LLM、零写入）。

它从 ActionReceipt 与消息流计算一个墙钟观察窗口的可观测后果。它不评价用户心理，
不调参，也不驱动 NPC 行动；运行时到期调度留给 P4 后续独立接线。
"""


DEFAULT_HORIZON_MS = 24 * 3_600_000


def _is_feedback_evidence(message):
    """判定一条 actor-local input 是否可关闭用户反馈观察窗。

    storage 的 ``role=user`` 只表示“相对本 Actor 的 received input”，不能把
    NPC peer delivery 误作真实用户对上一 action 的反馈。旧历史没有该字段时保留
    human-only 时代的兼容语义；新的 canonical ingress 必须给出显式 policy。
    """
    return (isinstance(message, dict) and message.get("role") == "user"
            and bool(message.get("preference_evidence", True)))


def _action_index(action, messages):
    """优先按 action_id 找精确消息；旧/损坏数据不猜测。"""
    action_id = action.get("action_id")
    if not action_id:
        return None
    for idx, message in enumerate(messages):
        if message.get("role") == "npc" and message.get("action_id") == action_id:
            return idx
    return None


def observe_action(action, messages, reference_ts, horizon_ms=DEFAULT_HORIZON_MS):
    """对一条行动收据进行窗口观察。

    返回的 ``outcome_vector`` 只含直接可见行为：是否在窗口内回复、延迟、下一轮 R、
    中间 NPC 行动数和归因置信度。R 仅是响应强度观察，不能单独推出好坏；若窗口还
    未到期，也不把沉默误判为负反馈。
    """
    opened_at = int(action.get("ts", 0))
    horizon_ms = max(0, int(horizon_ms))
    due_at = opened_at + horizon_ms
    reference_ts = int(reference_ts)
    index = _action_index(action, messages)
    base = {
        "action_id": action.get("action_id"),
        "opened_at": opened_at,
        "due_at": due_at,
        "reference_ts": reference_ts,
        "source": action.get("source"),
        "recall_refs": list(action.get("recall_refs") or []),
    }
    if index is None:
        base.update({"status": "orphan", "closed_at": None,
                     "outcome_vector": {"answered": None, "confidence": "none"}})
        return base

    following = messages[index + 1:]
    next_user_index = next((i for i, row in enumerate(following)
                            if _is_feedback_evidence(row)), None)
    if next_user_index is None:
        if reference_ts < due_at:
            base.update({"status": "open", "closed_at": None,
                         "outcome_vector": {"answered": None, "confidence": "open"}})
            return base
        before_user = following
        base.update({"status": "timeout", "closed_at": due_at,
                     "outcome_vector": {
                         "answered": False,
                         "delay_ms": None,
                         "next_R": None,
                         "npc_actions_before_response": sum(
                             1 for row in before_user if row.get("role") == "npc"),
                         "confidence": "low" if any(
                             row.get("role") == "npc" for row in before_user) else "high",
                     }})
        return base

    user = following[next_user_index]
    before_user = following[:next_user_index]
    reply_at = int(user.get("ts", opened_at))
    in_window = reply_at <= due_at
    overlaps = sum(1 for row in before_user if row.get("role") == "npc")
    base.update({"status": "reply" if in_window else "late_reply",
                 "closed_at": reply_at if in_window else due_at,
                 "outcome_vector": {
                     "answered": bool(in_window),
                     "delay_ms": max(0, reply_at - opened_at),
                     "next_R": user.get("R"),
                     "npc_actions_before_response": overlaps,
                     "confidence": "high" if overlaps == 0 else "low",
                 }})
    return base


def observe_actions(actions, messages, reference_ts, horizon_ms=DEFAULT_HORIZON_MS):
    """对一组行动收据逐条观察；输入顺序就是 append-only 行动顺序。"""
    return [observe_action(action, messages, reference_ts, horizon_ms=horizon_ms)
            for action in actions]
