"""Package-neutral provider request/attempt/response/parse lifecycle host."""
from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from framework.model_api import freeze_model_payload, model_data_fingerprint, thaw_model_data
from framework.provider_codec import (
    ProviderCodecParseReceipt,
    execute_provider_codec_parse,
)
from framework.provider_host import (
    ProviderExecutionResult,
    ProviderRequest,
    execute_provider,
)
from framework.receipt_adapters import (
    adapt_provider_attempt,
    adapt_provider_codec_parse,
    adapt_provider_failure,
    adapt_provider_request,
    adapt_provider_response,
)
from framework.receipt_dag import (
    LifecycleArtifactEvidence,
    LifecycleFailureEvidence,
)
from framework.runtime_kernel import RuntimePositionState, RuntimeSessionBinding


@dataclass(frozen=True)
class ProviderLifecycleResult:
    """One fully receipted provider phase with no state or Commit authority."""

    status: str
    request: ProviderRequest
    execution: ProviderExecutionResult
    parse_receipt: ProviderCodecParseReceipt | None
    parsed_payload: Mapping[str, Any] | None
    evidence: Mapping[
        str, LifecycleArtifactEvidence | LifecycleFailureEvidence]

    def __post_init__(self) -> None:
        if self.parsed_payload is not None:
            object.__setattr__(
                self, "parsed_payload", freeze_model_payload(
                    self.parsed_payload))
        object.__setattr__(self, "evidence", MappingProxyType(
            dict(self.evidence)))


def execute_provider_transport(
    *,
    stage_prefix: str,
    session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState,
    operation_kind: str,
    source_fact_ref: str,
    codec_contract: str,
    request_payload: Mapping[str, Any],
    transport_payload: Mapping[str, Any],
    implementation_ref: str,
    invocation_evidence: Mapping[str, Any],
    provider_invoke: Callable[[ProviderRequest, Mapping[str, Any]], object],
    parent_receipt_refs: Sequence[str] = (),
) -> ProviderLifecycleResult:
    """Receive opaque provider data; this alone grants no parsed-candidate status."""
    prefix = str(stage_prefix or "").strip()
    if not prefix:
        raise ValueError("provider lifecycle stage prefix must be non-empty")
    if not callable(provider_invoke):
        raise TypeError("provider transport requires an invoke callable")
    request = ProviderRequest.create(
        session_binding=session_binding,
        current_position=current_position,
        operation_kind=operation_kind,
        source_fact_ref=source_fact_ref,
        codec_contract=codec_contract,
        request_payload=request_payload,
        transport_options={"response_format": "structured"},
    )
    evidence: dict[
        str, LifecycleArtifactEvidence | LifecycleFailureEvidence] = {}
    request_stage = f"{prefix}_provider_request"
    attempt_stage = f"{prefix}_provider_attempt"
    response_stage = f"{prefix}_provider_response"
    evidence[request_stage] = adapt_provider_request(
        stage=request_stage,
        request=request,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=parent_receipt_refs,
    )
    execution = execute_provider(
        request=request,
        implementation_ref=implementation_ref,
        invocation_evidence={
            **dict(invocation_evidence),
            "transport_request_fingerprint": model_data_fingerprint(
                transport_payload),
        },
        invoke=lambda actual: provider_invoke(actual, transport_payload),
    )
    evidence[attempt_stage] = adapt_provider_attempt(
        stage=attempt_stage,
        attempt=execution.attempt,
        request=request,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=(evidence[request_stage].receipt_ref,),
    )
    response_parents = (
        evidence[request_stage].receipt_ref,
        evidence[attempt_stage].receipt_ref,
    )
    if execution.status == "failed":
        evidence[response_stage] = adapt_provider_failure(
            stage=response_stage,
            failure=execution.failure,
            attempt=execution.attempt,
            request=request,
            session_binding=session_binding,
            current_position=current_position,
            parent_receipt_refs=response_parents,
        )
        return ProviderLifecycleResult(
            status="failed",
            request=request,
            execution=execution,
            parse_receipt=None,
            parsed_payload=None,
            evidence=evidence,
        )
    evidence[response_stage] = adapt_provider_response(
        stage=response_stage,
        response=execution.response,
        attempt=execution.attempt,
        request=request,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=response_parents,
    )
    return ProviderLifecycleResult(
        status="received", request=request, execution=execution,
        parse_receipt=None, parsed_payload=None, evidence=evidence,
    )


def execute_provider_lifecycle(
    *, stage_prefix: str, session_binding: RuntimeSessionBinding,
    current_position: RuntimePositionState, operation_kind: str,
    source_fact_ref: str, codec_contract: str,
    request_payload: Mapping[str, Any], transport_payload: Mapping[str, Any],
    implementation_ref: str, invocation_evidence: Mapping[str, Any],
    provider_invoke: Callable[[ProviderRequest, Mapping[str, Any]], object],
    parse: Callable[[Mapping[str, Any]], object],
    parent_receipt_refs: Sequence[str] = (),
    response_projection: Callable[[Mapping[str, Any]], tuple[Mapping[str, Any], Mapping[str, Any]]] | None = None,
) -> ProviderLifecycleResult:
    """Run transport and mandatory codec parsing through the same receipts."""
    if not callable(parse):
        raise TypeError("provider lifecycle requires a parse callable")
    phase = execute_provider_transport(
        stage_prefix=stage_prefix, session_binding=session_binding,
        current_position=current_position, operation_kind=operation_kind,
        source_fact_ref=source_fact_ref, codec_contract=codec_contract,
        request_payload=request_payload, transport_payload=transport_payload,
        implementation_ref=implementation_ref, invocation_evidence=invocation_evidence,
        provider_invoke=provider_invoke, parent_receipt_refs=parent_receipt_refs)
    if phase.status == "failed":
        return phase
    prefix = str(stage_prefix or "").strip()
    request, execution = phase.request, phase.execution
    evidence = dict(phase.evidence)
    response_stage = f"{prefix}_provider_response"
    parse_stage = f"{prefix}_codec_parse"
    parse_input = execution.response.response_payload
    parse_parents = (evidence[response_stage].receipt_ref,)
    if response_projection is not None:
        projection_stage = f"{prefix}_response_projection"
        try:
            projected, projection_detail = response_projection(parse_input)
            projected = freeze_model_payload(projected)
            projection_detail = freeze_model_payload(projection_detail)
        except ValueError as exc:
            evidence[projection_stage] = LifecycleFailureEvidence.create(
                stage=projection_stage, failure_kind="response_structure_unrepairable",
                failure_ref=model_data_fingerprint({"response": execution.response.fingerprint,
                    "exception_type": type(exc).__name__}),
                session_binding_fingerprint=session_binding.fingerprint,
                temporal_position_fingerprint=current_position.position.fingerprint,
                runtime_ordinal=current_position.runtime_ordinal,
                parent_receipt_refs=parse_parents)
            return ProviderLifecycleResult(status="failed", request=request, execution=execution,
                parse_receipt=None, parsed_payload=None, evidence=evidence)
        evidence[projection_stage] = LifecycleArtifactEvidence.create(
            stage=projection_stage, artifact_kind="provider-response-structure-projection-v1",
            receipt_ref=model_data_fingerprint({"response": execution.response.fingerprint,
                "projected_payload": thaw_model_data(projected), "projection_detail": thaw_model_data(projection_detail)}),
            session_binding_fingerprint=session_binding.fingerprint,
            temporal_position_fingerprint=current_position.position.fingerprint,
            runtime_ordinal=current_position.runtime_ordinal,
            input_payload={"provider_response_fingerprint": execution.response.fingerprint,
                "raw_payload": thaw_model_data(parse_input)},
            output_payload={"projected_payload": thaw_model_data(projected), "projection_detail": thaw_model_data(projection_detail)},
            parent_receipt_refs=parse_parents)
        parse_input = projected
        parse_parents = (*parse_parents, evidence[projection_stage].receipt_ref)
    parse_receipt = execute_provider_codec_parse(
        response=execution.response,
        codec_contract=codec_contract,
        parse=lambda _original: parse(parse_input),
    )
    evidence[parse_stage] = adapt_provider_codec_parse(
        stage=parse_stage,
        parse_receipt=parse_receipt,
        response=execution.response,
        session_binding=session_binding,
        current_position=current_position,
        parent_receipt_refs=parse_parents,
    )
    return ProviderLifecycleResult(
        status=("parsed" if parse_receipt.status == "parsed" else "failed"),
        request=request,
        execution=execution,
        parse_receipt=parse_receipt,
        parsed_payload=(
            parse_receipt.parsed_payload
            if parse_receipt.status == "parsed" else None),
        evidence=evidence,
    )


__all__ = ("ProviderLifecycleResult", "execute_provider_lifecycle", "execute_provider_transport")
