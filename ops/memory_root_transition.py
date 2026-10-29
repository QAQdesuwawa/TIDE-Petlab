"""Product state snapshot bridge plus CX67 root-transition compatibility exports."""
from __future__ import annotations

from collections.abc import Mapping
import copy

from model_packages.cx_six_seven_v1.memory_root_transition import *  # noqa: F401,F403
from model_packages.cx_six_seven_v1.memory_root_transition import _same_vector

def prepare_actual_recall_root_state(
        *, state: Mapping[str, object],
        transition: ActualRecallRootTransition,
        base_constants: Mapping[str, object],
        effective_constants: Mapping[str, object]):
    """Prepare the one future-facing state snapshot for an actual recall.

    The root delta is already settled by ``transition``.  This function only
    reuses the existing drive and hidden-pressure projections so the persisted
    derived snapshot cannot lag behind the newly committed six roots.  It does
    not advance wall time, rebuild expression resources, or select memories.
    """
    if not isinstance(state, Mapping):
        raise ValueError("state must be a mapping")
    if not isinstance(transition, ActualRecallRootTransition):
        raise ValueError("transition must be ActualRecallRootTransition")
    if not _same_vector(state.get("vars") or {}, transition.x_before):
        raise ValueError("state vars must match transition x_before")
    if not isinstance(base_constants, Mapping):
        raise ValueError("base_constants must be a mapping")
    if not isinstance(effective_constants, Mapping):
        raise ValueError("effective_constants must be a mapping")

    from core.drives import calculate_drives
    from core.vectors import get_drive_vector
    from temporal.bus import update_hidden

    prepared = copy.deepcopy(dict(state))
    prepared["vars"] = dict(transition.x_after)
    drives = calculate_drives(dict(base_constants), prepared["vars"])
    update_hidden(
        prepared, dict(effective_constants), None, [],
        transition.evaluation_time_ms,
    )
    # These are snapshots of the just-committed roots, not a second memory
    # effect.  Keeping them aligned prevents the next event from expressing
    # the same root jump again as if it were newly introduced then.
    prepared["prev_vars"] = dict(transition.x_after)
    prepared["last_drives"] = drives
    prepared["last_end_drives"] = get_drive_vector(drives)
    return prepared
