"""F16 主回复投递规划的纯合同。

这里没有 Store、Actor、UI 或 pending reply：只把已经结算的根状态快照和两个时间事实
编译为 F16 receipt。实际 message/pending 的提交仍只能由 Runner 完成，deadline 的等待
仍只能由 Actor 完成。
"""
from temporal.delay import decide_delay_seconds
from temporal.output_time import resolve_output_timeline


def plan_reply_delivery(*, delayed, constants, variables, event_delta, baseline, rng,
                        received_ms, ready_ms):
    """返回版本化 F16 规划，不改变任一输入对象。"""
    if delayed:
        delay_sec, delay_receipt = decide_delay_seconds(
            constants, variables, event_delta, baseline, rng, include_receipt=True)
    else:
        # 即时出口不调用节律函数、更不消费 RNG；receipt 仍显式存在，便于追溯。
        delay_sec, delay_receipt = 0, {"mode": "immediate", "delay_sec": 0}
    timeline = resolve_output_timeline(
        received_ms=int(received_ms), ready_ms=int(ready_ms),
        cadence_delay_ms=int(delay_sec) * 1000)
    return {
        "delay_sec": int(delay_sec),
        "delay_receipt": dict(delay_receipt),
        "timeline": timeline,
        "send_at": int(timeline["t_sent"]),
    }
