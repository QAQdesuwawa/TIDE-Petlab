# ops/preference.py
"""偏好先验（架构文档 §14）：贝叶斯融合 + α 曲线。纯函数。

实时占比 = α × 先验 + (1−α) × 近端行为拟合
α 随轮数衰减（前 20 轮 0.6 → 100 轮后 0.2）；长时间断联 → 行为拟合过期，
α 小幅回摆（尊重用户声明的先验）。
"""
import math

from model_packages.cx_six_seven_v1.preference_calibration_parameters import PREFERENCE
from interaction_extensions.preference_calibration.cx_six_seven.fitting import PROTOTYPES


def normalize_declared_weights(weights):
    """用户主动提交的五维占比 → 完整、非负、归一的 B₀。

    这是滑块/标签等直接权重输入的本地入口；它不从对话行为猜偏好，也不改变
    ``fuse`` 的行为拟合逻辑。
    """
    if not isinstance(weights, dict) or set(weights) != set(PROTOTYPES):
        raise ValueError("偏好权重必须包含且仅包含五种原型")
    try:
        normalized = {key: float(weights[key]) for key in PROTOTYPES}
    except (TypeError, ValueError):
        raise ValueError("偏好权重必须是数字")
    if any(value < 0 or not math.isfinite(value) for value in normalized.values()):
        raise ValueError("偏好权重必须是有限的非负数")
    total = sum(normalized.values())
    if total <= 0:
        raise ValueError("偏好权重和必须大于零")
    return {key: round(value / total, 6) for key, value in normalized.items()}


def alpha(turns, last_gap_hours=None, params=None):
    """α(turns)：线性衰减曲线 + 断联回摆。"""
    p = params or PREFERENCE
    t = max(0, int(turns))
    ts, te = p["alpha_turn_start"], p["alpha_turn_end"]
    if t <= ts:
        a = p["alpha_start"]
    elif t >= te:
        a = p["alpha_end"]
    else:
        a = p["alpha_start"] + (p["alpha_end"] - p["alpha_start"]) * ((t - ts) / (te - ts))
    if last_gap_hours is not None and last_gap_hours >= p["detach_hours"]:
        a = min(p["alpha_start"], a + p["detach_revert"])
    return round(a, 4)


def fuse(prior, behavior, turns, last_gap_hours=None, params=None, warmup=False):
    """贝叶斯融合：先验与行为拟合按 α 加权后归一化。

    warmup=True（行为拟合冷启动，样本不足）→ α = 1.0，纯先验主导——
    开局体验必须由用户声明主导，不能被样本不足的粗算稀释（实现设计 FITTING.fit_warmup_n）。
    """
    if warmup:
        a = 1.0
    else:
        a = alpha(turns, last_gap_hours, params)
    keys = sorted(set(list((prior or {}).keys()) + list((behavior or {}).keys())))
    merged = {k: a * float((prior or {}).get(k, 0.0))
                 + (1.0 - a) * float((behavior or {}).get(k, 0.0))
              for k in keys}
    total = sum(merged.values())
    if total <= 0:
        n = len(keys) or 1
        return {k: 1.0 / n for k in keys}
    return {k: round(v / total, 4) for k, v in merged.items()}
