"""真实模型语义投影 provider；请求与解析只由显式注入 codec 持有。"""
from dataclasses import replace
import math

class LLMSemanticProjector:
    """使用职责独立的 LLM Client 生成单回合连续语义投影。"""

    def __init__(self, agent, *, codec):
        self.agent = agent
        self.codec = codec
        if (not callable(getattr(self.codec, "build_request", None))
                or not callable(getattr(self.codec, "parse_response", None))):
            raise ValueError("semantic provider requires a request/parser codec")
        temperature = getattr(self.codec, "temperature", None)
        if (isinstance(temperature, bool) or not isinstance(temperature, (int, float))
                or not math.isfinite(temperature) or temperature < 0):
            raise ValueError("semantic codec requires a finite non-negative temperature")
        if not isinstance(getattr(self.codec, "require_json", None), bool):
            raise ValueError("semantic codec requires a boolean require_json setting")

    def project(self, *, text, dialogue_tail, recall_hint, state_vars,
                event_kind="user_input"):
        # state_vars 保留在通用接口里供本地模型实现使用；远端提示不传它，避免把 NPC
        # 现状伪装成用户心理事实。投影只从当前原文和已获资格 evidence 得出。
        del state_vars, dialogue_tail
        system, user = self.codec.build_request(
            text=text, recall_hint=recall_hint, event_kind=event_kind)
        raw = self.agent.call(system, user,
                              temperature=self.codec.temperature,
                              require_json=self.codec.require_json)
        projection = self.codec.parse_response(raw, event_kind=event_kind)
        if projection is None:
            return None
        # Provider 身份由运行时决定，不能相信模型自报来源/模型名。
        # 保留首次 provider JSON 的无正文 axis-presence receipt；运行时身份不能相信模型自报。
        return replace(
            projection,
            source="llm_semantic_projector",
            model=(self.agent.model_for("semantic")
                   if hasattr(self.agent, "model_for") else "semantic"),
        )
