# agents/llm_client.py
# 自 M.A.N.E. agents/llm_client.py 逐字符照抄（T.I.D.E. 实现设计 §3.1）。
"""LLM 底座：多 Key 密钥池 + 同步/异步双客户端 + 断线重连装甲。

职责边界（低耦合）：仅负责鉴权、传输、重试与空响应校验，
不参与任何业务语义（变量提取、意图解析均由各 Agent 自行处理）。

迭代要点（对齐 MANE OPS 底座，接口保持原版不变）：
- 移除源码内硬编码 Key，改环境变量 / .env（入口统一 load_dotenv）；
- 多 Key 并发：API_KEYS='k1,k2,k3'（逗号分隔）→ 每次调用轮询取 key，
  重试时换下一个 key（绕开单 key 限流）；单 key（API_KEY）行为与旧版一致；
- 指数退避 + 随机抖动（原版固定 sleep 2s）；
- 只重试瞬时错误（429/5xx/超时），400/401 立即失败；429 优先尊重 Retry-After；
- 超时/重试/输出预算可配置（LLM_TIMEOUT / LLM_MAX_RETRIES /
  LLM_MAX_OUTPUT_TOKENS 环境变量覆盖，默认值取自 core/params.py）；
- DeepSeek V4 的短结构化调用默认使用 non-thinking；可用 LLM_THINKING_MODE 覆写，
  非 DeepSeek 兼容服务不发送厂商私有参数；
- call() 同步接口签名与原版完全一致；新增 async_call() 异步接口；
- 成本优化（按 agent 分模型）：MODEL_NAME_<AGENT_TAG> 环境变量可为单个 agent
  指定模型（如 MODEL_NAME_EXTRACTOR=deepseek-v4-flash）；未设置时回落 MODEL_NAME。
"""
import asyncio
import json
import logging
import os
import random
import sys
import threading
import time

import httpx
import openai

from core.params import DEFAULTS as PARAMS


def parse_api_keys(env):
    """API_KEYS='k1,k2,k3'（优先）或 API_KEY='k1' → 去重去空白的密钥列表。"""
    raw = (env.get("API_KEYS") or "").strip() or (env.get("API_KEY") or "").strip()
    keys = [k.strip() for k in raw.split(",") if k.strip()]
    return list(dict.fromkeys(keys))  # 保序去重


def configured_proxy(env, key="LLM_PROXY", fallback_key=None):
    """读取显式代理配置；空值保持 SDK 的既有直连/环境变量行为。

    代理属于传输层，不应被业务编排、词元或 prompt 感知。``fallback_key``
    仅用于 embedding 继承聊天代理这一类同一出口的配置。
    """
    value = (env.get(key) or "").strip()
    if value:
        return value
    if fallback_key:
        return (env.get(fallback_key) or "").strip() or None
    return None


def build_sync_http_client(proxy_url, timeout):
    """有显式代理时构造同步传输；未配置时返回 None 以保持历史行为。"""
    if not proxy_url:
        return None
    return httpx.Client(proxy=proxy_url, timeout=timeout, trust_env=True)


def build_async_http_client(proxy_url, timeout):
    """同步传输的异步对应物。"""
    if not proxy_url:
        return None
    return httpx.AsyncClient(proxy=proxy_url, timeout=timeout, trust_env=True)


def is_transient(exc):
    """共享的传输重试判定；不包含任何聊天或 embedding 业务语义。"""
    status = getattr(exc, "status_code", None)
    if status is not None:
        return status == 429 or status >= 500
    # 超时 / 连接中断 / 未知网络异常：默认视为瞬时错误，允许重试
    return True


def retry_delay(attempt, exc):
    """共享指数退避与 Retry-After 处理，供 OpenAI 兼容传输客户端复用。"""
    base = min(2 ** attempt, 8) + random.uniform(0, 1)
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None) or {}
    retry_after = headers.get("Retry-After")
    if retry_after is not None:
        try:
            return max(base, float(retry_after))
        except ValueError:
            pass
    return base


def _console_safe_print(*args):
    """控制台安全打印：GBK 控制台遇到 emoji 会抛 UnicodeEncodeError——
    后台线程（异步提取）死在打印上会静默吞掉结果。编码失败就剥掉不可编码字符。"""
    try:
        print(*args)
    except UnicodeEncodeError:
        try:
            enc = (sys.stdout.encoding if sys.stdout and sys.stdout.encoding
                   else "ascii")
            safe = [str(a).encode(enc, "ignore").decode(enc) for a in args]
            print(*safe)
        except Exception:
            pass


def empty_response_diagnostic(response):
    """提取空响应的无正文传输元数据，供排障而不写入 prompt/用户内容。"""
    choices = getattr(response, "choices", None) or ()
    choice = choices[0] if choices else None
    message = getattr(choice, "message", None)
    content = getattr(message, "content", None)
    usage = getattr(response, "usage", None)
    return {
        "choices": len(choices),
        "finish_reason": getattr(choice, "finish_reason", None),
        "content_type": type(content).__name__,
        "content_length": len(content) if isinstance(content, str) else None,
        "has_reasoning_content": bool(getattr(message, "reasoning_content", None)),
        "has_refusal": bool(getattr(message, "refusal", None)),
        "has_tool_calls": bool(getattr(message, "tool_calls", None)),
        "prompt_tokens": getattr(usage, "prompt_tokens", None) if usage else None,
        "completion_tokens": getattr(usage, "completion_tokens", None) if usage else None,
    }


class EmptyModelResponseError(ValueError):
    """服务端成功返回但没有可用正文；只携带脱敏传输诊断。"""

    def __init__(self, response):
        self.diagnostic = empty_response_diagnostic(response)
        super().__init__("大模型返回了空数据：" + json.dumps(
            self.diagnostic, ensure_ascii=False, sort_keys=True))


class LLMClient:
    # Runner 通过该能力标记把 usage sink 绑定到单次调用，而非写入共享实例属性。
    supports_call_usage_sink = True
    supports_streaming = True
    supports_stream_restarts = True
    # Runner 仅在该能力明确声明时才把 Actor 的取消闸透传进来，避免破坏第三方
    # responder 的既有 stream_call 合同。
    supports_stream_cancellation = True

    def __init__(self, *, timeout=None, max_retries=None):
        """构造传输客户端；显式参数只覆盖本实例，不改动进程环境。"""
        api_keys = parse_api_keys(os.environ)
        if not api_keys:
            raise RuntimeError(
                "未检测到 API_KEY / API_KEYS。请复制 .env.example 为 .env，并配置任一 "
                "OpenAI 兼容服务（云端 Key 或本地 Ollama / LM Studio）；仅体验界面可设置 "
                "TIDE_DEMO_MODE=1。"
            )
        self.base_url = os.getenv("BASE_URL", "https://api.deepseek.com")
        # 桌面交互的默认目标是低延迟；需要更强文风可按 responder 单独覆写，
        # 不把供应商模型名散落到 Runner / UI / 提示词链路里。
        self.model = os.getenv("MODEL_NAME", "deepseek-v4-flash")
        # 只接受显式项目配置：避免悄悄猜测某个本机代理端口，也不把 VPN
        # 的运行状态耦合进业务层。未设置时让 OpenAI SDK 保持其原有行为。
        self.proxy_url = configured_proxy(os.environ)
        # 部分本地 OpenAI 兼容服务尚未实现 response_format。关闭后仍要求提示词输出
        # JSON，但不向服务端发送该参数，解析器继续承担容错职责。
        self.json_mode = os.getenv("LLM_JSON_MODE", "1").strip().lower() not in {
            "0", "false", "no", "off"
        }

        if timeout is None:
            try:
                timeout = float(os.getenv("LLM_TIMEOUT", str(PARAMS["llm_timeout"])))
            except ValueError:
                timeout = PARAMS["llm_timeout"]
        else:
            timeout = float(timeout)
        if timeout <= 0:
            raise ValueError("timeout 必须大于零")
        if max_retries is None:
            try:
                max_retries = int(os.getenv("LLM_MAX_RETRIES", str(PARAMS["llm_max_retries"])))
            except ValueError:
                max_retries = PARAMS["llm_max_retries"]
        else:
            max_retries = int(max_retries)
        if max_retries < 1:
            raise ValueError("max_retries 必须至少为 1")
        self.timeout_seconds = timeout
        self.default_max_retries = max_retries

        # 每个 key 一套同步/异步客户端（密钥池）
        self.api_keys = api_keys
        # 重试由本类的 call()/async_call() 统一计数与换 key；禁用 SDK 隐式重试，
        # 否则预注册的 max_retries 与单请求超时会被底层再次放大。
        self._clients = []
        self._async_clients = []
        for key in api_keys:
            sync_kwargs = dict(api_key=key, base_url=self.base_url,
                               timeout=self.timeout_seconds, max_retries=0)
            async_kwargs = dict(api_key=key, base_url=self.base_url,
                                timeout=self.timeout_seconds, max_retries=0)
            sync_transport = build_sync_http_client(self.proxy_url, self.timeout_seconds)
            async_transport = build_async_http_client(self.proxy_url, self.timeout_seconds)
            if sync_transport is not None:
                sync_kwargs["http_client"] = sync_transport
            if async_transport is not None:
                async_kwargs["http_client"] = async_transport
            self._clients.append(openai.OpenAI(**sync_kwargs))
            self._async_clients.append(openai.AsyncOpenAI(**async_kwargs))
        # 向后兼容别名：指向第一个 key 的客户端
        self.api_key = api_keys[0]
        self.client = self._clients[0]
        self.async_client = self._async_clients[0]
        self._rr = 0
        self._rr_lock = threading.Lock()

        # 可追溯钩子：调用台账（usage_sink 由编排层注入，默认关闭，行为零影响）。
        self.usage_sink = None
        self.agent_tag = "llm"
        # 编排层温度覆盖（批跑多样性轴）：None = 不覆盖，沿用各 Agent 传入温度
        self.temperature_override = None

    # ------------------------------------------------------------------
    # 密钥池轮询（线程安全）
    # ------------------------------------------------------------------
    def model_for(self, tag=None):
        """生效模型（按 agent 维度）：MODEL_NAME_<TAG> 覆盖 → MODEL_NAME 兜底。

        tag 缺省用实例 agent_tag。env 覆盖只对指定 tag 生效——
        未覆盖的 agent 天然回落主模型。
        """
        tag = (tag or self.agent_tag or "llm").upper()
        explicit = os.getenv(f"MODEL_NAME_{tag}")
        if explicit:
            return explicit
        # 语义投影和 extractor 都是受限 JSON 测量职责。若用户已经明确为 extractor
        # 选了低价模型，却未重复配置 semantic，则复用该选择；写作 responder 永远不
        # 受此影响。显式 MODEL_NAME_SEMANTIC 仍有最高优先级。
        if tag == "SEMANTIC":
            return os.getenv("MODEL_NAME_EXTRACTOR", self.model)
        return self.model

    def max_output_tokens_for(self, tag=None):
        """读取传输输出预算；agent 覆写不改变任何业务状态或提示词语义。

        `LLM_MAX_OUTPUT_TOKENS_<TAG>` 适合把结构化测量位压得更短，或让显式
        长文模式的 responder 提高上限。无效配置保守回退参数中心默认值，避免
        把一次环境拼写错误扩大为桌面请求失败。
        """
        tag = (tag or self.agent_tag or "llm").upper()
        raw = (os.getenv(f"LLM_MAX_OUTPUT_TOKENS_{tag}")
               or os.getenv("LLM_MAX_OUTPUT_TOKENS")
               or str(PARAMS["llm_max_output_tokens"]))
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = int(PARAMS["llm_max_output_tokens"])
        return value if value > 0 else int(PARAMS["llm_max_output_tokens"])

    def thinking_mode_for(self, tag=None):
        """返回 DeepSeek thinking 开关或 ``None``（不发送厂商私有字段）。

        TIDE 的 responder/semantic 都要求短、可解析 JSON；DeepSeek V4 默认 thinking
        会把大量 token 放在 reasoning_content，并可能出现 final content 为空。auto
        只对明确的 DeepSeek V4 端点关闭该模式。其他 OpenAI 兼容服务保持原请求形态。
        """
        tag = (tag or self.agent_tag or "llm").upper()
        raw = (os.getenv(f"LLM_THINKING_MODE_{tag}")
               or os.getenv("LLM_THINKING_MODE")
               or str(PARAMS["llm_thinking_mode"])).strip().lower()
        if raw in {"enabled", "disabled"}:
            return raw
        if raw not in {"", "auto"}:
            return None
        model = self.model_for(tag).lower()
        is_deepseek_v4 = ("api.deepseek.com" in self.base_url.lower()
                          and model.startswith("deepseek-v4-"))
        return "disabled" if is_deepseek_v4 else None

    def _next_sync(self):
        with self._rr_lock:
            client = self._clients[self._rr % len(self._clients)]
            self._rr += 1
            return client

    def _next_async(self):
        with self._rr_lock:
            client = self._async_clients[self._rr % len(self._async_clients)]
            self._rr += 1
            return client

    # ------------------------------------------------------------------
    # 重试策略：指数退避 + 随机抖动；仅瞬时错误重试，永久错误立即失败
    # ------------------------------------------------------------------
    @staticmethod
    def _is_transient(exc):
        return is_transient(exc)

    def _retry_delay(self, attempt, exc):
        return retry_delay(attempt, exc)

    @staticmethod
    def _format_exc(exc):
        status = getattr(exc, "status_code", None)
        return f"[HTTP {status}] {exc}" if status else str(exc)

    def call(self, system_prompt, user_content, temperature=0.7, require_json=False,
             max_retries=None, usage_sink=None):
        """同步调用方法：带有断线重连装甲（接口与原版完全一致）。

        每次尝试轮询取一个 key 的客户端——重试时自动换 key，绕开单 key 限流。
        """
        if max_retries is None:
            max_retries = self.default_max_retries
        response_format = {"type": "json_object"} if require_json and self.json_mode else None
        effective_temperature = temperature if self.temperature_override is None else self.temperature_override

        for attempt in range(max_retries):
            client = self._next_sync()  # 密钥池轮询（含重试换 key）
            try:
                kwargs = dict(
                    model=self.model_for(),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=effective_temperature,
                    max_tokens=self.max_output_tokens_for())
                if response_format is not None:
                    kwargs["response_format"] = response_format
                thinking_mode = self.thinking_mode_for()
                if thinking_mode is not None:
                    kwargs["extra_body"] = {"thinking": {"type": thinking_mode}}
                response = client.chat.completions.create(**kwargs)
                result_text = response.choices[0].message.content
                if not result_text or result_text.strip() == "":
                    raise EmptyModelResponseError(response)
                self._report_usage(response, usage_sink=usage_sink)
                return result_text
            except Exception as exc:
                _console_safe_print(
                    f"⚠️ [LLM 警告] 第 {attempt + 1}/{max_retries} 次请求失败: {self._format_exc(exc)}")
                if attempt >= max_retries - 1 or not self._is_transient(exc):
                    _console_safe_print("❌❌❌ [致命崩溃] LLM 已耗尽重试或命中不可重试错误，底盘击穿！")
                    raise
                _console_safe_print("⏳ 触发断线重连保护，指数退避并切换密钥后重新呼叫大模型...")
                time.sleep(self._retry_delay(attempt, exc))

    def stream_call(self, system_prompt, user_content, temperature=0.7, require_json=False,
                    on_delta=None, on_restart=None, max_retries=None, usage_sink=None,
                    should_continue=None):
        """同步 SSE 调用，逐段交给只读订阅者，最终仍返回完整原文。

        这层不知道 JSON 字段、角色或消息提交；``on_delta`` 仅用于可撤销 UI 草稿。
        若传输失败，已经通知过的片段不代表成功，调用方必须按既有失败/取消路径丢弃。
        """
        if max_retries is None:
            max_retries = self.default_max_retries
        response_format = {"type": "json_object"} if require_json and self.json_mode else None
        effective_temperature = temperature if self.temperature_override is None else self.temperature_override

        def cancelled():
            return callable(should_continue) and not should_continue()

        for attempt in range(max_retries):
            if cancelled():
                return ""
            client = self._next_sync()
            try:
                kwargs = dict(
                    model=self.model_for(),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    temperature=effective_temperature,
                    max_tokens=self.max_output_tokens_for(), stream=True)
                if response_format is not None:
                    kwargs["response_format"] = response_format
                thinking_mode = self.thinking_mode_for()
                if thinking_mode is not None:
                    kwargs["extra_body"] = {"thinking": {"type": thinking_mode}}
                stream, pieces, last_chunk = client.chat.completions.create(**kwargs), [], None
                for chunk in stream:
                    # 新 Enter 到达后，旧流没有任何提交资格。尽快关闭本次 SSE，
                    # 让 Actor 立即开始下一条用户事务，而不是等完整 JSON 收完。
                    if cancelled():
                        close = getattr(stream, "close", None)
                        if callable(close):
                            close()
                        return ""
                    last_chunk = chunk
                    choices = getattr(chunk, "choices", None) or ()
                    choice = choices[0] if choices else None
                    delta = getattr(choice, "delta", None)
                    content = getattr(delta, "content", None)
                    if not content:
                        continue
                    part = str(content)
                    pieces.append(part)
                    if callable(on_delta):
                        on_delta(part)
                result_text = "".join(pieces)
                if not result_text.strip():
                    # SSE 末块未必有完整 response 形态；保持异常不含 prompt 正文。
                    raise ValueError("大模型流式返回了空正文")
                if last_chunk is not None:
                    self._report_usage(last_chunk, usage_sink=usage_sink)
                return result_text
            except Exception as exc:
                if cancelled():
                    return ""
                _console_safe_print(
                    f"⚠️ [LLM 流式警告] 第 {attempt + 1}/{max_retries} 次请求失败: {self._format_exc(exc)}")
                if attempt >= max_retries - 1 or not self._is_transient(exc):
                    _console_safe_print("❌❌❌ [致命崩溃] LLM 流式请求已耗尽重试或命中不可重试错误！")
                    raise
                _console_safe_print("⏳ 流式请求触发断线重连，准备切换密钥重试...")
                # 已吐出的 delta 属于失败连接，不能与下一次完整 JSON 拼接；传输层
                # 只通知撤回，是否/如何展示由上层可选订阅者决定。
                if callable(on_restart):
                    on_restart()
                time.sleep(self._retry_delay(attempt, exc))

    async def async_call(self, system_prompt, user_content, temperature=0.7, require_json=False,
                         max_retries=None, usage_sink=None):
        """异步调用方法：与 call() 同构（密钥池轮询 + 重试换 key）。"""
        if max_retries is None:
            max_retries = self.default_max_retries
        response_format = {"type": "json_object"} if require_json and self.json_mode else None
        effective_temperature = temperature if self.temperature_override is None else self.temperature_override

        for attempt in range(max_retries):
            client = self._next_async()
            try:
                kwargs = dict(
                    model=self.model_for(),
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content}
                    ],
                    temperature=effective_temperature,
                    max_tokens=self.max_output_tokens_for())
                if response_format is not None:
                    kwargs["response_format"] = response_format
                thinking_mode = self.thinking_mode_for()
                if thinking_mode is not None:
                    kwargs["extra_body"] = {"thinking": {"type": thinking_mode}}
                response = await client.chat.completions.create(**kwargs)
                result_text = response.choices[0].message.content
                if not result_text or result_text.strip() == "":
                    raise EmptyModelResponseError(response)
                self._report_usage(response, usage_sink=usage_sink)
                return result_text
            except Exception as exc:
                print(f"⚠️ [异步 LLM 警告] 第 {attempt + 1}/{max_retries} 次请求失败: {self._format_exc(exc)}")
                if attempt >= max_retries - 1 or not self._is_transient(exc):
                    print("❌❌❌ [致命崩溃] 异步 LLM 已耗尽重试或命中不可重试错误！")
                    raise
                await asyncio.sleep(self._retry_delay(attempt, exc))

    # ------------------------------------------------------------------
    # 调用台账（可追溯）：默认关闭，编排层注入 usage_sink 后启用
    # ------------------------------------------------------------------
    def _report_usage(self, response, usage_sink=None):
        sink = self.usage_sink if usage_sink is None else usage_sink
        if sink is None:
            return
        try:
            usage = getattr(response, "usage", None)
            prompt_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            completion_tokens = getattr(usage, "completion_tokens", None) if usage else None
            details = getattr(usage, "prompt_tokens_details", None) if usage else None
            cache_hit = (
                getattr(usage, "prompt_cache_hit_tokens", None)
                if usage else None
            )
            if cache_hit is None and details is not None:
                cache_hit = getattr(details, "cached_tokens", None)
            cache_miss = (
                getattr(usage, "prompt_cache_miss_tokens", None)
                if usage else None
            )
            if (
                cache_miss is None
                and isinstance(prompt_tokens, int)
                and isinstance(cache_hit, int)
            ):
                cache_miss = prompt_tokens - cache_hit
            sink({
                "agent_tag": self.agent_tag,
                "model": self.model_for(),
                "served_model": getattr(response, "model", None),
                "prompt_tokens": prompt_tokens,
                "prompt_cache_hit_tokens": cache_hit,
                "prompt_cache_miss_tokens": cache_miss,
                "completion_tokens": completion_tokens,
                "completed_at_unix_ms": int(time.time() * 1000),
            })
        except Exception as exc:
            # Do not retry a successful model call or expose provider payloads.
            logging.getLogger(__name__).error(
                "usage_receipt_delivery_failed error_type=%s", type(exc).__name__)
