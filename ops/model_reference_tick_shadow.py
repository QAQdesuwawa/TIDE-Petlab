"""F2 read-only comparison for the six/seven reference model's opening tick.

The generic framework deliberately does not know P3, F15, or a crossing edge.
This compatibility reader uses already committed owner-local rows. It never
refreshes a plan, consumes an edge, or authorizes an F16 message.
"""
from __future__ import annotations

from collections.abc import Mapping

from framework.model_api import ModelStageExecution
from framework.model_runtime import BoundInteractionModel
from ops.model_tick_opening_shadow import read_committed_tick_opening_shadow


def _row(value: object, name: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"tick {name} is missing")
    return value


def compare_reference_tick_opening_shadow(
    execution: ModelStageExecution, *,
    p3_receipt: Mapping[str, object],
    wall_boundary: Mapping[str, object],
    actor_observation: Mapping[str, object],
    profile: Mapping[str, object],
    runtime: BoundInteractionModel,
) -> dict[str, object]:
    """Compare model raw qualification and observation advice to one formal tick.

    `baseline_edge_dispatch_eligible` is a separate, already settled one-shot
    scheduling fact. It is *not* equivalent to the raw mathematical crossing
    and does not prove that a due tick has committed a message.
    """
    model = read_committed_tick_opening_shadow(
        execution, p3_receipt=p3_receipt, wall_boundary=wall_boundary,
        actor_observation=actor_observation, profile=profile, runtime=runtime,
    )
    p3 = _row(p3_receipt.get("detail"), "P3 detail")
    event = _row(p3.get("event"), "P3 event")
    boundary = _row(wall_boundary.get("detail"), "wall boundary")
    candidate = _row(boundary.get("f15_candidate"), "F15 candidate")
    edge = _row(boundary.get("opening_edge"), "opening edge")
    tick_id = event["id"]
    at_ms = wall_boundary["ts"]

    anchor_ts = candidate.get("anchor_ts")
    anchor_turn = candidate.get("anchor_turn_id")
    raw = candidate.get("eligible")
    qualified = candidate.get("qualified_at_ms")
    if (type(raw) is not bool
            or type(anchor_turn) is not str
            or (anchor_ts is not None and (type(anchor_ts) is not int or anchor_ts < 0))
            or candidate.get("formula_version") != "opening-authority-receipt-v1"
            or candidate.get("authority_event_id") != tick_id
            or candidate.get("authority_event_kind") != "tick"
            or qualified != (at_ms if raw else None)):
        raise ValueError("tick F15 candidate differs from committed P3 tick")
    margin = p3.get("margin")
    if (type(margin) not in (int, float)
            or raw != bool(anchor_ts is not None and margin >= 0.0)):
        raise ValueError("tick F15 candidate conflicts with committed P3 margin or anchor")

    dispatch = edge.get("dispatch_eligible")
    disposition = edge.get("disposition")
    expected_dispositions = (
        {"crossed"} if raw and dispatch
        else {"held_above_after_consumption"} if raw
        else {"below_zero", "below_zero_rearmed"}
    )
    if (edge.get("version") != "opening-edge-receipt-v1"
            or edge.get("anchor") != [candidate["formula_version"], anchor_ts, anchor_turn]
            or edge.get("observed_at_ms") != at_ms
            or type(edge.get("above")) is not bool or edge["above"] != raw
            or edge.get("last_margin") != round(float(margin), 6)
            or type(dispatch) is not bool or (dispatch and not raw)
            or disposition not in expected_dispositions):
        raise ValueError("tick opening edge differs from F15 candidate")

    interval = p3.get("h_next_ms")
    if type(interval) is not int or interval <= 0:
        raise ValueError("tick P3 observation interval is invalid")
    observed = _row(actor_observation.get("detail"), "Actor observation")
    bounds = _row(_row(p3.get("H_projection"), "P3 observation projection").get(
        "bounds_ms"), "P3 observation bounds")
    if (observed.get("version") != "tick-from-opening-authority-v1"
            or type(observed.get("interval_ms")) is not int
            or observed["interval_ms"] != interval
            or type(observed.get("density")) is not float
            or observed["density"] != p3.get("Q_tick")
            or observed.get("effective_constants_version") != p3.get(
                "effective_constants_version")
            or observed.get("authority_receipt_version") != p3.get("version")
            or observed.get("bounds_ms") != bounds):
        raise ValueError("tick Actor observation differs from committed P3 readout")
    baseline_next = at_ms + observed["interval_ms"]
    raw_match = model.eligible == raw
    next_match = model.next_observation_at_ms == baseline_next
    return {
        "tick_id": tick_id,
        "plan_version": boundary["plan_version"],
        "model_raw_eligible": model.eligible,
        "baseline_raw_eligible": raw,
        "raw_eligibility_matches": raw_match,
        "model_next_observation_at_ms": model.next_observation_at_ms,
        "baseline_next_observation_at_ms": baseline_next,
        "baseline_actor_interval_ms": observed["interval_ms"],
        "baseline_actor_density": observed["density"],
        "next_observation_matches": next_match,
        "baseline_edge_dispatch_eligible": dispatch,
        "baseline_edge_disposition": disposition,
        "matched": raw_match and next_match,
        "stage_receipt": model.stage_receipt,
    }
