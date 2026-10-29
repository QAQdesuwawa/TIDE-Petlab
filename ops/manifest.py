# ops/manifest.py
"""运行清单（承 MANE ops/manifest.py / OPS data/manifest.py）：会话溯源快照。

来源凭证 = 角色卡 sha256 + 标定区参数快照 sha256 + 模型 + 种子 + 创建时间。
用途：P7 金标数据的版本戳（MANE 铁律：labels 强制带戳，旧数据不冒充新口径）。
冻结区（算法）在代码里，版本 = 代码修订；标定区在 params.py，版本 = params_sha。
"""
import hashlib
import json
import os
import time

from core.params import (TIME, SALIENCE, FITTING, PREFERENCE, MEMORY,
                         TOKEN_PRIORITY, DICT_DELTAS, AMMO_HIT_DELTAS, DELAY,
                         REWARD, LEDGER, FOLLOWUP, LENGTH_MODE,
                         RECALL_GATE, PERSONA, ANTI_LOOP, DEFAULTS,
                         CONCENTRATION, BREAKPOINTS, RESPONSE_PACK, DRIFT,
                         USER_LINE, CHECKS, RECALL_GEOMETRY, DENY, CADENCE, FORM,
                         RECALL_CALIBRATION, DRIVES, PROACTIVE, LEVERAGE)


def params_snapshot():
    """标定区参数快照（调参改 params.py 后 sha 必变 → 旧数据自动失效）。

    B3 扩围：覆盖全部"语义/测量相关"旋钮——不只显著性与记忆，还有 R 定义
    （REWARD）、调动杠杆（DELAY/FOLLOWUP/LENGTH_MODE/RECALL_GATE）、
    装配（PERSONA/ANTI_LOOP）与温度（DEFAULTS）。纯调度/表现层（ACTOR/UI）除外：
    它们不改样本语义。
    """
    return {"TIME": TIME, "SALIENCE": SALIENCE, "FITTING": FITTING,
            "DRIVES": DRIVES, "PROACTIVE": PROACTIVE, "LEVERAGE": LEVERAGE,
            "PREFERENCE": PREFERENCE, "MEMORY": MEMORY,
            "TOKEN_PRIORITY": TOKEN_PRIORITY,
            "DICT_DELTAS": DICT_DELTAS, "AMMO_HIT_DELTAS": AMMO_HIT_DELTAS,
            "DELAY": DELAY, "REWARD": REWARD, "LEDGER": LEDGER,
            "FOLLOWUP": FOLLOWUP, "LENGTH_MODE": LENGTH_MODE,
            "CADENCE": CADENCE, "FORM": FORM, "RECALL_CALIBRATION": RECALL_CALIBRATION,
            "RECALL_GATE": RECALL_GATE, "PERSONA": PERSONA,
            "ANTI_LOOP": ANTI_LOOP, "DEFAULTS": DEFAULTS,
            "CONCENTRATION": CONCENTRATION, "BREAKPOINTS": BREAKPOINTS,
            "RESPONSE_PACK": RESPONSE_PACK, "DRIFT": DRIFT,
            "USER_LINE": USER_LINE, "CHECKS": CHECKS,
            "RECALL_GEOMETRY": RECALL_GEOMETRY, "DENY": DENY}


def sha256_text(obj):
    """可 JSON 化对象 → 16 位 sha256 摘要（排序键保证稳定）。"""
    data = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]


def build_manifest(card, seed=None):
    return {
        "card_sha": sha256_text(card),
        "params_sha": sha256_text(params_snapshot()),
        "model": os.getenv("MODEL_NAME", "unknown"),
        "seed": seed,
        "created_at": int(time.time() * 1000),
    }
