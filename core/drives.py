# core/drives.py
"""纯公式层：驱动力结算（自 M.A.N.E. core/drives.py 逐字符迁出）。

删除（T.I.D.E. 实现设计 §3.3）：
- apply_biological_decay（疲劳 → temporal 连续场，挂钟口径，见实现设计 §4.1）
- run_payoff_matrix / apply_emotional_aftershock（MANE UI 流程函数）
保留：calculate_drives（V4 非线性突变版，逐字符不改）/ calculate_baseline。
"""
import math

from core.params import DRIVES
from model_packages.cx_six_seven_v1.role_baseline import calculate_baseline


def relationship_geometry(variables, threat_total_cap=None):
    """从既有六轴状态读出关系几何，不写状态也不推断新事实。

    ``S/T/R_mag`` 是驱动力与后续调度共同可引用的同一份坐标；调用方不得
    重新实现它们的定义。``T`` 的上限仍由 F05 的参数快照决定。
    """
    cap = (DRIVES["threat_total_cap"] if threat_total_cap is None
           else float(threat_total_cap))
    l_self = float(variables["L_self"])
    l_opponent = float(variables["L_opponent"])
    return {
        "S": l_self - l_opponent,
        "T": min(cap, l_self + l_opponent),
        "R_mag": abs(float(variables["V_attr"]) - float(variables["V_rep"])),
    }


def calculate_drives(constants, variables, params=None):
    """
    计算 NPC 的三大驱动力 (D_aggro, D_defend, D_chaos)
    [V4 非线性突变版]：阶跃断电、Sigmoid堰塞湖、极值侧抑制与杠杆维度碾压
    """
    cfg = dict(DRIVES)
    if params is not None:
        cfg.update(dict(params))

    # 提取变量
    E_env = variables["E_env"]
    P_need = variables["P_need"]

    # 提取常数
    a1, a2 = constants["a1"], constants["a2"]
    b1, b2, b3 = constants["b1"], constants["b2"], constants["b3"]
    g1, g2 = constants["g1"], constants["g2"]

    # 1. 基础博弈变量计算（唯一关系几何定义）
    geometry = relationship_geometry(variables, cfg["threat_total_cap"])
    S, T, R_mag = geometry["S"], geometry["T"], geometry["R_mag"]

    # 辅助函数：Sigmoid 突变曲线 (用于模拟阈值决堤)
    def sigmoid(x, k=10, x0=0.5):
        return 1.0 / (1.0 + math.exp(-k * (x - x0)))

    # ==========================================
    # 2. 突变逻辑与乘法器结算
    # ==========================================

    # [阶跃拦截开关]：如果对方筹码对我方形成绝对碾压 (S < -0.4)，侵略欲被物理掐断
    # 使用 sigmoid 模拟阶跃：当 S 低于 -0.4 时，aggro_lock 迅速趋近于 0
    aggro_lock = sigmoid(S, k=float(cfg["aggro_lock_k"]),
                         x0=float(cfg["aggro_lock_x0"]))

    # [情绪堰塞湖]：环境压强堆积到 1.2 时，防线才开始真正感受到几何级压迫
    pressure_burst = (sigmoid(E_env + T, k=float(cfg["pressure_burst_k"]),
                              x0=float(cfg["pressure_burst_x0"]))
                      * float(cfg["pressure_burst_scale"]))

    # ==========================================
    # 3. 核心维度计算
    # ==========================================

    # D_aggro: 只有在未被锁死的情况下，才能发挥攻击性
    D_aggro_raw = (a1 * max(0.0, S) * float(cfg["aggro_leverage_scale"])
                    + a2 * math.pow(P_need, float(cfg["aggro_need_power"]))
                    * (1.0 - R_mag))
    D_aggro = D_aggro_raw * aggro_lock

    # D_defend: 杠杆乘数惩罚。手握底牌(S>0)指数级无视压力，底牌被爆(S<0)防御破防
    courage_shield = math.exp(-b3 * S * float(cfg["courage_shield_scale"]))
    D_defend_raw = b1 * (E_env + pressure_burst) + b2 * P_need
    D_defend = D_defend_raw * courage_shield

    # D_chaos: 欧拉雪崩效应。极压与极高羁绊相遇时触发核爆 (e^x - 1)
    avalanche = math.exp((E_env + T) * R_mag) - 1.0
    D_chaos_raw = g1 * avalanche + g2 * math.pow(P_need * R_mag,
                                                   float(cfg["chaos_need_power"]))
    D_chaos = D_chaos_raw

    # ==========================================
    # 4. 极值侧抑制 (Lateral Inhibition / Winner-Takes-All)
    # ==========================================
    # 消除"又想打人又害怕"的泥泞感。极端状态下，大脑通道会被最高驱力劫持。
    if (D_aggro > float(cfg["lateral_aggro_line"])
            and D_defend > float(cfg["lateral_defend_line"])):
        if D_aggro > D_defend:
            D_defend *= float(cfg["lateral_defend_when_aggro"])
        else:
            D_aggro *= float(cfg["lateral_aggro_when_defend"])

    return {
        "D_aggro": round(max(0.0, D_aggro), 3),
        "D_defend": round(max(0.0, D_defend), 3),
        "D_chaos": round(max(0.0, D_chaos), 3)
    }
