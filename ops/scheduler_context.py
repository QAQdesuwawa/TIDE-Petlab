"""单次调度计算的短生命周期根状态快照；不持久化、不拥有规则。

这个边界故意只承载已经结算的 ``X`` 和观察时刻。驱动力、关系几何、反应强度、
未回应计数、B0/D(t) 都属于各自的表达、观测或校准层，不能作为 F14/F15/rhythm 的
“顺手可拿”输入重新回流进调度。
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SchedulerContext:
    now_ms: int
    state: dict


def build_scheduler_context(*, state, now_ms):
    """复制已结算根状态，防止候选计算原地污染 Runner。"""
    # 调度上下文是一次计算的快照。只复制外层 dict 会让 ``context.state``
    # 与 Runner 的 ``state["vars"]`` 共享同一个可变对象；节律这类先计算、
    # 后提交的事务会因此绕过提交边界。这里保留轻量复制，但把可参与根量运算的
    # vars/weights 断开，调用方若要写入必须显式把 context.state 提交回 Runner。
    s = dict(state or {})
    variables = dict(s.get("vars") or {})
    s["vars"] = variables
    if isinstance(s.get("weights"), dict):
        s["weights"] = dict(s["weights"])
    return SchedulerContext(now_ms=int(now_ms), state=s)
