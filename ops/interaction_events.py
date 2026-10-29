"""事件—行动因果记录合同（P1）。

这里只定义 append-only 记录的最小形状和确定性 ID；它不是通用事件总线，也不做
调度、检索或状态写入。Actor 仍是调度边界，Runner 仍是事务编排边界。
"""
import math


SCHEMA_VERSION = 1


def output_commit_prepared(*, action_id, source, received_ms, ready_ms,
                           cadence_delay_ms, applied_deltas, params_sha,
                           turn_id=None, self_deltas=None, receipt_deltas=None,
                           receipt_effect=None, state_transition=None,
                           memory_delta=None, memory_state_receipt=None,
                           recall_commit_plan=None, effective_constants=None,
                           effective_constants_version=None):
    """T0.1 的可恢复输出提交载荷（尚未由运行时写入）。

    它只保存已经验证过的向量和时间事实，供后续决定持久化顺序/中断恢复；不含
    回复原文、不做状态写入，也不参与任何选择或提示词。
    """
    from core.params import FALLBACK_VARS
    from temporal.output_time import resolve_output_timeline
    timeline = resolve_output_timeline(
        received_ms=received_ms, ready_ms=ready_ms,
        cadence_delay_ms=cadence_delay_ms)
    allowed = set(FALLBACK_VARS)
    deltas = applied_deltas or {}
    unknown = set(deltas) - allowed
    if unknown:
        raise ValueError("输出向量含未知轴")
    row = {
        "schema_version": SCHEMA_VERSION,
        "kind": "output_commit_prepared",
        "action_id": str(action_id),
        "source": str(source),
        "turn_id": str(turn_id) if turn_id is not None else None,
        "timeline": timeline,
        "applied_deltas": {key: float(value) for key, value in deltas.items()
                           if float(value) != 0.0},
        "params_sha": str(params_sha),
    }
    # 拆开冻结通用 e_out 与逐字证据 e_self：恢复只应用合成后的 applied_deltas，
    # 审计则能区分“已经说出口的释压”与模型自述，不能把后者伪装成前者。
    for key, value in (("self_deltas", self_deltas), ("receipt_deltas", receipt_deltas)):
        if value is not None:
            unknown = set(value) - allowed
            if unknown:
                raise ValueError("输出子向量含未知轴")
            row[key] = {axis: float(amount) for axis, amount in value.items()
                        if float(amount) != 0.0}
    if receipt_effect is not None:
        row["receipt_effect"] = dict(receipt_effect)
    memory_state_transition = None
    if state_transition is not None:
        if not isinstance(state_transition, dict):
            raise ValueError("输出状态回执必须为字典")
        required = ("version", "X_before", "Delta_X_event", "X_after")
        missing = [key for key in required if key not in state_transition]
        if missing:
            raise ValueError("输出状态回执缺少：" + ", ".join(missing))
        if state_transition["version"] != "output-state-transition-v1":
            raise ValueError("输出状态回执版本无效")
        transition = {"version": state_transition["version"]}
        for name in ("X_before", "Delta_X_event", "X_after"):
            values = state_transition[name]
            if not isinstance(values, dict) or set(values) != allowed:
                raise ValueError("输出状态回执必须包含完整六轴")
            normalized = {}
            for axis in FALLBACK_VARS:
                value = float(values[axis])
                if not math.isfinite(value):
                    raise ValueError("输出状态回执必须为有限数值")
                normalized[axis] = value
            transition[name] = normalized
        for axis in FALLBACK_VARS:
            expected = transition["X_before"][axis] + transition["Delta_X_event"][axis]
            if not math.isclose(transition["X_after"][axis], expected,
                                rel_tol=0.0, abs_tol=1e-9):
                raise ValueError("输出状态回执 X_after 与变化量不一致")
        row["state_transition"] = transition
        # P4 的实际记忆影响不是 output 的原始事件印痕，不能塞回上面的三段
        # ``state_transition``。它仅在已验证 memory batch 后，作为独立表现锚保存。
        candidate_memory_transition = state_transition.get("memory_state_transition")
        if candidate_memory_transition is not None:
            if not isinstance(candidate_memory_transition, dict):
                raise ValueError("输出记忆状态回执必须为字典或空")
            memory_state_transition = dict(candidate_memory_transition)
    # 表现层的时间比较必须回到同一版 C 与同一条 F16 完成后的 X；不能拿
    # ``last_end_drives`` 或可变的 profile 当前值补算。这里先把本次 output 状态
    # 结算实际绑定的 C_effective 冻结进 prepared 事务，真正已发送后才由
    # ``output_state_committed`` 追加它对应的 X_after anchor。
    if effective_constants is not None or effective_constants_version is not None:
        if effective_constants is None or not str(effective_constants_version or "").strip():
            raise ValueError("表现锚必须同时提供 C_effective 与其不可变版本")
        from core.effective_constants import CONSTANT_ORDER
        if not isinstance(effective_constants, dict) or set(effective_constants) != set(CONSTANT_ORDER):
            raise ValueError("表现锚 C_effective 必须完整覆盖七常量")
        normalized_constants = {}
        for axis in CONSTANT_ORDER:
            value = float(effective_constants[axis])
            if not math.isfinite(value):
                raise ValueError("表现锚 C_effective 必须为有限数值")
            normalized_constants[axis] = value
        row["expression_binding"] = {
            "version": "expression-root-binding-v1",
            "effective_constants_version": str(effective_constants_version),
            "effective_constants": normalized_constants,
        }
    if memory_delta is not None or memory_state_receipt is not None:
        # prepared 可持有已经冻结的 P4 batch，仍不表示它已经发生；真正状态/recall
        # 提交必须等待 message 已送达后的 Runner 边界。
        from temporal.bus import _memory_delta_receipt
        normalized_delta, receipt = _memory_delta_receipt({
            "memory_delta": memory_delta,
            "memory_state_receipt": memory_state_receipt,
        })
        row["memory_delta"] = normalized_delta
        row["memory_state_receipt"] = dict(receipt)
        # 旧 prepared 没有 C_effective binding 时仍可按原 P4 合同恢复状态；只是
        # 不能事后猜一份 C 来制造表现锚，故安全地不产生 memory card。
        if memory_state_transition is not None and row.get("expression_binding") is not None:
            memory_anchor = memory_expression_anchor(
                committed_ms=int(timeline["t_sent"]), event_kind="npc_output",
                memory_transition=memory_state_transition,
                expression_binding=row.get("expression_binding"))
            # 不能静默把不完整 P4 transition 降级为另一种来源；prepared 既宣称
            # 有 memory batch，就只能在其完整、同 C 的事实锚可成立时留给表现层。
            if memory_anchor is None:
                raise ValueError("输出记忆表现锚必须来自完整 P4 状态转换")
            row["memory_expression_anchor"] = memory_anchor
    if recall_commit_plan is not None:
        plan = []
        for item in tuple(recall_commit_plan or ()):
            if not isinstance(item, (tuple, list)) or len(item) != 3:
                raise ValueError("输出 recall commit plan 格式无效")
            source, record, kwargs = item
            if str(source) not in {"memory", "ledger"} or not isinstance(record, dict) or not isinstance(kwargs, dict):
                raise ValueError("输出 recall commit plan 内容无效")
            plan.append([str(source), dict(record), dict(kwargs)])
        row["recall_commit_plan"] = plan
    return row


def classify_output_commit_recovery(payload, *, message_action_ids,
                                    receipt_action_ids, state_action_ids):
    """按可证明证据分类 T0.1 输出中断状态，绝不猜测补写。"""
    action_id = str((payload or {}).get("action_id") or "")
    if not action_id:
        raise ValueError("输出提交载荷缺少 action_id")
    message = action_id in {str(item) for item in (message_action_ids or ())}
    receipt = action_id in {str(item) for item in (receipt_action_ids or ())}
    state = action_id in {str(item) for item in (state_action_ids or ())}
    if not message:
        return "not_sent"
    if not receipt:
        return "sent_receipt_repair"
    if not state:
        return "sent_state_replay"
    return "complete"


def output_expression_anchor(*, action_id, sent_ms, state, expression_binding):
    """构造一条只读表现锚，供下一次 writer 做同 C 的时间比较。

    它是 F16 已完成后根状态的不可变快照，不是新根量，不进入 P3/F15/F16、记忆
    选择或 tick。缺少 prepared 时冻结的 binding 时宁可返回 ``None``，绝不从当前
    profile、``prev_vars`` 或 UI 猜一份锚。
    """
    if not isinstance(expression_binding, dict):
        return None
    constants = expression_binding.get("effective_constants")
    constants_version = str(expression_binding.get("effective_constants_version") or "").strip()
    if not constants_version or not isinstance(constants, dict):
        return None
    from core.effective_constants import CONSTANT_ORDER
    from core.params import FALLBACK_VARS
    if set(constants) != set(CONSTANT_ORDER):
        return None
    variables = (state or {}).get("vars") if isinstance(state, dict) else None
    if not isinstance(variables, dict) or set(variables) != set(FALLBACK_VARS):
        return None
    normalized_constants = {}
    normalized_variables = {}
    for axis in CONSTANT_ORDER:
        value = float(constants[axis])
        if not math.isfinite(value):
            return None
        normalized_constants[axis] = value
    for axis in FALLBACK_VARS:
        value = float(variables[axis])
        if not math.isfinite(value):
            return None
        normalized_variables[axis] = value
    return {
        "version": "output-expression-anchor-v1",
        "action_id": str(action_id),
        "sent_ms": int(sent_ms),
        "effective_constants_version": constants_version,
        "effective_constants": normalized_constants,
        "X_after": normalized_variables,
    }


def interaction_residual_expression_anchor(*, action_id, turn_id, sent_ms,
                                           event_anchor, output_anchor):
    """冻结一次 F03→直接 F16 的无正文互动残量事实。

    它不把早已被 F16 吸收的 event card 重投给主动 writer。锚只记住这次已完成
    互动在同一 ``C_effective`` 下的起点和 F16 终点；后续 writer 仍须用当下已结算
    ``X_now`` 判断残量是否存在。缺少精确 turn/action 对应或同 C 绑定时保持空白。
    """
    if (not str(turn_id or "").strip() or not isinstance(event_anchor, dict)
            or not isinstance(output_anchor, dict)):
        return None
    from core.effective_constants import CONSTANT_ORDER
    from core.params import FALLBACK_VARS

    event_required = {"version", "event_ms", "event_kind", "effective_constants_version",
                      "effective_constants", "X_before", "Delta_X_event", "X_after"}
    output_required = {"version", "action_id", "sent_ms", "effective_constants_version",
                       "effective_constants", "X_after"}
    if (not event_required <= set(event_anchor) or not output_required <= set(output_anchor)
            or str(event_anchor.get("version")) != "event-expression-anchor-v1"
            or str(output_anchor.get("version")) != "output-expression-anchor-v1"
            or str(output_anchor.get("action_id")) != str(action_id)):
        return None
    try:
        output_sent_ms = int(output_anchor.get("sent_ms"))
        anchor_sent_ms = int(sent_ms)
    except (TypeError, ValueError):
        return None
    if output_sent_ms != anchor_sent_ms:
        return None
    event_version = str(event_anchor.get("effective_constants_version") or "").strip()
    output_version = str(output_anchor.get("effective_constants_version") or "").strip()
    event_constants = event_anchor.get("effective_constants")
    output_constants = output_anchor.get("effective_constants")
    if (not event_version or event_version != output_version
            or not isinstance(event_constants, dict) or not isinstance(output_constants, dict)
            or set(event_constants) != set(CONSTANT_ORDER)
            or set(output_constants) != set(CONSTANT_ORDER)):
        return None

    constants = {}
    vectors = {}
    for axis in CONSTANT_ORDER:
        try:
            left, right = float(event_constants[axis]), float(output_constants[axis])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(left) or not math.isfinite(right) or not math.isclose(
                left, right, rel_tol=0.0, abs_tol=1e-12):
            return None
        constants[axis] = left
    for name, raw in (("X_before_event", event_anchor.get("X_before")),
                      ("X_after_event", event_anchor.get("X_after")),
                      ("X_after_output", output_anchor.get("X_after"))):
        if not isinstance(raw, dict) or set(raw) != set(FALLBACK_VARS):
            return None
        parsed = {}
        for axis in FALLBACK_VARS:
            try:
                value = float(raw[axis])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            parsed[axis] = value
        vectors[name] = parsed
    delta = event_anchor.get("Delta_X_event")
    if not isinstance(delta, dict) or set(delta) != set(FALLBACK_VARS):
        return None
    for axis in FALLBACK_VARS:
        try:
            value = float(delta[axis])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value) or not math.isclose(
                vectors["X_before_event"][axis] + value,
                vectors["X_after_event"][axis], rel_tol=0.0, abs_tol=1e-9):
            return None
    try:
        event_ms = int(event_anchor["event_ms"])
    except (KeyError, TypeError, ValueError):
        return None
    if event_ms > anchor_sent_ms:
        return None
    return {
        "version": "interaction-residual-expression-anchor-v1",
        "action_id": str(action_id),
        "turn_id": str(turn_id),
        "sent_ms": anchor_sent_ms,
        "event_ms": event_ms,
        "effective_constants_version": output_version,
        "effective_constants": constants,
        **vectors,
    }


def memory_expression_anchor(*, committed_ms, event_kind, memory_transition,
                             expression_binding):
    """冻结一次**已经状态提交**的 P4 影响，供后续 writer 做只读表达投影。

    它只接受 temporal bus 已经给出的 ``memory_state_transition``：因此既不能由
    recall candidate、writer、草稿或 UI 制造，也不会把被选记忆的正文／ID交给
    表现层。七常量快照与六轴前后状态同样冻结，避免后来 C 版本改变被误读为
    “记忆让角色变了”。该锚不参与下一次 P4、P3、tick 或状态写入。
    """
    if not isinstance(expression_binding, dict) or not isinstance(memory_transition, dict):
        return None
    from core.effective_constants import CONSTANT_ORDER
    from core.params import FALLBACK_VARS

    constants = expression_binding.get("effective_constants")
    constants_version = str(expression_binding.get("effective_constants_version") or "").strip()
    if (not constants_version or not isinstance(constants, dict)
            or set(constants) != set(CONSTANT_ORDER)):
        return None
    required = {"X_before", "Delta_X_memory", "X_after"}
    if not required <= set(memory_transition):
        return None
    normalized_constants, normalized_vectors = {}, {}
    for axis in CONSTANT_ORDER:
        try:
            value = float(constants[axis])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        normalized_constants[axis] = value
    for name in ("X_before", "Delta_X_memory", "X_after"):
        raw = memory_transition.get(name)
        if not isinstance(raw, dict) or set(raw) != set(FALLBACK_VARS):
            return None
        parsed = {}
        for axis in FALLBACK_VARS:
            try:
                value = float(raw[axis])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            parsed[axis] = value
        normalized_vectors[name] = parsed
    for axis in FALLBACK_VARS:
        expected = (normalized_vectors["X_before"][axis]
                    + normalized_vectors["Delta_X_memory"][axis])
        if not math.isclose(expected, normalized_vectors["X_after"][axis],
                            rel_tol=0.0, abs_tol=1e-9):
            return None
    return {
        "version": "memory-expression-anchor-v1",
        "committed_ms": int(committed_ms),
        "event_kind": str(event_kind),
        "effective_constants_version": constants_version,
        "effective_constants": normalized_constants,
        **normalized_vectors,
    }


def event_expression_anchor(*, event_ms, event_kind, event_transition,
                            expression_binding):
    """冻结一次已经结算的 F03 外部事件，供 writer 做只读表达投影。

    该锚只承认 temporal bus 的 ``state_transition``；不会携带用户正文、语义模型
    理由、候选记忆或 writer 草稿。即使直接回复被取消，已提交用户输入的 F03 仍是
    共同历史；相反，NPC 自己的输出只能在其 F16 完成后成为下一轮事实，不能倒灌
    指导那条已写完的文本。
    """
    if not isinstance(expression_binding, dict) or not isinstance(event_transition, dict):
        return None
    from core.effective_constants import CONSTANT_ORDER
    from core.params import FALLBACK_VARS

    constants = expression_binding.get("effective_constants")
    constants_version = str(expression_binding.get("effective_constants_version") or "").strip()
    if (not constants_version or not isinstance(constants, dict)
            or set(constants) != set(CONSTANT_ORDER)):
        return None
    required = {"X_before", "Delta_X_event", "X_after"}
    if not required <= set(event_transition):
        return None
    normalized_constants, normalized_vectors = {}, {}
    for axis in CONSTANT_ORDER:
        try:
            value = float(constants[axis])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        normalized_constants[axis] = value
    for name in ("X_before", "Delta_X_event", "X_after"):
        raw = event_transition.get(name)
        if not isinstance(raw, dict) or set(raw) != set(FALLBACK_VARS):
            return None
        parsed = {}
        for axis in FALLBACK_VARS:
            try:
                value = float(raw[axis])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            parsed[axis] = value
        normalized_vectors[name] = parsed
    for axis in FALLBACK_VARS:
        expected = (normalized_vectors["X_before"][axis]
                    + normalized_vectors["Delta_X_event"][axis])
        if not math.isclose(expected, normalized_vectors["X_after"][axis],
                            rel_tol=0.0, abs_tol=1e-9):
            return None
    return {
        "version": "event-expression-anchor-v1",
        "event_ms": int(event_ms),
        "event_kind": str(event_kind),
        "effective_constants_version": constants_version,
        "effective_constants": normalized_constants,
        **normalized_vectors,
    }


def time_expression_anchor(*, settled_ms, event_kind, time_transition,
                           expression_binding):
    """冻结一次已经提交的 F08 根时间变化，供 writer 做只读表达投影。

    它只接收 F08 产生的完整时间 receipt，不接受 UI preview、typing、timer 回调、
    草稿或模型完成时刻。这里没有“时间到了所以说话”的权力：动态 tick 仍是 F14/F15
    唯一采样/发送入口；该锚只描述一次已经发生的墙钟状态变化。
    """
    if not isinstance(expression_binding, dict) or not isinstance(time_transition, dict):
        return None
    from core.effective_constants import CONSTANT_ORDER
    from core.params import FALLBACK_VARS

    constants = expression_binding.get("effective_constants")
    constants_version = str(expression_binding.get("effective_constants_version") or "").strip()
    if (not constants_version or not isinstance(constants, dict)
            or set(constants) != set(CONSTANT_ORDER)):
        return None
    required = {"X_before", "Delta_X_time", "X_after", "elapsed_ms"}
    if not required <= set(time_transition):
        return None
    try:
        elapsed_ms = int(time_transition["elapsed_ms"])
    except (TypeError, ValueError):
        return None
    if elapsed_ms <= 0:
        return None
    normalized_constants, normalized_vectors = {}, {}
    for axis in CONSTANT_ORDER:
        try:
            value = float(constants[axis])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        normalized_constants[axis] = value
    for name in ("X_before", "Delta_X_time", "X_after"):
        raw = time_transition.get(name)
        if not isinstance(raw, dict) or set(raw) != set(FALLBACK_VARS):
            return None
        parsed = {}
        for axis in FALLBACK_VARS:
            try:
                value = float(raw[axis])
            except (TypeError, ValueError):
                return None
            if not math.isfinite(value):
                return None
            parsed[axis] = value
        normalized_vectors[name] = parsed
    for axis in FALLBACK_VARS:
        expected = (normalized_vectors["X_before"][axis]
                    + normalized_vectors["Delta_X_time"][axis])
        if not math.isclose(expected, normalized_vectors["X_after"][axis],
                            rel_tol=0.0, abs_tol=1e-9):
            return None
    return {
        "version": "time-expression-anchor-v1",
        "settled_ms": int(settled_ms),
        "event_kind": str(event_kind),
        "elapsed_ms": elapsed_ms,
        "effective_constants_version": constants_version,
        "effective_constants": normalized_constants,
        **normalized_vectors,
    }


def output_state_committed(*, action_id, ts, recovery=False, expression_anchor=None,
                           interaction_residual_anchor=None):
    """输出时间状态已落盘后的审计行。

    这不是第二份 state，也不含心理解释；它只让较早 action 在单一 watermark
    被后续 action 覆盖后，仍能被恢复器证明为已完成。调用方必须先保存带
    ``last_output_state_action_id`` 的 state，再追加本行。
    """
    row = {
        "schema_version": SCHEMA_VERSION,
        "kind": "output_state_committed",
        "action_id": str(action_id),
        "ts": int(ts),
    }
    if recovery:
        row["recovery"] = {"kind": "sent_state_replay"}
    if expression_anchor is not None:
        if not isinstance(expression_anchor, dict):
            raise ValueError("表现锚必须为字典")
        required = {"version", "action_id", "sent_ms", "effective_constants_version",
                    "effective_constants", "X_after"}
        if not required <= set(expression_anchor):
            raise ValueError("表现锚字段不完整")
        if str(expression_anchor["action_id"]) != str(action_id) or int(expression_anchor["sent_ms"]) != int(ts):
            raise ValueError("表现锚必须属于当前输出提交")
        row["expression_anchor"] = dict(expression_anchor)
    if interaction_residual_anchor is not None:
        if not isinstance(interaction_residual_anchor, dict):
            raise ValueError("互动残量表现锚必须为字典")
        required = {"version", "action_id", "turn_id", "sent_ms", "event_ms",
                    "effective_constants_version", "effective_constants",
                    "X_before_event", "X_after_event", "X_after_output"}
        if not required <= set(interaction_residual_anchor):
            raise ValueError("互动残量表现锚字段不完整")
        if (str(interaction_residual_anchor["version"])
                != "interaction-residual-expression-anchor-v1"
                or str(interaction_residual_anchor["action_id"]) != str(action_id)
                or int(interaction_residual_anchor["sent_ms"]) != int(ts)):
            raise ValueError("互动残量表现锚必须属于当前输出提交")
        row["interaction_residual_anchor"] = dict(interaction_residual_anchor)
    return row


def output_state_recovery_skipped(*, action_id, ts, state_anchor_ms):
    """记录无法按单调时间线补放的历史输出状态。

    这只是一条恢复诊断：消息与发送收据仍保留，且不会把较早的输出向量硬塞进
    已前进的状态锚。它让下一次启动知道该异常历史已被明确隔离，而不是反复失败。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "output_state_recovery_skipped",
        "action_id": str(action_id),
        "ts": int(ts),
        "reason": "out_of_order_t_sent",
        "state_anchor_ms": int(state_anchor_ms),
    }


def make_turn_id(ts, sequence):
    """在同一角色的 append-only 用户回合流中生成可重放 ID。"""
    sequence = int(sequence)
    if sequence < 1:
        raise ValueError("turn sequence 必须从 1 开始")
    return f"turn:{int(ts)}:{sequence}"


def make_action_id(ts, sequence):
    """在同一角色的 append-only NPC 行动流中生成可重放 ID。"""
    sequence = int(sequence)
    if sequence < 1:
        raise ValueError("action sequence 必须从 1 开始")
    return f"action:{int(ts)}:{sequence}"


def turn_opened(*, turn_id, ts, params_sha):
    """构造一条用户回合开始事件。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "turn_opened",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "params_sha": str(params_sha),
    }


def input_received(*, turn_id, ts, text, params_sha):
    """用户原文到达即追加的事实事件。

    它不取代带 S/R/词元的 ``messages.jsonl`` 正式消息行；后者在同一事务提交时才有
    完整测量字段。这个入口记录保证生成中断时原文仍在 append-only 时间线上。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "input_received",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "text": str(text),
        "params_sha": str(params_sha),
    }


def input_measurement_committed(*, turn_id, ts, delta_x, measurement,
                               state_anchor_ms, params_sha, source_contributions=None,
                               expression_anchor=None, time_expression_anchor=None,
                               delivery_reference=None, participant_source_kind=None,
                               preference_evidence=None, response_policy=None):
    """F03 已成为共同历史的最小收据。

    用户原文由 ``input_received`` 记录；本行只补充其已实际作用过的六轴增量、
    测量来源、逐来源六轴贡献和状态时间锚。它不含 prompt、回复草稿或模型正文，因而既能在慢
    responder/新 Enter 场景下证明输入未丢，也不能把未发送 NPC 输出伪装为事实。
    """
    from core.params import FALLBACK_VARS

    supplied = delta_x if isinstance(delta_x, dict) else {}
    unknown = set(supplied) - set(FALLBACK_VARS)
    if unknown:
        raise ValueError("F03 Delta_X 含未知轴")
    contributions = {}
    for source, values in dict(source_contributions or {}).items():
        if not isinstance(values, dict):
            continue
        source_unknown = set(values) - set(FALLBACK_VARS)
        if source_unknown:
            raise ValueError("F03 来源贡献含未知轴")
        contributions[str(source)] = {
            axis: float(values.get(axis, 0.0) or 0.0) for axis in FALLBACK_VARS
        }
    row = {
        "schema_version": SCHEMA_VERSION,
        "kind": "input_measurement_committed",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "delta_x": {axis: float(supplied.get(axis, 0.0) or 0.0)
                    for axis in FALLBACK_VARS},
        "measurement": dict(measurement or {}),
        # 这是 F03 已采用的连续输入来源，不是新的状态或调度输入；保留它使
        # 慢 responder 尚未返回时也可审计 semantic/ammo/dictionary 是否真进总线。
        "source_contributions": contributions,
        "state_anchor_ms": (None if state_anchor_ms is None else int(state_anchor_ms)),
        "params_sha": str(params_sha),
    }
    if expression_anchor is not None:
        if not isinstance(expression_anchor, dict):
            raise ValueError("F03 表现锚必须为字典")
        required = {"version", "event_ms", "event_kind", "effective_constants_version",
                    "effective_constants", "X_before", "Delta_X_event", "X_after"}
        if not required <= set(expression_anchor):
            raise ValueError("F03 表现锚字段不完整")
        if (str(expression_anchor["version"]) != "event-expression-anchor-v1"
                or int(expression_anchor["event_ms"]) != int(ts)):
            raise ValueError("F03 表现锚必须属于当前输入结算")
        row["expression_anchor"] = dict(expression_anchor)
    if time_expression_anchor is not None:
        if not isinstance(time_expression_anchor, dict):
            raise ValueError("F08 表现锚必须为字典")
        required = {"version", "settled_ms", "event_kind", "elapsed_ms",
                    "effective_constants_version", "effective_constants",
                    "X_before", "Delta_X_time", "X_after"}
        if not required <= set(time_expression_anchor):
            raise ValueError("F08 表现锚字段不完整")
        if (str(time_expression_anchor["version"]) != "time-expression-anchor-v1"
                or int(time_expression_anchor["settled_ms"]) != int(ts)):
            raise ValueError("F08 表现锚必须属于当前输入结算")
        row["time_expression_anchor"] = dict(time_expression_anchor)
    # 新 Participant/Conversation 通路只附 source event 已证明的 exact lineage；它不
    # 参与 F03 测量，也不让 legacy 输入伪装成一条新式 delivery。
    if delivery_reference is not None:
        if not isinstance(delivery_reference, dict):
            raise ValueError("F03 delivery_reference 必须为字典")
        required = {"event_id", "conversation_id", "delivery_id",
                    "sender_participant_id", "membership_version"}
        if set(delivery_reference) != required:
            raise ValueError("F03 delivery_reference 字段不完整")
        normalized = {
            "event_id": str(delivery_reference["event_id"]),
            "conversation_id": str(delivery_reference["conversation_id"]),
            "delivery_id": str(delivery_reference["delivery_id"]),
            "sender_participant_id": str(delivery_reference["sender_participant_id"]),
            "membership_version": int(delivery_reference["membership_version"]),
        }
        if not all(normalized[key] for key in (
                "event_id", "conversation_id", "delivery_id", "sender_participant_id")):
            raise ValueError("F03 delivery_reference ID 不能为空")
        if normalized["membership_version"] < 1:
            raise ValueError("F03 delivery_reference membership_version 无效")
        row["delivery_reference"] = normalized
    if participant_source_kind is not None:
        source_kind = str(participant_source_kind).strip().lower()
        if source_kind not in {"human", "npc", "simulation"}:
            raise ValueError("F03 participant_source_kind 无效")
        if not isinstance(preference_evidence, bool):
            raise ValueError("F03 preference_evidence 必须为布尔值")
        row["participant_source_kind"] = source_kind
        row["preference_evidence"] = preference_evidence
    if response_policy is not None:
        policy = str(response_policy).strip().lower()
        if policy not in {"required", "eligible"}:
            raise ValueError("F03 response_policy 只能是 required 或 eligible")
        row["response_policy"] = policy
    elif delivery_reference is not None:
        raise ValueError("canonical delivery 的 F03 必须保存 response_policy")
    return row


def input_aborted(*, turn_id, ts, reason, params_sha):
    """主回复在提交前被新的用户提交取代时的无正文审计事实。

    它不代表一条用户消息、不会进入 dialogue tail、记忆或标定样本。原始输入的
    生命周期由调用方的本地工作态负责；此行只保留可追溯的中止边界。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "input_aborted",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "reason": str(reason),
        "params_sha": str(params_sha),
    }


def semantic_projected(*, turn_id, ts, projection, params_sha):
    """一次输入语义投影的可回放记录；以后复算必须追加 revision，不能覆写。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "semantic_projected",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "projection": dict(projection),
        "params_sha": str(params_sha),
    }


def output_semantic_measurement_committed(*, action_id, turn_id, ts, delta_x,
                                          measurement, params_sha,
                                          state_transition=None,
                                          memory_delta=None,
                                          memory_state_receipt=None):
    """F16 后对同一已提交 NPC 输出的 Agent 1 测量事实。

    它不保存正文、prompt、草稿或 writer token。``delta_x`` 是该 action 唯一正式
    output-event 根增量；writer 的 ``e_self`` 和旧通用 release 只能留在 prepared
    审计字段，不能替代本行或再次写入根状态。
    """
    from core.params import FALLBACK_VARS
    supplied = delta_x if isinstance(delta_x, dict) else {}
    unknown = set(supplied) - set(FALLBACK_VARS)
    if unknown:
        raise ValueError("F16 output semantic Delta_X 含未知轴")
    row = {
        "schema_version": SCHEMA_VERSION,
        "kind": "output_semantic_measurement_committed",
        "action_id": str(action_id),
        "turn_id": str(turn_id) if turn_id is not None else None,
        "ts": int(ts),
        "delta_x": {axis: float(supplied.get(axis, 0.0) or 0.0)
                    for axis in FALLBACK_VARS},
        "measurement": dict(measurement or {}),
        "params_sha": str(params_sha),
    }
    if state_transition is not None:
        row["state_transition"] = dict(state_transition)
    if memory_delta is not None:
        row["memory_delta"] = dict(memory_delta)
    if memory_state_receipt is not None:
        row["memory_state_receipt"] = dict(memory_state_receipt)
    return row


def reference_frame_resolved(*, turn_id, ts, reference_frame, params_sha):
    """当前回合引用归属的独立审计行；不代表任何历史事实被改写。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "reference_frame_resolved",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "reference_frame": dict(reference_frame),
        "params_sha": str(params_sha),
    }


def extractor_observed(*, source_turn_id, source_ts, observed_at_ms, extraction,
                       params_sha):
    """记录 extractor 对某条既有用户输入完成的异步测量。

    它只陈述观测结果，不代表该结果已经参与后续输入计算；消费关系由
    ``extractor_applied`` 单独记录，避免把观察与因果混为一条事件。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "extractor_observed",
        "turn_id": str(source_turn_id),
        "source_turn_id": str(source_turn_id),
        "source_ts": int(source_ts),
        "observed_at_ms": int(observed_at_ms),
        "extraction": dict(extraction or {}),
        "params_sha": str(params_sha),
    }


def extractor_applied(*, source_turn_id, source_ts, consumer_turn_id, ts,
                      params_sha):
    """记录一条已归档 extractor 观察在下一用户回合被消费。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "extractor_applied",
        "turn_id": str(consumer_turn_id),
        "source_turn_id": str(source_turn_id),
        "source_ts": int(source_ts),
        "consumer_turn_id": str(consumer_turn_id),
        "ts": int(ts),
        "params_sha": str(params_sha),
    }


def extractor_failed(*, source_turn_id, source_ts, observed_at_ms, error_type,
                     params_sha):
    """记录 extractor 调用失败；失败不阻断已完成的当前回复事务。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "extractor_failed",
        "turn_id": str(source_turn_id),
        "source_turn_id": str(source_turn_id),
        "source_ts": int(source_ts),
        "observed_at_ms": int(observed_at_ms),
        "error_type": str(error_type),
        "params_sha": str(params_sha),
    }


def user_feedback(*, turn_id, ts, feedback, attribution, b0, params_sha, source="explicit"):
    """用户显式反馈的 append-only 事实；不改写旧消息、旧 reward 或参数。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "user_feedback",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "feedback": str(feedback),
        "source": str(source),
        "action_id": attribution.get("action_id"),
        "attribution_confidence": str(attribution.get("attribution_confidence", "none")),
        "candidate_action_ids": [str(item) for item in
                                 (attribution.get("candidate_action_ids") or [])],
        "action_signature": dict(attribution.get("action_signature") or {}),
        "b0": dict(b0 or {}),
        "params_sha": str(params_sha),
    }


def cadence_feedback(*, feedback_id, ts, feedback, attribution, b0, cadence,
                     params_sha, source="ui_cadence", presentation=None):
    """只读节律体验标签：关联唯一已发送 action 与当时可回放的计划快照。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "cadence_feedback",
        "feedback_id": str(feedback_id),
        "ts": int(ts),
        "feedback": str(feedback),
        "source": str(source),
        "action_id": attribution.get("action_id"),
        "attribution_confidence": str(attribution.get("attribution_confidence", "none")),
        "candidate_action_ids": [str(item) for item in
                                 (attribution.get("candidate_action_ids") or [])],
        "action_signature": dict(attribution.get("action_signature") or {}),
        "b0": dict(b0 or {}),
        "cadence": dict(cadence or {}),
        "presentation": dict(presentation or {}),
        "params_sha": str(params_sha),
    }


def feedback_interpreted(*, turn_id, ts, intent, params_sha):
    """语义层确认的反馈意图收据；不含用户原文，不承担参数更新。"""
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "feedback_interpreted",
        "turn_id": str(turn_id),
        "ts": int(ts),
        "label": str(intent.get("label") or ""),
        "confidence": float(intent.get("confidence", 0.0)),
        "source": str(intent.get("source") or "feedback_interpreter"),
        "model": str(intent.get("model") or "unknown"),
        "params_sha": str(params_sha),
    }


def npc_self_expressed(*, action_id, ts, source, turn_id, applied_deltas,
                       evidence, params_sha):
    """NPC 定稿中明确说出的自身立场，写入极小状态回流的审计事实。

    这不是用户输入事件，也不是奖励记录；只记已经通过逐字证据校验的实际增量。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "npc_self_expressed",
        "action_id": str(action_id),
        "ts": int(ts),
        "source": str(source or "reply"),
        "turn_id": (str(turn_id) if turn_id is not None else None),
        "applied_deltas": {str(k): float(v) for k, v in (applied_deltas or {}).items()},
        "evidence": [str(item) for item in (evidence or [])],
        "params_sha": str(params_sha),
    }


def npc_output_discharged(*, action_id, ts, source, turn_id, applied_deltas,
                           receipt_effect, params_sha):
    """一条已实际发送输出的通用根状态后效应审计行。

    这里不含原文、词元或人格标签；`receipt_effect` 仅保留根向量投影的数值收据。
    它与 ``npc_self_expressed`` 分离，避免把系统的 e_out 错记为模型的自述。
    """
    return {
        "schema_version": SCHEMA_VERSION,
        "kind": "npc_output_discharged",
        "action_id": str(action_id),
        "ts": int(ts),
        "source": str(source or "reply"),
        "turn_id": (str(turn_id) if turn_id is not None else None),
        "applied_deltas": {str(k): float(v) for k, v in (applied_deltas or {}).items()
                           if float(v) != 0.0},
        "receipt_effect": dict(receipt_effect or {}),
        "params_sha": str(params_sha),
    }


def action_receipt(*, action_id, ts, source, params_sha, turn_id=None,
                   cause_id=None, recall_mode=None, recall_refs=None,
                   state_snapshot_turn=None, recall_strategy=None,
                   calibration_snapshot=None):
    """构造 NPC 消息真正发送后的行动收据。

    ``recall_refs`` 记录现行实现实际已注入的召回来源；P2 统一裁决上线后会收敛为
    单一候选或空值。这里不评价效果，亦不修改任何状态。
    """
    rec = {
        "schema_version": SCHEMA_VERSION,
        "action_id": str(action_id),
        "ts": int(ts),
        "source": str(source or "reply"),
        "params_sha": str(params_sha),
        "recall_refs": [dict(item) for item in (recall_refs or [])],
    }
    if turn_id is not None:
        rec["turn_id"] = str(turn_id)
    if cause_id is not None:
        rec["cause_id"] = str(cause_id)
    if recall_mode is not None:
        rec["recall_mode"] = str(recall_mode)
    if recall_strategy is not None:
        rec["recall_strategy"] = str(recall_strategy)
    if state_snapshot_turn is not None:
        rec["state_snapshot_turn"] = int(state_snapshot_turn)
    if calibration_snapshot is not None:
        # 只接受由动作提交边界构造的无正文快照；receipt 不能借此变成 prompt 旁路。
        rec["calibration_snapshot"] = dict(calibration_snapshot)
    return rec
