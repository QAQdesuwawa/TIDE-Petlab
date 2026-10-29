# core/vectors.py
# 自 M.A.N.E. core/vectors.py 逐字符照抄（T.I.D.E. 实现设计 §3.1）。
"""纯几何算子库（零业务语义）：向量运算 + 3D 特有算子。"""
import math


def vector_magnitude(v):
    return math.sqrt(v["x"] ** 2 + v["y"] ** 2 + v["z"] ** 2)


def dot_product(v1, v2):
    return v1["x"] * v2["x"] + v1["y"] * v2["y"] + v1["z"] * v2["z"]


def cosine_similarity(v1, v2):
    mag1 = vector_magnitude(v1)
    mag2 = vector_magnitude(v2)
    if mag1 == 0 or mag2 == 0:
        return 0.0
    return round(dot_product(v1, v2) / (mag1 * mag2), 3)


def component_difference(v1, v2):
    return {
        "delta_aggro": round(v1["x"] - v2["x"], 3),
        "delta_defend": round(v1["y"] - v2["y"], 3),
        "delta_chaos": round(v1["z"] - v2["z"], 3)
    }


def satisfy_score(v1, v2):
    return round(dot_product(v1, v2), 3)


def get_drive_vector(drives):
    return {"x": drives["D_aggro"], "y": drives["D_defend"], "z": drives["D_chaos"]}


def get_baseline_vector(baseline):
    return {"x": baseline["E_aggro"], "y": baseline["E_defend"], "z": baseline["E_chaos"]}


# ---------------------------------------------------------------------
# 3D 特有算子（2D 体系永远算不出的量）
# ---------------------------------------------------------------------
def cross_product(v1, v2):
    """叉积 w = v1 × v2：|w| = 对抗强度（同向为 0，正交最大），方向 = 对抗轴。"""
    return {
        "x": v1["y"] * v2["z"] - v1["z"] * v2["y"],
        "y": v1["z"] * v2["x"] - v1["x"] * v2["z"],
        "z": v1["x"] * v2["y"] - v1["y"] * v2["x"],
    }


def scalar_triple_product(v1, v2, v3):
    """标量三重积 v1·(v2×v3)：旋向（行为相对对方天性的"左右手"）。"""
    return dot_product(v1, cross_product(v2, v3))


def plane_out_projection(v, normal_a, normal_b):
    """平面外投影（离奇度）：v 相对由 normal_a/normal_b 张成平面的垂直分量。

    返回 {"parallel": {...}, "perpendicular": {...}, "oddity": 0~1}
    oddity = |垂直分量| / |v|：行为超出"天性解释平面"的比例——
    0 = 完全可预测，1 = 完全失常。
    """
    mag = vector_magnitude(v)
    if mag == 0:
        return {"parallel": {"x": 0, "y": 0, "z": 0},
                "perpendicular": {"x": 0, "y": 0, "z": 0}, "oddity": 0.0}
    normal = cross_product(normal_a, normal_b)
    n_mag = vector_magnitude(normal)
    if n_mag < 1e-9:  # 两天性向量共线：退化为单轴，全部视为平行
        return {"parallel": dict(v), "perpendicular": {"x": 0, "y": 0, "z": 0}, "oddity": 0.0}
    proj_len = dot_product(v, normal) / n_mag
    perp = {k: proj_len * normal[k] / n_mag for k in ("x", "y", "z")}
    parallel = {k: v[k] - perp[k] for k in ("x", "y", "z")}
    return {"parallel": parallel, "perpendicular": perp,
            "oddity": round(abs(proj_len) / mag, 3)}


def magnitude_ratio(v1, v2):
    """模长比 |v1|/|v2|（v2 为 0 时返回 0）。"""
    m2 = vector_magnitude(v2)
    if m2 == 0:
        return 0.0
    return round(vector_magnitude(v1) / m2, 3)
