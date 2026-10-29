"""Versioned parameters for the optional six/seven preference adapter."""

VERSION = "cx67-preference-calibration-parameters-v1"

SALIENCE = {
    "w_dv_attr": 0.2, "w_dv_rep": 0.2, "w_pain": 0.2,
    "w_entropy": 0.2, "w_pneed": 0.2,
    "tier_pain_weight": {"日常": 0.0, "私密": 0.3, "创伤": 0.7},
    "tier_lever_delta": {"日常": 0.2, "私密": 0.5, "创伤": 1.0},
}
REWARD = {
    "emotion_full": 0.08,
    "attack_full": 0.08,
    "length_full": 120.0,
    "tier_intensity_floor": 0.8,
    "w_intensity": 0.5,
    "w_change": 0.5,
    "change_warmup_n": 3,
    "strong_r": 0.6,
    "weak_r": 0.3,
    "range_warmup_n": 5,
    "range_window_n": 20,
    "range_quantile": 0.9,
    "reaction_token_texts": {
        "emo": "[反应·情绪浓]", "atk": "[反应·对抗]",
        "len": "[反应·投入]", "change": "[反应·变化]",
    },
    "reaction_polarity": {
        "emo": 1.0, "atk": -1.0, "len": 1.0, "change": 0.5,
    },
}
PERSONA = {"variant_close_v": 0.65, "variant_far_v": 0.35}
RECALL_CALIBRATION = {
    "version": "recall_projection_v1", "enabled": False, "directions": {},
}
CADENCE = {
    "version": "calibration_projection_v3",
    "enabled": False,
    "direction": {
        "Counter_Phobic": -0.40, "Mirroring": 0.35, "Avoidance": -0.55,
        "Trauma_Recon": 0.65, "Pragmatism": -0.30,
    },
    "control_directions": {},
}
FORM = {
    "version": "form_projection_root_v2", "enabled": False, "directions": {},
    "activity_bias": -0.15, "activity_event_gain": 0.70,
    "activity_matrix": (
        (0.06, 0.10, 0.00, 0.00, 0.00, 0.00),
        (0.00, 0.00, 0.00, 0.10, 0.04, 0.00),
        (-0.12, 0.00, 0.00, -0.04, 0.00, 0.00),
        (-0.05, 0.00, 0.00, -0.06, 0.00, 0.00),
        (0.00, 0.00, -0.08, 0.00, 0.00, -0.05),
        (0.06, 0.00, 0.00, 0.12, 0.08, 0.10),
        (0.08, 0.00, 0.00, 0.24, 0.18, 0.16),
    ),
    "level_mid_ratio": 0.4, "level_strong_ratio": 0.7,
}
CONCENTRATION = {
    "exclude_categories": ["compat", "nature"], "min_emit": 0.2,
    "gain": 1.0, "text": "[对方情绪很浓]",
}
FITTING = {
    "window_n": 20, "window_hours": 48.0, "refine_every_n": 5,
    "cold_start_n": 20, "density_scale": 11.0, "burst_boost": 1.5,
    "fit_warmup_n": 5, "user_dominant_min_w": 0.3,
    "heart_token_strength": 1.0, "extractor_correction": 0.5,
}
DRIFT = {
    "conc_floor": 0.2, "conc_qual_mult": 1.5, "conc_hist_n": 20,
    "persist_k": 6, "sim_line": 0.75, "l1_line": 0.25,
    "half_step": 0.3, "cooldown_turns": 20,
}
PREFERENCE = {
    "alpha_start": 0.6, "alpha_end": 0.2, "alpha_turn_start": 20,
    "alpha_turn_end": 100, "detach_hours": 168.0, "detach_revert": 0.1,
}

__all__ = (
    "VERSION", "SALIENCE", "REWARD", "PERSONA", "RECALL_CALIBRATION", "CADENCE",
    "FORM", "CONCENTRATION", "FITTING", "DRIFT", "PREFERENCE",
)
