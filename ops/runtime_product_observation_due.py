"""Read-only due proofs for the exact persisted owner-local active plan.

The caller supplies only an external coordinate.  The bound TemporalDomain
normalizes that coordinate and decides whether the durable target is due.  No
state, plan, model, clock, timer, action, or output authority lives here.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import freeze_model_payload, model_data_fingerprint
from framework.temporal_domain import ObservationDueProof, TemporalPosition
from framework.temporal_runtime import BoundTemporalDomain
from ops.runtime_product_observation_plan import (
    RuntimeProductObservationPlan,
    load_active_product_observation_plan,
)
from ops.runtime_session_bootstrap import BootstrappedRuntimeSession


RUNTIME_PRODUCT_OBSERVATION_DUE_PROBE_VERSION = (
    "runtime-product-observation-due-probe-v1"
)


@dataclass(frozen=True)
class RuntimeProductObservationDueProbe:
    plan: RuntimeProductObservationPlan
    sampled_position: TemporalPosition
    due_proof: ObservationDueProof
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def probe_active_product_observation_due(
    session: BootstrappedRuntimeSession,
    *,
    temporal_runtime: BoundTemporalDomain,
    target_coordinate: object,
) -> RuntimeProductObservationDueProbe:
    """Prove due state for the durable active target without side effects."""
    if not isinstance(session, BootstrappedRuntimeSession):
        raise ValueError("observation due probe requires a runtime session")
    if not isinstance(temporal_runtime, BoundTemporalDomain):
        raise ValueError("observation due probe requires a temporal runtime")
    if temporal_runtime.binding != session.session_binding.temporal_binding:
        raise ValueError("observation due probe temporal binding differs from session")

    plan = load_active_product_observation_plan(
        session.profile,
        session_binding=session.session_binding,
        temporal_runtime=temporal_runtime,
    )
    if (
        plan.target.origin_position.fingerprint
        != session.position.position.fingerprint
    ):
        raise ValueError("active observation plan origin is not current")

    sampled_position = temporal_runtime.normalize_position(target_coordinate)
    due_proof = temporal_runtime.prove_observation_due(
        plan.target, sampled_position)
    material = {
        "version": RUNTIME_PRODUCT_OBSERVATION_DUE_PROBE_VERSION,
        "status": "active_observation_due_probed",
        "session_binding_fingerprint": session.session_binding.fingerprint,
        "plan_id": plan.plan_id,
        "plan_receipt_fingerprint": model_data_fingerprint(plan.receipt),
        "target_fingerprint": plan.target.fingerprint,
        "sampled_position": sampled_position.to_record(),
        "due_proof": due_proof.to_record(),
        "authority": {
            "position_advance": False,
            "action_assessment": False,
            "active_plan_mutation": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return RuntimeProductObservationDueProbe(
        plan=plan,
        sampled_position=sampled_position,
        due_proof=due_proof,
        receipt=receipt,
    )


__all__ = (
    "RUNTIME_PRODUCT_OBSERVATION_DUE_PROBE_VERSION",
    "RuntimeProductObservationDueProbe",
    "probe_active_product_observation_due",
)
