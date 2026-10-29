"""仍在产品路径中的 peer reactive 根向量投影。

本模块不解释回复文本、不读取词元、记忆、问号、场景或消息条数。自主续话的旧 F14
比较器已迁至 ``legacy_followup_oracle``；正式自主开口由 dynamic tick 消费 P3 receipt。
这里仅保留对本轮已送达外部 Delta_X 的 peer reactive 资格，已提交输出的后效已冻结并
写回 ``X``。
"""

from core.params import PEER_REACTIVE
from ops.followup_root_vector import peer_reactive_margin


def reaction_magnitude(parts):
    """把既有 R 卡四维观测压为审计读数，不参与即时调度。"""
    raw = (parts or {}).get("components") if isinstance(parts, dict) else None
    comp = raw if isinstance(raw, dict) else (parts if isinstance(parts, dict) else {})

    def unit(key):
        try:
            return max(0.0, min(1.0, float(comp.get(key, 0.0) or 0.0)))
        except (TypeError, ValueError):
            return 0.0

    values = {key: unit(key) for key in ("emo", "atk", "len", "change")}
    intensity = min(1.0, values["emo"] + values["atk"] + values["len"])
    return {
        "strength": round(max(intensity, values["change"]), 6),
        "intensity": round(intensity, 6),
        "components": values,
    }


def reactive_reply_signal(state_after, constants, variable_deltas=None, params=None):
    """正式 peer reactive 资格：独立的根向量 ``I_R - Theta_R``。

    防御和推进都可以形成对一条已送达来信的本轮接招；没有新的外部 ``e`` 时，旧状态
    不能自行伪造 peer 输入。它不读取 receipt 计数，也不接受 P、关系几何或
    cadence/B0 现场副本。
    """
    p = params if isinstance(params, dict) and "impulse_state_matrix" in params else PEER_REACTIVE
    readout = peer_reactive_margin(state_after or {}, constants or {}, variable_deltas, params=p)
    return {
        "eligible": bool(readout["eligible"]),
        "score": float(readout["margin"]),
        "base": float(readout["impulse"]),
        "base_raw": float(readout["impulse_state"]),
        "attenuation": 1.0,
        # None 明确表示本路径不存在 old fixed-line 资格。
        "line": None,
        "sources": {
            "mode": "reactive_peer",
            "impulse": readout["impulse"], "gate": readout["gate"],
            "margin": readout["margin"], "event_present": readout["event_present"],
            "root_delta": readout["root_delta"], "event_delta": readout["event_delta"],
            "impulse_state_terms": readout["impulse_state_terms"],
            "impulse_event_terms": readout["impulse_event_terms"],
            "gate_state_terms": readout["gate_state_terms"],
            "gate_event_terms": readout["gate_event_terms"],
            "direction_cosine": readout["direction_cosine"],
            "direction_warn": readout["direction_warn"],
            "formula_version": readout["version"],
        },
        "peer_reactive": readout,
    }
