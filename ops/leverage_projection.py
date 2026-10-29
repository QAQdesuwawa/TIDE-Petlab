"""关系杠杆到调度的只读影子投影。

不产生任务、不改参数副本；先把同一份 S/T/R_mag 与 drives 作为可回放 receipt
接到既有 cadence 收据中。非零 W_leverage 必须后续由冻结样本提出。
"""
from core.drives import relationship_geometry
from core.params import LEVERAGE


def leverage_projection(*, variables, drives=None, params=None):
    p = params or LEVERAGE
    geometry = relationship_geometry(variables)
    return {
        "version": str(p["version"]),
        "active": False,
        "geometry": {key: round(float(value), 6) for key, value in geometry.items()},
        "drives": {key: round(float((drives or {}).get(key, 0.0)), 6)
                   for key in ("D_aggro", "D_defend", "D_chaos")},
        "control_coefficients": {"shadow": {}, "active": {}},
        "guard": "zero_direction",
    }
