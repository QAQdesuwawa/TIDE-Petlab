"""Observable response-intensity measurement for the six/seven adapter.

``R`` is an intensity/change observation, not reward, preference, benefit, or
quality.  These deterministic functions never update calibration parameters.
"""

import math

from model_packages.cx_six_seven_v1.preference_calibration_parameters import REWARD

from .fitting import extract_features


def personal_ranges(msgs, params=None):
    """Return personal full-scale ranges once the history is warm."""
    p = params or REWARD
    msgs = [m for m in (msgs or []) if str(m.get("text", "")).strip()]
    if len(msgs) < p["range_warmup_n"]:
        return None
    msgs = msgs[-p["range_window_n"]:]
    emo, atk, length = [], [], []
    for message in msgs:
        features = message.get("features") or extract_features(
            str(message.get("text", ""))
        )
        emo.append(float(features.get("emotion", 0.0) or 0.0))
        atk.append(float(features.get("attack", 0.0) or 0.0))
        length.append(float(len(str(message.get("text", "")))))
    emo.sort()
    atk.sort()
    length.sort()
    quantile = float(p["range_quantile"])

    def quant(values):
        if not values:
            return 0.0
        index = min(
            len(values) - 1,
            max(0, int(math.ceil(quantile * len(values))) - 1),
        )
        return values[index]

    return {
        "emotion_full": max(float(p["emotion_full"]), quant(emo)),
        "attack_full": max(float(p["attack_full"]), quant(atk)),
        "length_full": max(float(p["length_full"]), quant(length)),
    }


def compute_reward_parts(text, features=None, delay_ms=None, baseline=None,
                         params=None, tier_hint=None, ranges=None):
    """Return the historical scalar R and its four observable components."""
    p = params or REWARD
    personal = ranges or {}
    emo_full = float(personal.get("emotion_full") or p["emotion_full"])
    atk_full = float(personal.get("attack_full") or p["attack_full"])
    len_full = float(personal.get("length_full") or p["length_full"])
    measured = features if features is not None else extract_features(text)
    text_length = max(1.0, float(len(text or "")))
    emo = min(1.0, float(measured.get("emotion", 0.0)) / emo_full)
    atk = min(1.0, float(measured.get("attack", 0.0)) / atk_full)
    length = min(1.0, text_length / len_full)
    intensity = min(1.0, emo + atk + length)
    if tier_hint in ("创伤", "私密"):
        intensity = max(intensity, p["tier_intensity_floor"])

    change = 0.0
    warm = (
        baseline is not None and not baseline.get("cold", True)
    ) or (
        baseline is not None
        and baseline.get("n", 0) >= p["change_warmup_n"]
    )
    if warm and delay_ms is not None:
        median_delay = baseline.get("median_ms")
        if median_delay:
            fast = max(0.0, 1.0 - float(delay_ms) / float(median_delay))
            median_length = baseline.get("median_len")
            length_boost = (
                max(0.0, text_length / max(1.0, float(median_length)) - 1.0)
                if median_length else 0.0
            )
            change = min(1.0, max(fast, length_boost))

    value = p["w_intensity"] * intensity + p["w_change"] * change
    value = round(min(1.0, max(0.0, value)), 4)
    return {
        "R": value,
        "components": {
            "emo": round(emo, 4),
            "atk": round(atk, 4),
            "len": round(length, 4),
            "change": round(change, 4),
        },
    }


def reaction_tokens(parts, params=None):
    """Project response components into the historical local token family."""
    p = params or REWARD
    texts = p["reaction_token_texts"]
    components = (parts or {}).get("components") or {}
    return [
        {
            "text": texts[key],
            "layer": "表",
            "category": "reaction",
            "strength": float(components.get(key, 0.0) or 0.0),
        }
        for key in ("emo", "atk", "len", "change")
    ]


def compute_reward(text, features=None, delay_ms=None, baseline=None,
                   params=None, tier_hint=None, ranges=None):
    """Return the scalar compatibility projection of response intensity."""
    return compute_reward_parts(
        text,
        features=features,
        delay_ms=delay_ms,
        baseline=baseline,
        params=params,
        tier_hint=tier_hint,
        ranges=ranges,
    )["R"]


def label_of(value, params=None):
    """Return the historical high/low triage code, never a quality label."""
    p = params or REWARD
    if value >= p["strong_r"]:
        return 1
    if value <= p["weak_r"]:
        return 0
    return None


__all__ = (
    "REWARD",
    "compute_reward",
    "compute_reward_parts",
    "label_of",
    "personal_ranges",
    "reaction_tokens",
)
