"""Owner-local authorization for the complete Full Memory candidate set.

This host reads only validated canonical node products from one runtime
session.  It does not score, select actual recall, hydrate text, interpret a
model dimension, or mutate state.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.history import (
    FRAMEWORK_HISTORY_PROFILE_KEY,
    HistoryAuthority,
    HistoryRead,
    InfluenceEdge,
)


RUNTIME_FULL_MEMORY_CANDIDATE_AUTHORIZATION_VERSION = (
    "runtime-full-memory-candidate-authorization-v1")


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


@dataclass(frozen=True)
class RuntimeFullMemoryCandidateAuthorization:
    session_binding_fingerprint: str
    owner_participant_id: str
    current_event_id: str
    evaluation_position_fingerprint: str
    evaluation_runtime_ordinal: int
    candidate_event_ids: tuple[str, ...]
    node_fingerprints: tuple[str, ...]
    history_read: HistoryRead
    history_influence_edges: tuple[InfluenceEdge, ...]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        for field in (
            "session_binding_fingerprint", "owner_participant_id",
            "current_event_id", "evaluation_position_fingerprint",
        ):
            object.__setattr__(
                self, field, _identifier(getattr(self, field), field))
        if (
            isinstance(self.evaluation_runtime_ordinal, bool)
            or not isinstance(self.evaluation_runtime_ordinal, int)
            or self.evaluation_runtime_ordinal < 0
        ):
            raise ValueError("evaluation_runtime_ordinal must be non-negative")
        candidates = tuple(
            _identifier(item, "candidate_event_id")
            for item in self.candidate_event_ids
        )
        nodes = tuple(
            _identifier(item, "node_fingerprint")
            for item in self.node_fingerprints
        )
        if len(candidates) != len(set(candidates)):
            raise ValueError("candidate authorization contains duplicate events")
        if len(candidates) != len(nodes):
            raise ValueError("candidate authorization node set is incomplete")
        object.__setattr__(self, "candidate_event_ids", candidates)
        object.__setattr__(self, "node_fingerprints", nodes)
        if not isinstance(self.history_read, HistoryRead):
            raise ValueError("candidate authorization requires HistoryRead")
        edges = tuple(self.history_influence_edges)
        if any(not isinstance(item, InfluenceEdge) for item in edges):
            raise ValueError("candidate authorization history edges are invalid")
        object.__setattr__(self, "history_influence_edges", edges)
        if not isinstance(self.receipt, Mapping):
            raise ValueError("candidate authorization receipt must be a mapping")
        frozen = freeze_model_payload(self.receipt)
        material = dict(frozen)
        fingerprint = material.pop("fingerprint", None)
        if fingerprint != model_data_fingerprint(material):
            raise ValueError("candidate authorization receipt fingerprint is invalid")
        expected = {
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "owner_participant_id": self.owner_participant_id,
            "current_event_id": self.current_event_id,
            "evaluation_position_fingerprint": (
                self.evaluation_position_fingerprint),
            "evaluation_runtime_ordinal": self.evaluation_runtime_ordinal,
            "candidate_event_ids": self.candidate_event_ids,
            "node_fingerprints": self.node_fingerprints,
            "history_read_fingerprint": self.history_read.fingerprint,
            "history_influence_edge_fingerprints": tuple(
                item.fingerprint for item in edges),
        }
        if any(frozen.get(key) != value for key, value in expected.items()):
            raise ValueError("candidate authorization receipt differs from product")
        if (
            (frozen.get("complete_owner_local_node_set") is not True
             and not (frozen.get("selection_mode") == "prepared-subset-v1"
                      and frozen.get("complete_owner_local_node_set") is False))
            or frozen.get("state_commit_authority") is not False
            or frozen.get("memory_commit_authority") is not False
        ):
            raise ValueError("candidate authorization authority is invalid")
        object.__setattr__(self, "receipt", frozen)


def _history_authority(
    profile: Mapping[str, Any], *, session_binding_fingerprint: str,
) -> HistoryAuthority:
    stored = profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
    if stored is not None:
        history = HistoryAuthority.from_record(stored)
        if history.session_binding_fingerprint != session_binding_fingerprint:
            raise ValueError("framework history binding is invalid")
        return history
    if (
        "runtime_full_memory_nodes" in profile
        or "runtime_full_memory_lineage" in profile
    ):
        raise ValueError(
            "legacy memory journals require explicit history migration")
    return HistoryAuthority(
        session_binding_fingerprint=session_binding_fingerprint)


def authorize_runtime_full_memory_candidates(
    *, profile: Mapping[str, Any], session_binding_fingerprint: str,
    context: ModelInvocationContext | ModelInvocationContextV2,
    requested_candidate_ids: Sequence[str] | None = None,
    source_evidence: Mapping[str, Any] | None = None,
) -> RuntimeFullMemoryCandidateAuthorization:
    """Authorize every prior accepted node owned by this participant."""
    if not isinstance(profile, Mapping):
        raise ValueError("profile must be a mapping")
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("context must be a model invocation context")
    identity = context_fact_identity(context)
    binding = _identifier(
        session_binding_fingerprint, "session_binding_fingerprint")
    owner = _identifier(identity.subject_ref, "context subject reference")
    current_event = _identifier(identity.event_ref, "context event reference")
    if context.temporal_position is None:
        raise ValueError("candidate authorization requires temporal position")
    history = _history_authority(
        profile, session_binding_fingerprint=binding)
    if requested_candidate_ids is None:
        facts = history.facts()
    else:
        if source_evidence is None:
            raise ValueError("indexed candidate subset requires source evidence")
        requested = tuple(requested_candidate_ids)
        if len(set(requested)) != len(requested):
            raise ValueError("duplicate indexed candidate")
        facts = tuple(history.fact(identity) for identity in requested)
    for fact in facts:
        if owner not in fact.visible_to_refs:
            raise ValueError("candidate node belongs to another owner")
        if fact.fact_id == current_event:
            raise ValueError("current event cannot be a memory candidate")
        if fact.accepted_runtime_ordinal > context.runtime_ordinal:
            raise ValueError("future node cannot be a memory candidate")
        node_record = fact.model_payload.get("runtime_full_memory_node")
        if not isinstance(node_record, Mapping) or (
                node_record.get("future_evaluation_eligible") is not True):
            raise ValueError("ineligible node cannot be a memory candidate")

    candidate_ids = tuple(fact.fact_id for fact in facts)
    node_fingerprints = tuple(
        _identifier(
            fact.model_payload["runtime_full_memory_node"].get("fingerprint"),
            "node_fingerprint",
        )
        for fact in facts
    )
    history_read = history.authorize_read(
        reader_ref=owner,
        trigger_fact_id=current_event,
        before_runtime_ordinal=context.runtime_ordinal + 1,
        requested_fact_ids=candidate_ids,
        authorization_ref={
            "session_binding_fingerprint": binding,
            "evaluation_position_fingerprint": (
                context.temporal_position.fingerprint),
            "evaluation_runtime_ordinal": context.runtime_ordinal,
            **({"prepared_source":source_evidence} if source_evidence is not None else {}),
        },
    )
    history_edges = history.influence_edges() if requested_candidate_ids is None else ()
    material = {
        "version": RUNTIME_FULL_MEMORY_CANDIDATE_AUTHORIZATION_VERSION,
        "session_binding_fingerprint": binding,
        "owner_participant_id": owner,
        "current_event_id": current_event,
        "current_conversation_id": identity.scope_ref,
        "evaluation_position_fingerprint": (
            context.temporal_position.fingerprint),
        "evaluation_runtime_ordinal": context.runtime_ordinal,
        "candidate_event_ids": list(candidate_ids),
        "node_fingerprints": list(node_fingerprints),
        "history_read_fingerprint": history_read.fingerprint,
        "history_influence_edge_fingerprints": [
            item.fingerprint for item in history_edges
        ],
        "source_conversations": [
            fact.scope_ref for fact in facts
        ],
        "accepted_runtime_ordinals": [
            fact.accepted_runtime_ordinal for fact in facts
        ],
        "complete_owner_local_node_set": requested_candidate_ids is None,
        **({"selection_mode":"prepared-subset-v1", "source_evidence":source_evidence}
           if requested_candidate_ids is not None else {}),
        "state_commit_authority": False,
        "memory_commit_authority": False,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeFullMemoryCandidateAuthorization(
        session_binding_fingerprint=binding,
        owner_participant_id=owner,
        current_event_id=current_event,
        evaluation_position_fingerprint=(
            context.temporal_position.fingerprint),
        evaluation_runtime_ordinal=context.runtime_ordinal,
        candidate_event_ids=candidate_ids,
        node_fingerprints=node_fingerprints,
        history_read=history_read,
        history_influence_edges=history_edges,
        receipt=receipt,
    )


__all__ = (
    "RUNTIME_FULL_MEMORY_CANDIDATE_AUTHORIZATION_VERSION",
    "RuntimeFullMemoryCandidateAuthorization",
    "authorize_runtime_full_memory_candidates",
)
