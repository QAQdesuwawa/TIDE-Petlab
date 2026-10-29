"""Exact-ID RAG documents and authorized content hydration.

This product adapter is the only place where the policy-transaction product
turns one committed exchange into provider-readable text and later restores
that text for realization.  Address selection remains package-owned, while
authorization, immutable fact identity, and temporal ordering remain owned by
the framework.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import json
from types import MappingProxyType
from typing import Any

from framework.content_hydration import (
    OpaqueContentRead,
    OpaqueContentRecord,
    hydrate_authorized_content,
)
from framework.history import HistoryAuthority, HistoryRead
from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.temporal_domain import TemporalPosition
from interaction_extensions.content_attention import ContentOccupationPolicy
from model_packages.policy_transaction_v1.rag_address import (
    CompleteEmbeddingScoreProviderBinding,
    EmbeddingAddressProviderBinding,
)
from model_packages.policy_transaction_v1.state_space import (
    DEFAULT_STATE_SPACE_DEFINITION,
    normalize_state_space_definition,
)
from model_packages.policy_transaction_v1.text_codec import (
    VERBOSE_MEASUREMENT_TRANSPORT,
    normalize_measurement_transport_profile,
    normalize_reference_query_vocabulary,
)


POLICY_TRANSACTION_EXCHANGE_CONTENT_VERSION = (
    "policy-transaction-exchange-content-v1")


@dataclass(frozen=True)
class PolicyTransactionRagRuntimeBinding:
    """Product composition of address provider and content attention policy."""

    runtime_config_fingerprint: str
    implementation_manifest_sha256: str
    address_provider: EmbeddingAddressProviderBinding
    content_occupation_policy: ContentOccupationPolicy

    def __post_init__(self) -> None:
        for value, field in (
            (self.runtime_config_fingerprint, "runtime_config_fingerprint"),
            (self.implementation_manifest_sha256,
             "implementation_manifest_sha256"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(ch not in "0123456789abcdef" for ch in value)
            ):
                raise ValueError(f"{field} must be a lowercase sha256 fingerprint")
        if not isinstance(
                self.address_provider, EmbeddingAddressProviderBinding):
            raise ValueError("RAG runtime requires an embedding address provider")
        if not isinstance(self.content_occupation_policy, ContentOccupationPolicy):
            raise ValueError("RAG runtime requires a content occupation policy")


@dataclass(frozen=True)
class PolicyTransactionRagRuntimeBindingV2:
    """Single-qualification RAG composition for long-memory experiments."""

    runtime_config_fingerprint: str
    implementation_manifest_sha256: str
    score_provider: CompleteEmbeddingScoreProviderBinding
    memory_relevance_policy: Mapping[str, Any]
    maximum_prompt_characters: int
    reference_query_vocabulary: Mapping[str, Any] | None = None
    measurement_transport_profile: str = VERBOSE_MEASUREMENT_TRANSPORT
    state_space_definition: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.state_space_definition is not None:
            object.__setattr__(self, 'state_space_definition', freeze_model_payload(
                normalize_state_space_definition(self.state_space_definition)))
        for value, field in (
            (self.runtime_config_fingerprint, "runtime_config_fingerprint"),
            (self.implementation_manifest_sha256,
             "implementation_manifest_sha256"),
        ):
            if (
                not isinstance(value, str)
                or len(value) != 64
                or any(ch not in "0123456789abcdef" for ch in value)
            ):
                raise ValueError(f"{field} must be a lowercase sha256 fingerprint")
        if not isinstance(
                self.score_provider, CompleteEmbeddingScoreProviderBinding):
            raise ValueError("RAG v2 requires a complete embedding score provider")
        policy = _mapping(
            self.memory_relevance_policy, "memory_relevance_policy")
        if set(policy) != {
            "identity", "minimum_fused_relevance", "agreement_gain",
        }:
            raise ValueError("memory relevance policy fields are invalid")
        identity = str(policy["identity"] or "").strip()
        if not identity:
            raise ValueError("memory relevance policy identity is empty")
        normalized = {"identity": identity}
        for field in ("minimum_fused_relevance", "agreement_gain"):
            value = policy[field]
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{field} must be numeric")
            value = float(value)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{field} must be in [0, 1]")
            normalized[field] = value
        object.__setattr__(
            self, "memory_relevance_policy", MappingProxyType(normalized))
        if (
            isinstance(self.maximum_prompt_characters, bool)
            or not isinstance(self.maximum_prompt_characters, int)
            or self.maximum_prompt_characters <= 0
        ):
            raise ValueError("maximum_prompt_characters must be positive")
        if self.reference_query_vocabulary is not None:
            vocabulary = normalize_reference_query_vocabulary(
                self.reference_query_vocabulary,
                state_space_definition=DEFAULT_STATE_SPACE_DEFINITION,
            )
            object.__setattr__(
                self,
                "reference_query_vocabulary",
                freeze_model_payload(vocabulary),
            )
        object.__setattr__(
            self,
            "measurement_transport_profile",
            normalize_measurement_transport_profile(
                self.measurement_transport_profile),
        )


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    try:
        result = thaw_model_data(value)
    except ValueError as exc:
        raise ValueError(f"{field} must contain model data") from exc
    if not isinstance(result, dict):
        raise ValueError(f"{field} must be a mapping")
    return result


def _identifiers(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an identifier sequence")
    result = tuple(str(item or "").strip() for item in value)
    if not result or any(not item for item in result):
        raise ValueError(f"{field} must contain non-empty identifiers")
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicate identifiers")
    return result


def _exchange_payload(value: object) -> dict[str, Any]:
    record = _mapping(value, "policy transaction exchange content")
    required = {
        "version", "inbound_event", "committed_action",
        "temporal_coordinate", "source_receipt_refs",
        "content_fingerprint", "fingerprint",
    }
    if set(record) != required:
        raise ValueError("policy transaction exchange content fields are invalid")
    if record["version"] != POLICY_TRANSACTION_EXCHANGE_CONTENT_VERSION:
        raise ValueError("policy transaction exchange content version is invalid")
    fingerprint = record.pop("fingerprint")
    if fingerprint != model_data_fingerprint(record):
        raise ValueError("policy transaction exchange content fingerprint is invalid")
    content = {
        "inbound_event": _mapping(record["inbound_event"], "inbound_event"),
        "committed_action": _mapping(
            record["committed_action"], "committed_action"),
    }
    if record["content_fingerprint"] != model_data_fingerprint(content):
        raise ValueError("policy transaction exchange content body is invalid")
    record["source_receipt_refs"] = list(_identifiers(
        record["source_receipt_refs"], "source_receipt_refs"))
    return {**record, "fingerprint": fingerprint}


def build_committed_exchange_content(
    *, inbound_event: Mapping[str, Any], committed_action: Mapping[str, Any],
    temporal_coordinate: object, source_receipt_refs: Sequence[str],
) -> Mapping[str, Any]:
    """Build one immutable product payload stored inside a committed fact."""
    content = {
        "inbound_event": _mapping(inbound_event, "inbound_event"),
        "committed_action": _mapping(committed_action, "committed_action"),
    }
    material = {
        "version": POLICY_TRANSACTION_EXCHANGE_CONTENT_VERSION,
        **content,
        "temporal_coordinate": thaw_model_data(temporal_coordinate),
        "source_receipt_refs": list(_identifiers(
            source_receipt_refs, "source_receipt_refs")),
        "content_fingerprint": model_data_fingerprint(content),
    }
    return freeze_model_payload({
        **material,
        "fingerprint": model_data_fingerprint(material),
    })


def _fact_exchange(fact: object) -> dict[str, Any]:
    payload = getattr(fact, "model_payload", None)
    if not isinstance(payload, Mapping):
        raise ValueError("committed fact has no model payload")
    if "policy_transaction_exchange_content" not in payload:
        raise ValueError("committed fact has no policy transaction exchange content")
    return _exchange_payload(payload["policy_transaction_exchange_content"])


def _document_text(exchange: Mapping[str, Any]) -> str:
    return json.dumps(
        {
            "inbound_event": exchange["inbound_event"],
            "committed_action": exchange["committed_action"],
        },
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def build_rag_candidate_documents(
    *, history: HistoryAuthority, session_id: str, task_scope_ref: str,
    reader_ref: str,
) -> tuple[dict[str, Any], ...]:
    """Expose same-scope visible committed exchanges to an address provider.

    This function deliberately does not rank, cap, or authorize a HistoryRead.
    It only closes the address provider's candidate universe.
    """
    if not isinstance(history, HistoryAuthority):
        raise ValueError("RAG candidate documents require HistoryAuthority")
    normalized_session = str(session_id or "").strip()
    normalized_scope = str(task_scope_ref or "").strip()
    normalized_reader = str(reader_ref or "").strip()
    if not normalized_session or not normalized_scope or not normalized_reader:
        raise ValueError("RAG candidate scope and reader must be non-empty")
    documents = []
    for fact in history.facts():
        fact.verify_integrity()
        if fact.scope_ref != normalized_scope:
            raise ValueError("committed fact escapes the RAG task scope")
        if normalized_reader not in fact.visible_to_refs:
            raise ValueError("committed fact is not visible to the RAG reader")
        exchange = _fact_exchange(fact)
        documents.append({
            "event_id": fact.fact_id,
            "session_id": normalized_session,
            "task_scope_ref": normalized_scope,
            "content_fingerprint": exchange["content_fingerprint"],
            "text": _document_text(exchange),
        })
    return tuple(documents)


def hydrate_recalled_exchange_content(
    *, history: HistoryAuthority, history_read: HistoryRead,
    selected_fact_ids: Sequence[str], temporal_runtime: object,
    evaluation_position: TemporalPosition,
    evaluation_runtime_ordinal: int,
):
    """Hydrate only exact IDs already authorized by one HistoryRead."""
    if not isinstance(history, HistoryAuthority):
        raise ValueError("content hydration requires HistoryAuthority")
    accepted_read = history.to_record()["reads"]
    if not any(
        row.get("fingerprint") == getattr(history_read, "fingerprint", None)
        for row in accepted_read
    ):
        raise ValueError("content hydration requires an authorized history read")
    if not hasattr(temporal_runtime, "normalize_position") or not hasattr(
            temporal_runtime, "prove_order"):
        raise ValueError("content hydration requires a temporal runtime")

    def load(fact_id: str) -> OpaqueContentRead:
        fact = history.fact(fact_id)
        exchange = _fact_exchange(fact)
        position = temporal_runtime.normalize_position(
            coordinate=exchange["temporal_coordinate"])
        if position.fingerprint != fact.temporal_position_fingerprint:
            raise ValueError(
                "committed exchange position differs from History fact")
        record = OpaqueContentRecord.create(
            fact_id=fact.fact_id,
            session_binding_fingerprint=fact.session_binding_fingerprint,
            temporal_position=position,
            accepted_runtime_ordinal=fact.accepted_runtime_ordinal,
            payload={
                "inbound_event": exchange["inbound_event"],
                "committed_action": exchange["committed_action"],
            },
            source_receipt_refs=exchange["source_receipt_refs"],
        )
        proof = temporal_runtime.prove_order(
            from_position=position,
            to_position=evaluation_position,
        )
        return OpaqueContentRead(record=record, order_proof=proof)

    return hydrate_authorized_content(
        history_read=history_read,
        selected_fact_ids=selected_fact_ids,
        evaluation_position=evaluation_position,
        evaluation_runtime_ordinal=evaluation_runtime_ordinal,
        content_loader=load,
    )


__all__ = (
    "POLICY_TRANSACTION_EXCHANGE_CONTENT_VERSION",
    "PolicyTransactionRagRuntimeBinding",
    "PolicyTransactionRagRuntimeBindingV2",
    "build_committed_exchange_content",
    "build_rag_candidate_documents",
    "hydrate_recalled_exchange_content",
)
