# temporal/gate.py
"""τ₀ 启动门：τ₀ = A·e^(−B·P_end)（架构文档 §11.4）。

连续函数，无硬编码阈值：
  P_end → 0.9 ⇒ τ₀ ≈ 1 分钟（极紧张：停 5 秒也触发）
  P_end → 0.2 ⇒ τ₀ ≈ 27 分钟（平静结束：停几分钟等于没停）
"""
import math

from core.params import TIME


def tau0_minutes(P_end, params=None):
    """返回 τ₀（分钟）。P_end ∈ [0,1]。"""
    p = params or TIME
    return p["tau0_A"] * math.exp(-p["tau0_B"] * P_end)


def field_active(age_minutes, P_end, params=None):
    """场是否已越过启动门。"""
    return age_minutes >= tau0_minutes(P_end, params)
