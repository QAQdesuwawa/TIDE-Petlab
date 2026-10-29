"""B₀/D(t) 到既有召回 propensity 系数的局部影子映射。

它只观察已经由 turn_recall 产生的候选选择摘要；不创建候选、不修改资格门、排序、
随机抽样或交付。默认 shadow 让后续冻结回放能标定“既有系数如何投影”，而非把
用户偏好变成某个词或事件的硬规则。
"""
from __future__ import annotations

from dataclasses import dataclass

from model_packages.cx_six_seven_v1.preference_calibration_parameters import CADENCE, RECALL_CALIBRATION
from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import (
    PROTOTYPES,
    cos_similarity,
    uniform_guard,
)


ROWS = ("propensity_base", "propensity_salience_weight",
        "propensity_relevance_weight", "propensity_exposure_weight")


def _clamp(value, low, high):
    return max(float(low), min(float(high), float(value)))


def _weights(raw):
    values = {key: max(0.0, float((raw or {}).get(key, 0.0) or 0.0))
              for key in PROTOTYPES}
    total = sum(values.values())
    if total <= 0.0:
        return {key: 1.0 / len(PROTOTYPES) for key in PROTOTYPES}
    return {key: value / total for key, value in values.items()}


def _direction(params, row):
    configured = params.get("directions") or {}
    raw = configured.get(row) if isinstance(configured, dict) else None
    raw = raw if isinstance(raw, dict) else CADENCE.get("direction", {})
    return {key: _clamp(raw.get(key, 0.0), -1.0, 1.0) for key in PROTOTYPES}


@dataclass(frozen=True)
class RecallProjection:
    version: str
    active: bool
    guard: str | None
    candidate_gate: float
    alignment: float
    shadow: dict
    controls: dict

    def as_record(self):
        return {
            "version": self.version, "active": self.active, "guard": self.guard,
            "candidate_gate": self.candidate_gate, "alignment": self.alignment,
            "shadow": dict(self.shadow), "controls": dict(self.controls),
        }


def compile_recall_projection(*, baseline, dynamic, selection, params=None):
    """只在已有候选选择摘要上计算四行 W_recall shadow。"""
    p = params or RECALL_CALIBRATION
    b0, dt = _weights(baseline), _weights(dynamic)
    info = selection if isinstance(selection, dict) else {}
    score = info.get("score_max") if isinstance(info.get("score_max"), dict) else {}
    candidate_count = max(0, int(info.get("candidate_count", 0) or 0))
    # relevance 是选择层已有的候选相关性，而不是另一个语义模型或用户奖励。
    relevance = _clamp(score.get("relevance", 0.0) or 0.0, 0.0, 1.0)
    if not bool(info.get("available", False)):
        gate, alignment, guard = 0.0, 0.0, "no_selection_receipt"
    elif candidate_count <= 0 or relevance <= 0.0:
        gate, alignment, guard = 0.0, 0.0, "no_eligible_candidate"
    elif uniform_guard(b0):
        gate, alignment, guard = relevance, 0.0, "uniform_baseline"
    else:
        gate = relevance
        alignment = _clamp(cos_similarity(b0, dt), 0.0, 1.0)
        guard = "zero_alignment" if alignment <= 0.0 else None
    shadow = {
        row: round(_clamp(sum(b0[key] * _direction(p, row)[key] for key in PROTOTYPES)
                                * alignment * gate, -1.0, 1.0), 6)
        for row in ROWS
    }
    active = bool(p.get("enabled", False)) and guard is None
    controls = dict(shadow) if active else {row: 0.0 for row in ROWS}
    return RecallProjection(str(p.get("version") or RECALL_CALIBRATION["version"]), active,
                            guard, round(gate, 6), round(alignment, 6), shadow, controls)
