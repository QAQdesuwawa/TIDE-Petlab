"""Shared receipt-lineage verification for formal runtime stage hosts."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    model_data_fingerprint,
)
from framework.model_api.identity import same_model_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.model_runtime import BoundInteractionModel
from framework.runtime_kernel import RuntimeKernelContractError


def verify_runtime_stage_execution(
    execution: ModelStageExecution,
    *,
    stage: str,
    runtime: BoundInteractionModel,
    context: ModelInvocationContext | ModelInvocationContextV2,
    invocation_payload: Mapping[str, Any],
) -> None:
    """Verify one stage execution against its exact binding, input and fact."""
    if not isinstance(execution, ModelStageExecution):
        raise RuntimeKernelContractError(
            f"{stage} must return a model stage execution")
    receipt = execution.receipt
    binding = runtime.binding
    if (
        receipt.stage != stage
        or receipt.package_id != binding.package_id
        or receipt.package_version != binding.package_version
        or receipt.schema_fingerprint != binding.schema_fingerprint
        or receipt.parameter_fingerprint != binding.parameter_fingerprint
        or receipt.operator_version != binding.operator_versions.get(stage)
        or not same_model_fact_identity(context, receipt)
        or receipt.input_fingerprint
        != model_data_fingerprint(invocation_payload)
        or receipt.output_fingerprint
        != model_data_fingerprint(execution.payload)
    ):
        raise RuntimeKernelContractError(
            f"{stage} execution does not match formal runtime transaction")


__all__ = ("verify_runtime_stage_execution",)
