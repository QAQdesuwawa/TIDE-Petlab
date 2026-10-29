"""离线演示 Agent：让界面与完整编排在没有模型或密钥时也可体验。

它不模拟真实模型质量，只返回满足各 Agent JSON 契约的确定性结果；因此适合
GitHub 首次运行、GUI 冒烟和排查本地环境，不能替代真实对话模型。
"""
import json


class DemoClient:
    """实现与 LLMClient 相同的最小 ``call`` 协议。"""

    def __init__(self, agent_tag="llm"):
        self.agent_tag = agent_tag
        self.usage_sink = None
        self.temperature_override = None

    def call(self, system_prompt, user_content, temperature=0.7,
             require_json=False, max_retries=None):
        del system_prompt, user_content, temperature, require_json, max_retries
        tag = self.agent_tag
        if tag == "labor":
            return json.dumps({
                "persona_card": "# 人格卡\n温柔、克制，愿意认真回应对方。",
                "weights": {"Mirroring": 0.5, "Pragmatism": 0.5},
                "ammo": []}, ensure_ascii=False)
        if tag == "extractor":
            return json.dumps({
                "vars": {"E_env": 0.5, "L_self": 0.0, "L_opponent": 0.0,
                         "P_need": 0.5, "V_attr": 0.5, "V_rep": 0.3},
                "tier": "日常", "pain": 0.0, "emotion": "平静"}, ensure_ascii=False)
        if tag in {"preference", "ledger"}:
            key = "weights" if tag == "preference" else "facts"
            return json.dumps({key: {"Counter_Phobic": 0.2, "Mirroring": 0.2,
                                     "Avoidance": 0.2, "Trauma_Recon": 0.2,
                                     "Pragmatism": 0.2} if key == "weights" else [],
                               "contradictions": []}, ensure_ascii=False)
        return json.dumps({"reply": "我在。慢慢说，不着急。",
                           "facts": [], "contradictions": []}, ensure_ascii=False)


def demo_mode_enabled(env=None):
    """仅 ``1/true/yes/on`` 显式开启，避免无意把演示当成真实模型。"""
    source = env if env is not None else __import__("os").environ
    return str(source.get("TIDE_DEMO_MODE", "")).strip().lower() in {
        "1", "true", "yes", "on"
    }


def make_demo_agents(tags=None):
    """返回可直接传入 ``Guild`` / ``Runner`` 的指定演示 provider。"""
    selected = ("labor", "extractor", "responder", "preference", "ledger") \
        if tags is None else tuple(tags)
    return {f"{tag}_agent": DemoClient(tag) for tag in selected}
