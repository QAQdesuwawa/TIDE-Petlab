"""F2 read-only creation comparison anchored to a saved Runner profile.

Only the two creation products stored verbatim in the profile are compared here.
Effective constants have no independent committed creation baseline in this
profile and must not be recomputed and mislabeled as one.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    ModelStageReceipt, freeze_model_payload, model_data_fingerprint,
)
from framework.model_runtime import BoundInteractionModel
from ops.model_shadow_context import creation_context_from_committed_profile


@dataclass(frozen=True)
class ShadowCreationComparison:
    stage: str
    creation_profile_fingerprint: str
    baseline_output_fingerprint: str
    shadow_output_fingerprint: str
    matched: bool
    shadow_stage_receipt: ModelStageReceipt

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "kind": "model_shadow_creation_comparison",
            "stage": self.stage,
            "creation_profile_fingerprint": self.creation_profile_fingerprint,
            "baseline_output_fingerprint": self.baseline_output_fingerprint,
            "shadow_output_fingerprint": self.shadow_output_fingerprint,
            "matched": self.matched,
            "shadow_stage_receipt": self.shadow_stage_receipt.to_record(),
        }


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"saved creation profile lacks {name}")
    return value


def compare_committed_creation_stage(
    runtime: BoundInteractionModel,
    stage: str,
    *,
    committed_profile: Mapping[str, Any],
) -> ShadowCreationComparison:
    """Compare saved C_base or X0 against a bound model without writing either.

    The state stage starts from persisted normalized X0, so it proves parity on
    that saved input, not reconstruction from a discarded raw creation card.
    """
    if stage not in {"initialize_constants", "initialize_state"}:
        raise ValueError("creation shadow comparison has no saved baseline for stage")
    context = creation_context_from_committed_profile(
        committed_profile, runtime=runtime)
    profile = _mapping(committed_profile, "profile")
    if stage == "initialize_constants":
        weights = _mapping(profile.get("weights"), "weights")
        constants = _mapping(profile.get("constants"), "constants")
        payload = {"weights": dict(weights)}
        baseline = {"constants": dict(constants)}
    else:
        initial_state = _mapping(profile.get("initial_state"), "initial_state")
        state_vars = _mapping(initial_state.get("vars"), "initial_state.vars")
        payload = {"initial_vars": {"vars": dict(state_vars)}}
        baseline = {"state_vars": dict(state_vars)}

    profile_fingerprint = model_data_fingerprint(freeze_model_payload(profile))
    baseline_fingerprint = model_data_fingerprint(freeze_model_payload(baseline))
    execution = runtime.invoke(stage, payload=payload, context=context)
    shadow_fingerprint = execution.receipt.output_fingerprint
    return ShadowCreationComparison(
        stage=stage,
        creation_profile_fingerprint=profile_fingerprint,
        baseline_output_fingerprint=baseline_fingerprint,
        shadow_output_fingerprint=shadow_fingerprint,
        matched=baseline_fingerprint == shadow_fingerprint,
        shadow_stage_receipt=execution.receipt,
    )
