"""按调用绑定 LLM 台账归属，不把角色 Store 写入共享客户端。"""

from framework.model_api import freeze_model_payload, model_data_fingerprint, thaw_model_data
from ops.provider_cost_ledger import build_exact_provider_cost_receipt


def build_priced_usage_sink(sink, *, pricing, pricing_fingerprint, rate_period_for):
    """Opt-in accounting over observed usage, with explicit frozen rate selection.

    Missing evidence remains an unpriced record, not a zero-cost call. The
    caller owns persistence and the completion-time tariff policy; this helper
    neither guesses rates nor changes provider or model behavior.
    """
    if not callable(sink) or not callable(rate_period_for):
        raise TypeError("priced usage requires a sink and rate-period selector")
    frozen = freeze_model_payload(pricing)
    if model_data_fingerprint(frozen) != pricing_fingerprint:
        raise ValueError("pricing fingerprint differs from frozen rate card")

    def record(usage):
        call = thaw_model_data(freeze_model_payload(usage))
        try:
            period = rate_period_for(freeze_model_payload(call))
            receipt = build_exact_provider_cost_receipt(calls=[call], pricing=frozen,
                pricing_fingerprint=pricing_fingerprint, rate_periods=[period])
        except (ValueError, TypeError, KeyError) as exc:
            row = {**call, "cost_status": "unpriced", "pricing_fingerprint": pricing_fingerprint,
                   "cost_error": str(exc)}
        else:
            row = {**call, "cost_status": "priced", "cost_receipt": receipt}
        sink(row)

    return record


class UsageBoundAgent:
    """给支持逐调用 usage sink 的传输 client 绑定一个 Runner-local 收据目标。"""

    def __init__(self, inner, usage_sink):
        self._inner = inner
        self._usage_sink = usage_sink

    def call(self, *args, **kwargs):
        return self._inner.call(*args, usage_sink=self._usage_sink, **kwargs)

    def stream_call(self, *args, **kwargs):
        """流式 responder 与普通调用共享同一份 Runner-local usage 归属。"""
        return self._inner.stream_call(*args, usage_sink=self._usage_sink, **kwargs)

    async def async_call(self, *args, **kwargs):
        return await self._inner.async_call(*args, usage_sink=self._usage_sink, **kwargs)

    def __getattr__(self, name):
        return getattr(self._inner, name)


def bind_usage_sink(agent, usage_sink):
    """返回调用级绑定；旧 stub 继续使用其历史 ``usage_sink`` 协议。

    真正的共享 LLMClient 通过 ``supports_call_usage_sink`` 声明可安全绑定，避免
    最后创建的 Runner 覆盖此前角色的可变 sink。测试 stub 不声明时维持兼容，
    因为它们不承担多角色并发传输语义。
    """
    if agent is None:
        return None
    if bool(getattr(agent, "supports_call_usage_sink", False)):
        return UsageBoundAgent(agent, usage_sink)
    if hasattr(agent, "usage_sink"):
        agent.usage_sink = usage_sink
    return agent
