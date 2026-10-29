"""Model-neutral read-only action assessment product."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from framework.model_api import (
    ModelStageExecution,
    ModelStageReceipt,
    freeze_model_payload,
    model_data_fingerprint,
)
from framework.model_api.identity import receipt_fact_identity
from framework.model_api.invocation_v2 import ModelStageReceiptV2
from framework.temporal_domain import TemporalPosition


@dataclass(frozen=True)
class ActionAssessment:
    action_kind: str
    eligible: bool
    constraints: Mapping[str, object]
    observation_advice: Mapping[str, object]
    stage_receipt: ModelStageReceipt | ModelStageReceiptV2

    def __post_init__(self) -> None:
        action_kind = str(self.action_kind or "").strip()
        if not action_kind:
            raise ValueError("action_kind must be a non-empty identifier")
        if type(self.eligible) is not bool:
            raise ValueError("eligible must be boolean")
        object.__setattr__(self, "action_kind", action_kind)
        object.__setattr__(self, "constraints", freeze_model_payload(
            {"value": self.constraints})["value"])
        object.__setattr__(self, "observation_advice", freeze_model_payload(
            {"value": self.observation_advice})["value"])


def read_action_assessment(
    execution: ModelStageExecution,
    *,
    source_fact_ref: str,
    source_position: TemporalPosition,
    source_runtime_ordinal: int,
) -> ActionAssessment:
    """Validate one package-owned assessment without granting authority."""
    if not isinstance(execution, ModelStageExecution):
        raise ValueError("action assessment requires model stage execution")
    if not isinstance(source_position, TemporalPosition):
        raise ValueError("action assessment requires temporal position")
    source_position.verify_integrity()
    source_ref = str(source_fact_ref or "").strip()
    if not source_ref:
        raise ValueError("action assessment requires source fact reference")
    if (
        isinstance(source_runtime_ordinal, bool)
        or not isinstance(source_runtime_ordinal, int)
        or source_runtime_ordinal < 0
    ):
        raise ValueError("action assessment requires runtime ordinal")
    receipt = execution.receipt
    identity = receipt_fact_identity(receipt)
    if receipt.stage != "assess_action":
        raise ValueError("action assessment requires assess_action stage")
    if (
        identity.event_ref != source_ref
        or identity.action_ref is not None
        or receipt.temporal_position is None
        or receipt.temporal_position.fingerprint != source_position.fingerprint
        or receipt.runtime_ordinal != source_runtime_ordinal
    ):
        raise ValueError("action assessment differs from source fact")
    if model_data_fingerprint(execution.payload) != receipt.output_fingerprint:
        raise ValueError("action assessment payload differs from stage receipt")
    decision = execution.payload.get("action_assessment")
    if not isinstance(decision, Mapping):
        raise ValueError("action_assessment output is missing")
    required = {
        "version", "source_shared_projection_fingerprint", "action_kind",
        "eligible", "constraints", "observation_advice",
        "action_commit_authority",
    }
    if set(decision) != required:
        raise ValueError("action_assessment fields are invalid")
    if decision.get("action_commit_authority") is not False:
        raise ValueError("model action assessment cannot carry commit authority")
    constraints = decision.get("constraints")
    advice = decision.get("observation_advice")
    if not isinstance(constraints, Mapping) or not isinstance(advice, Mapping):
        raise ValueError("action assessment opaque products are invalid")
    return ActionAssessment(
        action_kind=decision.get("action_kind"),
        eligible=decision.get("eligible"),
        constraints=constraints,
        observation_advice=advice,
        stage_receipt=receipt,
    )


__all__ = ("ActionAssessment", "read_action_assessment")
