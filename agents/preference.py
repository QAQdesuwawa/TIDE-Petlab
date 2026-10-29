# agents/preference.py
"""开局偏好先验：用户喜好描述 → 5 维图式权重（架构文档 §14.1，权重调整器，非选择题）。

用户用自己的话说喜好，不填问卷、不感到被侧写。解析复用 ops.fitting.parse_refine（同 JSON 形状）。
"""
import os

from ops.fitting import parse_refine


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "preference.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_preference_messages(preference_text):
    system = _load_prompt()
    user = "【用户描述的陪伴偏好】\n" + (preference_text or "")
    return system, user


def parse_preference(raw):
    return parse_refine(raw)
