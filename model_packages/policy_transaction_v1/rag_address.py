"""Replaceable semantic address-provider contract for policy transactions.

The provider may read current committed query text and a caller-closed set of
committed candidate documents.  Its only accepted product is one similarity
for every exact candidate ID.  This module applies the versioned eligibility
band and emits the content-free address receipt consumed by ``recall_history``.
It has no state, History, Commit, hydration, or realization authority.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data

from .memory import build_rag_address_receipt, build_rag_score_receipt


EMBEDDING_ADDRESS_REQUEST_VERSION = (
    "policy-transaction-embedding-address-request-v1")
EMBEDDING_ADDRESS_RESULT_VERSION = (
    "policy-transaction-embedding-address-result-v1")


@dataclass(frozen=True)
class EmbeddingAddressProviderBinding:
    """One explicit provider plus its frozen, versioned selection policy."""

    invoke: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    minimum_similarity: float
    maximum_distance_from_best: float
    selection_policy_identity: str

    def __post_init__(self) -> None:
        if not callable(self.invoke):
            raise ValueError("embedding address provider invoke must be callable")
        object.__setattr__(self, "minimum_similarity", _minimum_similarity(
            self.minimum_similarity))
        object.__setattr__(self, "maximum_distance_from_best", _maximum_distance(
            self.maximum_distance_from_best))
        object.__setattr__(self, "selection_policy_identity", _identifier(
            self.selection_policy_identity, "selection_policy_identity"))

    def resolve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return resolve_embedding_address(
            request=request,
            provider_invoke=self.invoke,
            minimum_similarity=self.minimum_similarity,
            maximum_distance_from_best=self.maximum_distance_from_best,
            selection_policy_identity=self.selection_policy_identity,
        )


@dataclass(frozen=True)
class CompleteEmbeddingScoreProviderBinding:
    """Provider binding that publishes scores but never selects memories."""

    invoke: Callable[[Mapping[str, Any]], Mapping[str, Any]]
    scoring_policy_identity: str

    def __post_init__(self) -> None:
        if not callable(self.invoke):
            raise ValueError("embedding score provider invoke must be callable")
        object.__setattr__(self, "scoring_policy_identity", _identifier(
            self.scoring_policy_identity, "scoring_policy_identity"))

    def resolve(self, request: Mapping[str, Any]) -> dict[str, Any]:
        return resolve_embedding_scores(
            request=request,
            provider_invoke=self.invoke,
            scoring_policy_identity=self.scoring_policy_identity,
        )


def _plain(value: object, field: str) -> Any:
    try:
        return thaw_model_data(value)
    except ValueError as exc:
        raise ValueError(f"{field} must contain model data") from exc


def _mapping(value: object, field: str) -> dict[str, Any]:
    result = _plain(value, field)
    if not isinstance(result, dict):
        raise ValueError(f"{field} must be a mapping")
    return result


def _exact(value: object, fields: set[str], field: str) -> dict[str, Any]:
    result = _mapping(value, field)
    if set(result) != fields:
        raise ValueError(f"{field} fields are invalid")
    return result


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _sha256(value: object, field: str) -> str:
    result = _identifier(value, field)
    if len(result) != 64 or any(ch not in "0123456789abcdef" for ch in result):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return result


def _similarity(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("candidate similarity must be numeric")
    result = float(value)
    if result < -1.0 or result > 1.0:
        raise ValueError("candidate similarity must be in [-1, 1]")
    return result


def _minimum_similarity(value: object) -> float:
    return _similarity(value)


def _maximum_distance(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("maximum_distance_from_best must be numeric")
    result = float(value)
    if result < 0.0 or result > 2.0:
        raise ValueError("maximum_distance_from_best must be in [0, 2]")
    return result


def build_embedding_address_request(
    *, scope: object, query_text: object, candidate_documents: object,
    query_context: object,
) -> dict[str, Any]:
    """Freeze one address request over a same-session/task candidate set."""
    scope_record = _exact(
        scope, {"session_id", "task_scope_ref"}, "RAG address scope")
    session_id = _identifier(scope_record["session_id"], "session_id")
    task_scope_ref = _identifier(
        scope_record["task_scope_ref"], "task_scope_ref")
    text = _identifier(query_text, "RAG query text")
    context = _mapping(query_context, "RAG query context")
    if isinstance(candidate_documents, (str, bytes)) or not isinstance(
            candidate_documents, Sequence):
        raise ValueError("candidate_documents must be a sequence")
    documents = []
    seen = set()
    for raw in candidate_documents:
        row = _exact(
            raw,
            {
                "event_id", "session_id", "task_scope_ref",
                "content_fingerprint", "text",
            },
            "RAG candidate document",
        )
        event_id = _identifier(row["event_id"], "candidate event_id")
        if event_id in seen:
            raise ValueError("candidate event IDs cannot repeat")
        seen.add(event_id)
        if (
            _identifier(row["session_id"], "candidate session_id") != session_id
            or _identifier(
                row["task_scope_ref"], "candidate task_scope_ref")
            != task_scope_ref
        ):
            raise ValueError("candidate document escapes the RAG address scope")
        documents.append({
            "event_id": event_id,
            "session_id": session_id,
            "task_scope_ref": task_scope_ref,
            "content_fingerprint": _sha256(
                row["content_fingerprint"], "candidate content_fingerprint"),
            "text": _identifier(row["text"], "candidate text"),
        })
    material = {
        "version": EMBEDDING_ADDRESS_REQUEST_VERSION,
        "scope": {
            "session_id": session_id,
            "task_scope_ref": task_scope_ref,
        },
        "query": {
            "text": text,
            "context": context,
        },
        "candidate_documents": documents,
        "state_commit_authority": False,
        "historical_influence_authority": False,
        "content_generation_authority": False,
    }
    return {**material, "fingerprint": model_data_fingerprint(material)}


def _validate_request(value: object) -> dict[str, Any]:
    record = _mapping(value, "embedding address request")
    fingerprint = record.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(record):
        raise ValueError("embedding address request fingerprint is invalid")
    if record.get("version") != EMBEDDING_ADDRESS_REQUEST_VERSION:
        raise ValueError("embedding address request version is invalid")
    if (
        record.get("state_commit_authority") is not False
        or record.get("historical_influence_authority") is not False
        or record.get("content_generation_authority") is not False
    ):
        raise ValueError("embedding address request cannot grant authority")
    rebuilt = build_embedding_address_request(
        scope=record.get("scope"),
        query_text=_mapping(record.get("query"), "RAG query").get("text"),
        query_context=_mapping(record.get("query"), "RAG query").get("context"),
        candidate_documents=record.get("candidate_documents"),
    )
    if rebuilt["fingerprint"] != fingerprint:
        raise ValueError("embedding address request differs from its fingerprint")
    return rebuilt


def resolve_embedding_address(
    *, request: object,
    provider_invoke: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    minimum_similarity: object,
    maximum_distance_from_best: object,
    selection_policy_identity: object,
) -> dict[str, Any]:
    """Invoke one provider and convert its complete score vector to exact IDs.

    Every score above both the calibrated absolute floor and the calibrated
    distance-from-best band survives.  Therefore the selected count is a
    consequence of the score field, never a fixed capacity or Top-N cap.
    """
    normalized_request = _validate_request(request)
    if not callable(provider_invoke):
        raise ValueError("provider_invoke must be callable")
    floor = _minimum_similarity(minimum_similarity)
    distance = _maximum_distance(maximum_distance_from_best)
    policy_identity = _identifier(
        selection_policy_identity, "selection_policy_identity")
    result = _exact(
        provider_invoke(normalized_request),
        {
            "status", "provider_identity", "model_identity",
            "index_identity", "candidate_scores",
        },
        "embedding address provider result",
    )
    status = _identifier(result["status"], "embedding provider status")
    if status not in {"available", "unavailable", "failed"}:
        raise ValueError("embedding provider status is invalid")
    documents = normalized_request["candidate_documents"]
    candidate_ids = [row["event_id"] for row in documents]
    raw_scores = result["candidate_scores"]
    if isinstance(raw_scores, (str, bytes)) or not isinstance(
            raw_scores, Sequence):
        raise ValueError("candidate_scores must be a sequence")
    scores = []
    seen = set()
    for raw in raw_scores:
        row = _exact(
            raw, {"event_id", "similarity"}, "candidate score")
        event_id = _identifier(row["event_id"], "candidate score event_id")
        if event_id in seen:
            raise ValueError("candidate score IDs cannot repeat")
        seen.add(event_id)
        scores.append({
            "event_id": event_id,
            "similarity": _similarity(row["similarity"]),
        })
    if status == "available":
        if seen != set(candidate_ids) or len(scores) != len(candidate_ids):
            raise ValueError(
                "candidate scores differ from the closed candidate set")
    elif scores:
        raise ValueError(
            "unavailable or failed provider cannot publish candidate scores")

    addressed = []
    if scores:
        best = max(row["similarity"] for row in scores)
        cutoff = max(floor, best - distance)
        addressed = sorted(
            (row for row in scores if row["similarity"] >= cutoff),
            key=lambda row: (-row["similarity"], row["event_id"]),
        )
    query_fingerprint = model_data_fingerprint(
        normalized_request["query"])
    return build_rag_address_receipt(
        status=status,
        provider_identity=result["provider_identity"],
        model_identity=result["model_identity"],
        index_identity=result["index_identity"],
        query_fingerprint=query_fingerprint,
        candidate_event_ids=candidate_ids,
        addressed_events=addressed,
        selection_policy_identity=policy_identity,
    )


def resolve_embedding_scores(
    *, request: object,
    provider_invoke: Callable[[Mapping[str, Any]], Mapping[str, Any]],
    scoring_policy_identity: object,
) -> dict[str, Any]:
    """Return the provider's complete score vector without eligibility logic."""
    normalized_request = _validate_request(request)
    if not callable(provider_invoke):
        raise ValueError("provider_invoke must be callable")
    policy_identity = _identifier(
        scoring_policy_identity, "scoring_policy_identity")
    result = _exact(
        provider_invoke(normalized_request),
        {
            "status", "provider_identity", "model_identity",
            "index_identity", "candidate_scores",
        },
        "embedding score provider result",
    )
    status = _identifier(result["status"], "embedding provider status")
    if status not in {"available", "unavailable", "failed"}:
        raise ValueError("embedding provider status is invalid")
    raw_scores = result["candidate_scores"]
    if isinstance(raw_scores, (str, bytes)) or not isinstance(
            raw_scores, Sequence):
        raise ValueError("candidate_scores must be a sequence")
    scores = []
    for raw in raw_scores:
        row = _exact(raw, {"event_id", "similarity"}, "candidate score")
        scores.append({
            "event_id": _identifier(
                row["event_id"], "candidate score event_id"),
            "similarity": _similarity(row["similarity"]),
        })
    candidate_ids = [
        row["event_id"] for row in normalized_request["candidate_documents"]]
    return build_rag_score_receipt(
        status=status,
        provider_identity=result["provider_identity"],
        model_identity=result["model_identity"],
        index_identity=result["index_identity"],
        query_fingerprint=model_data_fingerprint(normalized_request["query"]),
        candidate_event_ids=candidate_ids,
        candidate_scores=scores,
        scoring_policy_identity=policy_identity,
    )


__all__ = (
    "CompleteEmbeddingScoreProviderBinding",
    "EmbeddingAddressProviderBinding",
    "EMBEDDING_ADDRESS_REQUEST_VERSION",
    "EMBEDDING_ADDRESS_RESULT_VERSION",
    "build_embedding_address_request",
    "resolve_embedding_address",
    "resolve_embedding_scores",
)
