"""Post-acceptance, model-independent Full Memory lineage product.

This module does not recompute Memory, interpret model dimensions, mutate
state, or write storage.  It binds the exact model-produced parent/child edge
evidence and per-parent projected contributions to one Kernel acceptance.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data
from framework.model_api.identity import context_fact_identity
from ops.runtime_full_memory_event_transaction import (
    RuntimeFullMemoryEventTransaction,
)


RUNTIME_FULL_MEMORY_LINEAGE_VERSION = (
    "runtime-full-memory-lineage-product-v1")


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _freeze(value: Any, field: str = "value") -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain finite serializable model data")


def _same_data(left: object, right: object) -> bool:
    return model_data_fingerprint(left) == model_data_fingerprint(right)


@dataclass(frozen=True)
class RuntimeMemoryLineageEdge:
    parent_event_id: str
    child_event_id: str
    projected_contribution: Mapping[str, Any]
    contribution_fingerprint: str
    edge_evidence: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "parent_event_id", _identifier(
            self.parent_event_id, "parent_event_id"))
        object.__setattr__(self, "child_event_id", _identifier(
            self.child_event_id, "child_event_id"))
        if not isinstance(self.projected_contribution, Mapping):
            raise ValueError("projected_contribution must be a mapping")
        frozen_contribution = _freeze(
            dict(self.projected_contribution), "projected_contribution")
        object.__setattr__(
            self, "projected_contribution", frozen_contribution)
        fingerprint = _identifier(
            self.contribution_fingerprint, "contribution_fingerprint")
        if fingerprint != model_data_fingerprint(frozen_contribution):
            raise ValueError("projected contribution fingerprint is invalid")
        object.__setattr__(self, "contribution_fingerprint", fingerprint)
        if not isinstance(self.edge_evidence, Mapping):
            raise ValueError("edge_evidence must be a mapping")
        if self.edge_evidence.get("state_commit_authority") is not False:
            raise ValueError("lineage edge evidence cannot carry commit authority")
        object.__setattr__(
            self, "edge_evidence", _freeze(
                dict(self.edge_evidence), "edge_evidence"))

    def to_record(self) -> dict[str, Any]:
        return {
            "parent_event_id": self.parent_event_id,
            "child_event_id": self.child_event_id,
            "projected_contribution": thaw_model_data(
                self.projected_contribution),
            "contribution_fingerprint": self.contribution_fingerprint,
            "edge_evidence": thaw_model_data(self.edge_evidence),
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]):
        required = {
            "parent_event_id", "child_event_id", "projected_contribution",
            "contribution_fingerprint", "edge_evidence",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("runtime memory lineage edge schema is invalid")
        return cls(**dict(record))


@dataclass(frozen=True)
class RuntimeFullMemoryLineageProduct:
    version: str
    session_binding_fingerprint: str
    child_event_id: str
    temporal_position_fingerprint: str
    acceptance_fingerprint: str
    settlement_stage_receipt_fingerprint: str
    recall_stage_output_fingerprint: str
    influence_stage_output_fingerprint: str
    aggregate_stage_output_fingerprint: str
    parent_event_ids: tuple[str, ...]
    edges: tuple[RuntimeMemoryLineageEdge, ...]
    storage_commit_authority: bool
    current_evaluation_visible: bool
    future_evaluation_eligible: bool
    fingerprint: str

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "child_event_id": self.child_event_id,
            "temporal_position_fingerprint": (
                self.temporal_position_fingerprint),
            "acceptance_fingerprint": self.acceptance_fingerprint,
            "settlement_stage_receipt_fingerprint": (
                self.settlement_stage_receipt_fingerprint),
            "recall_stage_output_fingerprint": (
                self.recall_stage_output_fingerprint),
            "influence_stage_output_fingerprint": (
                self.influence_stage_output_fingerprint),
            "aggregate_stage_output_fingerprint": (
                self.aggregate_stage_output_fingerprint),
            "parent_event_ids": list(self.parent_event_ids),
            "edges": [edge.to_record() for edge in self.edges],
            "storage_commit_authority": self.storage_commit_authority,
            "current_evaluation_visible": self.current_evaluation_visible,
            "future_evaluation_eligible": self.future_evaluation_eligible,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]):
        required = {
            "version", "session_binding_fingerprint", "child_event_id",
            "temporal_position_fingerprint", "acceptance_fingerprint",
            "settlement_stage_receipt_fingerprint",
            "recall_stage_output_fingerprint",
            "influence_stage_output_fingerprint",
            "aggregate_stage_output_fingerprint", "parent_event_ids",
            "edges", "storage_commit_authority",
            "current_evaluation_visible", "future_evaluation_eligible",
            "fingerprint",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("runtime memory lineage product schema is invalid")
        material = dict(thaw_model_data(record))
        fingerprint = material.pop("fingerprint")
        if model_data_fingerprint(material) != fingerprint:
            raise ValueError("runtime memory lineage product fingerprint is invalid")
        if (
            material["version"] != RUNTIME_FULL_MEMORY_LINEAGE_VERSION
            or material["storage_commit_authority"] is not False
            or material["current_evaluation_visible"] is not False
            or material["future_evaluation_eligible"] is not True
        ):
            raise ValueError("runtime memory lineage product authority is invalid")
        parent_ids = tuple(
            _identifier(item, "parent_event_id")
            for item in material["parent_event_ids"]
        )
        if len(parent_ids) != len(set(parent_ids)):
            raise ValueError("runtime memory lineage parents contain duplicates")
        raw_edges = material["edges"]
        if not isinstance(raw_edges, list):
            raise ValueError("runtime memory lineage edges must be a list")
        edges = tuple(
            RuntimeMemoryLineageEdge.from_record(item) for item in raw_edges)
        if tuple(edge.parent_event_id for edge in edges) != parent_ids:
            raise ValueError("runtime memory lineage edge order differs from parents")
        child = _identifier(material["child_event_id"], "child_event_id")
        if any(edge.child_event_id != child for edge in edges):
            raise ValueError("runtime memory lineage edge child is inconsistent")
        return cls(
            version=material["version"],
            session_binding_fingerprint=_identifier(
                material["session_binding_fingerprint"],
                "session_binding_fingerprint"),
            child_event_id=child,
            temporal_position_fingerprint=_identifier(
                material["temporal_position_fingerprint"],
                "temporal_position_fingerprint"),
            acceptance_fingerprint=_identifier(
                material["acceptance_fingerprint"],
                "acceptance_fingerprint"),
            settlement_stage_receipt_fingerprint=_identifier(
                material["settlement_stage_receipt_fingerprint"],
                "settlement_stage_receipt_fingerprint"),
            recall_stage_output_fingerprint=_identifier(
                material["recall_stage_output_fingerprint"],
                "recall_stage_output_fingerprint"),
            influence_stage_output_fingerprint=_identifier(
                material["influence_stage_output_fingerprint"],
                "influence_stage_output_fingerprint"),
            aggregate_stage_output_fingerprint=_identifier(
                material["aggregate_stage_output_fingerprint"],
                "aggregate_stage_output_fingerprint"),
            parent_event_ids=parent_ids,
            edges=edges,
            storage_commit_authority=False,
            current_evaluation_visible=False,
            future_evaluation_eligible=True,
            fingerprint=fingerprint,
        )


def _published_product(execution, field: str) -> Mapping[str, Any]:
    published = execution.payload.get(field)
    if not isinstance(published, Mapping):
        raise ValueError(f"{field} is missing from its signed stage output")
    if execution.receipt.output_fingerprint != model_data_fingerprint(
            execution.payload):
        raise ValueError(f"{field} stage output fingerprint is invalid")
    return published


def build_runtime_full_memory_lineage_product(
    *, transaction: RuntimeFullMemoryEventTransaction,
) -> RuntimeFullMemoryLineageProduct:
    """Bind model-produced edges to one already accepted settlement."""
    if not isinstance(transaction, RuntimeFullMemoryEventTransaction):
        raise ValueError(
            "transaction must be RuntimeFullMemoryEventTransaction")
    context = transaction.context
    identity = context_fact_identity(context)
    if identity.event_ref is None or context.temporal_position is None:
        raise ValueError("lineage product requires committed event context")

    recall_published = _published_product(
        transaction.recall_execution, "recall_decision")
    influence_published = _published_product(
        transaction.influence_execution, "historical_influence")
    aggregate_published = _published_product(
        transaction.aggregate_execution, "historical_aggregate")
    settlement_published = _published_product(
        transaction.settlement_execution, "settlement")

    recall_ids = transaction.recall.recalled_event_ids
    influence_ids = tuple(influence_published.get("recalled_event_ids") or ())
    if len(influence_ids) != len(recall_ids) or set(influence_ids) != set(recall_ids):
        raise ValueError("influence exact IDs differ from recall stage")
    aggregate_ids = tuple(aggregate_published.get("recalled_event_ids") or ())
    if len(aggregate_ids) != len(recall_ids) or set(aggregate_ids) != set(recall_ids):
        raise ValueError("aggregate exact IDs differ from recall stage")
    if not _same_data(
        aggregate_published.get("aggregate"),
        transaction.aggregate.aggregate,
    ):
        raise ValueError("aggregate product differs from signed stage output")
    if transaction.aggregate.source_influence_output_fingerprint != (
            transaction.influence_execution.receipt.output_fingerprint):
        raise ValueError("aggregate does not reference the influence stage")

    settlement_input = {
        "state": settlement_published.get("state_before"),
        "parameters": transaction.session.parameters.parameters,
        "progress_contribution": (
            None if transaction.progress_preparation is None
            else transaction.progress_preparation.contribution
        ),
        "event_contribution": transaction.event_contribution_execution.payload.get(
            "event_contribution"),
        "historical_aggregate": aggregate_published,
    }
    if transaction.settlement_execution.receipt.input_fingerprint != (
            model_data_fingerprint(settlement_input)):
        raise ValueError("accepted settlement does not consume this aggregate")
    acceptance = transaction.acceptance
    if acceptance.stage_receipt_fingerprint != model_data_fingerprint(
            transaction.settlement_execution.receipt.to_record()):
        raise ValueError("acceptance does not reference this settlement stage")
    if transaction.session.state != acceptance.state or (
            transaction.session.position != acceptance.position):
        raise ValueError("transaction session differs from Kernel acceptance")
    if not _same_data(
            acceptance.state.state,
            settlement_published.get("state_candidate")):
        raise ValueError("accepted state differs from settlement candidate")
    anchor = transaction.session.profile.get("runtime_session_binding")
    if not isinstance(anchor, Mapping) or not _same_data(
            anchor.get("last_state_commit"), acceptance.to_record()):
        raise ValueError("session profile does not publish this acceptance")

    raw_edges = influence_published.get("later_edges")
    raw_aggregate = aggregate_published.get("aggregate")
    if not isinstance(raw_edges, (tuple, list)):
        raise ValueError("historical influence later_edges must be a sequence")
    if not isinstance(raw_aggregate, Mapping):
        raise ValueError("historical aggregate payload must be a mapping")
    contributions = raw_aggregate.get("event_projected_contributions")
    if not isinstance(contributions, Mapping):
        raise ValueError("historical aggregate contributions must be a mapping")
    edge_by_parent = {}
    for raw_edge in raw_edges:
        if not isinstance(raw_edge, Mapping):
            raise ValueError("historical lineage edge must be a mapping")
        parent = _identifier(
            raw_edge.get("parent_event_id"), "parent_event_id")
        child = _identifier(
            raw_edge.get("child_event_id"), "child_event_id")
        evidence = raw_edge.get("edge_evidence")
        if parent in edge_by_parent:
            raise ValueError("historical lineage contains duplicate parents")
        if child != identity.event_ref:
            raise ValueError("historical lineage child differs from trigger")
        if not isinstance(evidence, Mapping):
            raise ValueError("historical lineage edge evidence must be a mapping")
        edge_by_parent[parent] = evidence
    if set(edge_by_parent) != set(recall_ids) or set(contributions) != set(
            recall_ids):
        raise ValueError("lineage edges and contributions must match recall IDs")

    edges = tuple(
        RuntimeMemoryLineageEdge(
            parent_event_id=parent,
            child_event_id=identity.event_ref,
            projected_contribution=contributions[parent],
            contribution_fingerprint=model_data_fingerprint(
                contributions[parent]),
            edge_evidence=edge_by_parent[parent],
        )
        for parent in recall_ids
    )
    material = {
        "version": RUNTIME_FULL_MEMORY_LINEAGE_VERSION,
        "session_binding_fingerprint": (
            transaction.session.session_binding.fingerprint),
        "child_event_id": identity.event_ref,
        "temporal_position_fingerprint": (
            context.temporal_position.fingerprint),
        "acceptance_fingerprint": acceptance.fingerprint,
        "settlement_stage_receipt_fingerprint": (
            acceptance.stage_receipt_fingerprint),
        "recall_stage_output_fingerprint": (
            transaction.recall_execution.receipt.output_fingerprint),
        "influence_stage_output_fingerprint": (
            transaction.influence_execution.receipt.output_fingerprint),
        "aggregate_stage_output_fingerprint": (
            transaction.aggregate_execution.receipt.output_fingerprint),
        "parent_event_ids": list(recall_ids),
        "edges": [edge.to_record() for edge in edges],
        "storage_commit_authority": False,
        "current_evaluation_visible": False,
        "future_evaluation_eligible": True,
    }
    return RuntimeFullMemoryLineageProduct(
        version=material["version"],
        session_binding_fingerprint=material[
            "session_binding_fingerprint"],
        child_event_id=material["child_event_id"],
        temporal_position_fingerprint=material[
            "temporal_position_fingerprint"],
        acceptance_fingerprint=material["acceptance_fingerprint"],
        settlement_stage_receipt_fingerprint=material[
            "settlement_stage_receipt_fingerprint"],
        recall_stage_output_fingerprint=material[
            "recall_stage_output_fingerprint"],
        influence_stage_output_fingerprint=material[
            "influence_stage_output_fingerprint"],
        aggregate_stage_output_fingerprint=material[
            "aggregate_stage_output_fingerprint"],
        parent_event_ids=recall_ids,
        edges=edges,
        storage_commit_authority=False,
        current_evaluation_visible=False,
        future_evaluation_eligible=True,
        fingerprint=model_data_fingerprint(material),
    )


__all__ = (
    "RUNTIME_FULL_MEMORY_LINEAGE_VERSION",
    "RuntimeFullMemoryLineageProduct",
    "RuntimeMemoryLineageEdge",
    "build_runtime_full_memory_lineage_product",
)
