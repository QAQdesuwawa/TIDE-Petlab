"""Product bridge from one verified required lifecycle to Kernel permission.

This module creates no action, action ID, Writer attempt, Commit, history row,
tick, timer, or send callback.  It only proves that the exact committed input
lifecycle carried the ``required`` reply policy.
"""
from __future__ import annotations

from dataclasses import replace

from framework.model_api import model_data_fingerprint, thaw_model_data
from interaction_extensions.conversation_action_protocol import (
    RuntimeActionAuthorization,
    RuntimeActionAuthorizationAuthority,
)
from framework.runtime_kernel import (
    RuntimeKernelContractError,
)
from ops.runtime_product_fact_lifecycle import RuntimeProductFactLifecycle
from ops.runtime_autonomous_observation import (
    RuntimeAutonomousObservationTransaction,
)


def authorize_required_product_reply(
    lifecycle: RuntimeProductFactLifecycle,
) -> RuntimeActionAuthorization:
    if not isinstance(lifecycle, RuntimeProductFactLifecycle):
        raise RuntimeKernelContractError(
            "required reply authorization requires product fact lifecycle")
    receipt = thaw_model_data(lifecycle.receipt)
    fingerprint = receipt.pop("fingerprint", None)
    if model_data_fingerprint(receipt) != fingerprint:
        raise RuntimeKernelContractError(
            "product lifecycle receipt fingerprint is invalid")
    evidence = lifecycle.evidence
    transaction = lifecycle.transaction
    session = transaction.session
    if (
        evidence.response_policy != "required"
        or receipt.get("response_policy") != "required"
        or receipt.get("event_id") != evidence.event_id
        or receipt.get("conversation_id") != evidence.conversation_id
        or receipt.get("participant_id") != evidence.participant_id
        or receipt.get("evidence_receipt_fingerprint")
        != model_data_fingerprint(evidence.receipt)
        or receipt.get("transaction_receipt_fingerprint")
        != model_data_fingerprint(transaction.receipt)
    ):
        raise RuntimeKernelContractError(
            "required reply authorization requires exact required lifecycle")
    authority = RuntimeActionAuthorizationAuthority(
        session_binding=session.session_binding)
    return authority.authorize_required_reply(
        current_position=session.position,
        conversation_id=evidence.conversation_id,
        participant_id=evidence.participant_id,
        source_event_id=evidence.event_id,
        response_policy=evidence.response_policy,
        source_evidence_fingerprint=model_data_fingerprint(lifecycle.receipt),
    )


def map_assessed_action_to_product_autonomous_opening(
    observation: RuntimeAutonomousObservationTransaction,
    *,
    accepted_action_kind: str,
) -> RuntimeAutonomousObservationTransaction:
    """Explicitly map one package action kind into the chat product protocol.

    The generic observation remains unchanged unless the caller selects this
    product adapter.  The returned receipt binds the exact derived legacy
    authorization so downstream recovery cannot confuse an ad-hoc replacement
    with the originally assessed tick.
    """
    if not isinstance(observation, RuntimeAutonomousObservationTransaction):
        raise RuntimeKernelContractError(
            "autonomous product mapping requires formal observation")
    if observation.authorization is not None:
        raise RuntimeKernelContractError(
            "autonomous product mapping requires an unmapped observation")
    authorization = RuntimeActionAuthorizationAuthority(
        session_binding=observation.session.session_binding,
    ).authorize_autonomous_opening(
        current_position=observation.session.position,
        due_proof=observation.due_proof,
        assessment=observation.assessment,
        accepted_action_kind=accepted_action_kind,
    )
    receipt = dict(thaw_model_data(observation.receipt))
    fingerprint = receipt.pop("fingerprint", None)
    if model_data_fingerprint(receipt) != fingerprint:
        raise RuntimeKernelContractError(
            "autonomous observation receipt fingerprint is invalid")
    if receipt.get("authorization_fingerprint") is not None:
        raise RuntimeKernelContractError(
            "autonomous observation receipt is already authorized")
    receipt["authorization_fingerprint"] = authorization.fingerprint
    receipt["fingerprint"] = model_data_fingerprint(receipt)
    return replace(
        observation,
        authorization=authorization,
        receipt=receipt,
    )


__all__ = (
    "authorize_required_product_reply",
    "map_assessed_action_to_product_autonomous_opening",
)
