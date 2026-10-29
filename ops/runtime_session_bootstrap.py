"""Model-independent composition host for one formal runtime session.

The caller selects and binds both packages and supplies every initialization
payload explicitly.  This host owns neither package selection nor persistence;
it only creates or restores Kernel products and a schema-v3 profile value.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelStageExecution,
    model_data_fingerprint,
)
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import (
    RuntimeKernelContractError,
    RuntimeModelParameters,
    RuntimeModelState,
    RuntimePositionState,
    RuntimeProgressAuthority,
    RuntimeSessionBinding,
    RuntimeStateCommitAuthority,
)
from framework.temporal_runtime import BoundTemporalDomain
from framework.runtime_variant import RuntimeVariantBinding
from ops.runtime_session_binding import (
    append_runtime_session_binding,
    restore_runtime_progress_transactions,
    restore_runtime_session_model_snapshot,
)
from ops.runtime_expression_state import initialize_runtime_expression_state


@dataclass(frozen=True)
class BootstrappedRuntimeSession:
    profile: Mapping[str, Any]
    session_binding: RuntimeSessionBinding
    position: RuntimePositionState
    parameters: RuntimeModelParameters
    state: RuntimeModelState
    receipt: Mapping[str, Any]


def _verify_initial_state_execution(
    execution: ModelStageExecution,
    *,
    session_binding: RuntimeSessionBinding,
    invocation_payload: Mapping[str, Any],
) -> Any:
    if not isinstance(execution, ModelStageExecution):
        raise RuntimeKernelContractError(
            "initialize_state must return a model stage execution")
    receipt = execution.receipt
    binding = session_binding.model_binding
    if (
        receipt.stage != "initialize_state"
        or receipt.session_id != session_binding.session_id
        or receipt.package_id != binding.package_id
        or receipt.package_version != binding.package_version
        or receipt.schema_fingerprint != binding.schema_fingerprint
        or receipt.parameter_fingerprint != binding.parameter_fingerprint
        or receipt.operator_version
        != binding.operator_versions.get("initialize_state")
        or receipt.input_fingerprint
        != model_data_fingerprint(invocation_payload)
        or receipt.output_fingerprint
        != model_data_fingerprint(execution.payload)
    ):
        raise RuntimeKernelContractError(
            "initialize_state execution does not match runtime binding")
    if "state" not in execution.payload:
        raise RuntimeKernelContractError(
            "initialize_state output must contain state")
    return execution.payload["state"]


def _receipt(
    *,
    mode: str,
    session_binding: RuntimeSessionBinding,
    position: RuntimePositionState,
    parameters: RuntimeModelParameters,
    state: RuntimeModelState,
    parameters_stage_receipt_fingerprint: str,
    state_stage_receipt_fingerprint: str | None,
) -> dict[str, Any]:
    material = {
        "version": "runtime-session-bootstrap-v1",
        "mode": mode,
        "session_binding_fingerprint": session_binding.fingerprint,
        "position_fingerprint": position.fingerprint,
        "model_parameters_fingerprint": parameters.fingerprint,
        "model_state_fingerprint": state.fingerprint,
        "parameters_stage_receipt_fingerprint": (
            parameters_stage_receipt_fingerprint),
        "state_stage_receipt_fingerprint": state_stage_receipt_fingerprint,
    }
    return dict(material, fingerprint=model_data_fingerprint(material))


def bootstrap_runtime_session(
    *,
    profile: Mapping[str, object],
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    runtime_variant: RuntimeVariantBinding,
    initial_coordinate: object,
    parameter_payload: Mapping[str, Any],
    state_payload: Mapping[str, Any],
) -> BootstrappedRuntimeSession:
    """Create a schema-v3 session from explicit, package-owned inputs."""
    if not isinstance(model_runtime, BoundInteractionModel):
        raise RuntimeKernelContractError(
            "bootstrap requires a registry-bound model runtime")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise RuntimeKernelContractError(
            "bootstrap requires a registry-bound temporal runtime")
    if not isinstance(parameter_payload, Mapping):
        raise RuntimeKernelContractError(
            "parameter_payload must be a mapping")
    if not isinstance(state_payload, Mapping):
        raise RuntimeKernelContractError("state_payload must be a mapping")

    session_binding = RuntimeSessionBinding.create(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        runtime_variant=runtime_variant,
    )
    progress_authority = RuntimeProgressAuthority(
        session_binding=session_binding,
        temporal_runtime=temporal_runtime,
    )
    position = progress_authority.initialize(initial_coordinate)
    context = progress_authority.invocation_context_v2(
        position,
        scope_ref=None,
        subject_ref=None,
        event_ref=None,
        action_ref=None,
    )

    parameters_execution = model_runtime.invoke(
        "initialize_parameters",
        payload=parameter_payload,
        context=context,
    )
    parameters = RuntimeModelParameters.create(
        session_binding=session_binding,
        execution=parameters_execution,
    )
    state_execution = model_runtime.invoke(
        "initialize_state",
        payload=state_payload,
        context=context,
    )
    initial_state = _verify_initial_state_execution(
        state_execution,
        session_binding=session_binding,
        invocation_payload=state_payload,
    )
    state = RuntimeModelState.create(
        session_binding=session_binding,
        state=initial_state,
        revision=0,
        committed_runtime_ordinal=position.runtime_ordinal,
    )
    anchored_profile = append_runtime_session_binding(
        profile,
        session_binding=session_binding,
        initial_state=position,
        initial_model_parameters=parameters,
        initial_model_state=state,
    )
    anchored_profile = initialize_runtime_expression_state(
        anchored_profile,
        session_binding=session_binding,
    )
    receipt = _receipt(
        mode="created",
        session_binding=session_binding,
        position=position,
        parameters=parameters,
        state=state,
        parameters_stage_receipt_fingerprint=(
            parameters.initialization_receipt_fingerprint),
        state_stage_receipt_fingerprint=model_data_fingerprint(
            state_execution.receipt.to_record()),
    )
    return BootstrappedRuntimeSession(
        profile=anchored_profile,
        session_binding=session_binding,
        position=position,
        parameters=parameters,
        state=state,
        receipt=receipt,
    )


def restore_bootstrapped_runtime_session(
    *,
    profile: Mapping[str, object],
    model_runtime: BoundInteractionModel,
    temporal_runtime: BoundTemporalDomain,
    runtime_variant: RuntimeVariantBinding,
) -> BootstrappedRuntimeSession:
    """Restore v3 products without rerunning either initialization stage."""
    session_binding = RuntimeSessionBinding.create(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
        runtime_variant=runtime_variant,
    )
    session_binding.verify_runtimes(
        model_runtime=model_runtime,
        temporal_runtime=temporal_runtime,
    )
    authority = RuntimeStateCommitAuthority(
        session_binding=session_binding)
    progress_authority = RuntimeProgressAuthority(
        session_binding=session_binding,
        temporal_runtime=temporal_runtime,
    )
    position, parameters, state = restore_runtime_session_model_snapshot(
        profile,
        session_binding=session_binding,
        authority=authority,
    )
    restore_runtime_progress_transactions(
        profile,
        materialize=False,
        session_binding=session_binding,
        progress_authority=progress_authority,
        state_authority=authority,
    )
    receipt = _receipt(
        mode="restored",
        session_binding=session_binding,
        position=position,
        parameters=parameters,
        state=state,
        parameters_stage_receipt_fingerprint=(
            parameters.initialization_receipt_fingerprint),
        state_stage_receipt_fingerprint=None,
    )
    return BootstrappedRuntimeSession(
        profile=dict(profile),
        session_binding=session_binding,
        position=position,
        parameters=parameters,
        state=state,
        receipt=receipt,
    )


__all__ = (
    "BootstrappedRuntimeSession",
    "bootstrap_runtime_session",
    "restore_bootstrapped_runtime_session",
)
