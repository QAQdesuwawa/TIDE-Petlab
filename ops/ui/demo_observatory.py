"""比赛演示的只读数值投影。

这里不是总线、调度器或第二份状态机。它只读取已经结算的 temporal state、已保存的
clock plan 与无正文 telemetry，供 Tk 演示面板展示。任何缺失字段均降级为空/零，
绝不为了图表补造事件、记忆或心理解释。
"""
from __future__ import annotations

import math

ROOT_METERS = (
    ("E_env", "环境压力"),
    ("L_self", "自我杠杆"),
    ("L_opponent", "关系杠杆"),
    ("P_need", "靠近需求"),
    ("V_attr", "吸引强度"),
    ("V_rep", "关系张力"),
)

# 这些不是第二套调度分类，而是展示边界：只有已提交、确实可能改变根状态的
# 因果事实才开始一段新的可视化轨迹。``opening_plan_*`` 只是“已越线，开始生成”
# 的观察点；它没有新 receipt，不能伪装成一次状态重置。
OPENING_SEGMENT_BOUNDARIES = frozenset((
    "input_committed", "reply_receipt", "opening_receipt", "share_receipt",
))
OPENING_PLAN_MARKERS = frozenset(("opening_plan_reply", "opening_plan_tick",
                                  "opening_plan_expired"))


def committed_opening_trace(events):
    """从已落盘 P3 authority receipt 回建演示折线的真实点。

    这只解决 UI 后开／重开时内存 telemetry 已丢失的问题；不能补 tick、不能创建
    plan marker，更不能用当前 state 重算 I/Theta。events 的 append-only 顺序也是同
    毫秒 receipt 的因果顺序，故刻意不按 timestamp 重排。
    """
    labels = {
        "human_input": "input_committed",
        "received_participant_event": "input_committed",
        "tick": "tick",
        "npc_output": "reply_receipt",
    }
    trace = []
    by_authority_event_id = {}
    for row in events or ():
        if not isinstance(row, dict):
            continue
        if row.get("kind") == "opening_dispatch_committed":
            detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
            authority = detail.get("authority") if isinstance(detail.get("authority"), dict) else {}
            source_id = str(authority.get("event_id") or "")
            source = by_authority_event_id.get(source_id)
            action_id = str(row.get("action_id") or "")
            if source is not None and action_id:
                # dispatch 是已提交事实，却不是第二个 I/Theta 读数。把 exact
                # F16 绑定在它确实消费的 tick 点，供跨 F16 分页的 UI 可追溯。
                source["opening_dispatch"] = {
                    "action_id": action_id,
                    "ts": _positive_int(row.get("ts")),
                }
            continue
        if row.get("kind") != "opening_authority_receipt":
            continue
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        event = detail.get("event") if isinstance(detail.get("event"), dict) else {}
        kind = str(event.get("kind") or "")
        if kind not in labels or not all(key in detail for key in ("I_open", "Theta_open", "margin")):
            continue
        try:
            ts = int(event.get("observed_at_ms"))
        except (TypeError, ValueError):
            continue
        point = {"ts": ts, "event": labels[kind],
                 "impulse": _number(detail.get("I_open")),
                 "gate": _number(detail.get("Theta_open")),
                 "margin": _number(detail.get("margin")),
                 # P3 已经算出的下一观察间隔，只供展示横轴定位。UI 绝不
                 # 另算 H(Q)、改 deadline 或补造 tick。
                 "interval_ms": _positive_int(detail.get("h_next_ms"))}
        trace.append(point)
        by_authority_event_id[str(event.get("id") or "")] = point
    return trace


def segment_opening_trace(rows):
    """按已提交因果 receipt 切分开口展示轨迹。

    返回值只给 UI 绘图使用，不能反写 Runner；数学锚仍属于 F15。特别地，NPC
    receipt 开新段表达“F06 后 X 已变化”，而不是把 NPC 输出伪造成新的用户事件。
    """
    segments = []
    current = []
    for raw in rows or ():
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        event = str(row.get("event") or "state")
        # 第一项必然要有容器；之后只有真实提交边界才切段。
        if current and event in OPENING_SEGMENT_BOUNDARIES:
            segments.append(current)
            current = []
        row["segment_id"] = len(segments)
        row["segment_start"] = not current
        row["plan_marker"] = event in OPENING_PLAN_MARKERS
        current.append(row)
    if current:
        segments.append(current)
    return segments


def opening_trace_page(rows, capacity=12, *, restart_when_full=False):
    """返回当前因果段可绘制的一页，以及页码。

    ``capacity`` 只是一块画布能同时容纳多少个已结算点，绝不是 F15 的窗口、
    轮询上限或状态重置。满页后下一页从前一页的末点起笔，因此观众仍能看见
    两条线在同一连续状态上的后续变化，而不是让 x 坐标悄悄跑出右边界。
    """
    points = [dict(row) for row in (rows or ()) if isinstance(row, dict)]
    limit = max(2, int(capacity or 2))
    if len(points) <= limit:
        page, current, total = points, 1, 1
    else:
        # 每一新页复用上一页末点，故每页实际向前推进 limit - 1 个新 observation。
        stride = limit - 1
        page_index = max(0, (len(points) - 2) // stride)
        start = page_index * stride
        total_pages = max(1, ((len(points) - 2) // stride) + 1)
        page, current, total = points[start:start + limit], page_index + 1, total_pages
    # 抵达最右端后，展示层可在本次动画结束时提前打开下一屏。它只带着已结算
    # 的末点重新起笔；下一条正式 tick 才会给这一页添加新点。
    if restart_when_full and len(page) >= limit and page:
        return [page[-1]], current + 1, total + 1
    return page, current, total


def opening_trace_time_page(segments):
    """取一张按真实观察区间而非 receipt 个数排列的只读展示页。

    每页从上一已提交 P3 receipt 走到它的 ``h_next_ms`` 观察终点。F16 仍是
    数学上的因果切片；下一段只有一个点时，展示携带上一段最后一个 committed
    point 作为连续起笔，不插值、不新建 receipt。
    """
    groups = [
        [dict(row) for row in segment if isinstance(row, dict) and not row.get("plan_marker")]
        for segment in (segments or ()) if segment
    ]
    groups = [group for group in groups if group]
    if not groups:
        return [], None, None, False
    current = groups[-1]
    carried = False
    if len(current) >= 2:
        # 一张画面呈现一个真实 observation interval；下一 tick 再换页。
        page = current[-2:]
    elif len(groups) >= 2:
        page = [dict(groups[-2][-1]), dict(current[0])]
        page[0]["presentation_carry"] = True
        carried = True
    else:
        page = [dict(current[0])]
    # F16 切出的新段已经有自己的 P3/H(Q) deadline；携带的前点只解释连续性，
    # 不能拿它的旧 interval 覆盖新段的真实观察窗。
    horizon_anchor = page[-1] if carried else page[0]
    start_ms = int(horizon_anchor.get("ts", 0))
    interval_ms = _positive_int(horizon_anchor.get("interval_ms"))
    if interval_ms is None and len(page) >= 2:
        interval_ms = max(1, int(page[-1].get("ts", start_ms)) - start_ms)
    end_ms = start_ms + interval_ms if interval_ms is not None else None
    return page, start_ms, end_ms, carried


def _nice_axis_step(rough_step):
    """返回纯展示用的 1/2/5 十进制刻度，不参与任何数学读数。"""
    rough_step = max(float(rough_step), 1e-12)
    exponent = math.floor(math.log10(rough_step))
    scale = 10.0 ** exponent
    normalized = rough_step / scale
    if normalized < 2.0:
        factor = 1.0
    elif normalized < 5.0:
        factor = 2.0
    else:
        factor = 5.0
    return factor * scale


def opening_trace_viewport_details(rows, *, padding_ratio=0.18, tick_count=4,
                                   flat_span=0.01, domain=(0.0, 1.0)):
    """编译当前 committed 因果页的自动纵轴，只决定坐标阅读方式。

    I_open/Theta_open 的真实定义域始终是 0--1；这里根据已提交点的范围生成一条
    可读刻度，避免固定 0.08 跨度吞掉 1e-5 级的真实差异。它不插值、不计算未来
    根状态、不改变 P3/F15/F16，也不产生任何 receipt。
    """
    values = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        for key in ("impulse", "gate"):
            try:
                value = float(row.get(key))
            except (TypeError, ValueError):
                continue
            if math.isfinite(value) and (domain is None or domain[0] <= value <= domain[1]):
                values.append(value)
    if not values:
        return {"lower": 0.0, "upper": 1.0, "ticks": (0.0, 0.5, 1.0),
                "raw_low": None, "raw_high": None, "flat": True,
                "mode": "empty"}
    raw_low, raw_high = min(values), max(values)
    raw_span = raw_high - raw_low
    flat = raw_span == 0.0
    target_ticks = max(2, int(tick_count))
    # 无差异时仍需给唯一真实点一个可读位置，但会明确标为“无已结算差异”；
    # 有差异时视口完全由实际跨度和统一的刻度取整得到，不保留 0.08 这种
    # 与数据无关的最小窗口。
    base_span = float(flat_span) if flat else raw_span
    padding = base_span * max(0.0, float(padding_ratio))
    padded_low = raw_low - padding
    padded_high = raw_high + padding
    if domain is not None:
        padded_low = max(domain[0], padded_low)
        padded_high = min(domain[1], padded_high)
    if padded_high <= padded_low:
        midpoint = (raw_low + raw_high) / 2.0
        padded_low = midpoint - base_span / 2.0
        padded_high = midpoint + base_span / 2.0
        if domain is not None:
            padded_low = max(domain[0], padded_low)
            padded_high = min(domain[1], padded_high)
    step = _nice_axis_step((padded_high - padded_low) / (target_ticks - 1))
    lower = math.floor(padded_low / step) * step
    upper = math.ceil(padded_high / step) * step
    if domain is not None:
        lower = max(domain[0], lower)
        upper = min(domain[1], upper)
    if upper <= lower:
        upper = lower + step
        if domain is not None:
            upper = min(domain[1], upper)
            lower = max(domain[0], upper - step)
    count = max(1, int(round((upper - lower) / step)))
    ticks = tuple(round(lower + index * step, 9) for index in range(count + 1))
    if ticks[-1] < upper:
        ticks = (*ticks, round(upper, 9))
    return {"lower": round(lower, 9), "upper": round(upper, 9), "ticks": ticks,
            "raw_low": raw_low, "raw_high": raw_high, "flat": flat,
            "mode": "auto_committed"}


def opening_trace_viewport(rows, **kwargs):
    """保留旧的范围接口；新渲染器可读取 `opening_trace_viewport_details`。"""
    details = opening_trace_viewport_details(rows, **kwargs)
    return details["lower"], details["upper"]


def opening_trace_delta_rows(rows):
    """把已提交 I/Theta 转成相对本段首点的只读变化序列。

    该小图只回答“本段内分别增减多少”，不替代原值图的 I/Theta/crossing；因此两条
    线都从各自的真实首点归零是刻度定义，而不是状态重置或新的根量。
    """
    source = [dict(row) for row in (rows or ()) if isinstance(row, dict)]
    if not source:
        return []
    try:
        origin_i = float(source[0]["impulse"])
        origin_t = float(source[0]["gate"])
    except (KeyError, TypeError, ValueError):
        return []
    result = []
    for row in source:
        try:
            impulse = float(row["impulse"]) - origin_i
            gate = float(row["gate"]) - origin_t
        except (KeyError, TypeError, ValueError):
            continue
        if math.isfinite(impulse) and math.isfinite(gate):
            result.append({"ts": row.get("ts"), "impulse": impulse, "gate": gate})
    return result

FIELD_METERS = (
    ("P", "互动势能"),
    # E 是超过 H_max 后的爆发余量，不是全部情绪的代名词；根量与 P 仍会在
    # 未越线时正常变化。名称必须避免让演示者误把 E=0 读成“状态没有变化”。
    ("E", "越线张力"),
    ("H_max", "动态爆发容量"),
)

REASON_LABELS = {
    "opening_due": "统一开口候选",
}


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _positive_int(value):
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _meter(key, label, source):
    value = _number((source or {}).get(key))
    return {
        "key": key,
        "label": label,
        "value": round(value, 3),
        # UI 钳制只决定条形长度；原值仍单独保留给读者。
        "bar": max(0.0, min(1.0, value)),
    }


def _turn_silence_anchor(messages):
    """返回“已正式回复后等待用户”的起点，而非用户输入起点。

    用户刚提交、NPC 仍在生成时，不能称作用户沉默，更不能让 F15 提前累积。
    只有最近一条正式 NPC 输出晚于最近用户输入，才有一段可展示/计算的未回应时间。
    """
    latest_user = None
    latest_npc = None
    for index, row in enumerate(messages or ()):
        if not isinstance(row, dict):
            continue
        try:
            ts = int(row.get("ts"))
        except (TypeError, ValueError):
            continue
        role = str(row.get("role") or row.get("sender") or "")
        if role == "user":
            point = (ts, index)
            latest_user = point if latest_user is None else max(latest_user, point)
        elif role == "npc":
            point = (ts, index)
            latest_npc = point if latest_npc is None else max(latest_npc, point)
    # 同毫秒的冻结/极速输出合法；此时 append-only 顺序才是因果顺序。
    if latest_npc is None or (latest_user is not None and latest_npc <= latest_user):
        return None
    return latest_npc[0]


def _urgency_snapshot(authority_receipt):
    """仅投影已提交 P3 receipt；刷新不得从状态重算开口读数。"""
    receipt = authority_receipt if isinstance(authority_receipt, dict) else {}
    if not all(key in receipt for key in ("I_open", "Theta_open", "margin")):
        return {"available": False, "curve": [], "gates": [], "value": 0.0,
                "line": 0.0, "gap_ms": 0, "eligible": False, "forecast": []}
    value = _number(receipt.get("I_open"))
    line = _number(receipt.get("Theta_open"))
    margin = _number(receipt.get("margin"))
    event = receipt.get("event") if isinstance(receipt.get("event"), dict) else {}
    gates = [
        {"key": "margin", "label": "开口余量", "ok": margin >= 0.0,
         "detail": f"{margin:.3f} = 冲动 {value:.3f} − 门限 {line:.3f}"},
    ]
    return {"available": True, "curve": [], "gates": gates,
            "value": round(value, 6), "line": round(line, 6),
            "line_base": round(line, 6),
            "gap_ms": 0,
            "eligible": all(row["ok"] for row in gates),
            "reference_ts": None,
            "settled_at_ms": event.get("observed_at_ms"),
            "horizon_ms": 0,
            "opening_projection": {"formula_versions": dict(receipt.get("formula_versions") or {}),
                                  "q_tick": _number(receipt.get("Q_tick"))},
            "margin": margin, "forecast": []}


def _latest_receipt(action_receipts):
    rows = [row for row in action_receipts or () if isinstance(row, dict)]
    if not rows:
        return {"available": False}
    row = max(rows, key=lambda item: int(item.get("ts", 0) or 0))
    return {"available": True, "source": str(row.get("source") or "reply"),
            "ts": int(row.get("ts", 0) or 0),
            "recall_count": len(row.get("recall_refs") or ())}


def _exact_opening_dispatch(events, action_receipts, authority_receipt):
    """只以同一 authority event 的 committed F16 证明“已投递”。"""
    authority = (authority_receipt.get("event")
                 if isinstance(authority_receipt, dict) else None)
    event_id = str((authority or {}).get("id") or "")
    if not event_id:
        return None
    actions = {str(row.get("action_id") or "")
               for row in action_receipts or () if isinstance(row, dict)}
    for row in reversed(list(events or ())):
        if not isinstance(row, dict) or row.get("kind") != "opening_dispatch_committed":
            continue
        action_id = str(row.get("action_id") or "")
        detail = row.get("detail") if isinstance(row.get("detail"), dict) else {}
        dispatched = detail.get("authority") if isinstance(detail.get("authority"), dict) else {}
        if action_id and action_id in actions and str(dispatched.get("event_id") or "") == event_id:
            return {"action_id": action_id, "ts": row.get("ts")}
    return None


def _trend(telemetry, current_state, tick_history=()):
    """投影正式提交与真实 Actor tick 的根量轨迹；不补帧、不插值。"""
    rows = []

    def append_root_point(ts, state):
        values = state.get("vars") if isinstance(state.get("vars"), dict) else {}
        # Alpha 0.2 之前的 telemetry 只存 P/E。缺失根量不能被补成 0，否则会
        # 在图上伪造“状态被回复清空”的断崖；宁可少画旧点，也不编造历史。
        if "P_need" not in values or "V_rep" not in values:
            return
        rows.append({"ts": int(ts or 0), "P": _number(state.get("P")),
                     "E": _number(state.get("E")), "P_need": _number(values.get("P_need")),
                     "V_rep": _number(values.get("V_rep"))})

    for row in telemetry or ():
        if not isinstance(row, dict) or row.get("kind") != "turn_telemetry":
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        append_root_point(row.get("ts"), state)
    for row in tick_history or ():
        if not isinstance(row, dict):
            continue
        state = row.get("state") if isinstance(row.get("state"), dict) else {}
        append_root_point(row.get("ts"), state)
    rows.sort(key=lambda row: int(row["ts"]))
    values = (current_state or {}).get("vars") or {}
    if "P_need" in values and "V_rep" in values:
        current = {"ts": int((current_state or {}).get("last_ts") or 0),
                   "P": _number((current_state or {}).get("P")),
                   "E": _number((current_state or {}).get("E")),
                   "P_need": _number(values.get("P_need")), "V_rep": _number(values.get("V_rep"))}
        if not rows or rows[-1] != current:
            rows.append(current)
    return rows[-8:]


def build_demo_observatory_snapshot(*, state, constants=None, clock_plan=None,
                                    telemetry=(), messages=(), action_receipts=(), events=(),
                                    actor_tick=None, delivery=None, cadence=None,
                                    now_ms=None, tick_history=(), opening_trace=()):
    """编译一张演示读数快照；纯函数、无 I/O、无副作用。"""
    settled = state if isinstance(state, dict) else {}
    # 数值条只能展示 Actor/Runner 已真实结算的状态。F15 曲线另有明确的墙钟
    # 推演语义；不能让根量条在两次 tick 之间自行 advance 而伪装成状态已经提交。
    snapshot = settled
    variables = snapshot.get("vars") if isinstance(snapshot.get("vars"), dict) else {}
    plan = clock_plan if isinstance(clock_plan, dict) else {}
    last = next((row for row in reversed(list(telemetry or ()))
                 if isinstance(row, dict) and row.get("kind") == "turn_telemetry"), {})
    recall = last.get("recall") if isinstance(last.get("recall"), dict) else {}
    uncertainties = set(last.get("uncertainties") or ()) if isinstance(last, dict) else set()
    measurement = last.get("measurement") if isinstance(last.get("measurement"), dict) else None
    opening = plan.get("opening") if isinstance(plan.get("opening"), dict) else {}
    opening_sources = (opening.get("sources") if isinstance(opening, dict) else {}) or {}
    opening_edge = plan.get("opening_edge") if isinstance(plan.get("opening_edge"), dict) else {}
    next_at = plan.get("at_ms")
    try:
        next_at = int(next_at) if next_at is not None else None
    except (TypeError, ValueError):
        next_at = None
    now = int(now_ms) if now_ms is not None else None
    raw_reasons = list(plan.get("reasons") or ())
    reasons = [REASON_LABELS.get(str(item), str(item)) for item in raw_reasons]
    silence_anchor = _turn_silence_anchor(messages)
    awaiting_formal_reply = bool(messages) and silence_anchor is None
    # F15 的 elapsed time 只属于“NPC 已正式说完、尚未得到回应”的区间。若当前
    # 回合仍在生成，则不给面板一条从用户 Enter 起算的伪开口曲线。
    # 数学锚在用户提交时已建立；但主回复尚未正式送达时，展示层不把生成耗时
    # 伪装成“用户沉默”。正式 reply receipt 到达后会在同一 anchor 上开始追加轨迹。
    authority_receipt = plan.get("opening_authority_receipt")
    urgency = (_urgency_snapshot(authority_receipt)
               if not awaiting_formal_reply else {
                   "available": False, "curve": [], "gates": [], "value": 0.0,
                   "line": 0.0, "gap_ms": 0, "eligible": False, "forecast": [],
                   "reason": "awaiting_formal_reply",
               })
    # F15 已越线后，pending receipt 是唯一正式资格事实。UI 不能再拿自己的
    # 刷新时刻重算一个自然衰减后的值，造成“上方负余量、下方已进入计划”的自相
    # 矛盾；只读展示持久化的 crossing，直到 Actor 正式输出或新 Enter 替换计划。
    is_pending_opening = (
        bool(opening.get("eligible"))
        and next_at is not None
        and (now is None or now < next_at)
        and str(opening_sources.get("formula_version") or "").startswith(
            "opening-authority-receipt-v1")
    )
    if is_pending_opening:
        impulse = _number(opening_sources.get("impulse"))
        gate = _number(opening_sources.get("gate"))
        margin = _number(opening_sources.get("margin"))
        urgency = {
            "available": True, "curve": [], "value": impulse, "line": gate,
            "line_base": gate, "margin": margin, "eligible": True,
            "gap_ms": 0, "reference_ts": opening_sources.get("anchor_ts"),
            "settled_at_ms": opening.get("qualified_at_ms"), "horizon_ms": 0,
            "forecast": [], "locked_crossing": True,
            "gates": [{"key": "margin", "label": "已锁定开口余量", "ok": True,
                       "detail": f"{margin:.3f} = 冲动 {impulse:.3f} − 门限 {gate:.3f}"}],
        }
    opening_view = {
        # P3 计划只读其冻结的 sources；旧 plan 继续明确标 legacy，不能把 base/score
        # 偷换成当前 I/Theta。
        "available": str(opening_sources.get("formula_version") or "").startswith(
            "opening-authority-receipt-v1"),
        "legacy": bool(opening) and not str(
            opening_sources.get("formula_version") or "").startswith(
                "opening-authority-receipt-v1"),
        "eligible": bool(opening.get("eligible")),
        "impulse": _number(opening_sources.get("impulse")),
        "gate": _number(opening_sources.get("gate")),
        "margin": _number(opening_sources.get("margin")),
        "formula_version": str(opening_sources.get("formula_version") or ""),
        "due_at_ms": opening.get("at_ms"),
        "delay_ms": opening.get("delay_ms"),
    }
    # 只对新版本计划呈现离散 crossing receipt；旧档案保持原字段形状。
    if opening_edge:
        opening_view.update({
            "dispatch_eligible": bool(opening_edge.get("dispatch_eligible")),
            "edge_disposition": str(opening_edge.get("disposition") or ""),
            # edge 只是 F15 的离散读数；只有 exact dispatch receipt 才是自主 F16。
            "dispatch": _exact_opening_dispatch(
                events, action_receipts, authority_receipt),
        })
    # 主图的轨迹只来自 Actor 的正式 transition telemetry。保留其顺序而不按 ts
    # 去重：同毫秒的“计划成立 → 正式 receipt”是不同因果事件，不能压成一个点。
    live_trace = []
    for row in opening_trace or ():
        if (not isinstance(row, dict) or not isinstance(row.get("opening"), dict)
                or row["opening"].get("available") is False):
            continue
        try:
            live_trace.append({"ts": int(row.get("ts")), "event": str(row.get("event") or "state"),
                               "impulse": _number(row["opening"].get("impulse")),
                               "gate": _number(row["opening"].get("gate")),
                               "margin": _number(row["opening"].get("margin")),
                               "interval_ms": _positive_int(row["opening"].get("h_next_ms"))})
        except (TypeError, ValueError):
            continue
    # Receipt 是可恢复权威事实；live trace 只在刚启动、尚未有落盘 receipt 时兜底。
    trace = committed_opening_trace(events) or live_trace
    measurement_available = bool((measurement or {}).get("semantic_available"))
    measurement_source = str((measurement or {}).get("semantic_source") or "")
    if measurement_available:
        measurement_status = "本轮已取得语义测量" + (
            f"（{measurement_source}）" if measurement_source else "")
    elif "semantic_projection_unavailable" in uncertainties:
        measurement_status = "本轮无语义测量：仅使用可用降级测量"
    else:
        measurement_status = "本轮语义测量状态未留存（旧遥测）"
    trace = trace[-24:]
    segments = segment_opening_trace(trace)
    return {
        "settled_at_ms": settled.get("last_advance_ms"),
        "projected_at_ms": now,
        "root_meters": [_meter(key, label, variables) for key, label in ROOT_METERS],
        "field_meters": [_meter(key, label, snapshot) for key, label in FIELD_METERS],
        "trend": _trend(telemetry, snapshot, tick_history),
        "urgency": urgency,
        "opening_trace": trace,
        "opening_segments": segments,
        "opening_trace_source": "committed_receipt" if committed_opening_trace(events) else "live_telemetry",
        "user_silence_ms": (None if silence_anchor is None or now is None
                            else max(0, now - silence_anchor)),
        "silence_anchor_ms": silence_anchor,
        "awaiting_formal_reply": awaiting_formal_reply,
        "memory": {
            "candidate_count": int(recall.get("candidate_count", 0) or 0),
            "selected_count": int(recall.get("actual_reference_count", 0) or 0),
            "suppressed_count": int(recall.get("suppressed_count", 0) or 0),
        },
        "measurement": {
            "available": measurement_available,
            # 来源是已提交 telemetry 的只读 provenance；面板不猜模型，也不把
            # “有测量”误画成“语义模型一定可用”。这让录制者能一眼区分 Flash
            # 投影与本地降级，而无需暴露 prompt 或内部投影正文。
            "source": measurement_source,
            "status": measurement_status,
        },
        "clock": {
            "status": str(plan.get("status") or "idle"),
            "version": int(plan.get("version", 0) or 0),
            "next_at_ms": next_at,
            "remaining_ms": (max(0, next_at - now) if next_at is not None and now is not None else None),
            "reasons": reasons,
            "opening_eligible": bool(opening.get("eligible")),
            "opening_at_ms": opening.get("at_ms"),
            "opening_edge": dict(opening_edge),
        },
        "opening": opening_view,
        "tick": dict(actor_tick or {}),
        "delivery": dict(delivery or {}),
        "receipt": _latest_receipt(action_receipts),
    }
