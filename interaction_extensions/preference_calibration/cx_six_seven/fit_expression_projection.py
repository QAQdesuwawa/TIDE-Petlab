"""稳定用户偏好到小说家素材的受限投影。

这不是新的心理状态、词元家族或行为矩阵。它只读取同一轮已经由总线算出的三个
三维向量：NPC 当前驱动力 ``D_NPC``、用户声明的稳定基线 ``E_user`` 与当前互动
方向 ``D_user``。投影的唯一输出是给**既有**可表达 NPC 资源的连续排序倍率：

* 当前互动与 B0 同向，且 NPC 当前驱动力与 B0 有距离时，优先时间变化素材；
* 否则，优先当前 NPC 状态素材。

不输出原型名、用户标签、数值或台词；不写 Store、不进入状态。表达层只得到资源的
临时权重和无正文收据，保持“用户偏好不是对 NPC 的行动指令”。
"""
from dataclasses import dataclass
import math

from model_packages.cx_six_seven_v1.preference_geometry import cosine_similarity


_AXES = ("x", "y", "z")


def _vector(value):
    raw = value if isinstance(value, dict) else {}
    out = {}
    for axis in _AXES:
        try:
            out[axis] = min(1.0, max(0.0, float(raw.get(axis, 0.0))))
        except (TypeError, ValueError):
            out[axis] = 0.0
    return out


def _normalised_distance(left, right):
    """[0,1]^3 上的欧氏距离，除 sqrt(3) 后天然落入 [0,1]。"""
    return min(1.0, math.sqrt(sum((left[axis] - right[axis]) ** 2 for axis in _AXES))
               / math.sqrt(len(_AXES)))


@dataclass(frozen=True)
class FitExpressionProjection:
    """同轮表达投影；不含用户原文、B0 标签或可直接写入 prompt 的语义。"""

    activation: float
    npc_state_weight: float
    npc_time_weight: float

    def weight_for(self, lane):
        if lane == "npc_time_change":
            return self.npc_time_weight
        if lane == "npc_variable_state":
            return self.npc_state_weight
        return 1.0

    def as_record(self):
        return {
            "activation": self.activation,
            "npc_state_weight": self.npc_state_weight,
            "npc_time_weight": self.npc_time_weight,
            "formula": "distance(D_NPC,E_user) * cosine(E_user,D_user)",
        }


def compile_fit_expression_projection(*, npc_drive, user_baseline, user_dynamic):
    """从既有向量推导一次临时排序投影。

    ``gap`` 是 NPC 当前状态相对用户稳定偏好的距离；``agreement`` 是用户当前互动
    与该偏好的同向度。二者相乘，避免仅凭 B0 或仅凭一次情绪就改变小说家素材。
    这个乘积没有自由阈值/可调系数；排序倍率保持 [1,2]，只在已有资源之间竞争。
    """
    npc = _vector(npc_drive)
    baseline = _vector(user_baseline)
    dynamic = _vector(user_dynamic)
    gap = _normalised_distance(npc, baseline)
    agreement = max(0.0, float(cosine_similarity(baseline, dynamic)))
    activation = round(min(1.0, gap * agreement), 4)
    return FitExpressionProjection(
        activation=activation,
        # 张力被当前输入与声明共同确认时，时间中的 NPC 变化更值得写进对白；
        # 否则保持对眼前状态的侧重。二者皆为已有可表达来源，没有新词元。
        npc_state_weight=round(2.0 - activation, 4),
        npc_time_weight=round(1.0 + activation, 4),
    )
