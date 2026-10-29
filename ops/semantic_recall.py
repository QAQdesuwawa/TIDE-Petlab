"""受限语义候选重排的纯数据契约。

本模块不提出候选、不调用模型，也不选择或写入记忆。它只验证不可信重排器是否
只能在调用方给定的候选 ID 集合中返回带当前输入证据的相关性记录。
"""
from dataclasses import dataclass
from typing import Any, Iterable


def _clamp01(value: Any) -> float:
    try:
        return min(1.0, max(0.0, float(value)))
    except (TypeError, ValueError):
        return 0.0


@dataclass(frozen=True)
class SemanticRecallMatch:
    """一条仅对已提出候选的相关性证据。"""

    candidate_id: str
    relevance: float
    confidence: float
    evidence: str

    def as_record(self):
        return {
            "candidate_id": self.candidate_id,
            "relevance": self.relevance,
            "confidence": self.confidence,
            "evidence": self.evidence,
        }


def coerce_rerank_matches(raw: Any, *, candidate_ids: Iterable[str], user_text: str):
    """验证模型输出；任一越界或无证据项使整批降级为空。

    空结果是安全的“未提供可用语义证据”，不是本地候选的否定结论。调用方仍可走
    原有词面、时间和概率路径。
    """
    offered = {str(value) for value in candidate_ids if str(value).strip()}
    text = str(user_text or "")
    if not isinstance(raw, dict) or not isinstance(raw.get("matches"), list):
        return ()
    matches, seen = [], set()
    for item in raw["matches"]:
        if not isinstance(item, dict):
            return ()
        candidate_id = str(item.get("candidate_id") or "").strip()
        evidence = str(item.get("evidence") or "").strip()
        if (not candidate_id or candidate_id not in offered or candidate_id in seen
                or not evidence or evidence not in text):
            return ()
        relevance = _clamp01(item.get("relevance"))
        confidence = _clamp01(item.get("confidence"))
        matches.append(SemanticRecallMatch(candidate_id, relevance, confidence, evidence))
        seen.add(candidate_id)
    return tuple(matches)
