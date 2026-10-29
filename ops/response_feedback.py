"""NPC 已说出口的自我表达 → 极小墙钟回流。

该模块不扫描关键词、不决定 NPC 应说什么。它只消费 responder 随定稿回复给出的
连续 ``self_state_delta`` 与逐字证据，统一执行三道硬边界：证据必须出现在回复中、
幅度必须小、用户的 L_self 永远不能由 NPC 输出改动。
"""
from core.drives import calculate_drives
from core.params import RESPONSE_FEEDBACK
from core.vectors import get_drive_vector
from temporal.bus import update_hidden


def _number(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def validated_self_deltas(reply, raw_deltas, evidence, params=None):
    """验证同一回复的自我表达回流，返回 ``(applied, evidence)``。

    evidence 至少一条必须是回复中的逐字短片段；否则整条自报废弃。这样 responder
    不能只在 JSON 里声称发生了关系变化，却没有在实际发出的文本中说出口。
    """
    p = params or RESPONSE_FEEDBACK
    text = str(reply or "")
    evidence = [str(item).strip()[:120] for item in (evidence or []) if str(item).strip()]
    evidence = [item for item in evidence if item in text]
    caps = p.get("max_abs") or {}
    if not evidence or not isinstance(raw_deltas, dict):
        return {key: 0.0 for key in caps}, []
    out = {}
    for key, cap_raw in caps.items():
        cap = max(0.0, _number(cap_raw))
        value = max(-cap, min(cap, _number(raw_deltas.get(key, 0.0))))
        if key == "L_self":
            value = 0.0
        elif key == "L_opponent":
            value = max(0.0, value)  # 已说出口的牌不可在时间线上“收回”。
        out[key] = round(value, 4)
    return out, evidence


def apply_self_deltas(state, constants, deltas, now_ms, *, f06_constants=None):
    """原地写入已验证的极小变化，并刷新依赖状态；不重跑用户事件/词元 rank。"""
    applied = {key: float(value) for key, value in (deltas or {}).items()
               if abs(float(value)) > 0.0}
    if not applied:
        return [], None
    vars_ = state.get("vars") or {}
    for key, value in applied.items():
        if key in vars_:
            vars_[key] = min(1.0, max(0.0, float(vars_[key]) + value))
    drives = calculate_drives(constants, vars_)
    events = []
    # 输出自述的 F05/表达驱动力和 F06 势能可以有不同常量消费面：前者仍是
    # 角色基底，后者接入会话已绑定的有效常量。默认保留纯层旧调用等价行为。
    update_hidden(state, f06_constants if f06_constants is not None else constants,
                  None, events, int(now_ms))
    state["last_drives"] = drives
    state["last_end_drives"] = get_drive_vector(drives)
    return events, drives
