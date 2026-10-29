"""已退役 F14 续话公式的冻结比较 oracle。

它只供历史公式对照测试读取，不能被 Actor、Runner、墙钟、UI、工具或计划器导入。
正式自主开口一律由 dynamic tick 消费 P3 ``opening_authority_receipt`` 后进入
``opening_due → dispatch_opening_action``。保留本文件是为了让旧 receipt 可比较，
不是为了保留另一条运行时判定或 writer。
"""
import random

from ops.followup_root_vector import f14_margin
from temporal.delay import decide_delay_seconds
from temporal.opening import opening_margin


def continuation_signal(state_after, constants, variable_deltas=None, params=None):
    """返回旧 F14 根向量读数；仅限 fixture/回归 oracle，绝不具有发送资格。"""
    readout = f14_margin(state_after or {}, constants or {}, variable_deltas, params=params)
    return {
        "eligible": bool(readout["eligible"]),
        "score": float(readout["margin"]),
        "base": float(readout["impulse"]),
        "base_raw": float(readout["impulse_state"]),
        "attenuation": float(readout["attenuation"]),
        "sources": {
            "impulse": readout["impulse"], "gate": readout["gate"],
            "margin": readout["margin"],
            "root_delta": readout["root_delta"], "event_delta": readout["event_delta"],
            "impulse_state_terms": readout["impulse_state_terms"],
            "impulse_event_terms": readout["impulse_event_terms"],
            "gate_state_terms": readout["gate_state_terms"],
            "gate_event_terms": readout["gate_event_terms"],
            "direction_cosine": readout["direction_cosine"],
            "direction_warn": readout["direction_warn"],
            "formula_version": readout["version"],
        },
        "f14": readout,
    }


def continuation_candidate_for_runner(runner, state, now_ms, *, variable_deltas=None,
                                      observation_source="reply_receipt",
                                      effective_constants=None,
                                      effective_constants_version=None):
    """重建旧候选以比较历史 receipt；只读 ``runner``，绝不调度或写入。"""
    del variable_deltas
    if effective_constants is None:
        binding = runner._effective_time_binding()
        effective_constants = binding["effective_constants"]
        effective_constants_version = binding["effective_constants_version"]
    elif not effective_constants_version:
        raise ValueError("统一开口必须携带 C_effective 的版本收据")
    context = runner._scheduler_context(state, now_ms)
    readout = opening_margin(context.state, effective_constants, now_ms)
    signal = {
        "eligible": bool(readout.get("eligible")),
        "qualified_at_ms": (int(now_ms) if readout.get("eligible") else None),
        "score": float(readout.get("margin", -1.0)),
        "base": float(readout.get("impulse", 0.0)),
        "base_raw": float(readout.get("impulse_state", 0.0)),
        "observation_source": str(observation_source),
        "sources": {
            "impulse": readout.get("impulse"), "gate": readout.get("gate"),
            "margin": readout.get("margin"),
            "root_delta": readout.get("root_delta", {}),
            "event_delta": readout.get("event_delta", {}),
            "event_delta_original": readout.get("event_delta_original", {}),
            "formula_version": readout.get("version"),
            "effective_constants_version": str(effective_constants_version),
            "anchor_ts": readout.get("anchor_ts"),
            "elapsed_hours": readout.get("elapsed_hours"),
        },
        "opening": readout,
    }
    if signal["eligible"]:
        delay_rng = random.Random(
            f"{runner.seed or 0}:opening:{int(now_ms)}:f16-root-vector-v1")
        delay_sec, delay_receipt = decide_delay_seconds(
            effective_constants, context.state.get("vars") or {},
            readout.get("event_delta") or {}, runner.baseline, delay_rng, include_receipt=True)
        signal["delay_ms"] = int(delay_sec * 1000)
        signal["at_ms"] = int(now_ms) + int(signal["delay_ms"])
        signal["delay_receipt"] = {"mode": "opening_root_cadence", **delay_receipt}
    return signal
