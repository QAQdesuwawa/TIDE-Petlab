"""Atomic profile value for one accepted Full Memory state plus lineage."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_runtime import BoundInteractionModel
from framework.history import (
    FRAMEWORK_HISTORY_PROFILE_KEY,
    ActualRecall,
    CommittedFact,
    HistoryAuthority,
    InfluenceEdge,
)
from ops.runtime_full_memory_event_transaction import (
    RuntimeFullMemoryEventTransaction,
)
from ops.runtime_full_memory_lineage_product import (
    RuntimeFullMemoryLineageProduct,
    build_runtime_full_memory_lineage_product,
)
from ops.runtime_full_memory_node_product import (
    RuntimeFullMemoryNodeProduct,
    build_runtime_full_memory_node_product,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


RUNTIME_FULL_MEMORY_PROFILE_COMMIT_VERSION = (
    "runtime-full-memory-profile-commit-v2")
RUNTIME_FULL_MEMORY_LINEAGE_JOURNAL_KEY = (
    "runtime_full_memory_lineage")
RUNTIME_FULL_MEMORY_NODE_JOURNAL_KEY = "runtime_full_memory_nodes"


@dataclass(frozen=True)
class RuntimeFullMemoryProfileCommit:
    session: BootstrappedRuntimeSession
    transaction: RuntimeFullMemoryEventTransaction
    lineage: RuntimeFullMemoryLineageProduct
    node: RuntimeFullMemoryNodeProduct
    receipt: Mapping[str, Any]
    already_committed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def read_runtime_full_memory_lineage_journal(
    profile: Mapping[str, Any], *, session_binding_fingerprint: str,
) -> tuple[RuntimeFullMemoryLineageProduct, ...]:
    raw = profile.get(RUNTIME_FULL_MEMORY_LINEAGE_JOURNAL_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("runtime full memory lineage journal is invalid")
    restored = tuple(
        RuntimeFullMemoryLineageProduct.from_record(item) for item in raw)
    child_ids = tuple(item.child_event_id for item in restored)
    acceptance_ids = tuple(item.acceptance_fingerprint for item in restored)
    if len(child_ids) != len(set(child_ids)):
        raise ValueError("runtime full memory lineage child is duplicated")
    if len(acceptance_ids) != len(set(acceptance_ids)):
        raise ValueError("runtime full memory acceptance is duplicated")
    if any(
        item.session_binding_fingerprint != session_binding_fingerprint
        for item in restored
    ):
        raise ValueError("runtime full memory lineage binding is invalid")
    return restored


def read_runtime_full_memory_node_journal(
    profile: Mapping[str, Any], *, session_binding_fingerprint: str,
) -> tuple[RuntimeFullMemoryNodeProduct, ...]:
    raw = profile.get(RUNTIME_FULL_MEMORY_NODE_JOURNAL_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("runtime full memory node journal is invalid")
    restored = tuple(
        RuntimeFullMemoryNodeProduct.from_record(item) for item in raw)
    event_ids = tuple(item.event_id for item in restored)
    if len(event_ids) != len(set(event_ids)):
        raise ValueError("runtime full memory node event is duplicated")
    if any(
        item.session_binding_fingerprint != session_binding_fingerprint
        for item in restored
    ):
        raise ValueError("runtime full memory node binding is invalid")
    return restored


def _receipt(
    *, transaction: RuntimeFullMemoryEventTransaction,
    lineage: RuntimeFullMemoryLineageProduct,
    node: RuntimeFullMemoryNodeProduct,
    history: HistoryAuthority,
    history_record: Mapping[str, Any] | None = None,
    history_fact_count: int,
    already_committed: bool,
) -> dict[str, Any]:
    material = {
        "version": RUNTIME_FULL_MEMORY_PROFILE_COMMIT_VERSION,
        "session_binding_fingerprint": (
            transaction.session.session_binding.fingerprint),
        "child_event_id": lineage.child_event_id,
        "acceptance_fingerprint": lineage.acceptance_fingerprint,
        "lineage_fingerprint": lineage.fingerprint,
        "node_fingerprint": node.fingerprint,
        "history_fingerprint": (history.to_record() if history_record is None else history_record)["fingerprint"],
        "transaction_receipt_fingerprint": model_data_fingerprint(
            transaction.receipt),
        "history_fact_count": history_fact_count,
        "canonical_history_only": True,
        "already_committed": already_committed,
        "single_profile_value": True,
        "storage_write_performed": False,
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


def _fact_from_node(
    node: RuntimeFullMemoryNodeProduct,
    transaction: RuntimeFullMemoryEventTransaction,
) -> CommittedFact:
    metadata = thaw_model_data(transaction.context.metadata)
    source_ref = str(
        metadata.get("source_receipt_fingerprint")
        or metadata.get("history_source_ref")
        or node.event_id
    ).strip()
    if not source_ref:
        raise ValueError("committed history fact requires a source reference")
    return CommittedFact.create(
        fact_id=node.event_id,
        session_binding_fingerprint=node.session_binding_fingerprint,
        source_ref=source_ref,
        scope_ref=node.source_conversation_id,
        visible_to_refs=(node.owner_participant_id,),
        temporal_position_fingerprint=node.temporal_position_fingerprint,
        accepted_runtime_ordinal=node.accepted_runtime_ordinal,
        content_ref={"event_id": node.event_id},
        model_payload={"runtime_full_memory_node": node.to_record()},
        settlement_ref={
            "acceptance_fingerprint": node.acceptance_fingerprint,
            "materialization_stage_receipt_fingerprint": (
                node.materialization_stage_receipt_fingerprint),
        },
    )


def _history_with_transaction(
    *,
    profile: Mapping[str, Any],
    binding_fingerprint: str,
    node: RuntimeFullMemoryNodeProduct,
    lineage: RuntimeFullMemoryLineageProduct,
    transaction: RuntimeFullMemoryEventTransaction,
) -> tuple[HistoryAuthority, bool]:
    stored = profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
    if stored is None:
        if (
            RUNTIME_FULL_MEMORY_NODE_JOURNAL_KEY in profile
            or RUNTIME_FULL_MEMORY_LINEAGE_JOURNAL_KEY in profile
        ):
            raise ValueError(
                "legacy memory journals require explicit history migration")
        history = HistoryAuthority(
            session_binding_fingerprint=binding_fingerprint)
    else:
        history = HistoryAuthority.from_record(stored)
        if history.session_binding_fingerprint != binding_fingerprint:
            raise ValueError("framework history binding is invalid")

    existing_fact = next(
        (fact for fact in history.facts() if fact.fact_id == node.event_id),
        None,
    )

    accepted_read = None
    if transaction.history_read is not None:
        read = transaction.history_read
        accepted_read = history.authorize_read(
            reader_ref=read.reader_ref,
            trigger_fact_id=read.trigger_fact_id,
            before_runtime_ordinal=read.before_runtime_ordinal,
            requested_fact_ids=read.fact_ids,
            authorization_ref=read.authorization_ref,
        )
        if accepted_read.fingerprint != read.fingerprint:
            raise ValueError("transaction history read differs from authority")

    history.append_fact(_fact_from_node(node, transaction))
    if accepted_read is None:
        return history, existing_fact is not None

    recall = ActualRecall.create(
        recall_id=(
            "actual-recall:"
            + (model_data_fingerprint({"trigger":node.event_id,
                 "stage_receipt":transaction.recall_execution.receipt.to_record()})
               if transaction.context.metadata.get("indexed_memory_query") is not None
               else transaction.recall_execution.receipt.output_fingerprint)
        ),
        trigger_fact_id=node.event_id,
        history_read_fingerprint=accepted_read.fingerprint,
        recalled_fact_ids=transaction.recall.recalled_event_ids,
        model_stage_receipt_fingerprint=model_data_fingerprint(
            transaction.recall_execution.receipt.to_record()),
        model_output_fingerprint=(
            transaction.recall_execution.receipt.output_fingerprint),
    )
    history.append_actual_recall(recall, history_read=accepted_read)
    edge_by_parent = {edge.parent_event_id: edge for edge in lineage.edges}
    for parent_id in recall.recalled_fact_ids:
        source = edge_by_parent.get(parent_id)
        if source is None:
            raise ValueError("lineage is incomplete for actual recall")
        history.append_influence_edge(InfluenceEdge.create(
            edge_id=f"influence:{lineage.fingerprint}:{parent_id}",
            recall_id=recall.recall_id,
            parent_fact_id=parent_id,
            child_fact_id=node.event_id,
            contribution_payload=source.projected_contribution,
            evidence={
                "lineage_fingerprint": lineage.fingerprint,
                "acceptance_fingerprint": lineage.acceptance_fingerprint,
                "aggregate_stage_output_fingerprint": (
                    lineage.aggregate_stage_output_fingerprint),
                "edge_evidence": source.edge_evidence,
            },
        ))
    return history, existing_fact is not None


def commit_runtime_full_memory_profile(
    *, transaction: RuntimeFullMemoryEventTransaction,
    model_runtime: BoundInteractionModel,
) -> RuntimeFullMemoryProfileCommit:
    """Return one complete profile value; storage remains the caller's job."""
    if not isinstance(transaction, RuntimeFullMemoryEventTransaction):
        raise ValueError(
            "transaction must be RuntimeFullMemoryEventTransaction")
    lineage = build_runtime_full_memory_lineage_product(
        transaction=transaction)
    node = build_runtime_full_memory_node_product(
        transaction=transaction, model_runtime=model_runtime)
    history, already_committed = _history_with_transaction(
        profile=transaction.session.profile,
        binding_fingerprint=(
            transaction.session.session_binding.fingerprint),
        node=node,
        lineage=lineage,
        transaction=transaction,
    )
    if already_committed:
        receipt = _receipt(
            transaction=transaction,
            lineage=lineage,
            node=node,
            history=history,
            history_fact_count=len(history.facts()),
            already_committed=True,
        )
        return RuntimeFullMemoryProfileCommit(
            session=transaction.session,
            transaction=transaction,
            lineage=lineage,
            node=node,
            receipt=receipt,
            already_committed=True,
        )

    profile = dict(transaction.session.profile)
    if transaction.context.metadata.get("history_read_storage") == "snapshot-ranges-v2":
        history._compact_storage = True
    history_record = history.to_record()
    profile[FRAMEWORK_HISTORY_PROFILE_KEY] = history_record
    receipt = _receipt(
        transaction=transaction,
        lineage=lineage,
        node=node,
        history=history,
        history_record=history_record,
        history_fact_count=len(history.facts()),
        already_committed=False,
    )
    session = BootstrappedRuntimeSession(
        profile=profile,
        session_binding=transaction.session.session_binding,
        position=transaction.session.position,
        parameters=transaction.session.parameters,
        state=transaction.session.state,
        receipt=receipt,
    )
    transaction_with_lineage = replace(transaction, session=session)
    return RuntimeFullMemoryProfileCommit(
        session=session,
        transaction=transaction_with_lineage,
        lineage=lineage,
        node=node,
        receipt=receipt,
        already_committed=False,
    )


__all__ = (
    "RUNTIME_FULL_MEMORY_LINEAGE_JOURNAL_KEY",
    "RUNTIME_FULL_MEMORY_NODE_JOURNAL_KEY",
    "RUNTIME_FULL_MEMORY_PROFILE_COMMIT_VERSION",
    "RuntimeFullMemoryProfileCommit",
    "commit_runtime_full_memory_profile",
    "read_runtime_full_memory_lineage_journal",
    "read_runtime_full_memory_node_journal",
)
