"""Model-independent session binding and monotonic progress authority.

This module owns no Store, Actor, timer, UI, provider, or send callback.  It
validates the pure records that a later transaction/persistence host may commit.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    InteractionModelBinding,
    ModelStageExecution,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.compat_v1.invocation import ModelInvocationContext
from framework.model_runtime import BoundInteractionModel
from framework.model_api.identity import same_model_fact_identity
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.temporal_domain import (
    ProgressDelta,
    TemporalDomainBinding,
    TemporalDomainContractError,
    TemporalOrderProof,
    TemporalPosition,
)
from framework.temporal_runtime import BoundTemporalDomain
from framework.runtime_variant import RuntimeVariantBinding


class RuntimeKernelContractError(ValueError):
    pass


def _identifier(value: object, field_name: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise RuntimeKernelContractError(
            f"{field_name} must be a non-empty identifier")
    return normalized


@dataclass(frozen=True)
class RuntimeSessionBinding:
    """Exact model, progress domain, and TIDE runtime variant for one session."""

    session_id: str
    model_binding: InteractionModelBinding
    temporal_binding: TemporalDomainBinding
    runtime_variant: RuntimeVariantBinding
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        model_runtime: BoundInteractionModel,
        temporal_runtime: BoundTemporalDomain,
        runtime_variant: RuntimeVariantBinding,
    ) -> "RuntimeSessionBinding":
        if not isinstance(model_runtime, BoundInteractionModel):
            raise RuntimeKernelContractError(
                "runtime session requires a registry-bound model runtime")
        if not isinstance(temporal_runtime, BoundTemporalDomain):
            raise RuntimeKernelContractError(
                "runtime session requires a registry-bound temporal runtime")
        session_id = _identifier(model_runtime.session_id, "session_id")
        model_binding = model_runtime.binding
        temporal_binding = temporal_runtime.binding
        if not isinstance(runtime_variant, RuntimeVariantBinding):
            raise RuntimeKernelContractError(
                "runtime session requires a RuntimeVariantBinding")
        try:
            runtime_variant.verify_for(
                model_binding=model_binding,
                temporal_binding=temporal_binding,
            )
        except ValueError as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        material = {
            "session_id": session_id,
            "model_binding": model_binding.to_record(),
            "temporal_binding": temporal_binding.to_record(),
            "runtime_variant": runtime_variant.to_record(),
        }
        return cls(
            session_id=session_id,
            model_binding=model_binding,
            temporal_binding=temporal_binding,
            runtime_variant=runtime_variant,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        try:
            self.runtime_variant.verify_for(
                model_binding=self.model_binding,
                temporal_binding=self.temporal_binding,
            )
        except ValueError as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        material = {
            "session_id": _identifier(self.session_id, "session_id"),
            "model_binding": self.model_binding.to_record(),
            "temporal_binding": self.temporal_binding.to_record(),
            "runtime_variant": self.runtime_variant.to_record(),
        }
        if model_data_fingerprint(material) != self.fingerprint:
            raise RuntimeKernelContractError(
                "runtime session binding fingerprint is invalid")

    def verify_runtimes(
        self,
        *,
        model_runtime: BoundInteractionModel,
        temporal_runtime: BoundTemporalDomain,
    ) -> None:
        self.verify_integrity()
        if not isinstance(model_runtime, BoundInteractionModel):
            raise RuntimeKernelContractError(
                "runtime session requires a registry-bound model runtime")
        if model_runtime.session_id != self.session_id:
            raise RuntimeKernelContractError(
                "model runtime session does not match runtime session binding")
        if model_runtime.binding != self.model_binding:
            raise RuntimeKernelContractError(
                "model binding does not match runtime session binding")
        if not isinstance(temporal_runtime, BoundTemporalDomain):
            raise RuntimeKernelContractError(
                "runtime session requires a registry-bound temporal runtime")
        if temporal_runtime.binding != self.temporal_binding:
            raise RuntimeKernelContractError(
                "temporal binding does not match runtime session binding")

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "session_id": self.session_id,
            "model_binding": self.model_binding.to_record(),
            "temporal_binding": self.temporal_binding.to_record(),
            "runtime_variant": self.runtime_variant.to_record(),
            "runtime_variant_fingerprint": self.runtime_variant.fingerprint,
            "fingerprint": self.fingerprint,
        }

    @property
    def runtime_variant_fingerprint(self) -> str:
        return self.runtime_variant.fingerprint


@dataclass(frozen=True)
class RuntimePositionState:
    """Last accepted domain position plus Kernel-owned strict fact ordinal."""

    session_binding_fingerprint: str
    position: TemporalPosition
    runtime_ordinal: int
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        position: TemporalPosition,
        runtime_ordinal: int,
    ) -> "RuntimePositionState":
        session_binding.verify_integrity()
        if not isinstance(position, TemporalPosition):
            raise RuntimeKernelContractError("position must be TemporalPosition")
        position.verify_integrity()
        if position.binding != session_binding.temporal_binding:
            raise RuntimeKernelContractError(
                "position temporal binding does not match runtime session binding")
        if (
            isinstance(runtime_ordinal, bool)
            or not isinstance(runtime_ordinal, int)
            or runtime_ordinal < 0
        ):
            raise RuntimeKernelContractError(
                "runtime ordinal must be a non-negative integer")
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "position": position.to_record(),
            "runtime_ordinal": runtime_ordinal,
        }
        return cls(
            session_binding_fingerprint=session_binding.fingerprint,
            position=position,
            runtime_ordinal=runtime_ordinal,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self, session_binding: RuntimeSessionBinding) -> None:
        session_binding.verify_integrity()
        if self.session_binding_fingerprint != session_binding.fingerprint:
            raise RuntimeKernelContractError(
                "position state session binding does not match runtime session")
        expected = RuntimePositionState.create(
            session_binding=session_binding,
            position=self.position,
            runtime_ordinal=self.runtime_ordinal,
        )
        if expected.fingerprint != self.fingerprint:
            raise RuntimeKernelContractError(
                "runtime position state fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "position": self.position.to_record(),
            "runtime_ordinal": self.runtime_ordinal,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RuntimeModelParameters:
    """Immutable, signed parameter product for one formal model session."""

    session_binding_fingerprint: str
    initialization_output: Mapping[str, Any]
    initialization_receipt: Mapping[str, Any]
    initialization_receipt_fingerprint: str
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        execution: ModelStageExecution,
    ) -> "RuntimeModelParameters":
        session_binding.verify_integrity()
        if not isinstance(execution, ModelStageExecution):
            raise RuntimeKernelContractError(
                "model parameters require initialize_parameters execution")
        receipt = execution.receipt
        binding = session_binding.model_binding
        if (
            receipt.stage != "initialize_parameters"
            or receipt.session_id != session_binding.session_id
            or receipt.package_id != binding.package_id
            or receipt.package_version != binding.package_version
            or receipt.schema_fingerprint != binding.schema_fingerprint
            or receipt.parameter_fingerprint != binding.parameter_fingerprint
            or receipt.operator_version
            != binding.operator_versions.get("initialize_parameters")
            or receipt.output_fingerprint
            != model_data_fingerprint(execution.payload)
        ):
            raise RuntimeKernelContractError(
                "model parameter execution does not match runtime binding")
        parameters = execution.payload.get("parameters")
        if not isinstance(parameters, Mapping):
            raise RuntimeKernelContractError(
                "initialize_parameters output must contain parameters")
        try:
            output = freeze_model_payload(execution.payload)
            receipt_record = freeze_model_payload(receipt.to_record())
        except ValueError as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        receipt_fingerprint = model_data_fingerprint(receipt_record)
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "initialization_output": output,
            "initialization_receipt_fingerprint": receipt_fingerprint,
        }
        return cls(
            session_binding_fingerprint=session_binding.fingerprint,
            initialization_output=output,
            initialization_receipt=receipt_record,
            initialization_receipt_fingerprint=receipt_fingerprint,
            fingerprint=model_data_fingerprint(material),
        )

    @classmethod
    def restore(
        cls,
        record: Mapping[str, object],
        *,
        session_binding: RuntimeSessionBinding,
    ) -> "RuntimeModelParameters":
        if not isinstance(record, Mapping):
            raise RuntimeKernelContractError(
                "runtime model parameters record must be a mapping")
        required = {
            "session_binding_fingerprint", "initialization_output",
            "initialization_receipt", "initialization_receipt_fingerprint",
            "fingerprint",
        }
        if set(record) != required:
            raise RuntimeKernelContractError(
                "runtime model parameters record has invalid fields")
        output = record.get("initialization_output")
        receipt = record.get("initialization_receipt")
        if not isinstance(output, Mapping) or not isinstance(receipt, Mapping):
            raise RuntimeKernelContractError(
                "runtime model parameter output and receipt must be mappings")
        try:
            restored = cls(
                session_binding_fingerprint=str(
                    record.get("session_binding_fingerprint") or ""),
                initialization_output=freeze_model_payload(output),
                initialization_receipt=freeze_model_payload(receipt),
                initialization_receipt_fingerprint=str(
                    record.get("initialization_receipt_fingerprint") or ""),
                fingerprint=str(record.get("fingerprint") or ""),
            )
        except ValueError as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        restored.verify_integrity(session_binding)
        return restored

    @property
    def parameters(self) -> Mapping[str, Any]:
        value = self.initialization_output.get("parameters")
        if not isinstance(value, Mapping):
            raise RuntimeKernelContractError(
                "runtime model parameter product lacks parameters")
        return value

    def verify_integrity(self, session_binding: RuntimeSessionBinding) -> None:
        session_binding.verify_integrity()
        if self.session_binding_fingerprint != session_binding.fingerprint:
            raise RuntimeKernelContractError(
                "model parameter session binding does not match runtime session")
        receipt = self.initialization_receipt
        binding = session_binding.model_binding
        if (
            receipt.get("stage") != "initialize_parameters"
            or receipt.get("session_id") != session_binding.session_id
            or receipt.get("package_id") != binding.package_id
            or receipt.get("package_version") != binding.package_version
            or receipt.get("schema_fingerprint") != binding.schema_fingerprint
            or receipt.get("parameter_fingerprint")
            != binding.parameter_fingerprint
            or receipt.get("operator_version")
            != binding.operator_versions.get("initialize_parameters")
            or receipt.get("output_fingerprint")
            != model_data_fingerprint(self.initialization_output)
        ):
            raise RuntimeKernelContractError(
                "runtime model parameter receipt is invalid")
        if not isinstance(self.initialization_output.get("parameters"), Mapping):
            raise RuntimeKernelContractError(
                "runtime model parameter output is invalid")
        receipt_fingerprint = model_data_fingerprint(receipt)
        if receipt_fingerprint != self.initialization_receipt_fingerprint:
            raise RuntimeKernelContractError(
                "runtime model parameter receipt fingerprint is invalid")
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "initialization_output": self.initialization_output,
            "initialization_receipt_fingerprint": receipt_fingerprint,
        }
        if model_data_fingerprint(material) != self.fingerprint:
            raise RuntimeKernelContractError(
                "runtime model parameter fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "initialization_output": thaw_model_data(
                self.initialization_output),
            "initialization_receipt": thaw_model_data(
                self.initialization_receipt),
            "initialization_receipt_fingerprint": (
                self.initialization_receipt_fingerprint),
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RuntimeModelState:
    """Kernel-owned opaque model state at one accepted fact ordinal.

    The Kernel fingerprints and versions this value, but never interprets its
    dimensions. Its shape belongs exclusively to the bound InteractionModel.
    """

    session_binding_fingerprint: str
    state: Any
    revision: int
    committed_runtime_ordinal: int
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        state: Any,
        revision: int,
        committed_runtime_ordinal: int,
    ) -> "RuntimeModelState":
        session_binding.verify_integrity()
        for value, field_name in (
            (revision, "model state revision"),
            (committed_runtime_ordinal, "committed runtime ordinal"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise RuntimeKernelContractError(
                    f"{field_name} must be a non-negative integer")
        try:
            frozen_state = freeze_model_payload({"state": state})["state"]
        except ValueError as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "state": frozen_state,
            "revision": revision,
            "committed_runtime_ordinal": committed_runtime_ordinal,
        }
        return cls(
            session_binding_fingerprint=session_binding.fingerprint,
            state=frozen_state,
            revision=revision,
            committed_runtime_ordinal=committed_runtime_ordinal,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self, session_binding: RuntimeSessionBinding) -> None:
        session_binding.verify_integrity()
        if self.session_binding_fingerprint != session_binding.fingerprint:
            raise RuntimeKernelContractError(
                "model state session binding does not match runtime session")
        expected = RuntimeModelState.create(
            session_binding=session_binding,
            state=self.state,
            revision=self.revision,
            committed_runtime_ordinal=self.committed_runtime_ordinal,
        )
        if expected.fingerprint != self.fingerprint:
            raise RuntimeKernelContractError(
                "runtime model state fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "state": thaw_model_data(self.state),
            "revision": self.revision,
            "committed_runtime_ordinal": self.committed_runtime_ordinal,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RuntimeStateCommitAcceptance:
    """Pure receipt candidate for one Kernel-accepted model settlement."""

    session_binding_fingerprint: str
    previous_state_fingerprint: str
    previous_position_fingerprint: str
    state: RuntimeModelState
    position: RuntimePositionState
    stage_receipt_fingerprint: str
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        previous_state: RuntimeModelState,
        previous_position: RuntimePositionState,
        state: RuntimeModelState,
        position: RuntimePositionState,
        execution: ModelStageExecution,
    ) -> "RuntimeStateCommitAcceptance":
        previous_state.verify_integrity(session_binding)
        previous_position.verify_integrity(session_binding)
        state.verify_integrity(session_binding)
        position.verify_integrity(session_binding)
        if state.revision != previous_state.revision + 1:
            raise RuntimeKernelContractError(
                "accepted model state must increment revision exactly once")
        if position.runtime_ordinal != previous_position.runtime_ordinal + 1:
            raise RuntimeKernelContractError(
                "accepted settlement must increment runtime ordinal exactly once")
        if position.position.fingerprint != previous_position.position.fingerprint:
            raise RuntimeKernelContractError(
                "model settlement cannot change the accepted temporal position")
        if state.committed_runtime_ordinal != position.runtime_ordinal:
            raise RuntimeKernelContractError(
                "model state must bind to the settlement fact ordinal")
        stage_receipt_fingerprint = model_data_fingerprint(
            execution.receipt.to_record())
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "previous_state_fingerprint": previous_state.fingerprint,
            "previous_position_fingerprint": previous_position.fingerprint,
            "state_fingerprint": state.fingerprint,
            "position_fingerprint": position.fingerprint,
            "stage_receipt_fingerprint": stage_receipt_fingerprint,
        }
        return cls(
            session_binding_fingerprint=session_binding.fingerprint,
            previous_state_fingerprint=previous_state.fingerprint,
            previous_position_fingerprint=previous_position.fingerprint,
            state=state,
            position=position,
            stage_receipt_fingerprint=stage_receipt_fingerprint,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(
        self,
        *,
        session_binding: RuntimeSessionBinding,
        previous_state: RuntimeModelState,
        previous_position: RuntimePositionState,
    ) -> None:
        previous_state.verify_integrity(session_binding)
        previous_position.verify_integrity(session_binding)
        self.state.verify_integrity(session_binding)
        self.position.verify_integrity(session_binding)
        if self.session_binding_fingerprint != session_binding.fingerprint:
            raise RuntimeKernelContractError(
                "state commit acceptance session binding is invalid")
        if self.previous_state_fingerprint != previous_state.fingerprint:
            raise RuntimeKernelContractError(
                "state commit acceptance does not continue current model state")
        if self.previous_position_fingerprint != previous_position.fingerprint:
            raise RuntimeKernelContractError(
                "state commit acceptance does not continue current position")
        if self.state.revision != previous_state.revision + 1:
            raise RuntimeKernelContractError(
                "state commit acceptance model revision is invalid")
        if self.position.runtime_ordinal != previous_position.runtime_ordinal + 1:
            raise RuntimeKernelContractError(
                "state commit acceptance runtime ordinal is invalid")
        if self.position.position.fingerprint != previous_position.position.fingerprint:
            raise RuntimeKernelContractError(
                "state commit acceptance changed temporal position")
        if self.state.committed_runtime_ordinal != self.position.runtime_ordinal:
            raise RuntimeKernelContractError(
                "state commit acceptance ordinal lineage is invalid")
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "previous_state_fingerprint": previous_state.fingerprint,
            "previous_position_fingerprint": previous_position.fingerprint,
            "state_fingerprint": self.state.fingerprint,
            "position_fingerprint": self.position.fingerprint,
            "stage_receipt_fingerprint": self.stage_receipt_fingerprint,
        }
        if model_data_fingerprint(material) != self.fingerprint:
            raise RuntimeKernelContractError(
                "state commit acceptance fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "previous_state_fingerprint": self.previous_state_fingerprint,
            "previous_position_fingerprint": self.previous_position_fingerprint,
            "state": self.state.to_record(),
            "position": self.position.to_record(),
            "stage_receipt_fingerprint": self.stage_receipt_fingerprint,
            "fingerprint": self.fingerprint,
        }


@dataclass(frozen=True)
class RuntimeProgressAcceptance:
    """Pure receipt candidate for one Kernel-accepted position observation."""

    session_binding_fingerprint: str
    previous_state_fingerprint: str
    state: RuntimePositionState
    order_proof: TemporalOrderProof
    progress: ProgressDelta
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        previous_state: RuntimePositionState,
        state: RuntimePositionState,
        order_proof: TemporalOrderProof,
        progress: ProgressDelta,
    ) -> "RuntimeProgressAcceptance":
        previous_state.verify_integrity(session_binding)
        state.verify_integrity(session_binding)
        progress.verify_integrity()
        if state.runtime_ordinal != previous_state.runtime_ordinal + 1:
            raise RuntimeKernelContractError(
                "accepted position must increment runtime ordinal exactly once")
        if order_proof.from_position.fingerprint != previous_state.position.fingerprint:
            raise RuntimeKernelContractError(
                "order proof does not start at previous accepted position")
        if order_proof.to_position.fingerprint != state.position.fingerprint:
            raise RuntimeKernelContractError(
                "order proof does not end at next accepted position")
        if progress.order_proof.fingerprint != order_proof.fingerprint:
            raise RuntimeKernelContractError(
                "progress does not reference accepted order proof")
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "previous_state_fingerprint": previous_state.fingerprint,
            "state_fingerprint": state.fingerprint,
            "order_proof_fingerprint": order_proof.fingerprint,
            "progress_fingerprint": progress.fingerprint,
        }
        return cls(
            session_binding_fingerprint=session_binding.fingerprint,
            previous_state_fingerprint=previous_state.fingerprint,
            state=state,
            order_proof=order_proof,
            progress=progress,
            fingerprint=model_data_fingerprint(material),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "previous_state_fingerprint": self.previous_state_fingerprint,
            "state": self.state.to_record(),
            "order_proof": self.order_proof.to_record(),
            "progress": self.progress.to_record(),
            "fingerprint": self.fingerprint,
        }

    def verify_integrity(
        self,
        *,
        session_binding: RuntimeSessionBinding,
        previous_state: RuntimePositionState,
    ) -> None:
        previous_state.verify_integrity(session_binding)
        self.state.verify_integrity(session_binding)
        self.order_proof.verify_integrity()
        self.progress.verify_integrity()
        if self.session_binding_fingerprint != session_binding.fingerprint:
            raise RuntimeKernelContractError(
                "progress acceptance session binding is invalid")
        if self.previous_state_fingerprint != previous_state.fingerprint:
            raise RuntimeKernelContractError(
                "progress acceptance does not continue the current state")
        if self.state.runtime_ordinal != previous_state.runtime_ordinal + 1:
            raise RuntimeKernelContractError(
                "progress acceptance runtime ordinal is invalid")
        if (self.order_proof.from_position.fingerprint
                != previous_state.position.fingerprint):
            raise RuntimeKernelContractError(
                "progress acceptance does not start at current position")
        if (self.order_proof.to_position.fingerprint
                != self.state.position.fingerprint):
            raise RuntimeKernelContractError(
                "progress acceptance does not end at accepted position")
        if self.progress.order_proof.fingerprint != self.order_proof.fingerprint:
            raise RuntimeKernelContractError(
                "progress acceptance delta references another order proof")
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "previous_state_fingerprint": previous_state.fingerprint,
            "state_fingerprint": self.state.fingerprint,
            "order_proof_fingerprint": self.order_proof.fingerprint,
            "progress_fingerprint": self.progress.fingerprint,
        }
        if model_data_fingerprint(material) != self.fingerprint:
            raise RuntimeKernelContractError(
                "progress acceptance fingerprint is invalid")


@dataclass(frozen=True)
class RuntimeProgressPreparation:
    """Verified temporal progress that belongs to a pending fact settlement.

    Unlike :class:`RuntimeProgressAcceptance`, this evidence does not consume a
    runtime ordinal.  Its target position is the position at which the pending
    fact will be settled; the subsequent state settlement is the sole ordinal
    increment for that fact.
    """

    session_binding_fingerprint: str
    previous_state_fingerprint: str
    state: RuntimePositionState
    order_proof: TemporalOrderProof
    progress: ProgressDelta
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        session_binding: RuntimeSessionBinding,
        previous_state: RuntimePositionState,
        state: RuntimePositionState,
        order_proof: TemporalOrderProof,
        progress: ProgressDelta,
    ) -> "RuntimeProgressPreparation":
        previous_state.verify_integrity(session_binding)
        state.verify_integrity(session_binding)
        order_proof.verify_integrity()
        progress.verify_integrity()
        if state.runtime_ordinal != previous_state.runtime_ordinal:
            raise RuntimeKernelContractError(
                "prepared position cannot consume a runtime ordinal")
        if order_proof.from_position.fingerprint != previous_state.position.fingerprint:
            raise RuntimeKernelContractError(
                "prepared order proof does not start at current position")
        if order_proof.to_position.fingerprint != state.position.fingerprint:
            raise RuntimeKernelContractError(
                "prepared order proof does not end at target position")
        if progress.order_proof.fingerprint != order_proof.fingerprint:
            raise RuntimeKernelContractError(
                "prepared progress does not reference its order proof")
        material = {
            "session_binding_fingerprint": session_binding.fingerprint,
            "previous_state_fingerprint": previous_state.fingerprint,
            "state_fingerprint": state.fingerprint,
            "order_proof_fingerprint": order_proof.fingerprint,
            "progress_fingerprint": progress.fingerprint,
        }
        return cls(
            session_binding_fingerprint=session_binding.fingerprint,
            previous_state_fingerprint=previous_state.fingerprint,
            state=state,
            order_proof=order_proof,
            progress=progress,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(
        self,
        *,
        session_binding: RuntimeSessionBinding,
        previous_state: RuntimePositionState,
    ) -> None:
        expected = RuntimeProgressPreparation.create(
            session_binding=session_binding,
            previous_state=previous_state,
            state=self.state,
            order_proof=self.order_proof,
            progress=self.progress,
        )
        if (
            self.session_binding_fingerprint != expected.session_binding_fingerprint
            or self.previous_state_fingerprint != expected.previous_state_fingerprint
            or self.fingerprint != expected.fingerprint
        ):
            raise RuntimeKernelContractError(
                "prepared progress integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "previous_state_fingerprint": self.previous_state_fingerprint,
            "state": self.state.to_record(),
            "order_proof": self.order_proof.to_record(),
            "progress": self.progress.to_record(),
            "fingerprint": self.fingerprint,
        }


class RuntimeProgressAuthority:
    """Kernel-owned pure validator for host-submitted monotonic positions."""

    def __init__(
        self,
        *,
        session_binding: RuntimeSessionBinding,
        temporal_runtime: BoundTemporalDomain,
    ) -> None:
        if not isinstance(session_binding, RuntimeSessionBinding):
            raise RuntimeKernelContractError(
                "progress authority requires RuntimeSessionBinding")
        session_binding.verify_integrity()
        if not isinstance(temporal_runtime, BoundTemporalDomain):
            raise RuntimeKernelContractError(
                "progress authority requires a bound temporal runtime")
        if temporal_runtime.binding != session_binding.temporal_binding:
            raise RuntimeKernelContractError(
                "temporal binding does not match runtime session binding")
        self.session_binding = session_binding
        self.temporal_runtime = temporal_runtime

    def initialize(self, coordinate: object) -> RuntimePositionState:
        position = self.temporal_runtime.normalize_position(coordinate)
        return RuntimePositionState.create(
            session_binding=self.session_binding,
            position=position,
            runtime_ordinal=0,
        )

    def accept_position(
        self, current: RuntimePositionState, coordinate: object,
    ) -> RuntimeProgressAcceptance:
        if not isinstance(current, RuntimePositionState):
            raise RuntimeKernelContractError(
                "current state must be RuntimePositionState")
        current.verify_integrity(self.session_binding)
        next_position = self.temporal_runtime.normalize_position(coordinate)
        try:
            order_proof = self.temporal_runtime.prove_order(
                current.position, next_position)
            progress = self.temporal_runtime.derive_progress(order_proof)
        except TemporalDomainContractError as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        next_state = RuntimePositionState.create(
            session_binding=self.session_binding,
            position=next_position,
            runtime_ordinal=current.runtime_ordinal + 1,
        )
        return RuntimeProgressAcceptance.create(
            session_binding=self.session_binding,
            previous_state=current,
            state=next_state,
            order_proof=order_proof,
            progress=progress,
        )

    def prepare_position(
        self, current: RuntimePositionState, coordinate: object,
    ) -> RuntimeProgressPreparation:
        """Verify a target position without consuming the pending fact ordinal."""
        if not isinstance(current, RuntimePositionState):
            raise RuntimeKernelContractError(
                "current state must be RuntimePositionState")
        current.verify_integrity(self.session_binding)
        next_position = self.temporal_runtime.normalize_position(coordinate)
        try:
            order_proof = self.temporal_runtime.prove_order(
                current.position, next_position)
            progress = self.temporal_runtime.derive_progress(order_proof)
        except TemporalDomainContractError as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        prepared_state = RuntimePositionState.create(
            session_binding=self.session_binding,
            position=next_position,
            runtime_ordinal=current.runtime_ordinal,
        )
        return RuntimeProgressPreparation.create(
            session_binding=self.session_binding,
            previous_state=current,
            state=prepared_state,
            order_proof=order_proof,
            progress=progress,
        )

    def restore_state(self, record: Mapping[str, object]) -> RuntimePositionState:
        if not isinstance(record, Mapping):
            raise RuntimeKernelContractError(
                "runtime position state record must be a mapping")
        required = {
            "session_binding_fingerprint", "position",
            "runtime_ordinal", "fingerprint",
        }
        if set(record) != required:
            raise RuntimeKernelContractError(
                "runtime position state record has invalid fields")
        position_record = record.get("position")
        if not isinstance(position_record, Mapping):
            raise RuntimeKernelContractError(
                "runtime position state requires a position record")
        if set(position_record) != {"binding", "coordinate", "fingerprint"}:
            raise RuntimeKernelContractError(
                "runtime position record has invalid fields")
        if position_record.get("binding") != self.temporal_runtime.binding.to_record():
            raise RuntimeKernelContractError(
                "restored position temporal binding does not match runtime session")
        position = self.temporal_runtime.normalize_position(
            position_record.get("coordinate"))
        if position.to_record() != dict(position_record):
            raise RuntimeKernelContractError(
                "restored position fingerprint is invalid")
        state = RuntimePositionState.create(
            session_binding=self.session_binding,
            position=position,
            runtime_ordinal=record.get("runtime_ordinal"),
        )
        if record.get("session_binding_fingerprint") != self.session_binding.fingerprint:
            raise RuntimeKernelContractError(
                "restored state session binding does not match runtime session")
        if record.get("fingerprint") != state.fingerprint:
            raise RuntimeKernelContractError(
                "runtime position state fingerprint is invalid")
        return state

    def invocation_context_v2(
        self,
        state: RuntimePositionState,
        *,
        scope_ref: str | None,
        subject_ref: str | None,
        event_ref: str | None,
        action_ref: str | None,
        metadata: Mapping[str, object] | None = None,
    ) -> ModelInvocationContextV2:
        if not isinstance(state, RuntimePositionState):
            raise RuntimeKernelContractError(
                "context state must be RuntimePositionState")
        state.verify_integrity(self.session_binding)
        return ModelInvocationContextV2(
            session_id=self.session_binding.session_id,
            scope_ref=scope_ref,
            subject_ref=subject_ref,
            event_ref=event_ref,
            action_ref=action_ref,
            temporal_position=state.position,
            runtime_ordinal=state.runtime_ordinal,
            metadata={} if metadata is None else metadata,
        )


class RuntimeStateCommitAuthority:
    """Kernel-owned validator for one opaque InteractionModel settlement.

    This authority does not write storage. It produces the only valid candidate
    for a host transaction to persist as the next authoritative model state and
    fact ordinal.
    """

    def __init__(self, *, session_binding: RuntimeSessionBinding) -> None:
        if not isinstance(session_binding, RuntimeSessionBinding):
            raise RuntimeKernelContractError(
                "state commit authority requires RuntimeSessionBinding")
        session_binding.verify_integrity()
        self.session_binding = session_binding

    @staticmethod
    def _same_context_identity(
        context: ModelInvocationContext | ModelInvocationContextV2,
        execution: ModelStageExecution,
    ) -> bool:
        return same_model_fact_identity(context, execution.receipt)

    def _verify_receipt_binding(self, execution: ModelStageExecution) -> None:
        receipt = execution.receipt
        binding = self.session_binding.model_binding
        expected_operator = binding.operator_versions.get("settle_state")
        if (
            receipt.package_id != binding.package_id
            or receipt.package_version != binding.package_version
            or receipt.schema_fingerprint != binding.schema_fingerprint
            or receipt.parameter_fingerprint != binding.parameter_fingerprint
            or receipt.operator_version != expected_operator
        ):
            raise RuntimeKernelContractError(
                "settle_state receipt does not match runtime model binding")

    def accept_settlement(
        self,
        *,
        current_state: RuntimeModelState,
        current_position: RuntimePositionState,
        invocation_context: ModelInvocationContext | ModelInvocationContextV2,
        invocation_payload: Mapping[str, Any],
        execution: ModelStageExecution,
    ) -> RuntimeStateCommitAcceptance:
        if not isinstance(current_state, RuntimeModelState):
            raise RuntimeKernelContractError(
                "current model state must be RuntimeModelState")
        if not isinstance(current_position, RuntimePositionState):
            raise RuntimeKernelContractError(
                "current position must be RuntimePositionState")
        if not isinstance(
            invocation_context, (ModelInvocationContext, ModelInvocationContextV2)
        ):
            raise RuntimeKernelContractError(
                "settlement context must be a supported invocation context")
        if not isinstance(execution, ModelStageExecution):
            raise RuntimeKernelContractError(
                "settlement execution must be ModelStageExecution")
        if execution.receipt.stage != "settle_state":
            raise RuntimeKernelContractError(
                "state commit authority accepts only settle_state execution")

        current_state.verify_integrity(self.session_binding)
        current_position.verify_integrity(self.session_binding)
        if current_state.committed_runtime_ordinal > current_position.runtime_ordinal:
            raise RuntimeKernelContractError(
                "current model state is ahead of current runtime position")
        if not isinstance(invocation_payload, Mapping):
            raise RuntimeKernelContractError(
                "settle_state invocation payload must be a mapping")
        if "state" not in invocation_payload:
            raise RuntimeKernelContractError(
                "settle_state invocation payload must contain current model state")
        if (
            model_data_fingerprint(invocation_payload["state"])
            != model_data_fingerprint(current_state.state)
        ):
            raise RuntimeKernelContractError(
                "settle_state was not computed from current model state")

        receipt = execution.receipt
        if receipt.input_fingerprint != model_data_fingerprint(invocation_payload):
            raise RuntimeKernelContractError(
                "settle_state input fingerprint does not match invocation payload")
        if receipt.output_fingerprint != model_data_fingerprint(execution.payload):
            raise RuntimeKernelContractError(
                "settle_state output fingerprint does not match execution payload")
        self._verify_receipt_binding(execution)

        if (
            invocation_context.session_id != self.session_binding.session_id
            or invocation_context.temporal_position is None
            or invocation_context.runtime_ordinal is None
            or invocation_context.temporal_position.fingerprint
            != current_position.position.fingerprint
            or invocation_context.runtime_ordinal != current_position.runtime_ordinal
            or not self._same_context_identity(invocation_context, execution)
        ):
            raise RuntimeKernelContractError(
                "settle_state receipt context identity does not match current fact")

        settlement = execution.payload.get("settlement")
        if not isinstance(settlement, Mapping):
            raise RuntimeKernelContractError(
                "settle_state output must contain a settlement mapping")
        if "state_candidate" not in settlement:
            raise RuntimeKernelContractError(
                "settle_state output must contain state_candidate")

        next_position = RuntimePositionState.create(
            session_binding=self.session_binding,
            position=current_position.position,
            runtime_ordinal=current_position.runtime_ordinal + 1,
        )
        next_state = RuntimeModelState.create(
            session_binding=self.session_binding,
            state=settlement["state_candidate"],
            revision=current_state.revision + 1,
            committed_runtime_ordinal=next_position.runtime_ordinal,
        )
        return RuntimeStateCommitAcceptance.create(
            session_binding=self.session_binding,
            previous_state=current_state,
            previous_position=current_position,
            state=next_state,
            position=next_position,
            execution=execution,
        )

    def restore_state(self, record: Mapping[str, object]) -> RuntimeModelState:
        if not isinstance(record, Mapping):
            raise RuntimeKernelContractError(
                "runtime model state record must be a mapping")
        required = {
            "session_binding_fingerprint", "state", "revision",
            "committed_runtime_ordinal", "fingerprint",
        }
        if set(record) != required:
            raise RuntimeKernelContractError(
                "runtime model state record has invalid fields")
        restored = RuntimeModelState.create(
            session_binding=self.session_binding,
            state=record.get("state"),
            revision=record.get("revision"),
            committed_runtime_ordinal=record.get("committed_runtime_ordinal"),
        )
        if (
            record.get("session_binding_fingerprint")
            != self.session_binding.fingerprint
        ):
            raise RuntimeKernelContractError(
                "restored model state session binding does not match runtime session")
        if record.get("fingerprint") != restored.fingerprint:
            raise RuntimeKernelContractError(
                "runtime model state fingerprint is invalid")
        return restored

    def restore_position_state(
        self, record: Mapping[str, object],
    ) -> RuntimePositionState:
        if not isinstance(record, Mapping):
            raise RuntimeKernelContractError(
                "runtime position state record must be a mapping")
        required = {
            "session_binding_fingerprint", "position",
            "runtime_ordinal", "fingerprint",
        }
        if set(record) != required:
            raise RuntimeKernelContractError(
                "runtime position state record has invalid fields")
        position_record = record.get("position")
        if not isinstance(position_record, Mapping):
            raise RuntimeKernelContractError(
                "runtime position state requires a position record")
        if set(position_record) != {"binding", "coordinate", "fingerprint"}:
            raise RuntimeKernelContractError(
                "runtime position record has invalid fields")
        if position_record.get("binding") != (
            self.session_binding.temporal_binding.to_record()
        ):
            raise RuntimeKernelContractError(
                "restored position temporal binding does not match runtime session")
        try:
            position = TemporalPosition.create(
                binding=self.session_binding.temporal_binding,
                coordinate=position_record.get("coordinate"),
            )
        except (TypeError, ValueError) as exc:
            raise RuntimeKernelContractError(str(exc)) from exc
        if position.to_record() != dict(position_record):
            raise RuntimeKernelContractError(
                "restored position fingerprint is invalid")
        restored = RuntimePositionState.create(
            session_binding=self.session_binding,
            position=position,
            runtime_ordinal=record.get("runtime_ordinal"),
        )
        if (
            record.get("session_binding_fingerprint")
            != self.session_binding.fingerprint
        ):
            raise RuntimeKernelContractError(
                "restored position session binding does not match runtime session")
        if record.get("fingerprint") != restored.fingerprint:
            raise RuntimeKernelContractError(
                "runtime position state fingerprint is invalid")
        return restored


__all__ = (
    "RuntimeKernelContractError",
    "RuntimeModelParameters",
    "RuntimeModelState",
    "RuntimePositionState",
    "RuntimeProgressAcceptance",
    "RuntimeProgressPreparation",
    "RuntimeProgressAuthority",
    "RuntimeSessionBinding",
    "RuntimeStateCommitAcceptance",
    "RuntimeStateCommitAuthority",
)
