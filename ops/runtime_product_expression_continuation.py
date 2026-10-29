"""Restart-safe continuation for post-commit expression projection.

The interaction fact is already authoritative before this product is used.
The continuation freezes only the exact accepted settlement inputs required to
repeat the read-only expression projection after a process boundary.  It owns
no persistence, action, clock, routing, or delivery authority.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any
from framework.model_api.immutable_record import ImmutableModelRecord
from ops.owned_record_validation import OwnedJournalValidation

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    ModelStageReceipt,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import context_fact_identity
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import (
    RuntimeStateCommitAcceptance,
    RuntimeStateCommitAuthority,
)
from framework.temporal_domain import TemporalPosition
from framework.temporal_runtime import BoundTemporalDomain
from ops.model_expression_projection_shadow import (
    ExpressionProjectionShadow,
    produce_expression_projection_shadow,
)
from ops.runtime_committed_fact_transaction import (
    RuntimeCommittedFactTransaction,
)
from ops.runtime_full_memory_committed_fact_transaction import (
    RuntimeFullMemoryCommittedFactTransaction,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession
from ops.runtime_product_fact_bridge import RuntimeCommittedFactEvidence


PRODUCT_EXPRESSION_CONTINUATION_VERSION = (
    "runtime-product-expression-continuation-v1")
RUNTIME_PRODUCT_EXPRESSION_CONTINUATIONS_KEY = (
    "runtime_product_expression_continuations")
RUNTIME_PRODUCT_EXPRESSION_COMPLETIONS_KEY = (
    "runtime_product_expression_completions")
PRODUCT_EXPRESSION_COMPLETION_VERSION = (
    "runtime-product-expression-completion-v1")
_TRANSACTION_TYPES = (
    RuntimeCommittedFactTransaction,
    RuntimeFullMemoryCommittedFactTransaction,
)


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _verified_record(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be record data")
    record = dict(thaw_model_data(value))
    fingerprint = record.pop("fingerprint", None)
    if model_data_fingerprint(record) != fingerprint:
        raise ValueError(f"{label} fingerprint is invalid")
    return record


def _position(
    record: Mapping[str, Any] | None, *, session_binding,
) -> TemporalPosition | None:
    if record is None:
        return None
    if not isinstance(record, Mapping):
        raise ValueError("continuation temporal position must be record data")
    if set(record) != {"binding", "coordinate", "fingerprint"}:
        raise ValueError("continuation temporal position fields are invalid")
    if record.get("binding") != session_binding.temporal_binding.to_record():
        raise ValueError("continuation temporal binding is invalid")
    restored = TemporalPosition.create(
        binding=session_binding.temporal_binding,
        coordinate=record.get("coordinate"),
    )
    if restored.to_record() != dict(record):
        raise ValueError("continuation temporal position fingerprint is invalid")
    return restored


def _context(
    record: Mapping[str, Any], *, session_binding,
) -> ModelInvocationContext | ModelInvocationContextV2:
    if not isinstance(record, Mapping):
        raise ValueError("continuation context must be record data")
    legacy_common = {
        "session_id", "conversation_id", "participant_id", "event_id",
        "action_id", "metadata",
    }
    fields = set(record)
    scalar_legacy_fields = legacy_common | {"at_ms"}
    temporal_legacy_fields = legacy_common | {
        "temporal_position", "runtime_ordinal"}
    neutral_fields = {
        "session_id", "scope_ref", "subject_ref", "event_ref",
        "action_ref", "temporal_position", "runtime_ordinal", "metadata",
    }
    if fields == neutral_fields:
        context = ModelInvocationContextV2(
            session_id=record.get("session_id"),
            scope_ref=record.get("scope_ref"),
            subject_ref=record.get("subject_ref"),
            event_ref=record.get("event_ref"),
            action_ref=record.get("action_ref"),
            temporal_position=_position(
                record.get("temporal_position"),
                session_binding=session_binding,
            ),
            runtime_ordinal=record.get("runtime_ordinal"),
            metadata=record.get("metadata") or {},
        )
    elif fields in (scalar_legacy_fields, temporal_legacy_fields):
        context = ModelInvocationContext(
            session_id=record.get("session_id"),
            conversation_id=record.get("conversation_id"),
            participant_id=record.get("participant_id"),
            event_id=record.get("event_id"),
            action_id=record.get("action_id"),
            at_ms=(record.get("at_ms")
                   if fields == scalar_legacy_fields else None),
            temporal_position=_position(
                record.get("temporal_position"),
                session_binding=session_binding,
            ) if fields == temporal_legacy_fields else None,
            runtime_ordinal=(record.get("runtime_ordinal")
                             if fields == temporal_legacy_fields else None),
            metadata=record.get("metadata") or {},
        )
    else:
        raise ValueError("continuation context fields are invalid")
    if context.session_id != session_binding.session_id:
        raise ValueError("continuation context session is invalid")
    return context


def _stage_receipt(
    record: Mapping[str, Any], *, session_binding,
) -> ModelStageReceipt | ModelStageReceiptV2:
    if not isinstance(record, Mapping):
        raise ValueError("continuation stage receipt must be record data")
    common = {
        "package_id", "package_version", "stage", "operator_version",
        "schema_fingerprint", "parameter_fingerprint", "session_id",
        "input_fingerprint", "output_fingerprint",
    }
    fields = set(record)
    legacy_identity = {
        "conversation_id", "participant_id", "event_id", "action_id"}
    scalar_legacy_fields = common | legacy_identity | {"at_ms"}
    temporal_legacy_fields = common | legacy_identity | {
        "temporal_position", "runtime_ordinal"}
    neutral_fields = common | {
        "scope_ref", "subject_ref", "event_ref", "action_ref",
        "temporal_position", "runtime_ordinal",
    }
    if fields == neutral_fields:
        return ModelStageReceiptV2(
            package_id=record.get("package_id"),
            package_version=record.get("package_version"),
            stage=record.get("stage"),
            operator_version=record.get("operator_version"),
            schema_fingerprint=record.get("schema_fingerprint"),
            parameter_fingerprint=record.get("parameter_fingerprint"),
            session_id=record.get("session_id"),
            scope_ref=record.get("scope_ref"),
            subject_ref=record.get("subject_ref"),
            event_ref=record.get("event_ref"),
            action_ref=record.get("action_ref"),
            temporal_position=_position(
                record.get("temporal_position"),
                session_binding=session_binding,
            ),
            runtime_ordinal=record.get("runtime_ordinal"),
            input_fingerprint=record.get("input_fingerprint"),
            output_fingerprint=record.get("output_fingerprint"),
        )
    if fields not in (scalar_legacy_fields, temporal_legacy_fields):
        raise ValueError("continuation stage receipt fields are invalid")
    return ModelStageReceipt(
        package_id=record.get("package_id"),
        package_version=record.get("package_version"),
        stage=record.get("stage"),
        operator_version=record.get("operator_version"),
        schema_fingerprint=record.get("schema_fingerprint"),
        parameter_fingerprint=record.get("parameter_fingerprint"),
        session_id=record.get("session_id"),
        conversation_id=record.get("conversation_id"),
        participant_id=record.get("participant_id"),
        event_id=record.get("event_id"),
        action_id=record.get("action_id"),
        at_ms=(record.get("at_ms")
               if fields == scalar_legacy_fields else None),
        temporal_position=_position(
            record.get("temporal_position"), session_binding=session_binding)
        if fields == temporal_legacy_fields else None,
        runtime_ordinal=(
            record.get("runtime_ordinal")
            if fields == temporal_legacy_fields else None),
        input_fingerprint=record.get("input_fingerprint"),
        output_fingerprint=record.get("output_fingerprint"),
    )


@dataclass(frozen=True)
class RuntimeProductExpressionContinuation:
    """Exact immutable inputs for one post-commit expression retry."""

    delivery_id: str
    ingress_receipt_fingerprint: str
    fact_evidence: Mapping[str, Any]
    transaction_receipt: Mapping[str, Any]
    previous_state: Mapping[str, Any]
    previous_position: Mapping[str, Any]
    model_parameters_fingerprint: str
    settlement_context: Mapping[str, Any]
    settlement_execution: Mapping[str, Any]
    settlement_acceptance: Mapping[str, Any]
    committed_remainders: Mapping[str, Any]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "delivery_id", _identifier(
            self.delivery_id, "continuation delivery ID"))
        for field in (
            "transaction_receipt", "previous_state", "previous_position",
            "fact_evidence",
            "settlement_context", "settlement_execution",
            "settlement_acceptance", "committed_remainders", "receipt",
        ):
            object.__setattr__(
                self, field, freeze_model_payload(getattr(self, field)))

    def to_record(self) -> dict[str, Any]:
        return {
            "delivery_id": self.delivery_id,
            "ingress_receipt_fingerprint": self.ingress_receipt_fingerprint,
            "fact_evidence": thaw_model_data(self.fact_evidence),
            "transaction_receipt": thaw_model_data(self.transaction_receipt),
            "previous_state": thaw_model_data(self.previous_state),
            "previous_position": thaw_model_data(self.previous_position),
            "model_parameters_fingerprint": self.model_parameters_fingerprint,
            "settlement_context": thaw_model_data(self.settlement_context),
            "settlement_execution": thaw_model_data(self.settlement_execution),
            "settlement_acceptance": thaw_model_data(self.settlement_acceptance),
            "committed_remainders": thaw_model_data(self.committed_remainders),
            "receipt": thaw_model_data(self.receipt),
        }

    @classmethod
    def from_record(
        cls, record: Mapping[str, Any], *, session_binding,
    ) -> "RuntimeProductExpressionContinuation":
        if not isinstance(record, Mapping):
            raise ValueError("expression continuation must be record data")
        required = {
            "delivery_id", "ingress_receipt_fingerprint",
            "fact_evidence",
            "transaction_receipt", "previous_state", "previous_position",
            "model_parameters_fingerprint", "settlement_context",
            "settlement_execution", "settlement_acceptance",
            "committed_remainders", "receipt",
        }
        if set(record) != required:
            raise ValueError("expression continuation fields are invalid")
        restored = cls(**{key: record.get(key) for key in required})
        material = _verified_record(restored.receipt, "expression continuation receipt")
        if (
            material.get("version") != PRODUCT_EXPRESSION_CONTINUATION_VERSION
            or material.get("session_binding_fingerprint")
            != session_binding.fingerprint
            or material.get("delivery_id") != restored.delivery_id
            or material.get("ingress_receipt_fingerprint")
            != restored.ingress_receipt_fingerprint
            or material.get("transaction_receipt_fingerprint")
            != model_data_fingerprint(restored.transaction_receipt)
            or material.get("fact_evidence_fingerprint")
            != model_data_fingerprint(restored.fact_evidence)
            or material.get("previous_state_fingerprint")
            != model_data_fingerprint(restored.previous_state)
            or material.get("previous_position_fingerprint")
            != model_data_fingerprint(restored.previous_position)
            or material.get("model_parameters_fingerprint")
            != restored.model_parameters_fingerprint
            or material.get("settlement_context_fingerprint")
            != model_data_fingerprint(restored.settlement_context)
            or material.get("settlement_execution_fingerprint")
            != model_data_fingerprint(restored.settlement_execution)
            or material.get("settlement_acceptance_fingerprint")
            != model_data_fingerprint(restored.settlement_acceptance)
            or material.get("committed_remainders_fingerprint")
            != model_data_fingerprint(restored.committed_remainders)
        ):
            raise ValueError("expression continuation fingerprint lineage is invalid")
        return restored


@dataclass(frozen=True)
class RuntimeProductExpressionCompletion:
    """One persisted completion receipt plus its reconstructed expression."""

    expression: ExpressionProjectionShadow
    receipt: Mapping[str, Any]
    already_completed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.expression, ExpressionProjectionShadow):
            raise TypeError("expression completion requires projection shadow")
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


@dataclass(frozen=True)
class RecoveredRuntimeProductEventTransaction:
    """Minimal verified view of the already committed event settlement."""

    session: BootstrappedRuntimeSession
    context: ModelInvocationContext | ModelInvocationContextV2
    settlement_execution: ModelStageExecution
    acceptance: RuntimeStateCommitAcceptance


@dataclass(frozen=True)
class RecoveredRuntimeProductFactTransaction:
    """Body-free recovered transaction view; never grants recommit authority."""

    session: BootstrappedRuntimeSession
    event_transaction: RecoveredRuntimeProductEventTransaction
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def build_product_expression_continuation(
    *, transaction, evidence: RuntimeCommittedFactEvidence,
    ingress_receipt: Mapping[str, Any] | None = None,
    continuation_id: str | None = None,
    anchor_receipt: Mapping[str, Any] | None = None,
    committed_remainders: Mapping[str, object],
) -> RuntimeProductExpressionContinuation:
    """Freeze retry inputs from one fresh committed fact transaction."""
    if not isinstance(transaction, _TRANSACTION_TYPES):
        raise ValueError("expression continuation requires committed transaction")
    if not isinstance(evidence, RuntimeCommittedFactEvidence):
        raise ValueError("expression continuation requires fact evidence")
    if ingress_receipt is not None:
        if continuation_id is not None or anchor_receipt is not None:
            raise ValueError("expression continuation has multiple anchors")
        anchor = _verified_record(
            ingress_receipt, "recipient ingress receipt")
        if anchor.get("status") != "addressed_formal_fact_committed":
            raise ValueError("expression continuation requires addressed ingress")
        continuation_id = anchor.get("delivery_id")
        anchor_receipt = ingress_receipt
    else:
        continuation_id = _identifier(
            continuation_id, "expression continuation ID")
        anchor = _verified_record(
            anchor_receipt, "expression continuation anchor receipt")
    if not isinstance(committed_remainders, Mapping):
        raise ValueError("committed expression remainders must be a mapping")
    event = transaction.event_transaction
    previous = transaction.event_input_session
    event_identity = context_fact_identity(event.context)
    if (
        transaction.session is not event.session
        or evidence.event_id != event_identity.event_ref
        or evidence.participant_id != event_identity.subject_ref
        or evidence.conversation_id != event_identity.scope_ref
        or thaw_model_data(evidence.context_metadata)
        != thaw_model_data(event.context.metadata)
    ):
        raise ValueError("expression continuation differs from committed fact")
    if ingress_receipt is not None and (
        anchor.get("source_event_id") != event_identity.event_ref
        or anchor.get("recipient_participant_id")
        != event_identity.subject_ref
        or anchor.get("fact_transaction_receipt_fingerprint")
        != model_data_fingerprint(transaction.receipt)
    ):
        raise ValueError("expression continuation differs from recipient fact")
    if ingress_receipt is None and (
        anchor.get("event_id") != event_identity.event_ref
        or anchor.get("action_id") != event_identity.action_ref
        or anchor.get("participant_id") != event_identity.subject_ref
        or anchor.get("conversation_id") != event_identity.scope_ref
        or anchor.get("session_binding_fingerprint")
        != transaction.session.session_binding.fingerprint
    ):
        raise ValueError("expression continuation differs from anchor fact")
    execution_record = {
        "payload": thaw_model_data(event.settlement_execution.payload),
        "receipt": event.settlement_execution.receipt.to_record(),
    }
    frozen_remainders = freeze_model_payload(committed_remainders)
    data = {
        "delivery_id": continuation_id,
        "ingress_receipt_fingerprint": model_data_fingerprint(anchor_receipt),
        "fact_evidence": {
            "target_coordinate": thaw_model_data({
                "value": evidence.target_coordinate})["value"],
            "conversation_id": evidence.conversation_id,
            "participant_id": evidence.participant_id,
            "event_id": evidence.event_id,
            "action_id": evidence.action_id,
            "event_kind": evidence.event_kind,
            "response_policy": evidence.response_policy,
            "expected_runtime_ordinal": evidence.expected_runtime_ordinal,
            "measurement_payload": thaw_model_data(evidence.measurement_payload),
            "context_metadata": thaw_model_data(evidence.context_metadata),
            "model_binding": thaw_model_data(evidence.model_binding),
            "temporal_binding": thaw_model_data(evidence.temporal_binding),
            "receipt": thaw_model_data(evidence.receipt),
        },
        "transaction_receipt": thaw_model_data(transaction.receipt),
        "previous_state": previous.state.to_record(),
        "previous_position": transaction.settlement_input_position.to_record(),
        "model_parameters_fingerprint": previous.parameters.fingerprint,
        "settlement_context": event.context.to_record(),
        "settlement_execution": execution_record,
        "settlement_acceptance": event.acceptance.to_record(),
        "committed_remainders": thaw_model_data(frozen_remainders),
    }
    material = {
        "version": PRODUCT_EXPRESSION_CONTINUATION_VERSION,
        "session_binding_fingerprint": transaction.session.session_binding.fingerprint,
        "delivery_id": data["delivery_id"],
        "ingress_receipt_fingerprint": data["ingress_receipt_fingerprint"],
        "fact_evidence_fingerprint": model_data_fingerprint(
            data["fact_evidence"]),
        "transaction_receipt_fingerprint": model_data_fingerprint(
            data["transaction_receipt"]),
        "previous_state_fingerprint": model_data_fingerprint(
            data["previous_state"]),
        "previous_position_fingerprint": model_data_fingerprint(
            data["previous_position"]),
        "model_parameters_fingerprint": data["model_parameters_fingerprint"],
        "settlement_context_fingerprint": model_data_fingerprint(
            data["settlement_context"]),
        "settlement_execution_fingerprint": model_data_fingerprint(
            data["settlement_execution"]),
        "settlement_acceptance_fingerprint": model_data_fingerprint(
            data["settlement_acceptance"]),
        "committed_remainders_fingerprint": model_data_fingerprint(
            data["committed_remainders"]),
        "authority": {
            "fact_commit": False,
            "state_write": False,
            "memory_write": False,
            "expression_projection": True,
            "action": False,
        },
    }
    return RuntimeProductExpressionContinuation(
        **data,
        receipt=dict(material, fingerprint=model_data_fingerprint(material)),
    )


def restore_product_expression_projection(
    *, continuation: RuntimeProductExpressionContinuation,
    session: BootstrappedRuntimeSession, runtime: BoundInteractionModel,
) -> ExpressionProjectionShadow:
    """Re-run only the pure expression stages for the exact committed fact."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("expression continuation requires current runtime session")
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("expression continuation requires bound model runtime")
    restored = RuntimeProductExpressionContinuation.from_record(
        continuation.to_record(), session_binding=session.session_binding)
    authority = RuntimeStateCommitAuthority(
        session_binding=session.session_binding)
    previous_state = authority.restore_state(restored.previous_state)
    previous_position = authority.restore_position_state(
        restored.previous_position)
    acceptance_record = restored.settlement_acceptance
    if not isinstance(acceptance_record, Mapping):
        raise ValueError("continuation settlement acceptance is invalid")
    acceptance = RuntimeStateCommitAcceptance(
        session_binding_fingerprint=acceptance_record.get(
            "session_binding_fingerprint"),
        previous_state_fingerprint=acceptance_record.get(
            "previous_state_fingerprint"),
        previous_position_fingerprint=acceptance_record.get(
            "previous_position_fingerprint"),
        state=authority.restore_state(acceptance_record.get("state")),
        position=authority.restore_position_state(
            acceptance_record.get("position")),
        stage_receipt_fingerprint=acceptance_record.get(
            "stage_receipt_fingerprint"),
        fingerprint=acceptance_record.get("fingerprint"),
    )
    acceptance.verify_integrity(
        session_binding=session.session_binding,
        previous_state=previous_state,
        previous_position=previous_position,
    )
    if (
        session.state != acceptance.state
        or session.position != acceptance.position
        or session.parameters.fingerprint
        != restored.model_parameters_fingerprint
    ):
        raise ValueError(
            "expression continuation requires the current committed fact")
    context = _context(
        restored.settlement_context, session_binding=session.session_binding)
    execution_record = restored.settlement_execution
    if (
        not isinstance(execution_record, Mapping)
        or set(execution_record) != {"payload", "receipt"}
        or not isinstance(execution_record.get("payload"), Mapping)
    ):
        raise ValueError("continuation settlement execution is invalid")
    execution = ModelStageExecution(
        payload=freeze_model_payload(execution_record["payload"]),
        receipt=_stage_receipt(
            execution_record.get("receipt"),
            session_binding=session.session_binding,
        ),
    )
    return produce_expression_projection_shadow(
        runtime=runtime,
        session_binding=session.session_binding,
        previous_state=previous_state,
        previous_position=previous_position,
        model_parameters=session.parameters,
        settlement_context=context,
        settlement_execution=execution,
        settlement_acceptance=acceptance,
        committed_remainders=restored.committed_remainders,
    )


def restore_product_fact_products(
    *, continuation: RuntimeProductExpressionContinuation,
    session: BootstrappedRuntimeSession,
    runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
) -> tuple[RuntimeCommittedFactEvidence, RecoveredRuntimeProductFactTransaction]:
    """Restore body-free fact products for lifecycle verification only."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("fact product recovery requires runtime session")
    if not isinstance(runtime, BoundInteractionModel):
        raise ValueError("fact product recovery requires bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("fact product recovery requires temporal runtime")
    restored = RuntimeProductExpressionContinuation.from_record(
        continuation.to_record(), session_binding=session.session_binding)
    evidence_record = restored.fact_evidence
    required = {
        "target_coordinate", "conversation_id", "participant_id", "event_id",
        "action_id", "event_kind", "response_policy",
        "expected_runtime_ordinal", "measurement_payload", "context_metadata",
        "model_binding", "temporal_binding", "receipt",
    }
    if not isinstance(evidence_record, Mapping) or set(evidence_record) != required:
        raise ValueError("continuation fact evidence fields are invalid")
    evidence = RuntimeCommittedFactEvidence(**{
        key: evidence_record.get(key) for key in required
    })
    evidence.verify_for(
        model_runtime=runtime,
        temporal_runtime=temporal_runtime,
    )
    authority = RuntimeStateCommitAuthority(
        session_binding=session.session_binding)
    previous_state = authority.restore_state(restored.previous_state)
    previous_position = authority.restore_position_state(
        restored.previous_position)
    acceptance_record = restored.settlement_acceptance
    acceptance = RuntimeStateCommitAcceptance(
        session_binding_fingerprint=acceptance_record.get(
            "session_binding_fingerprint"),
        previous_state_fingerprint=acceptance_record.get(
            "previous_state_fingerprint"),
        previous_position_fingerprint=acceptance_record.get(
            "previous_position_fingerprint"),
        state=authority.restore_state(acceptance_record.get("state")),
        position=authority.restore_position_state(
            acceptance_record.get("position")),
        stage_receipt_fingerprint=acceptance_record.get(
            "stage_receipt_fingerprint"),
        fingerprint=acceptance_record.get("fingerprint"),
    )
    acceptance.verify_integrity(
        session_binding=session.session_binding,
        previous_state=previous_state,
        previous_position=previous_position,
    )
    if session.state != acceptance.state or session.position != acceptance.position:
        raise ValueError("fact product recovery requires current committed fact")
    context = _context(
        restored.settlement_context, session_binding=session.session_binding)
    execution_record = restored.settlement_execution
    if (
        not isinstance(execution_record, Mapping)
        or set(execution_record) != {"payload", "receipt"}
        or not isinstance(execution_record.get("payload"), Mapping)
    ):
        raise ValueError("continuation settlement execution is invalid")
    execution = ModelStageExecution(
        payload=freeze_model_payload(execution_record["payload"]),
        receipt=_stage_receipt(
            execution_record.get("receipt"),
            session_binding=session.session_binding,
        ),
    )
    event = RecoveredRuntimeProductEventTransaction(
        session=session,
        context=context,
        settlement_execution=execution,
        acceptance=acceptance,
    )
    transaction = RecoveredRuntimeProductFactTransaction(
        session=session,
        event_transaction=event,
        receipt=restored.transaction_receipt,
    )
    return evidence, transaction


_CONTINUATION_VALIDATION = OwnedJournalValidation()


def validated_product_expression_continuations(profile, *, session_binding):
    """Fully check new rows; reuse only previously checked immutable evidence."""
    raw = profile.get(RUNTIME_PRODUCT_EXPRESSION_CONTINUATIONS_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("expression continuation journal is invalid")
    return _CONTINUATION_VALIDATION.restore(
        session_binding.fingerprint, raw,
        lambda item: RuntimeProductExpressionContinuation.from_record(
            item, session_binding=session_binding))


def attach_product_expression_continuation(
    session: BootstrappedRuntimeSession,
    *, continuation: RuntimeProductExpressionContinuation,
) -> BootstrappedRuntimeSession:
    """Append one continuation to the same profile value as its fact."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("expression continuation requires runtime session")
    verified = RuntimeProductExpressionContinuation.from_record(
        continuation.to_record(), session_binding=session.session_binding)
    raw = session.profile.get(
        RUNTIME_PRODUCT_EXPRESSION_CONTINUATIONS_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("expression continuation journal is invalid")
    existing = validated_product_expression_continuations(
        session.profile, session_binding=session.session_binding)
    same = tuple(
        item for item in existing if item.delivery_id == verified.delivery_id)
    if same:
        if len(same) != 1 or same[0].to_record() != verified.to_record():
            raise ValueError("expression continuation conflicts with delivery")
        return session
    profile = dict(session.profile)
    profile[RUNTIME_PRODUCT_EXPRESSION_CONTINUATIONS_KEY] = [
        *(row if type(row) is ImmutableModelRecord else item.to_record()
          for row, item in zip(raw, existing)),
        verified.to_record(),
    ]
    return replace(session, profile=profile)


def load_product_expression_continuation(
    profile: Mapping[str, object], *, delivery_id: str, session_binding,
) -> RuntimeProductExpressionContinuation:
    """Load one exact pending continuation without selecting a recent item."""
    target = _identifier(delivery_id, "continuation delivery ID")
    raw = profile.get(
        RUNTIME_PRODUCT_EXPRESSION_CONTINUATIONS_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("expression continuation journal is invalid")
    restored = validated_product_expression_continuations(
        profile, session_binding=session_binding)
    matches = tuple(item for item in restored if item.delivery_id == target)
    if len(matches) != 1:
        raise ValueError("expression continuation delivery is not unique")
    return matches[0]


def load_product_expression_completion_receipt(
    profile: Mapping[str, object], *, delivery_id: str, session_binding,
) -> Mapping[str, Any] | None:
    """Load and verify one exact persisted expression-completion receipt."""
    target = _identifier(delivery_id, "completion delivery ID")
    continuation = load_product_expression_continuation(
        profile,
        delivery_id=target,
        session_binding=session_binding,
    )
    raw = profile.get(RUNTIME_PRODUCT_EXPRESSION_COMPLETIONS_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("expression completion journal is invalid")
    matches = []
    context = continuation.settlement_context
    context_identity = context_fact_identity(_context(
        context, session_binding=session_binding))
    accepted_position = continuation.settlement_acceptance.get("position")
    for item in raw:
        material = _verified_record(item, "expression completion receipt")
        if material.get("delivery_id") != target:
            continue
        required = {
            "version", "status", "session_binding_fingerprint",
            "delivery_id", "event_id", "participant_id",
            "conversation_id", "runtime_ordinal",
            "continuation_receipt_fingerprint",
            "expression_receipt_fingerprint", "selected_resource_ids",
            "authority",
        }
        if (
            set(material) != required
            or material.get("version")
            != PRODUCT_EXPRESSION_COMPLETION_VERSION
            or material.get("status") != "expression_projection_completed"
            or material.get("session_binding_fingerprint")
            != session_binding.fingerprint
            or material.get("event_id") != context_identity.event_ref
            or material.get("participant_id") != context_identity.subject_ref
            or material.get("conversation_id") != context_identity.scope_ref
            or not isinstance(accepted_position, Mapping)
            or material.get("runtime_ordinal")
            != accepted_position.get("runtime_ordinal")
            or material.get("continuation_receipt_fingerprint")
            != model_data_fingerprint(continuation.receipt)
            or material.get("authority") != {
                "fact_commit": False,
                "state_write": False,
                "memory_write": False,
                "action": False,
            }
            or not isinstance(material.get("selected_resource_ids"), list)
        ):
            raise ValueError("expression completion lineage is invalid")
        matches.append(freeze_model_payload(item))
    if len(matches) > 1:
        raise ValueError("expression completion delivery is not unique")
    return matches[0] if matches else None


def commit_product_expression_completion(
    session: BootstrappedRuntimeSession,
    *, continuation: RuntimeProductExpressionContinuation,
    expression: ExpressionProjectionShadow,
) -> tuple[BootstrappedRuntimeSession, RuntimeProductExpressionCompletion]:
    """Append a body-free receipt after one successful read-only projection."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("expression completion requires runtime session")
    verified = RuntimeProductExpressionContinuation.from_record(
        continuation.to_record(), session_binding=session.session_binding)
    if not isinstance(expression, ExpressionProjectionShadow):
        raise ValueError("expression completion requires projection shadow")
    expression_receipt = dict(thaw_model_data(expression.receipt))
    if (
        expression_receipt.get("version") != "expression-projection-shadow-v1"
        or expression_receipt.get("mode") != "read_only_shadow"
    ):
        raise ValueError("expression projection receipt is invalid")
    acceptance = verified.settlement_acceptance
    accepted_position = acceptance.get("position")
    accepted_state = acceptance.get("state")
    context = expression.context
    context_identity = context_fact_identity(context)
    continued_context = _context(
        verified.settlement_context,
        session_binding=session.session_binding,
    )
    continued_identity = context_fact_identity(continued_context)
    if (
        not isinstance(accepted_position, Mapping)
        or not isinstance(accepted_state, Mapping)
        or context_identity.event_ref
        != continued_identity.event_ref
        or context_identity.subject_ref
        != continued_identity.subject_ref
        or context_identity.scope_ref
        != continued_identity.scope_ref
        or context.runtime_ordinal
        != accepted_position.get("runtime_ordinal")
        or session.position.fingerprint != accepted_position.get("fingerprint")
        or session.state.fingerprint != accepted_state.get("fingerprint")
        or expression_receipt.get("settlement_acceptance_fingerprint")
        != acceptance.get("fingerprint")
    ):
        raise ValueError("expression completion differs from continuation")
    material = {
        "version": PRODUCT_EXPRESSION_COMPLETION_VERSION,
        "status": "expression_projection_completed",
        "session_binding_fingerprint": session.session_binding.fingerprint,
        "delivery_id": verified.delivery_id,
        "event_id": context_identity.event_ref,
        "participant_id": context_identity.subject_ref,
        "conversation_id": context_identity.scope_ref,
        "runtime_ordinal": context.runtime_ordinal,
        "continuation_receipt_fingerprint": model_data_fingerprint(
            verified.receipt),
        "expression_receipt_fingerprint": model_data_fingerprint(
            expression.receipt),
        "selected_resource_ids": list(expression.batch.selected_resource_ids),
        "authority": {
            "fact_commit": False,
            "state_write": False,
            "memory_write": False,
            "action": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    raw = session.profile.get(
        RUNTIME_PRODUCT_EXPRESSION_COMPLETIONS_KEY, ())
    if not isinstance(raw, (tuple, list)):
        raise ValueError("expression completion journal is invalid")
    existing = tuple(dict(thaw_model_data(item)) for item in raw)
    for item in existing:
        _verified_record(item, "expression completion receipt")
    same = tuple(
        item for item in existing
        if item.get("delivery_id") == verified.delivery_id
    )
    if same:
        if len(same) != 1 or same[0] != receipt:
            raise ValueError("expression completion conflicts with delivery")
        return session, RuntimeProductExpressionCompletion(
            expression=expression,
            receipt=same[0],
            already_completed=True,
        )
    profile = dict(session.profile)
    profile[RUNTIME_PRODUCT_EXPRESSION_COMPLETIONS_KEY] = [
        *existing, receipt,
    ]
    return (
        replace(session, profile=profile),
        RuntimeProductExpressionCompletion(
            expression=expression,
            receipt=receipt,
            already_completed=False,
        ),
    )


__all__ = (
    "PRODUCT_EXPRESSION_CONTINUATION_VERSION",
    "PRODUCT_EXPRESSION_COMPLETION_VERSION",
    "RUNTIME_PRODUCT_EXPRESSION_COMPLETIONS_KEY",
    "RUNTIME_PRODUCT_EXPRESSION_CONTINUATIONS_KEY",
    "RuntimeProductExpressionContinuation",
    "RuntimeProductExpressionCompletion",
    "RecoveredRuntimeProductEventTransaction",
    "RecoveredRuntimeProductFactTransaction",
    "attach_product_expression_continuation",
    "build_product_expression_continuation",
    "commit_product_expression_completion",
    "load_product_expression_completion_receipt",
    "load_product_expression_continuation",
    "restore_product_expression_projection",
    "restore_product_fact_products",
)
