"""角色消息入口的回应资格投影。

这里不认识角色名称、场景、关键词或“最多几条”。它只把 C/X/本轮已送达输入事件投影为
一次可审计资格。它不负责 F14 的“输出后要不要
自主续发”：收到对端已提交输入是另一种调度语义，防御与推进都可以构成反应，而不是把来信
静默吞掉。
"""
from ops.followup import reactive_reply_signal


def peer_response_eligibility(*, state, constants, variable_deltas, ammo_hits, reaction_parts, cadence):
    """从同一 C/X/e 读出 peer 的反应性资格，不把 F14 当作总开关。

    ``ammo_hits`` 与 ``reaction_parts`` 保留在外层 receipt，供输入测量/观测审计；它们不
    旁路决定结果。候选只读根状态、常量和本轮变量增量；不创建第二套人格状态或测试上限。
    """
    # cadence 保留为调用兼容和只读审计，不得把 B0/D(t) 现场副本送入 peer 资格。
    signal = reactive_reply_signal(state, constants, variable_deltas=variable_deltas)
    energy = sum(abs(float(value or 0.0)) for value in (variable_deltas or {}).values())
    return {
        "version": "peer_response_eligibility_v3",
        "eligible": bool(signal["eligible"]),
        "signal": signal,
        "event_energy": round(energy, 6),
        "cadence_version": (cadence or {}).get("version") if isinstance(cadence, dict) else None,
    }
