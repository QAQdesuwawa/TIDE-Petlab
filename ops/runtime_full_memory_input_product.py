"""Model-independent producer for one signed Full Memory input product.

The caller supplies an already-authorized exact candidate history.  The bound
Interaction Model owns every numeric transformation.  This host validates only
identity, exact-set completeness, stage lineage, and authority boundaries.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.history import (
    FRAMEWORK_HISTORY_PROFILE_KEY,
    HistoryAuthority,
    HistoryRead,
)
from ops.runtime_full_memory_candidate_authorization import (
    RuntimeFullMemoryCandidateAuthorization,
)
from ops.runtime_full_memory_node_product import RuntimeFullMemoryNodeProduct
from ops.runtime_stage_verification import verify_runtime_stage_execution


RUNTIME_FULL_MEMORY_INPUT_PRODUCT_VERSION = (
    "runtime-full-memory-input-product-v1")


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _event_ids(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an event-id sequence")
    result = tuple(_identifier(item, field) for item in value)
    if len(result) != len(set(result)):
        raise ValueError(f"{field} cannot contain duplicate event IDs")
    return result


@dataclass(frozen=True)
class RuntimeFullMemoryInputProduct:
    model_binding_fingerprint: str
    candidate_event_ids: tuple[str, ...]
    preparation_execution: ModelStageExecution
    history_read: HistoryRead | None
    memory_recall_payload: Mapping[str, Any]
    memory_influence_payload: Mapping[str, Any]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "model_binding_fingerprint", _identifier(
            self.model_binding_fingerprint, "model_binding_fingerprint"))
        object.__setattr__(self, "candidate_event_ids", _event_ids(
            self.candidate_event_ids, "candidate_event_ids"))
        if not isinstance(self.preparation_execution, ModelStageExecution):
            raise ValueError("preparation_execution must be a model execution")
        if self.history_read is not None and not isinstance(
                self.history_read, HistoryRead):
            raise ValueError("history_read must be HistoryRead")
        for field in (
            "memory_recall_payload", "memory_influence_payload", "receipt",
        ):
            value = getattr(self, field)
            if not isinstance(value, Mapping):
                raise ValueError(f"{field} must be a mapping")
            object.__setattr__(self, field, freeze_model_payload(value))


def _invoke_preparation(
    *, model_runtime: BoundInteractionModel,
    context: ModelInvocationContext | ModelInvocationContextV2,
    candidate_ids: tuple[str, ...], invocation_payload: Mapping[str, Any],
    candidate_authorization_fingerprint: str | None = None,
    history_read: HistoryRead | None = None,
    prepared_execution: ModelStageExecution | None = None,
) -> RuntimeFullMemoryInputProduct:
    identity = context_fact_identity(context)
    from interaction_extensions.memory_source_table import CONTEXT_KEY, VERSION, encode
    source_encoding = context.metadata.get(CONTEXT_KEY)
    if source_encoding is not None and prepared_execution is None:
        if source_encoding != VERSION:
            raise ValueError("unsupported memory source encoding")
        invocation_payload = encode(invocation_payload)
    execution = (prepared_execution if prepared_execution is not None else model_runtime.invoke(
        "prepare_memory_inputs", payload=invocation_payload, context=context))
    verify_runtime_stage_execution(
        execution,
        stage="prepare_memory_inputs",
        runtime=model_runtime,
        context=context,
        invocation_payload=invocation_payload,
    )
    published = execution.payload.get("memory_inputs")
    if not isinstance(published, Mapping):
        raise ValueError("prepare_memory_inputs must publish memory_inputs")
    required = {
        "version", "candidate_event_ids", "candidates",
        "state_commit_authority", "memory_commit_authority",
    }
    if set(published) != required:
        raise ValueError("prepared memory input fields do not match contract")
    if (
        published.get("state_commit_authority") is not False
        or published.get("memory_commit_authority") is not False
    ):
        raise ValueError("prepared memory inputs cannot carry commit authority")
    published_ids = _event_ids(
        published.get("candidate_event_ids"),
        "prepared candidate_event_ids",
    )
    if published_ids != candidate_ids:
        raise ValueError("prepared candidate set differs from authorization")
    raw_candidates = published.get("candidates")
    if (
        isinstance(raw_candidates, (str, bytes))
        or not isinstance(raw_candidates, Sequence)
    ):
        raise ValueError("prepared candidates must be a sequence")
    by_id: dict[str, Mapping[str, Any]] = {}
    for item in raw_candidates:
        if not isinstance(item, Mapping) or set(item) != {
            "event_id", "recall_input", "influence_input",
        }:
            raise ValueError("prepared candidate fields do not match contract")
        event_id = _identifier(item.get("event_id"), "prepared event_id")
        if event_id in by_id:
            raise ValueError("prepared candidates contain duplicate event IDs")
        if not isinstance(item.get("recall_input"), Mapping):
            raise ValueError("prepared recall input must be a mapping")
        if not isinstance(item.get("influence_input"), Mapping):
            raise ValueError("prepared influence input must be a mapping")
        by_id[event_id] = item
    if set(by_id) != set(candidate_ids):
        raise ValueError("prepared candidate records are incomplete")

    recall_payload = {
        "candidate_event_ids": list(candidate_ids),
        "historical_live": [
            by_id[event_id]["recall_input"] for event_id in candidate_ids
        ],
    }
    influence_payload = {
        "historical_readouts": [
            by_id[event_id]["influence_input"] for event_id in candidate_ids
        ],
    }
    binding_fingerprint = model_data_fingerprint(
        model_runtime.binding.to_record())
    material = {
        "version": RUNTIME_FULL_MEMORY_INPUT_PRODUCT_VERSION,
        "model_binding_fingerprint": binding_fingerprint,
        "session_id": identity.session_id,
        "conversation_id": identity.scope_ref,
        "participant_id": identity.subject_ref,
        "current_event_id": identity.event_ref,
        "temporal_position_fingerprint": (
            context.temporal_position.fingerprint),
        "runtime_ordinal": context.runtime_ordinal,
        "candidate_event_ids": list(candidate_ids),
        "candidate_authorization_fingerprint": (
            candidate_authorization_fingerprint),
        "history_read_fingerprint": (
            None if history_read is None else history_read.fingerprint),
        "preparation_stage_receipt_fingerprint": model_data_fingerprint(
            execution.receipt.to_record()),
        "recall_payload_fingerprint": model_data_fingerprint(recall_payload),
        "influence_payload_fingerprint": model_data_fingerprint(
            influence_payload),
        "state_commit_authority": False,
        "memory_commit_authority": False,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeFullMemoryInputProduct(
        model_binding_fingerprint=binding_fingerprint,
        candidate_event_ids=candidate_ids,
        preparation_execution=execution,
        history_read=history_read,
        memory_recall_payload=recall_payload,
        memory_influence_payload=influence_payload,
        receipt=receipt,
    )


def prepare_runtime_full_memory_input_product(
    *,
    model_runtime: BoundInteractionModel,
    context: ModelInvocationContext | ModelInvocationContextV2,
    authorized_candidate_event_ids: Sequence[str],
    canonical_projections: Sequence[Mapping[str, Any]],
    committed_lineage_transactions: Sequence[Mapping[str, Any]],
    propagation_bindings_by_ref: Mapping[str, Mapping[str, Any]],
    root_time_parameters_by_version: Mapping[str, Mapping[str, Any]],
) -> RuntimeFullMemoryInputProduct:
    """Ask the bound model to prepare both Full Memory stage inputs once."""
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("model_runtime must be a bound interaction model")
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("context must be a model invocation context")
    identity = context_fact_identity(context)
    candidate_ids = _event_ids(
        authorized_candidate_event_ids, "authorized_candidate_event_ids")
    if identity.event_ref is None:
        raise ValueError("memory input preparation requires current event")
    if identity.event_ref in candidate_ids:
        raise ValueError("current event cannot be a memory candidate")
    if context.temporal_position is None:
        raise ValueError("memory input preparation requires temporal position")
    if context.session_id != model_runtime.session_id:
        raise ValueError("memory input context differs from bound model session")
    for value, field in (
        (canonical_projections, "canonical_projections"),
        (committed_lineage_transactions, "committed_lineage_transactions"),
    ):
        if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
            raise ValueError(f"{field} must be a sequence")
    for value, field in (
        (propagation_bindings_by_ref, "propagation_bindings_by_ref"),
        (root_time_parameters_by_version, "root_time_parameters_by_version"),
    ):
        if not isinstance(value, Mapping):
            raise ValueError(f"{field} must be a mapping")

    invocation_payload = {
        "candidate_event_ids": list(candidate_ids),
        "canonical_projections": list(canonical_projections),
        "committed_lineage_transactions": list(
            committed_lineage_transactions),
        "propagation_bindings_by_ref": dict(propagation_bindings_by_ref),
        "root_time_parameters_by_version": dict(
            root_time_parameters_by_version),
    }
    return _invoke_preparation(
        model_runtime=model_runtime,
        context=context,
        candidate_ids=candidate_ids,
        invocation_payload=invocation_payload,
    )


def prepare_runtime_full_memory_input_product_from_profile(
    *,
    model_runtime: BoundInteractionModel,
    context: ModelInvocationContext | ModelInvocationContextV2,
    profile: Mapping[str, Any],
    candidate_authorization: RuntimeFullMemoryCandidateAuthorization,
) -> RuntimeFullMemoryInputProduct:
    """Prepare Full inputs from validated node/lineage profile journals."""
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("model_runtime must be a bound interaction model")
    if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError("context must be a model invocation context")
    identity = context_fact_identity(context)
    if not isinstance(profile, Mapping):
        raise ValueError("profile must be a mapping")
    if not isinstance(
        candidate_authorization, RuntimeFullMemoryCandidateAuthorization,
    ):
        raise ValueError(
            "profile input preparation requires candidate authorization")
    binding_fingerprint = candidate_authorization.session_binding_fingerprint
    candidate_ids = candidate_authorization.candidate_event_ids
    if identity.event_ref is None or identity.event_ref in candidate_ids:
        raise ValueError("current event cannot be a memory candidate")
    if context.temporal_position is None:
        raise ValueError("memory input preparation requires temporal position")
    expected_authorization = {
        "owner_participant_id": identity.subject_ref,
        "current_event_id": identity.event_ref,
        "evaluation_position_fingerprint": (
            context.temporal_position.fingerprint),
        "evaluation_runtime_ordinal": context.runtime_ordinal,
    }
    if any(
        getattr(candidate_authorization, field) != value
        for field, value in expected_authorization.items()
    ):
        raise ValueError("candidate authorization context differs from input")
    nodes = tuple(
        RuntimeFullMemoryNodeProduct.from_record(
            fact.model_payload["runtime_full_memory_node"])
        for fact in candidate_authorization.history_read.facts
    )
    nodes_by_id = {item.event_id: item for item in nodes}
    if not set(candidate_ids).issubset(nodes_by_id):
        raise ValueError("authorized candidate node set is incomplete")
    if tuple(node.event_id for node in nodes) != candidate_ids or tuple(
        node.fingerprint for node in nodes
    ) != candidate_authorization.node_fingerprints:
        raise ValueError(
            "candidate authorization differs from complete profile node set")
    candidate_nodes = [
        {
            "event_id": event_id,
            "accepted_runtime_ordinal": (
                nodes_by_id[event_id].accepted_runtime_ordinal),
            "projection": nodes_by_id[event_id].node_projection,
            "evolution_binding": nodes_by_id[event_id].node_evolution_binding,
        }
        for event_id in candidate_ids
    ]
    candidate_set = set(candidate_ids)
    stored_history = profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
    if stored_history is None:
        if candidate_ids or candidate_authorization.history_influence_edges:
            raise ValueError(
                "framework history is required for authorized candidates")
    else:
        history = HistoryAuthority.from_record(stored_history)
        if history.session_binding_fingerprint != binding_fingerprint:
            raise ValueError("framework history binding is invalid")
        accepted_read = history.authorize_read(
            reader_ref=candidate_authorization.history_read.reader_ref,
            trigger_fact_id=(
                candidate_authorization.history_read.trigger_fact_id),
            before_runtime_ordinal=(
                candidate_authorization.history_read.before_runtime_ordinal),
            requested_fact_ids=(
                candidate_authorization.history_read.fact_ids),
            authorization_ref=(
                candidate_authorization.history_read.authorization_ref),
        )
        if accepted_read.fingerprint != (
            candidate_authorization.history_read.fingerprint
        ):
            raise ValueError(
                "candidate authorization differs from framework history")
        if tuple(
            edge.fingerprint for edge in history.influence_edges()
        ) != tuple(
            edge.fingerprint
            for edge in candidate_authorization.history_influence_edges
        ):
            raise ValueError(
                "candidate lineage differs from framework history")
    runtime_edges = []
    for edge in candidate_authorization.history_influence_edges:
        if edge.child_fact_id not in candidate_set:
            continue
        if edge.parent_fact_id not in candidate_set:
            raise ValueError(
                "authorized candidates omit a committed lineage parent")
        evidence = edge.evidence
        source_evidence = evidence.get("edge_evidence")
        if not isinstance(source_evidence, Mapping):
            raise ValueError("framework history edge evidence is invalid")
        runtime_edges.append({
            "lineage_fingerprint": _identifier(
                evidence.get("lineage_fingerprint"),
                "lineage_fingerprint",
            ),
            "acceptance_fingerprint": _identifier(
                evidence.get("acceptance_fingerprint"),
                "acceptance_fingerprint",
            ),
            "aggregate_stage_output_fingerprint": _identifier(
                evidence.get("aggregate_stage_output_fingerprint"),
                "aggregate_stage_output_fingerprint",
            ),
            "parent_event_id": edge.parent_fact_id,
            "child_event_id": edge.child_fact_id,
            "projected_contribution": edge.contribution_payload,
            "edge_evidence": source_evidence,
        })
    return _invoke_preparation(
        model_runtime=model_runtime,
        context=context,
        candidate_ids=candidate_ids,
        invocation_payload={
            "candidate_event_ids": list(candidate_ids),
            "candidate_nodes": candidate_nodes,
            "runtime_lineage_edges": runtime_edges,
        },
        candidate_authorization_fingerprint=(
            candidate_authorization.receipt["fingerprint"]),
        history_read=candidate_authorization.history_read,
    )


__all__ = (
    "RUNTIME_FULL_MEMORY_INPUT_PRODUCT_VERSION",
    "RuntimeFullMemoryInputProduct",
    "prepare_runtime_full_memory_input_product",
    "prepare_runtime_full_memory_input_product_from_profile",
)
