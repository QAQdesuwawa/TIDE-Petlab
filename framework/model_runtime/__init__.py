"""Read-only invocation boundary for one version-pinned interaction model."""
from __future__ import annotations

from typing import Any, Callable, Mapping

from framework.model_api import (
    MODEL_API_VERSION_V1,
    MODEL_API_VERSION_V2,
    InteractionModelBinding,
    InteractionModelManifest,
    ModelInvocationContext,
    ModelStageExecution,
    ModelStageReceipt,
    freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)


class BoundInteractionModel:
    """Invoke explicit model stages without granting orchestration authority."""

    def __init__(
        self, package: object, manifest: InteractionModelManifest, *,
        operators: Mapping[str, Callable], session_id: str,
    ):
        self._package = package
        self.manifest = manifest
        self._operators = operators
        self._session_id = session_id

    @property
    def session_id(self) -> str:
        return self._session_id

    @property
    def binding(self) -> InteractionModelBinding:
        self._verify_registered_identity()
        return InteractionModelBinding.from_manifest(self.manifest)

    def _verify_registered_identity(self) -> None:
        if getattr(self._package, "manifest", None) is not self.manifest:
            raise RuntimeError("model manifest changed after registration")
        for stage, registered in self._operators.items():
            if getattr(self._package, stage, None) != registered:
                raise RuntimeError(f"model operator changed after registration: {stage}")

    def invoke(
        self,
        stage: str,
        *,
        payload: Mapping[str, Any],
        context: ModelInvocationContext | ModelInvocationContextV2,
    ) -> ModelStageExecution:
        if stage not in self.manifest.capabilities:
            raise ValueError(f"unknown model stage: {stage}")
        if not isinstance(context, (ModelInvocationContext, ModelInvocationContextV2)):
            raise ValueError("context must be a supported model invocation context")
        if context.session_id != self._session_id:
            raise ValueError("context.session_id does not match bound model session_id")
        if self.manifest.api_version == MODEL_API_VERSION_V1:
            if not isinstance(context, ModelInvocationContext):
                raise ValueError("model api v1 requires compatibility context")
            if context.at_ms is None:
                raise ValueError("model api v1 requires at_ms")
            creation_stages = {
                "initialize_constants", "initialize_state", "resolve_constants",
            }
            action_stage = "action_assessment"
        elif self.manifest.api_version == MODEL_API_VERSION_V2:
            if not isinstance(context, ModelInvocationContextV2):
                raise ValueError("model api v2 requires a v2 context")
            if context.temporal_position is None or context.runtime_ordinal is None:
                raise ValueError(
                    "model api v2 requires temporal position and runtime ordinal")
            creation_stages = {"initialize_parameters", "initialize_state"}
            action_stage = "assess_action"
        else:
            raise ValueError("unsupported model api version")
        if stage not in creation_stages:
            scope_ref = (
                context.scope_ref
                if isinstance(context, ModelInvocationContextV2)
                else context.conversation_id
            )
            subject_ref = (
                context.subject_ref
                if isinstance(context, ModelInvocationContextV2)
                else context.participant_id
            )
            event_ref = (
                context.event_ref
                if isinstance(context, ModelInvocationContextV2)
                else context.event_id
            )
            action_ref = (
                context.action_ref
                if isinstance(context, ModelInvocationContextV2)
                else context.action_id
            )
            owner_local_tick = (
                (stage in {
                    "advance_progress",
                    "settle_state",
                    "project_shared_semantics",
                    action_stage,
                } or (isinstance(context, ModelInvocationContextV2)
                      and stage in {"prepare_memory_inputs", "recall_history"}))
                and scope_ref is None
                and subject_ref is not None
                and event_ref is not None
                and action_ref is None
                and context.metadata.get("committed_event_kind") == "tick"
            )
            if (subject_ref is None
                    or (scope_ref is None and not owner_local_tick)):
                raise ValueError(
                    "non-creation model stage requires conversation and participant identity")
        self._verify_registered_identity()
        operator = getattr(self._package, stage, None)

        frozen_input = freeze_model_payload(payload)
        output = operator(payload=frozen_input, context=context)
        if not isinstance(output, Mapping):
            raise ValueError(f"model stage {stage} must return a mapping")
        frozen_output = freeze_model_payload(output)
        receipt_kwargs = {
            "package_id": self.manifest.package_id,
            "package_version": self.manifest.package_version,
            "stage": stage,
            "operator_version": self.manifest.operator_versions[stage],
            "schema_fingerprint": self.manifest.schema_fingerprint,
            "parameter_fingerprint": self.manifest.parameter_fingerprint,
            "session_id": context.session_id,
            "input_fingerprint": model_data_fingerprint(frozen_input),
            "output_fingerprint": model_data_fingerprint(frozen_output),
            "temporal_position": context.temporal_position,
            "runtime_ordinal": context.runtime_ordinal,
        }
        if isinstance(context, ModelInvocationContextV2):
            receipt = ModelStageReceiptV2(
                **receipt_kwargs,
                scope_ref=context.scope_ref,
                subject_ref=context.subject_ref,
                event_ref=context.event_ref,
                action_ref=context.action_ref,
            )
        else:
            receipt = ModelStageReceipt(
                **receipt_kwargs,
                conversation_id=context.conversation_id,
                participant_id=context.participant_id,
                event_id=context.event_id,
                action_id=context.action_id,
                at_ms=context.at_ms,
            )
        return ModelStageExecution(payload=frozen_output, receipt=receipt)
