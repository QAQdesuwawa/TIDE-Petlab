"""Bind one committed product fact to its post-commit expression projection.

The fact has already crossed the only Store mutation boundary before this
module is called.  This module only verifies and records lineage between the
product evidence, committed transaction, and read-only expression result.  It
owns no Runner, Store, Writer, provider, clock, dynamic tick, or send path.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Union

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import context_fact_identity
from ops.model_expression_projection_shadow import ExpressionProjectionShadow
from ops.runtime_committed_fact_transaction import (
    RuntimeCommittedFactTransaction,
)
from ops.runtime_full_memory_committed_fact_transaction import (
    RuntimeFullMemoryCommittedFactTransaction,
)
from ops.runtime_product_fact_bridge import RuntimeCommittedFactEvidence
from ops.runtime_product_expression_continuation import (
    RecoveredRuntimeProductFactTransaction,
)


PRODUCT_FACT_LIFECYCLE_VERSION = "runtime-product-fact-lifecycle-v1"
RuntimeProductFactTransaction = Union[
    RuntimeCommittedFactTransaction,
    RuntimeFullMemoryCommittedFactTransaction,
    RecoveredRuntimeProductFactTransaction,
]
_PRODUCT_FACT_TRANSACTION_TYPES = (
    RuntimeCommittedFactTransaction,
    RuntimeFullMemoryCommittedFactTransaction,
    RecoveredRuntimeProductFactTransaction,
)


@dataclass(frozen=True)
class RuntimeProductFactLifecycle:
    """One committed fact and its verified, read-only expression product."""

    evidence: RuntimeCommittedFactEvidence
    transaction: RuntimeProductFactTransaction
    expression: ExpressionProjectionShadow
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


class PostCommitExpressionProjectionError(RuntimeError):
    """Projection failed after the fact became authoritative.

    The committed transaction is retained so a caller can audit the fact and
    retry only the read-only projection.  The provider/model error text is not
    copied into the lifecycle message or receipt.
    """

    def __init__(
        self,
        *,
        transaction: RuntimeProductFactTransaction,
        evidence: RuntimeCommittedFactEvidence,
        cause: Exception,
    ) -> None:
        if not isinstance(transaction, _PRODUCT_FACT_TRANSACTION_TYPES):
            raise ValueError("post-commit error requires committed transaction")
        if not isinstance(evidence, RuntimeCommittedFactEvidence):
            raise ValueError("post-commit error requires product fact evidence")
        self.transaction = transaction
        self.evidence_receipt_fingerprint = model_data_fingerprint(
            evidence.receipt)
        self.cause_type = type(cause).__name__
        self.receipt = freeze_model_payload({
            "version": PRODUCT_FACT_LIFECYCLE_VERSION,
            "status": "expression_projection_failed_after_fact_commit",
            "event_id": context_fact_identity(
                transaction.event_transaction.context).event_ref,
            "event_kind": evidence.event_kind,
            "response_policy": evidence.response_policy,
            "evidence_receipt_fingerprint": (
                self.evidence_receipt_fingerprint),
            "transaction_receipt_fingerprint": model_data_fingerprint(
                transaction.receipt),
            "cause_type": self.cause_type,
            "authority": {
                "fact_rollback": False,
                "fact_recommit": False,
                "writer_attempt": False,
                "provider_call": False,
                "dynamic_tick": False,
                "send": False,
            },
        })
        super().__init__(
            "post-commit expression projection failed; "
            "the committed fact remains authoritative"
        )


def _verify_transaction_receipt(
    transaction: RuntimeProductFactTransaction,
) -> None:
    receipt = thaw_model_data(transaction.receipt)
    fingerprint = receipt.pop("fingerprint", None)
    if model_data_fingerprint(receipt) != fingerprint:
        raise ValueError("committed transaction receipt fingerprint is invalid")
    context = transaction.event_transaction.context
    identity = context_fact_identity(context)
    if (
        receipt.get("event_ref") != identity.event_ref
        or receipt.get("session_id") != identity.session_id
        or transaction.session is not transaction.event_transaction.session
    ):
        raise ValueError("committed transaction receipt differs from event")


def _verify_evidence_transaction_lineage(
    evidence: RuntimeCommittedFactEvidence,
    transaction: RuntimeProductFactTransaction,
) -> None:
    context = transaction.event_transaction.context
    identity = context_fact_identity(context)
    if (
        identity.scope_ref != evidence.conversation_id
        or identity.subject_ref != evidence.participant_id
        or identity.event_ref != evidence.event_id
        or identity.action_ref != evidence.action_id
        or thaw_model_data(context.metadata)
        != thaw_model_data(evidence.context_metadata)
    ):
        raise ValueError("committed transaction differs from product evidence")


def _verify_expression_lineage(
    transaction: RuntimeProductFactTransaction,
    expression: ExpressionProjectionShadow,
) -> None:
    event = transaction.event_transaction
    context = expression.context
    event_identity = context_fact_identity(event.context)
    expression_identity = context_fact_identity(context)
    accepted = event.acceptance.position
    position = context.temporal_position
    if (
        expression_identity.session_id != event_identity.session_id
        or expression_identity.scope_ref != event_identity.scope_ref
        or expression_identity.subject_ref != event_identity.subject_ref
        or expression_identity.event_ref != event_identity.event_ref
        or expression_identity.action_ref != event_identity.action_ref
        or context.runtime_ordinal != accepted.runtime_ordinal
        or position is None
        or position.fingerprint != accepted.position.fingerprint
        or expression.batch.selected_resource_ids
        != tuple(thaw_model_data(expression.receipt).get(
            "selected_resource_ids", ()))
    ):
        raise ValueError("expression projection differs from committed event")


def complete_product_fact_lifecycle(
    *,
    evidence: RuntimeCommittedFactEvidence,
    transaction: RuntimeProductFactTransaction,
    expression: ExpressionProjectionShadow,
) -> RuntimeProductFactLifecycle:
    """Verify three already-produced values and publish one lineage result."""
    if not isinstance(evidence, RuntimeCommittedFactEvidence):
        raise ValueError("lifecycle requires RuntimeCommittedFactEvidence")
    if not isinstance(transaction, _PRODUCT_FACT_TRANSACTION_TYPES):
        raise ValueError("lifecycle requires a formal committed fact transaction")
    if not isinstance(expression, ExpressionProjectionShadow):
        raise ValueError("lifecycle requires ExpressionProjectionShadow")
    _verify_transaction_receipt(transaction)
    _verify_evidence_transaction_lineage(evidence, transaction)
    _verify_expression_lineage(transaction, expression)

    event = transaction.event_transaction.context
    event_identity = context_fact_identity(event)
    material = {
        "version": PRODUCT_FACT_LIFECYCLE_VERSION,
        "status": "committed_with_expression_projection",
        "session_id": event_identity.session_id,
        "conversation_id": event_identity.scope_ref,
        "participant_id": event_identity.subject_ref,
        "event_id": event_identity.event_ref,
        "action_id": event_identity.action_ref,
        "event_kind": evidence.event_kind,
        "response_policy": evidence.response_policy,
        "evidence_receipt_fingerprint": model_data_fingerprint(
            evidence.receipt),
        "transaction_receipt_fingerprint": model_data_fingerprint(
            transaction.receipt),
        "expression_receipt_fingerprint": model_data_fingerprint(
            expression.receipt),
        "authority": {
            "history_write": False,
            "writer_attempt": False,
            "provider_call": False,
            "dynamic_tick": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeProductFactLifecycle(
        evidence=evidence,
        transaction=transaction,
        expression=expression,
        receipt=receipt,
    )


__all__ = (
    "PRODUCT_FACT_LIFECYCLE_VERSION",
    "PostCommitExpressionProjectionError",
    "RuntimeProductFactTransaction",
    "RuntimeProductFactLifecycle",
    "complete_product_fact_lifecycle",
)
