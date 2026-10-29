"""真实模型反馈判读器：当前用户原文 + 短对话尾部 → 可选的确认标签。"""
import json
import os
import re

from core.params import DEFAULTS
from ops.feedback_intent import coerce_feedback_intent


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "feedback_intent.md")
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _strip_fences(raw):
    text = str(raw or "").strip()
    matched = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return matched.group(1) if matched else text


def parse_feedback_intent(raw):
    try:
        return coerce_feedback_intent(json.loads(_strip_fences(raw)))
    except (TypeError, ValueError, json.JSONDecodeError):
        return None


def build_feedback_messages(text, dialogue_tail=None):
    # AUD-016：反馈标签不以 raw dialogue tail 作为隐形历史通道。
    del dialogue_tail
    return _load_prompt(), "【当前用户原文】\n" + str(text or "")


class LLMFeedbackInterpreter:
    """职责独立的 provider；它只给出标签或 None，不修改参数。"""

    def __init__(self, agent):
        self.agent = agent

    def interpret(self, *, text, dialogue_tail):
        system, user = build_feedback_messages(text, dialogue_tail)
        raw = self.agent.call(system, user, temperature=DEFAULTS["extractor_temperature"],
                              require_json=True)
        intent = parse_feedback_intent(raw)
        if intent is None:
            return None
        data = intent.as_record()
        data["source"] = "llm_feedback_interpreter"
        data["model"] = self.agent.model_for("feedback") if hasattr(self.agent, "model_for") else "feedback"
        return coerce_feedback_intent(data)
