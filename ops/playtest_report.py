"""将一次本地试玩压缩为可人工标注的只读报告。

它不是新的状态层、评分器或调参器：只汇合已有消息、prompt trace、输出 receipt 与
反馈/准备事件，帮助把“这条太黏/这次换题很好”的主观标注定位回已有通路。
完整 prompt、嵌入、词元正文和密钥永远不导出。
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime


def _time_label(value):
    try:
        return datetime.fromtimestamp(int(value) / 1000).strftime("%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError, OSError):
        return "未知时间"


def _short_ids(values):
    return [str(value) for value in (values or ()) if value is not None][:8]


def _calibration_summary(receipt, observation):
    """压缩已发送 action 的标定事实；不导出映射方向、正文或内部标识。"""
    snapshot = (receipt or {}).get("calibration_snapshot")
    if not isinstance(snapshot, dict):
        return {"available": False, "reason": "legacy_or_no_snapshot"}
    mappings = snapshot.get("mappings") if isinstance(snapshot.get("mappings"), dict) else {}
    assembly = (snapshot.get("prompt_assembly")
                if isinstance(snapshot.get("prompt_assembly"), dict) else {})
    recall = assembly.get("recall") if isinstance(assembly.get("recall"), dict) else {}
    selection = recall.get("selection") if isinstance(recall.get("selection"), dict) else {}
    suppressed = recall.get("suppressed_by_reason")
    form = snapshot.get("output_form") if isinstance(snapshot.get("output_form"), dict) else {}
    observation_row = observation if isinstance(observation, dict) else {}
    observed = observation_row.get("observation")
    observed = observed if isinstance(observed, dict) else {}

    def mapping_meta(name):
        row = mappings.get(name) if isinstance(mappings.get(name), dict) else {}
        return {"version": str(row.get("version") or "unknown"),
                "enabled": bool(row.get("enabled", False))}

    return {
        "available": True,
        "mappings": {name: mapping_meta(name) for name in ("cadence", "form", "recall")},
        "recall_selection": {
            "available": bool(selection.get("available", False)),
            "candidates": max(0, int(selection.get("candidate_count", 0) or 0)),
            "selected": max(0, int(selection.get("selected_count", 0) or 0)),
            "rejected": max(0, int(selection.get("rejected_count", 0) or 0)),
            "delivered": max(0, int(recall.get("delivered_count", 0) or 0)),
            "suppressed_by_reason": dict(sorted(suppressed.items()))
            if isinstance(suppressed, dict) else {},
        },
        "reference_scope": str((assembly.get("reference") or {}).get("delivery_scope") or "none"),
        "output_form": {key: max(0, int(form.get(key, 0) or 0))
                        for key in ("chars", "segments", "line_breaks", "question_marks", "ellipsis_marks")},
        "natural_outcome": {
            "closed": bool(observation_row),
            "status": str(observation_row.get("outcome_status") or "awaiting") if observation_row else "awaiting",
            "answered": observed.get("answered") if observed.get("answered") in (True, False) else None,
            "delay_ms": observed.get("delay_ms") if isinstance(observed.get("delay_ms"), int) else None,
        },
    }


def _wall_clock_summary(events, *, limit=8):
    """将墙钟追平收据投影为试玩时可读的最小事实。

    它故意不导出候选时间、参数哈希、原始异常、消息正文或任何内部 ID。这里的
    目的只是让试玩者不用翻 JSON，就能区分“没有候选”“被调度压制”“已实际提交”
    和“追平事务失败”。
    """
    rows = []
    for event in events:
        kind = event.get("kind")
        if kind not in {"wall_clock_catchup_evaluated", "wall_clock_catchup_failed"}:
            continue
        detail = event.get("detail") if isinstance(event.get("detail"), dict) else {}
        origin = str(detail.get("origin") or "direct")
        if kind == "wall_clock_catchup_failed":
            rows.append({
                "at": _time_label(event.get("ts")),
                "origin": origin,
                "status": "failed",
                "candidates": None,
                "outcome": "worker_exception",
                "committed": 0,
            })
            continue
        rows.append({
            "at": _time_label(event.get("ts")),
            "origin": origin,
            "status": "evaluated",
            "candidates": max(0, int(detail.get("candidate_count", 0) or 0)),
            "outcome": str(detail.get("outcome") or "unknown"),
            "committed": max(0, int(detail.get("committed_count", 0) or 0)),
        })
    return rows[-max(1, int(limit)):]


def build_playtest_snapshot(store, *, max_turns=12):
    """从一个 ``UserStore`` 读出最近互动的最小可审阅投影。"""
    max_turns = max(1, int(max_turns))
    messages = list(store.read_messages())
    user_turns = [row for row in messages if row.get("role") == "user" and row.get("turn_id")]
    selected_turn_ids = {str(row["turn_id"]) for row in user_turns[-max_turns:]}
    traces = {str(row.get("turn_id")): row for row in store.read_prompt_traces()
              if str(row.get("turn_id")) in selected_turn_ids}
    receipts = {str(row.get("action_id")): row for row in store.read_action_receipts()}
    prepared = {}
    feedback_by_action = defaultdict(list)
    observations_by_action = {}
    events = list(store.read_events())
    for event in events:
        if event.get("kind") == "output_commit_prepared" and event.get("action_id"):
            prepared[str(event["action_id"])] = event
        if event.get("kind") in {"user_feedback", "cadence_feedback"} and event.get("action_id"):
            feedback_by_action[str(event["action_id"])].append({
                "kind": str(event.get("kind")),
                "feedback": event.get("feedback"),
                "attribution": event.get("attribution_confidence"),
            })
        if event.get("kind") == "calibration_observed" and event.get("action_id"):
            # 相同 action 可有多个结果窗口 revision；只呈现最后一个已追加事实。
            observations_by_action[str(event["action_id"])] = event

    rows = []
    for user in user_turns[-max_turns:]:
        turn_id = str(user["turn_id"])
        npc = next((row for row in messages
                    if row.get("role") == "npc" and str(row.get("turn_id")) == turn_id), None)
        trace = traces.get(turn_id) or {}
        action_id = str(npc.get("action_id")) if npc and npc.get("action_id") else None
        receipt = receipts.get(action_id) if action_id else None
        timeline = (prepared.get(action_id) or {}).get("timeline") or {}
        assembly = trace.get("assembly_receipt") or {}
        rows.append({
            "turn_id": turn_id,
            "at": _time_label(user.get("ts")),
            "user": str(user.get("text") or ""),
            "npc": str((npc or {}).get("text") or ""),
            "source": (npc or {}).get("source"),
            "action_id": action_id,
            "recall_event_ids": _short_ids(trace.get("recall_event_ids")),
            "history_sources": _short_ids(trace.get("history_sources")),
            "history_exclusions": _short_ids(trace.get("history_exclusions")),
            "response_block_ids": _short_ids(trace.get("response_block_ids")),
            "persona_block_ids": _short_ids(trace.get("persona_block_ids")),
            "sent_ms": timeline.get("t_sent") or (receipt or {}).get("ts"),
            "ready_ms": timeline.get("t_ready"),
            "feedback": feedback_by_action.get(action_id, []),
            "calibration": _calibration_summary(receipt, observations_by_action.get(action_id)),
            # 只输出 ID/来源与理由的结构字段；故意不带 system/user prompt 正文。
            "assembly": {
                "budget_chars": assembly.get("budget_chars"),
                "selected_response_block_ids": _short_ids(
                    (assembly.get("selected") or {}).get("response_block_ids")),
            },
        })
    return {
        "schema_version": 2,
        "max_turns": max_turns,
        "turns": rows,
        "wall_clock": _wall_clock_summary(events),
    }


def render_playtest_markdown(snapshot, *, session_id):
    """生成可直接人工填写的报告；不包含 prompt/embedding/token 正文。"""
    lines = [
        "# TIDE 试玩记录",
        "",
        "这是一份本地只读导出。请只评价具体回合；不要据一次感觉直接改参数。",
        "",
        "建议标注：`自然 / 太黏 / 太冷 / 记忆错位 / 换题失败 / 等待不自然`。",
    ]
    turns = list(snapshot.get("turns") or ())
    if not turns:
        lines.extend(["", "当前会话还没有可导出的用户回合。"])
    for index, row in enumerate(turns, start=1):
        lines.extend([
            "",
            f"## 回合 {index} · {row['at']}",
            f"你：{row['user']}",
            f"TA：{row['npc'] or '（尚未产生正式回复）'}",
            "",
            "运行摘要："
            f" source={row.get('source') or 'pending'}"
            f" · recall={row.get('recall_event_ids') or '无'}"
            f" · history={row.get('history_sources') or '无'}",
            "装配摘要："
            f" response_cards={row.get('response_block_ids') or '无'}"
            f" · persona_cards={row.get('persona_block_ids') or '无'}",
        ])
        if row.get("ready_ms") and row.get("sent_ms"):
            lines.append("时间事实：ready=" + _time_label(row["ready_ms"])
                         + " · sent=" + _time_label(row["sent_ms"]))
        if row.get("history_exclusions"):
            lines.append("已压制来源：" + ", ".join(row["history_exclusions"]))
        if row.get("feedback"):
            lines.append("已有反馈：" + ", ".join(
                f"{item.get('feedback')}({item.get('attribution')})"
                for item in row["feedback"]))
        calibration = row.get("calibration") or {}
        if calibration.get("available"):
            selection = calibration.get("recall_selection") or {}
            mappings = calibration.get("mappings") or {}
            versions = "/".join(
                f"{name}:{meta.get('version')}({'on' if meta.get('enabled') else 'shadow'})"
                for name, meta in mappings.items())
            form = calibration.get("output_form") or {}
            outcome = calibration.get("natural_outcome") or {}
            lines.append(
                "标定摘要："
                f"maps={versions} · recall={selection.get('candidates', 0)}/"
                f"{selection.get('selected', 0)}/{selection.get('delivered', 0)}"
                f" · suppressed={selection.get('suppressed_by_reason') or '无'}"
                f" · form={form.get('chars', 0)}字/{form.get('segments', 0)}段"
                f" · outcome={outcome.get('status', 'awaiting')}")
        else:
            lines.append("标定摘要：当前 action 为旧记录或尚无 snapshot，不能用于本轮参数评审")
        lines.append("人工标注：________  原因：________________________________")
    wall_clock = list(snapshot.get("wall_clock") or ())
    if wall_clock:
        lines.extend(["", "## 最近墙钟追平", ""])
        for row in wall_clock:
            source = {"startup": "启动追平", "timer": "定时追平", "event": "事件追平"}.get(
                row.get("origin"), "直接追平")
            if row.get("status") == "failed":
                lines.append(f"- {row['at']} · {source} · 执行失败（未形成正式输出）")
            else:
                lines.append(
                    f"- {row['at']} · {source} · 候选 {row['candidates']} 个"
                    f" · 结果 {row['outcome']} · 正式提交 {row['committed']} 条")
    lines.extend([
        "",
        "## 使用边界",
        "",
        "- 这份报告不含完整 prompt、embedding、词元正文或密钥。",
        "- `recall=[]` 表示本轮没有旧事件实际进入作者条件，不等于系统没有内部候选。",
        "- 节律问题先配合 UI 的“反馈”按钮记录；参数仍须由冻结样本复放后人工决定。",
        "",
    ])
    return "\n".join(lines)
