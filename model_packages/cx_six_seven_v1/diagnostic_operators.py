"""Reference-model diagnostic catalog; metadata only, never live UI math.

Every source path names an already committed Alpha 0.4 product.  The generic
diagnostic exporter and UI therefore need no knowledge of the six roots, the
shared three-axis projection, or the opening formula.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import ModelInvocationContext
from framework.model_api.invocation_v2 import ModelInvocationContextV2

from .core_operators import _require_payload_keys


def _context(value: object) -> ModelInvocationContext | ModelInvocationContextV2:
    if not isinstance(value, (ModelInvocationContext, ModelInvocationContextV2)):
        raise ValueError(
            "context must be ModelInvocationContext or ModelInvocationContextV2")
    return value


_ROOT_METERS = (
    ("E_env", "环境压力"),
    ("L_self", "自我杠杆"),
    ("L_opponent", "关系杠杆"),
    ("P_need", "靠近需求"),
    ("V_attr", "吸引强度"),
    ("V_rep", "关系张力"),
)

_SHARED_PROJECTION_METERS = (
    ("exchange", "索取趋向"),
    ("withhold", "防御趋向"),
    ("tension", "情绪张力"),
)


class CxSixSevenDiagnosticAdapter:
    """Describe committed-readout views without observing or changing state."""

    def diagnostic_descriptor(
        self, *, payload: Mapping[str, Any],
        context: ModelInvocationContext | ModelInvocationContextV2,
    ) -> dict[str, Any]:
        _context(context)
        _require_payload_keys(payload, set(), stage="diagnostic_descriptor")
        def metric(key, label, product_id, path, *, low=0.0, high=1.0,
                   precision=3):
            return {
                "metric_id": key,
                "label": label,
                "source": {"product_id": product_id, "path": path},
                "display_domain": {"low": low, "high": high},
                "precision": precision,
            }

        return {"diagnostic_descriptor": {
            "schema_version": "model-diagnostic-descriptor-v1",
            "version": "cx67-diagnostic-descriptor-v3",
            "authority": "read_only",
            "panels": [
                {
                    "panel_id": "root_state",
                    "label": "连续根状态",
                    "chart": "bar",
                    "series": [
                        metric(key, label, "committed_model_state", [key])
                        for key, label in _ROOT_METERS
                    ],
                },
                {
                    "panel_id": "shared_projection",
                    "label": "三维互动投影",
                    "chart": "bar",
                    "series": [
                        metric(
                            key, label, "committed_shared_projection",
                            ["shared_projection", "vector", key],
                            low=-1.0, high=1.0,
                        )
                        for key, label in _SHARED_PROJECTION_METERS
                    ],
                },
                {
                    "panel_id": "opening",
                    "label": "开口资格读数",
                    "chart": "line",
                    "series": [
                        metric("impulse", "主动冲动",
                               "committed_action_assessment",
                               ["action_assessment", "constraints", "opening",
                                "impulse"],
                               precision=6),
                        metric("threshold", "动态门限",
                               "committed_action_assessment",
                               ["action_assessment", "constraints", "opening",
                                "threshold"],
                               precision=6),
                        metric("margin", "开口余量",
                               "committed_action_assessment",
                               ["action_assessment", "constraints", "opening",
                                "margin"],
                               low=-1.0, high=1.0, precision=6),
                    ],
                },
            ],
        }}
