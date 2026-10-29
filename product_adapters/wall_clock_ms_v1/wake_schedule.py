"""Read-only one-shot wake schedule for the wall-clock reference domain."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from framework.model_api import freeze_model_payload, model_data_fingerprint
from ops.runtime_product_observation_plan import RuntimeProductObservationPlan
from temporal_packages.wall_clock_ms_v1 import WallClockMillisecondsV1


@dataclass(frozen=True)
class WallClockObservationWakeSchedule:
    """A recoverable host wake derived from one exact persisted active plan."""

    owner_participant_id: str
    plan_id: str
    at_ms: int
    tick_event_id: str
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def build_wall_clock_observation_wake_schedule(
    *,
    plan: RuntimeProductObservationPlan,
    owner_participant_id: str,
) -> WallClockObservationWakeSchedule:
    """Project host scheduling data without creating another authority."""
    if not isinstance(plan, RuntimeProductObservationPlan):
        raise TypeError("wall-clock wake schedule requires an active plan")
    owner_id = str(owner_participant_id or "").strip()
    if not owner_id:
        raise ValueError("wall-clock wake schedule requires owner identity")
    at_ms = WallClockMillisecondsV1._milliseconds(
        plan.target.payload.get("at_ms"), "target.at_ms")
    identity_material = {
        "version": "wall-clock-observation-wake-identity-v1",
        "owner_participant_id": owner_id,
        "plan_id": plan.plan_id,
        "plan_receipt_fingerprint": model_data_fingerprint(plan.receipt),
        "target_fingerprint": plan.target.fingerprint,
        "at_ms": at_ms,
    }
    tick_event_id = (
        "tick:wall-clock-observation:"
        + model_data_fingerprint(identity_material)
    )
    material = {
        "version": "wall-clock-observation-wake-schedule-v1",
        **identity_material,
        "tick_event_id": tick_event_id,
        "authority": {
            "due_proof": False,
            "position_advance": False,
            "action_assessment": False,
            "send": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return WallClockObservationWakeSchedule(
        owner_participant_id=owner_id,
        plan_id=plan.plan_id,
        at_ms=at_ms,
        tick_event_id=tick_event_id,
        receipt=receipt,
    )


__all__ = (
    "WallClockObservationWakeSchedule",
    "build_wall_clock_observation_wake_schedule",
)
