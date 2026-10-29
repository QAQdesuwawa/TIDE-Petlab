"""Post-acceptance, package-produced canonical Memory node product."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelStageExecution,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import context_fact_identity
from framework.model_runtime import BoundInteractionModel
from ops.runtime_full_memory_event_transaction import (
    RuntimeFullMemoryEventTransaction,
)
from ops.runtime_stage_verification import verify_runtime_stage_execution


RUNTIME_FULL_MEMORY_NODE_PRODUCT_VERSION = (
    "runtime-full-memory-node-product-v2")


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


@dataclass(frozen=True)
class RuntimeFullMemoryNodeProduct:
    version: str
    session_binding_fingerprint: str
    owner_participant_id: str
    source_conversation_id: str
    event_id: str
    accepted_runtime_ordinal: int
    temporal_position_fingerprint: str
    acceptance_fingerprint: str
    materialization_stage_receipt_fingerprint: str
    node_projection: Mapping[str, Any]
    node_projection_fingerprint: str
    node_evolution_binding: Mapping[str, Any]
    storage_commit_authority: bool
    current_evaluation_visible: bool
    future_evaluation_eligible: bool
    fingerprint: str

    def __post_init__(self) -> None:
        for field in (
            "version", "session_binding_fingerprint", "owner_participant_id",
            "source_conversation_id", "event_id",
            "temporal_position_fingerprint", "acceptance_fingerprint",
            "materialization_stage_receipt_fingerprint",
            "node_projection_fingerprint", "fingerprint",
        ):
            object.__setattr__(self, field, _identifier(
                getattr(self, field), field))
        if (
            isinstance(self.accepted_runtime_ordinal, bool)
            or not isinstance(self.accepted_runtime_ordinal, int)
            or self.accepted_runtime_ordinal < 1
        ):
            raise ValueError("accepted_runtime_ordinal must be positive")
        if not isinstance(self.node_projection, Mapping):
            raise ValueError("node_projection must be a mapping")
        frozen = freeze_model_payload(self.node_projection)
        object.__setattr__(self, "node_projection", frozen)
        if model_data_fingerprint(frozen) != self.node_projection_fingerprint:
            raise ValueError("memory node projection fingerprint is invalid")
        if not isinstance(self.node_evolution_binding, Mapping):
            raise ValueError("node_evolution_binding must be a mapping")
        frozen_evolution = freeze_model_payload(self.node_evolution_binding)
        object.__setattr__(
            self, "node_evolution_binding", frozen_evolution)
        evolution_material = dict(thaw_model_data(frozen_evolution))
        evolution_fingerprint = evolution_material.pop("fingerprint", None)
        if evolution_fingerprint != model_data_fingerprint(evolution_material):
            raise ValueError("memory node evolution binding is invalid")
        if (
            self.storage_commit_authority is not False
            or self.current_evaluation_visible is not False
            or self.future_evaluation_eligible is not True
        ):
            raise ValueError("memory node product authority is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "owner_participant_id": self.owner_participant_id,
            "source_conversation_id": self.source_conversation_id,
            "event_id": self.event_id,
            "accepted_runtime_ordinal": self.accepted_runtime_ordinal,
            "temporal_position_fingerprint": (
                self.temporal_position_fingerprint),
            "acceptance_fingerprint": self.acceptance_fingerprint,
            "materialization_stage_receipt_fingerprint": (
                self.materialization_stage_receipt_fingerprint),
            "node_projection": thaw_model_data(self.node_projection),
            "node_projection_fingerprint": self.node_projection_fingerprint,
            "node_evolution_binding": thaw_model_data(
                self.node_evolution_binding),
            "storage_commit_authority": self.storage_commit_authority,
            "current_evaluation_visible": self.current_evaluation_visible,
            "future_evaluation_eligible": self.future_evaluation_eligible,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]):
        required = {
            "version", "session_binding_fingerprint", "owner_participant_id",
            "source_conversation_id", "event_id", "accepted_runtime_ordinal",
            "temporal_position_fingerprint", "acceptance_fingerprint",
            "materialization_stage_receipt_fingerprint", "node_projection",
            "node_projection_fingerprint", "node_evolution_binding",
            "storage_commit_authority",
            "current_evaluation_visible", "future_evaluation_eligible",
            "fingerprint",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("runtime full memory node schema is invalid")
        material = dict(thaw_model_data(record))
        fingerprint = material.pop("fingerprint")
        if model_data_fingerprint(material) != fingerprint:
            raise ValueError("runtime full memory node fingerprint is invalid")
        if material.get("version") != RUNTIME_FULL_MEMORY_NODE_PRODUCT_VERSION:
            raise ValueError("runtime full memory node version is unsupported")
        return cls(**material, fingerprint=fingerprint)


def build_runtime_full_memory_node_product(
    *,
    transaction: RuntimeFullMemoryEventTransaction,
    model_runtime: BoundInteractionModel,
) -> RuntimeFullMemoryNodeProduct:
    """Materialize the accepted child as an opaque future candidate node."""
    if not isinstance(transaction, RuntimeFullMemoryEventTransaction):
        raise ValueError(
            "transaction must be RuntimeFullMemoryEventTransaction")
    if not isinstance(model_runtime, BoundInteractionModel):
        raise ValueError("model_runtime must be BoundInteractionModel")
    if transaction.session.session_binding.model_binding != model_runtime.binding:
        raise ValueError("memory node model binding differs from transaction")
    context = transaction.context
    identity = context_fact_identity(context)
    if identity.event_ref is None or context.temporal_position is None:
        raise ValueError("memory node requires committed event context")
    if identity.subject_ref is None or identity.scope_ref is None:
        raise ValueError("memory node requires owner and source conversation")
    if transaction.session.state != transaction.acceptance.state or (
            transaction.session.position != transaction.acceptance.position):
        raise ValueError("memory node transaction is not accepted")
    invocation_payload = {
        "parameters": transaction.session.parameters.parameters,
        "event_contribution": (
            transaction.event_contribution_execution.payload[
                "event_contribution"]),
        "acceptance_fingerprint": transaction.acceptance.fingerprint,
    }
    execution = model_runtime.invoke(
        "materialize_memory_event",
        payload=invocation_payload,
        context=context,
    )
    verify_runtime_stage_execution(
        execution,
        stage="materialize_memory_event",
        runtime=model_runtime,
        context=context,
        invocation_payload=invocation_payload,
    )
    published = execution.payload.get("memory_node")
    if not isinstance(published, Mapping) or set(published) != {
        "version", "event_id", "temporal_position_fingerprint",
        "acceptance_fingerprint", "projection", "state_commit_authority",
        "memory_commit_authority", "evolution_binding",
    }:
        raise ValueError("materialized memory node fields are invalid")
    if published.get("event_id") != identity.event_ref:
        raise ValueError("materialized memory node event differs from context")
    if published.get("temporal_position_fingerprint") != (
            context.temporal_position.fingerprint):
        raise ValueError("materialized memory node position is invalid")
    if published.get("acceptance_fingerprint") != (
            transaction.acceptance.fingerprint):
        raise ValueError("materialized memory node acceptance is invalid")
    if (
        published.get("state_commit_authority") is not False
        or published.get("memory_commit_authority") is not False
    ):
        raise ValueError("materialized memory node carries forbidden authority")
    projection = published.get("projection")
    if not isinstance(projection, Mapping):
        raise ValueError("materialized memory node projection is invalid")
    evolution_binding = published.get("evolution_binding")
    if not isinstance(evolution_binding, Mapping):
        raise ValueError("materialized memory node evolution binding is invalid")
    projection_fingerprint = model_data_fingerprint(projection)
    material = {
        "version": RUNTIME_FULL_MEMORY_NODE_PRODUCT_VERSION,
        "session_binding_fingerprint": (
            transaction.session.session_binding.fingerprint),
        "owner_participant_id": identity.subject_ref,
        "source_conversation_id": identity.scope_ref,
        "event_id": identity.event_ref,
        "accepted_runtime_ordinal": (
            transaction.acceptance.position.runtime_ordinal),
        "temporal_position_fingerprint": (
            context.temporal_position.fingerprint),
        "acceptance_fingerprint": transaction.acceptance.fingerprint,
        "materialization_stage_receipt_fingerprint": model_data_fingerprint(
            execution.receipt.to_record()),
        "node_projection": projection,
        "node_projection_fingerprint": projection_fingerprint,
        "node_evolution_binding": evolution_binding,
        "storage_commit_authority": False,
        "current_evaluation_visible": False,
        "future_evaluation_eligible": True,
    }
    return RuntimeFullMemoryNodeProduct(
        **material,
        fingerprint=model_data_fingerprint(material),
    )


__all__ = (
    "RUNTIME_FULL_MEMORY_NODE_PRODUCT_VERSION",
    "RuntimeFullMemoryNodeProduct",
    "build_runtime_full_memory_node_product",
)
