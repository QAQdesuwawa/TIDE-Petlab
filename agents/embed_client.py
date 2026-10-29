# agents/embed_client.py
"""API 嵌入客户端（T5 接线）：openai 兼容 embeddings 端点 + 密钥池轮询。

传输/重试与 ``agents.llm_client`` 同构，但 embedding 可通过 ``EMBED_*`` 使用独立
端点和密钥；未设置时才回落聊天配置。这样更换向量服务不影响 responder。
协议 = embed(text) -> list[float]（runner/memory 零改动，BigramEmbedder 同款）。
"""
import os
import threading
import time

from agents.llm_client import (
    build_sync_http_client,
    configured_proxy,
    is_transient,
    parse_api_keys,
    retry_delay,
)

try:
    import openai
except ImportError:  # 测试环境无 openai 时降级（embed 不可用）
    openai = None


def _parse_embedding_api_keys(env):
    """读取独立 embedding 密钥，未配置时保持聊天密钥的历史回退行为。"""
    raw = (env.get("EMBED_API_KEYS") or "").strip() or \
          (env.get("EMBED_API_KEY") or "").strip()
    if raw:
        return parse_api_keys({"API_KEYS": raw})
    return parse_api_keys(env)


class EmbedClient:
    def __init__(self, model=None, timeout=60.0, max_retries=2):
        if openai is None:
            raise RuntimeError("未安装 openai 包")
        api_keys = _parse_embedding_api_keys(os.environ)
        if not api_keys:
            raise RuntimeError("未检测到 embedding 或聊天 API 密钥")
        self.base_url = (os.getenv("EMBED_BASE_URL") or
                         os.getenv("BASE_URL", "https://api.deepseek.com"))
        # 可用 EMBED_PROXY 单独覆盖；否则与聊天服务走同一显式出口。
        self.proxy_url = configured_proxy(
            os.environ, key="EMBED_PROXY", fallback_key="LLM_PROXY")
        self.model = model or os.getenv("EMBED_MODEL") or os.getenv("MODEL_NAME_EMBED")
        if not self.model:
            raise RuntimeError("未配置 EMBED_MODEL / MODEL_NAME_EMBED 环境变量")
        self.max_retries = max_retries
        self._clients = []
        for key in api_keys:
            kwargs = dict(api_key=key, base_url=self.base_url, timeout=timeout)
            transport = build_sync_http_client(self.proxy_url, timeout)
            if transport is not None:
                kwargs["http_client"] = transport
            self._clients.append(openai.OpenAI(**kwargs))
        self._rr = 0
        self._lock = threading.Lock()

    def _next(self):
        with self._lock:
            c = self._clients[self._rr % len(self._clients)]
            self._rr += 1
        return c

    def embed(self, text):
        """单条文本 → 向量（list[float]）；失败轮换密钥重试，最终抛异常。"""
        last = None
        for attempt in range(self.max_retries + 1):
            try:
                resp = self._next().embeddings.create(
                    model=self.model, input=(text or ""))
                return [float(x) for x in resp.data[0].embedding]
            except Exception as exc:  # noqa: BLE001
                last = exc
                if attempt >= self.max_retries or not is_transient(exc):
                    break
                time.sleep(retry_delay(attempt, exc))
        raise last if last else RuntimeError("embed 失败")

    def __call__(self, text):
        return self.embed(text)
