"""Versioned parameter resource for the official six/seven Full package.

The values are the already frozen reference formulas.  Publishing them here
does not make them framework defaults: a product composition root must still
select this resource explicitly, and experiments may supply another immutable
parameter package without changing Guild or Runner.
"""
from __future__ import annotations

from copy import deepcopy

from .continuous_root_projection import CONTINUOUS_ROOT_PROJECTION_B_V1
from .interaction_geometry import OPENING_AUTHORITY_V5
from .memory_influence_operators import CX_HISTORICAL_AGGREGATION_V1
from .memory_live_operators import CX_FORWARD_MEMORY_READOUT_V1
from .memory_recall_operators import CX_ACTUAL_RECALL_DUAL_READOUT_V1


REFERENCE_FULL_PARAMETER_VERSION = "cx67-reference-full-parameters-v1"


def reference_full_parameter_package() -> dict[str, object]:
    """Return an isolated copy of the official reference parameter resource."""
    return {
        "version": REFERENCE_FULL_PARAMETER_VERSION,
        "initial_state_default": {
            "E_env": 0.5, "L_self": 0.0, "L_opponent": 0.0,
            "P_need": 0.5, "V_attr": 0.0, "V_rep": 0.5,
        },
        "event_response": {
            "version": "cx67-event-response-v1",
            "response_scale": 0.7,
        },
        "progress_flow": {
            "version": "cx67-progress-flow-v1",
            "rest_point": {
                "E_env": 0.35, "P_need": 0.35,
                "V_attr": 0.50, "V_rep": 0.35,
            },
            "axis_rho_per_hour": {
                "E_env": 0.12, "P_need": 0.32,
                "V_attr": 0.035, "V_rep": 0.10,
            },
            "retention_scale": 0.8,
        },
        "shared_projection": deepcopy(CONTINUOUS_ROOT_PROJECTION_B_V1),
        "action_projection": deepcopy(OPENING_AUTHORITY_V5),
        "semantic_resources": {
            "version": "cx67-semantic-resources-v1",
            "emission_unit": 0.75,
        },
        "actual_recall": deepcopy(CX_ACTUAL_RECALL_DUAL_READOUT_V1),
        "historical_aggregation": deepcopy(CX_HISTORICAL_AGGREGATION_V1),
        "memory_forward_readout": deepcopy(CX_FORWARD_MEMORY_READOUT_V1),
    }


__all__ = (
    "REFERENCE_FULL_PARAMETER_VERSION",
    "reference_full_parameter_package",
)
