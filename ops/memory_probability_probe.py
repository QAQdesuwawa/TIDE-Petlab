"""零模型的台账记忆概率探针。

它只复用 :class:`ops.ledger.Ledger` 已经存在的候选、衰减和概率门，不参与
产品运行，也不改任何参数。探针将同一相关性下的不同显著性分别放进独立档案，
避免多个种子在同一次召回中互相抢占而把实验变成提示词容量测试。
"""
from __future__ import annotations

import random
import tempfile

from ops.ledger import Ledger
from ops.storage import UserStore


DAY_MS = 86_400_000
_QUERY_TOKENS = ({"text": "测试主题", "strength": 1.0},)
_QUERY_TEXT = "测试主题"


def validate_profiles(value):
    """规范化探针档位；档位是测试夹具，不是产品的业务规则。"""
    if not isinstance(value, list) or len(value) < 2:
        raise ValueError("profiles 至少需要两个重要度档位")
    output, seen = [], set()
    for index, raw in enumerate(value):
        row = dict(raw or {})
        profile_id = str(row.get("id") or "").strip()
        salience = row.get("salience")
        if not profile_id or profile_id in seen:
            raise ValueError("profiles.id 必须非空且唯一")
        if isinstance(salience, bool) or not isinstance(salience, (int, float)):
            raise ValueError("profiles.salience 必须是数值")
        salience = float(salience)
        if not 0.0 <= salience <= 1.0:
            raise ValueError("profiles.salience 必须位于 [0, 1]")
        seen.add(profile_id)
        output.append({"id": profile_id, "salience": salience})
    return output


def _one_trial(base_dir, profile, *, age_days, rng_seed):
    """一条记忆、一次随机抽样；返回本次的候选概率和是否命中。"""
    now = int(float(age_days) * DAY_MS)
    # 同一随机种子要横向比较不同档位；档位也必须参与 store ID，不能复用上一次
    # 试验留下的台账行。
    store = UserStore("trial_%s_%s" % (profile["id"], rng_seed), base_dir=base_dir)
    ledger = Ledger(store, rng=random.Random(int(rng_seed)))
    ledger.merge(["测试主题的过去陈述"], 0, S=profile["salience"],
                 tokens=list(_QUERY_TOKENS), topic=_QUERY_TEXT,
                 source="synthetic_fixture", seed_id=profile["id"])
    candidates = ledger.candidates(list(_QUERY_TOKENS), _QUERY_TEXT, now)
    if len(candidates) != 1:
        raise AssertionError("概率探针应恰有一条相关候选")
    score, decayed_salience, relevance, _ = candidates[0]
    params = ledger.params
    probability = min(params["recall_p_max"],
                      params["recall_p_base"] + params["recall_p_slope"] * score)
    hit, _ = ledger.maybe_recall(list(_QUERY_TOKENS), _QUERY_TEXT, now)
    return {"hit": hit is not None, "probability": probability,
            "score": score, "decayed_salience": decayed_salience,
            "relevance": relevance}


def run_ledger_probability_probe(profiles, *, age_days=3.0, trials=128,
                                 start_seed=1000):
    """运行确定性的随机种子扫掠，返回理论门值和实测命中频率。

    ``trials`` 的重复不是伪造更多用户，而是固定随机源下对概率门进行机械验算。
    每个档位使用相同种子序列和相同相关性，唯一变量是种入时的显著性。
    """
    profiles = validate_profiles(profiles)
    if isinstance(trials, bool) or int(trials) < 1:
        raise ValueError("trials 必须为正整数")
    trials = int(trials)
    if float(age_days) < 0:
        raise ValueError("age_days 不能为负")
    report = []
    with tempfile.TemporaryDirectory(prefix="tide_memory_probability_") as temp_dir:
        for profile in profiles:
            rows = [_one_trial(temp_dir, profile, age_days=age_days,
                               rng_seed=int(start_seed) + offset)
                    for offset in range(trials)]
            first = rows[0]
            hits = sum(1 for row in rows if row["hit"])
            report.append({
                "profile_id": profile["id"], "salience": profile["salience"],
                "age_days": float(age_days), "trials": trials, "hits": hits,
                "empirical_rate": round(hits / trials, 4),
                "theoretical_probability": round(first["probability"], 4),
                "score": round(first["score"], 4),
                "decayed_salience": round(first["decayed_salience"], 4),
                "relevance": round(first["relevance"], 4),
            })
    return {"probe_version": 1, "strategy": "legacy_ledger_probability_gate",
            "age_days": float(age_days), "trials": trials,
            "seed_range": [int(start_seed), int(start_seed) + trials - 1],
            "controlled_variables": ["same_query_tokens", "same_query_text",
                                     "same_age", "isolated_store_per_trial"],
            "results": report}
