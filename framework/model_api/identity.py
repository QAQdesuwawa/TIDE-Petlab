"""Identity comparison across the v2 contract and its legacy adapter."""
from __future__ import annotations

from dataclasses import dataclass

from framework.model_api import ModelInvocationContext, ModelStageReceipt
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)
from framework.temporal_domain import TemporalPosition


@dataclass(frozen=True)
class ModelFactIdentity:
    session_id: str
    scope_ref: str | None
    subject_ref: str | None
    event_ref: str | None
    action_ref: str | None
    temporal_position: TemporalPosition | None
    runtime_ordinal: int | None
    legacy_scalar_position: int | None


def context_fact_identity(
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> ModelFactIdentity:
    if isinstance(context, ModelInvocationContextV2):
        return ModelFactIdentity(
            session_id=context.session_id,
            scope_ref=context.scope_ref,
            subject_ref=context.subject_ref,
            event_ref=context.event_ref,
            action_ref=context.action_ref,
            temporal_position=context.temporal_position,
            runtime_ordinal=context.runtime_ordinal,
            legacy_scalar_position=None,
        )
    if isinstance(context, ModelInvocationContext):
        return ModelFactIdentity(
            session_id=context.session_id,
            scope_ref=context.conversation_id,
            subject_ref=context.participant_id,
            event_ref=context.event_id,
            action_ref=context.action_id,
            temporal_position=context.temporal_position,
            runtime_ordinal=context.runtime_ordinal,
            legacy_scalar_position=context.at_ms,
        )
    raise TypeError("unsupported model invocation context")


def receipt_fact_identity(
    receipt: ModelStageReceipt | ModelStageReceiptV2,
) -> ModelFactIdentity:
    if isinstance(receipt, ModelStageReceiptV2):
        return ModelFactIdentity(
            session_id=receipt.session_id,
            scope_ref=receipt.scope_ref,
            subject_ref=receipt.subject_ref,
            event_ref=receipt.event_ref,
            action_ref=receipt.action_ref,
            temporal_position=receipt.temporal_position,
            runtime_ordinal=receipt.runtime_ordinal,
            legacy_scalar_position=None,
        )
    if isinstance(receipt, ModelStageReceipt):
        return ModelFactIdentity(
            session_id=receipt.session_id,
            scope_ref=receipt.conversation_id,
            subject_ref=receipt.participant_id,
            event_ref=receipt.event_id,
            action_ref=receipt.action_id,
            temporal_position=receipt.temporal_position,
            runtime_ordinal=receipt.runtime_ordinal,
            legacy_scalar_position=receipt.at_ms,
        )
    raise TypeError("unsupported model stage receipt")


def same_model_fact_identity(
    context: ModelInvocationContext | ModelInvocationContextV2,
    receipt: ModelStageReceipt | ModelStageReceiptV2,
) -> bool:
    left = context_fact_identity(context)
    right = receipt_fact_identity(receipt)
    if left.temporal_position is None or right.temporal_position is None:
        same_position = (
            left.temporal_position is right.temporal_position
            and left.legacy_scalar_position == right.legacy_scalar_position
        )
    else:
        same_position = (
            left.temporal_position.fingerprint
            == right.temporal_position.fingerprint
        )
    return (
        left.session_id == right.session_id
        and left.scope_ref == right.scope_ref
        and left.subject_ref == right.subject_ref
        and left.event_ref == right.event_ref
        and left.action_ref == right.action_ref
        and left.runtime_ordinal == right.runtime_ordinal
        and same_position
    )


__all__ = (
    "ModelFactIdentity",
    "context_fact_identity",
    "receipt_fact_identity",
    "same_model_fact_identity",
)
