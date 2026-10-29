"""测试专用 AI 用户模拟器 adapter。

它位于 Agent 边界：只把 NPC 的可见输出和测试环境的隐藏 oracle 交给模拟用户模型，返回受限的
下一条用户行为。它不读取 Store、不调用 Runner、不写产品档案；调用方必须把失败显式写入测试
manifest，不能把失败伪装成用户沉默。
"""
from dataclasses import dataclass
import json
import os
import re
from typing import Dict, Iterable, Optional, Tuple


_SIGNALS = {"continue", "neutral", "stop"}
_MAX_DELAY_MS = 86_400_000


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "simulated_user.md")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _strip_fences(raw):
    text = str(raw or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return match.group(1) if match else text


@dataclass(frozen=True)
class SimulatedUserAgentResult:
    """模拟器的受限输出；失败没有伪造文本或延迟。"""

    status: str
    text: Optional[str] = None
    delay_ms: Optional[int] = None
    signal: Optional[str] = None
    error_type: Optional[str] = None

    def as_manifest_record(self):
        return {
            "status": self.status,
            "text": self.text,
            "delay_ms": self.delay_ms,
            "signal": self.signal,
            "error_type": self.error_type,
        }


def build_simulated_user_messages(*, npc_text: str, visible_history: Iterable[Dict[str, object]],
                                  public_condition: Optional[Dict[str, object]],
                                  hidden_oracle: Dict[str, object], allow_stop: bool = True):
    """构造模拟器调用；隐藏 oracle 只出现在 system，不进入 NPC 生成路径。"""
    system = _load_prompt() + "\n\n【隐藏模拟条件，仅供你生成下一步用户行为】\n" + json.dumps(
        hidden_oracle, ensure_ascii=False, sort_keys=True)
    history = []
    for item in visible_history or ():
        if not isinstance(item, dict):
            continue
        role, text = item.get("role"), item.get("text")
        if role in {"user", "npc"} and isinstance(text, str):
            history.append({"role": role, "text": text[:800]})
    user = {
        "public_condition": dict(public_condition or {}),
        "visible_history": history[-8:],
        "latest_npc_text": str(npc_text or "")[:2000],
        "termination_policy": ("may_stop" if allow_stop else "must_continue_this_turn"),
    }
    return system, json.dumps(user, ensure_ascii=False, sort_keys=True)


def parse_simulated_user_response(raw) -> SimulatedUserAgentResult:
    """只接受受限 JSON；多余字段（包括模型复述的隐藏条件）一律不带出。"""
    try:
        payload = json.loads(_strip_fences(raw))
    except Exception:
        return SimulatedUserAgentResult(status="failed", error_type="invalid_json")
    if not isinstance(payload, dict):
        return SimulatedUserAgentResult(status="failed", error_type="invalid_shape")
    signal = str(payload.get("signal") or "").strip()
    if signal not in _SIGNALS:
        return SimulatedUserAgentResult(status="failed", error_type="invalid_signal")
    if signal == "stop":
        return SimulatedUserAgentResult(status="ok", text=None, delay_ms=None, signal=signal)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return SimulatedUserAgentResult(status="failed", error_type="invalid_text")
    try:
        delay_ms = int(payload.get("delay_ms"))
    except (TypeError, ValueError):
        return SimulatedUserAgentResult(status="failed", error_type="invalid_delay")
    if not 0 <= delay_ms <= _MAX_DELAY_MS:
        return SimulatedUserAgentResult(status="failed", error_type="invalid_delay")
    return SimulatedUserAgentResult(status="ok", text=text.strip()[:2000],
                                    delay_ms=delay_ms, signal=signal)


class SimulatedUserAgent:
    """可替换模型客户端的最小 adapter；调用方负责保存调用预算和失败事件。"""

    def __init__(self, agent, *, model: str = "unknown"):
        if agent is None or not hasattr(agent, "call"):
            raise TypeError("SimulatedUserAgent 需要带 call() 的模型客户端")
        self.agent = agent
        self.model = str(model or "unknown")[:120]

    def react(self, *, npc_text: str, visible_history: Iterable[Dict[str, object]],
              public_condition: Optional[Dict[str, object]], hidden_oracle: Dict[str, object],
              allow_stop: bool = True):
        system, user = build_simulated_user_messages(
            npc_text=npc_text, visible_history=visible_history,
            public_condition=public_condition, hidden_oracle=hidden_oracle, allow_stop=allow_stop)
        try:
            raw = self.agent.call(system, user, temperature=0.2, require_json=True)
        except Exception as exc:
            return SimulatedUserAgentResult(status="failed", error_type=type(exc).__name__)
        return parse_simulated_user_response(raw)
