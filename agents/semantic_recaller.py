"""受限语义候选重排 provider：只评估调用方提出的候选。"""
import json
import os
import re

from core.params import DEFAULTS
from ops.semantic_recall import coerce_rerank_matches


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts",
                        "semantic_recall.md")
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read()


def _strip_fences(raw):
    text = str(raw or "").strip()
    match = re.search(r"```(?:json)?\s*(.*?)\s*```", text, re.S)
    return match.group(1) if match else text


def parse_semantic_recall(raw, *, candidate_ids, user_text):
    """模型 JSON → 封闭候选 ID 的纯契约；失败返回空元组。"""
    try:
        payload = json.loads(_strip_fences(raw))
    except (TypeError, ValueError, json.JSONDecodeError):
        return ()
    return coerce_rerank_matches(payload, candidate_ids=candidate_ids, user_text=user_text)


def build_semantic_recall_messages(*, text, dialogue_tail, candidates):
    """候选摘要由本地调用方提供；不接受 raw history 或模型自造的稳定 ID。"""
    # AUD-016：只允许本地 F10 已提出的 candidate summary 作为历史证据。
    del dialogue_tail
    rows = []
    for candidate in candidates:
        if not isinstance(candidate, dict):
            raise ValueError("候选摘要无效")
        candidate_id = str(candidate.get("candidate_id") or "").strip()
        summary = str(candidate.get("summary") or "").strip()
        if not candidate_id or not summary:
            raise ValueError("候选摘要缺少 ID 或文本")
        rows.append({"candidate_id": candidate_id, "summary": summary[:360]})
    return _load_prompt(), (
        "【当前用户原文】\n" + str(text or "")
        + "\n\n【候选记忆（只能从中选择）】\n"
        + json.dumps(rows, ensure_ascii=False, separators=(",", ":"))
    )


class LLMSemanticRecaller:
    """独立网络适配器；运行时接线前可单独用桩测试。"""

    def __init__(self, agent):
        self.agent = agent

    def rerank(self, *, text, dialogue_tail, candidates):
        system, user = build_semantic_recall_messages(
            text=text, dialogue_tail=dialogue_tail, candidates=candidates)
        raw = self.agent.call(system, user,
                              temperature=DEFAULTS["extractor_temperature"],
                              require_json=True)
        return parse_semantic_recall(
            raw, candidate_ids=[row["candidate_id"] for row in candidates], user_text=text)
