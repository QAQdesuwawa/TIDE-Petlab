"""T0.1-A 的输出时间 oracle（纯时间事实，尚不提交输出状态）。

这里的职责刻意很窄：由已观测的 ``t_received``、``t_ready`` 和既有节律延迟
计算不可倒流的 ``t_sent``，并在没有输出向量时验证“到发送点的时间推进”与普通
``advance`` 完全等价。它不持久化、不生成 action、不解析回复、更不改变 field。

有向量的 field 续接和跨文件提交顺序必须先通过独立 oracle；不能把它们混进这份
基础时间合同中。
"""
import copy
from core.drives import calculate_drives
from core.params import FALLBACK_VARS
from core.vectors import get_drive_vector
from temporal.bus import advance
from temporal.bus import update_hidden


def resolve_output_timeline(*, received_ms, ready_ms, cadence_delay_ms=0):
    """返回单在飞输出的真实可见时间。

    ``ready`` 是宿主观察到定稿完成的事实，不能早于输入到达；``sent`` 同时受
    定稿完成与既有节律延迟约束。该函数不把模型耗时解释为任何心理变量。
    """
    received = int(received_ms)
    ready = int(ready_ms)
    delay = int(cadence_delay_ms)
    if delay < 0:
        raise ValueError("cadence_delay_ms 不能为负")
    if ready < received:
        raise ValueError("t_ready 不能早于 t_received")
    sent = max(ready, received + delay)
    return {
        "t_received": received,
        "t_ready": ready,
        "t_sent": sent,
        "cadence_delay_ms": delay,
    }


def advance_to_output_time(state, timeline, params=None, *,
                           effective_constants=None, effective_constants_version=None,
                           effective_time_params=None):
    """零向量 oracle：把既有状态纯粹推进至 ``t_sent``。

    返回新 state 与时间收据，绝不原地改变调用者的 state。若当前状态已越过发送
    时刻，说明调用方试图把输出插回已结算的时间线；T0.1-A 必须显式拒绝，不能用
    ``advance`` 的静默零增量掩盖顺序错误。
    """
    sent = int(timeline["t_sent"])
    previous = state.get("last_advance_ms")
    if previous is None:
        previous = state.get("last_ts")
    if previous is not None and sent < int(previous):
        raise ValueError("t_sent 不能早于已结算的时间锚")
    out = copy.deepcopy(state)
    events = []
    advance(out, sent, params=params, events=events,
            effective_constants=effective_constants,
            effective_constants_version=effective_constants_version,
            effective_time_params=effective_time_params)
    return out, {
        "t_received": int(timeline["t_received"]),
        "t_ready": int(timeline["t_ready"]),
        "t_sent": sent,
        "advanced_from_ms": (int(previous) if previous is not None else None),
        "advanced": bool(previous is not None and sent > int(previous)),
        "events": events,
    }


def commit_output_vector(state, constants, *, sent_ms, deltas, params=None,
                         effective_constants=None, effective_constants_version=None,
                         effective_time_params=None, f06_constants=None):
    """纯 temporal 的输出向量提交候选，尚不持久化或接入 Runner。

    已验证微量变化是一个已提交输出事件：直接写入 ``X``，后续由 F08 的根量时间
    推进自然衰减。旧 field/offset 不再参与这条路径；杠杆轴仍是事件性状态。
    """
    timeline = {"t_received": int(sent_ms), "t_ready": int(sent_ms),
                "t_sent": int(sent_ms)}
    out, receipt = advance_to_output_time(
        state, timeline, params=params, effective_constants=effective_constants,
        effective_constants_version=effective_constants_version,
        effective_time_params=effective_time_params)
    values = out.get("vars") or {}
    applied = {}
    for key in values:
        try:
            value = float((deltas or {}).get(key, 0.0))
        except (TypeError, ValueError):
            value = 0.0
        if value:
            applied[key] = value
            values[key] = min(1.0, max(0.0, float(values[key]) + value))
    if applied:
        events = receipt["events"]
        # F05 表现驱动力继续由传入的角色基底常量计算；F06 显隐势能可显式
        # 读取同事务已绑定的 C_effective，不能因输出回流退回 C_base。
        drives = calculate_drives(constants, values)
        update_hidden(out, f06_constants if f06_constants is not None else constants,
                      None, events, int(sent_ms))
        out["last_drives"] = drives
        out["last_end_drives"] = get_drive_vector(drives)
    # ``last_ts`` 是下一段交互墙钟的因果起点，而不是“最近一次用户按 Enter”。
    # 一条已正式送达的 NPC 输出同样是时间总线事件：后续 F14/F15 与用户未回应
    # 时长必须从 t_sent 开始，不能把模型生成/展示时间伪装成用户的沉默。
    # 这只是既有正式 output receipt 的时间锚，不是新心理量或额外门限。
    out["last_ts"] = int(sent_ms)
    receipt["applied_deltas"] = applied
    return out, receipt


def output_state_transition(state, constants, *, timeline, deltas, params=None,
                            effective_constants=None, effective_constants_version=None,
                            effective_time_params=None, f06_constants=None,
                            memory_delta=None, memory_state_receipt=None):
    """构造一条已送达输出的纯状态转换回执，尚不提交任何运行时状态。

    ``X_before`` 严格是输出可见时刻已经结算完墙钟的六轴状态；事件变化只记录
    定稿输出造成的实际（经限幅）差值，而不是把模型等待或 UI 显示时间伪装成
    输出心理事件。返回的候选 state 与既有 ``commit_output_vector`` 同源，供提交边界
    在其完成原子性设计后显式消费；本函数本身不写 memory、store 或消息。
    """
    pre_output, time_receipt = advance_to_output_time(
        state, timeline, params=params, effective_constants=effective_constants,
        effective_constants_version=effective_constants_version,
        effective_time_params=effective_time_params)
    before = {axis: float(pre_output["vars"][axis]) for axis in FALLBACK_VARS}
    after_state, vector_receipt = commit_output_vector(
        pre_output, constants, sent_ms=int(timeline["t_sent"]), deltas=deltas, params=params,
        effective_constants=effective_constants,
        effective_constants_version=effective_constants_version,
        effective_time_params=effective_time_params,
        f06_constants=f06_constants)
    after = {axis: float(after_state["vars"][axis]) for axis in FALLBACK_VARS}
    actual_delta = {axis: after[axis] - before[axis] for axis in FALLBACK_VARS}
    memory_transition = None
    if memory_delta is not None or memory_state_receipt is not None:
        # 与 F08 同一合同：output 的原始事件印痕不能被历史影响污染；但两者在
        # 同一个纯 transition 中得到最终 X，调用方无需、也不得二次写状态。
        from temporal.bus import _memory_delta_receipt
        delta, receipt = _memory_delta_receipt({
            "memory_delta": memory_delta,
            "memory_state_receipt": memory_state_receipt,
        })
        memory_before = dict(after)
        for axis in FALLBACK_VARS:
            after_state["vars"][axis] = min(
                1.0, max(0.0, float(after_state["vars"][axis]) + float(delta[axis])))
        memory_after = {axis: float(after_state["vars"][axis]) for axis in FALLBACK_VARS}
        memory_transition = {
            "version": str(receipt.get("version") or ""),
            "X_before": memory_before,
            "Delta_X_memory": {
                axis: memory_after[axis] - memory_before[axis] for axis in FALLBACK_VARS},
            "X_after": memory_after,
            "selected_memory_ids": list(receipt.get("selected_memory_ids") or ()),
            "single_state_commit": True,
            "derived_only": True,
        }
    return after_state, {
        "version": "output-state-transition-v1",
        "X_before": before,
        "Delta_X_event": actual_delta,
        "X_after": after,
        "timeline": dict(timeline),
        "time_settled_before_event": True,
        "derived_only": True,
        "time_receipt": time_receipt,
        "output_vector_receipt": vector_receipt,
        "memory_state_transition": memory_transition,
    }


def mark_output_state_committed(state, action_id):
    """记录单角色串行输出时间提交水位；同一 action 重放保持幂等。"""
    action = str(action_id)
    if state.get("last_output_state_action_id") == action:
        return False
    state["last_output_state_action_id"] = action
    return True
