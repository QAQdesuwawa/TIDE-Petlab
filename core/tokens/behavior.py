# core/tokens/behavior.py
"""行为空间词元（T.I.D.E. 单 NPC 版）：六向量几何 + 用户伪向量 + 时间桥。

自 M.A.N.E. core/tokens/behavior.py 改造（实现设计 §3.2，算子公式零改动）：
- 角色 B 的三个操作数（D_B / E_B / D_B^prev）由用户伪向量顶替（架构文档 §9）：
    E_user = Σ wᵢ·Bᵢ          （5 模板基线凸组合，|E_user| = 拟合纯度）
    D_user = Σ (wᵢ + κ·ẇᵢ)·Bᵢ 钳制 [0,1]（位置 + 速度）
- 新增时间桥词元族：D_now vs D_end（沉默漂移后 vs 上次结束时刻，架构文档 §10.2）；
- L3 累积状态计时单位改挂钟（dominant_since 时间戳，架构文档 §8.2）。
"""
import math

from core.drives import calculate_drives, calculate_baseline
from core.params import TIME, FITTING
from core.vectors import (cosine_similarity, component_difference, satisfy_score,
                          get_drive_vector, get_baseline_vector,
                          cross_product, scalar_triple_product, plane_out_projection,
                          magnitude_ratio)

# 冰山分拣关键词（原版硬匹配，行为空间词元沿用）
_SURFACE_KEYS = ("权力", "死要面子", "核弹把柄", "逆恐", "安全", "无所求", "占优", "同向")
_HIDDEN_KEYS = ("高敏", "羁绊", "自毁", "绝境", "死穴", "饥渴", "渴望", "防备", "缺乏", "边缘")

# ---------------------------------------------------------------------
# 5 模板基线向量（冻结区，架构文档 §9.1：由 BASE_MATRIX 经 calculate_baseline 算出）
# ---------------------------------------------------------------------
TEMPLATE_BASELINES = {
    "Counter_Phobic": {"x": 0.882, "y": 0.441, "z": 0.165},
    "Mirroring":      {"x": 0.364, "y": 0.909, "z": 0.202},
    "Avoidance":      {"x": 0.081, "y": 0.992, "z": 0.101},
    "Trauma_Recon":   {"x": 0.406, "y": 0.316, "z": 0.857},
    "Pragmatism":     {"x": 0.446, "y": 0.892, "z": 0.074},
}
TEMPLATE_ORDER = list(TEMPLATE_BASELINES.keys())


def classify_layer(text):
    """冰山出生层：表/里 关键词判定（与原版一致）。"""
    if any(k in text for k in _SURFACE_KEYS):
        return "表"
    if any(k in text for k in _HIDDEN_KEYS):
        return "里"
    return "表"


def _clamp01(x):
    return min(1.0, max(0.0, x))


def compute_user_vectors(weights, weights_prev=None, dt_hours=0.0, kappa=None,
                         e_weights=None, response_seconds=None):
    """E_user / D_user：用户拟合权重 → 3D 伪向量（分量钳制 [0,1]）。

    E_user = Σ ewᵢ·Bᵢ；D_user = Σ (wᵢ + κ·Δwᵢ·r(Δt))·Bᵢ 钳制 [0,1]，其中
    r(Δt)=1-exp(-Δt/τ)。dt_hours ≤ 0、无 prev 或 τ ≤ 0 时 r 分别为 0、0、1。
    返回 (E_user, D_user, purity)。

    这里的 r 是连续系统的观测响应，不是对用户输入速度的字面估计：当前 w 已经
    表达了这条输入的即时方向，Δw 只是一项有界的“正在变化”修正。旧版 Δw/Δt
    会在 200ms 级消息间隔中趋向无穷，再被 [0,1] 钳制成伪造的几何极值。

    期②-4 接线切换（偏好拟合总线）：e_weights = 稳定寄存器 B₀（用户要什么），
    weights = 瞬时寄存器 D(t)（当下什么状态）——E_user/purity 走 B₀，
    D_user/速度走 D(t)；e_weights=None 时两者同源（旧行为，逐位等价）。
    purity = Σ ewᵢ²（拟合集中度，Herfindahl）：1 = 铁了心像某原型，0.2 = 五原型均匀。
    注意：|E_user| 不作置信度——5 基线聚集于正卦限，均匀权重下 |E_user| ≈ 0.88
    （P0 等价锁实证，架构文档 §9.2 已修正），集中度才是有效信号。
    """
    k = TIME["user_velocity_kappa"] if kappa is None else kappa
    tau_s = (TIME["user_vector_response_seconds"] if response_seconds is None
             else response_seconds)
    tau_s = max(0.0, float(tau_s))
    if weights_prev is None or dt_hours <= 0:
        response = 0.0
    elif tau_s <= 0:
        response = 1.0
    else:
        response = -math.expm1(-(float(dt_hours) * 3600.0) / tau_s)
    ew = e_weights if e_weights is not None else weights
    e = {"x": 0.0, "y": 0.0, "z": 0.0}
    d = {"x": 0.0, "y": 0.0, "z": 0.0}
    for name in TEMPLATE_ORDER:
        w = float(weights.get(name, 0.0))
        ew_i = float(ew.get(name, 0.0))
        b = TEMPLATE_BASELINES[name]
        delta_w = 0.0
        if weights_prev is not None:
            delta_w = w - float(weights_prev.get(name, 0.0))
        eff = w + k * delta_w * response
        for axis in ("x", "y", "z"):
            e[axis] += ew_i * b[axis]
            d[axis] += eff * b[axis]
    d = {axis: _clamp01(v) for axis, v in d.items()}
    purity = sum(v * v for v in ew.values())
    return e, d, round(purity, 3)


# =====================================================================
# L0：核心原子词元阵列 (正交特征提取) —— 逐字符继承
# =====================================================================
def generate_atomic_tags(constants, variables):
    """
    通过 O(N) 遍历 13 个参数，激活离散原子特征标签。
    """
    tags = []

    # 1. 常量级扫描 (底层天性)
    a1, a2 = constants["a1"], constants["a2"]
    b1, b2, b3 = constants["b1"], constants["b2"], constants["b3"]
    g1, g2 = constants["g1"], constants["g2"]

    if a1 >= 0.7:
        tags.append("[极度嗜好绝对权力与施压碾压]")
    elif a1 <= 0.3:
        tags.append("[缺乏主动攻击性/不愿施压]")

    if a2 >= 0.7:
        tags.append("[极易通过折磨他人来代偿自身空虚]")
    elif a2 <= 0.3:
        tags.append("[内部情绪不外化为攻击]")

    if b1 >= 0.7:
        tags.append("[高敏体质/极易受环境惊吓而退行]")
    elif b1 <= 0.3:
        tags.append("[环境钝感/对危机缺乏本能恐惧]")

    if b2 >= 0.7:
        tags.append("[极度虚荣/死要面子/极度需要外界关注]")
    elif b2 <= 0.3:
        tags.append("[不在乎外界眼光/抛弃自尊]")

    if b3 >= 0.7:
        tags.append("[处于下位时极易陷入服从与物理僵直]")
    elif b3 <= 0.3:
        tags.append("[面对威慑宁折不弯/不轻易屈服]")

    if g1 >= 0.7:
        tags.append("[极易被深厚羁绊与高压双重引爆理智]")
    elif g1 <= 0.3:
        tags.append("[情绪抗压极强/不轻易发癫]")

    if g2 >= 0.7:
        tags.append("[创伤强迫重复者/渴望通过同归于尽确认羁绊]")
    elif g2 <= 0.3:
        tags.append("[不依赖极端自毁来确认关系]")

    # 2. 变量级扫描 (当前状态)
    E_env = variables["E_env"]
    L_self = variables["L_self"]
    L_opponent = variables["L_opponent"]
    P_need = variables["P_need"]
    V_attr = variables["V_attr"]
    V_rep = variables["V_rep"]

    if E_env >= 0.7:
        tags.append("[当前身处生死边缘或极度高压的绝境]")
    elif E_env <= 0.3:
        tags.append("[当前身处极其安全放松的物理环境]")

    if L_self >= 0.7:
        tags.append("[手里捏着足以毁灭对方的绝对死穴/核弹把柄]")
    elif L_self <= 0.3:
        tags.append("[手里没有任何可以要挟对方的筹码]")

    if L_opponent >= 0.7:
        tags.append("[自己的死穴底牌已被对方公开捏死]")
    elif L_opponent <= 0.3:
        tags.append("[自己的底牌未暴露/未受明牌威慑]")

    if P_need >= 0.7:
        tags.append("[内心处于极度饥渴/索求不满/自尊碎裂的边缘]")
    elif P_need <= 0.3:
        tags.append("[情绪处于低欲求的稳定状态/无所求]")

    if V_attr >= 0.7:
        tags.append("[对对方抱有极度的渴望/依赖/不舍/心软]")
    elif V_attr <= 0.3:
        tags.append("[对对方没有任何留恋或正向情感]")

    if V_rep >= 0.7:
        tags.append("[对对方抱有极度的防备/厌恶/恐惧/屈辱]")
    elif V_rep <= 0.3:
        tags.append("[对对方没有防备和排斥]")

    return tags


# =====================================================================
# L1：普适性词元 (纯物理客观总结) —— 逐字符继承
# =====================================================================
def generate_universal_tokens(similarity, satisfy, delta):
    """生成普适性词元 (纯物理客观总结)"""
    tokens = []

    if similarity >= 0.8:
        tokens.append("[行为高度同向]")
    elif similarity >= 0.5:
        tokens.append("[行为中等同向]")
    elif similarity >= 0.2:
        tokens.append("[行为弱同向]")
    elif similarity >= -0.2:
        tokens.append("[行为完全无关]")
    else:
        tokens.append("[行为逆向对抗]")

    if satisfy >= 0.6:
        tokens.append("[高度顺应满足对方天性]")
    elif satisfy >= 0.3:
        tokens.append("[部分满足对方天性]")
    elif satisfy >= 0.1:
        tokens.append("[微弱满足对方天性]")
    else:
        tokens.append("[违逆/未满足对方天性]")

    if delta["delta_aggro"] > 0.3: tokens.append("[侵略维度物理占优]")
    if delta["delta_defend"] > 0.3: tokens.append("[防御逃避维度物理占优]")
    if delta["delta_chaos"] > 0.3: tokens.append("[崩溃维度物理占优]")

    return tokens


# =====================================================================
# L2：时间型对比 —— 逐字符继承
# =====================================================================
def analyze_self_change(D_current, D_prev):
    """自变分析：NPC 自身的行为变化"""
    if D_prev is None:
        return {"inertia": None, "delta": None, "tokens": ["[首轮无历史]"]}

    inertia = cosine_similarity(D_current, D_prev)
    delta = component_difference(D_current, D_prev)

    tokens = []
    if inertia > 0.9:
        tokens.append("[惯性延续]")
    elif inertia > 0.5:
        tokens.append("[渐进演变]")
    else:
        tokens.append("[行为突变]")

    if delta["delta_aggro"] > 0.3:
        tokens.append("[侵略性攀升]")
    elif delta["delta_aggro"] < -0.3:
        tokens.append("[侵略性衰退]")

    if delta["delta_defend"] > 0.3:
        tokens.append("[防御性攀升]")
    elif delta["delta_defend"] < -0.3:
        tokens.append("[防御性瓦解]")

    if delta["delta_chaos"] > 0.3:
        tokens.append("[崩溃加剧]")
    elif delta["delta_chaos"] < -0.3:
        tokens.append("[崩溃缓解]")

    return {"inertia": inertia, "delta": delta, "tokens": tokens}


def analyze_chase(D_chaser, D_target_prev):
    """追赶分析：NPC 是否在针对用户上一轮的姿态做调整（操作数替换：D_user^prev）"""
    if D_target_prev is None:
        return {"chase_inertia": None, "chase_delta": None, "drop_ratio": None, "tokens": ["[首轮无历史]"]}

    chase_inertia = cosine_similarity(D_chaser, D_target_prev)
    chase_delta = component_difference(D_chaser, D_target_prev)

    mag_chaser = math.sqrt(D_chaser["x"] ** 2 + D_chaser["y"] ** 2 + D_chaser["z"] ** 2)
    mag_target_prev = math.sqrt(D_target_prev["x"] ** 2 + D_target_prev["y"] ** 2 + D_target_prev["z"] ** 2)
    drop_ratio = mag_chaser / max(0.001, mag_target_prev)

    tokens = []
    if drop_ratio > 1.5 and chase_delta["delta_aggro"] > 0:
        tokens.append("[加码施压]")
    elif drop_ratio > 1.5 and chase_delta["delta_chaos"] > 0:
        tokens.append("[应激性爆发]")
    elif drop_ratio < 0.5 and chase_delta["delta_defend"] > 0:
        tokens.append("[同步退缩]")
    elif 0.8 < drop_ratio < 1.2 and chase_inertia > 0.8:
        tokens.append("[对等僵持]")

    return {
        "chase_inertia": chase_inertia,
        "chase_delta": chase_delta,
        "drop_ratio": round(drop_ratio, 3),
        "tokens": tokens
    }


# =====================================================================
# 时间桥词元族（T.I.D.E. 新增，架构文档 §10.2）
# =====================================================================
def analyze_time_bridge(D_now, D_end):
    """沉默漂移结算后的 NPC 姿态 vs 上次消息结束时刻的姿态。

    无结束快照（首轮）→ 空。所有词元 category=trend、layer=势。
    """
    if D_end is None:
        return {"similarity": None, "delta": None, "tokens": []}

    sim = cosine_similarity(D_now, D_end)
    delta = component_difference(D_now, D_end)
    tokens = []

    if sim > 0.85:
        tokens.append({"text": "[沉默后如常]", "layer": "势", "category": "trend",
                       "strength": round(sim, 3)})
    elif sim > 0.5:
        tokens.append({"text": "[沉默后微变]", "layer": "势", "category": "trend",
                       "strength": round(sim, 3)})
    else:
        tokens.append({"text": "[沉默后巨变]", "layer": "势", "category": "trend",
                       "strength": round(min(1.0, 1.0 - sim), 3)})

    if delta["delta_aggro"] > 0.3:
        tokens.append({"text": "[沉默后·躁]", "layer": "势", "category": "trend",
                       "strength": round(min(1.0, delta["delta_aggro"]), 3)})
    if delta["delta_defend"] > 0.3:
        tokens.append({"text": "[沉默后·防备]", "layer": "势", "category": "trend",
                       "strength": round(min(1.0, delta["delta_defend"]), 3)})
    if delta["delta_chaos"] > 0.3:
        tokens.append({"text": "[沉默后·失控倾向]", "layer": "势", "category": "trend",
                       "strength": round(min(1.0, delta["delta_chaos"]), 3)})

    return {"similarity": sim, "delta": delta, "tokens": tokens}


# =====================================================================
# L3：累积效应（挂钟口径，实现设计 §3.2）
# =====================================================================
def analyze_accumulation(accum_state, role, self_change, drives, now_ms=None, params=None):
    """累积效应分析：持续性只由挂钟时间确认，方向变化由真实事件增量累积。"""
    p = params or TIME
    role_state = accum_state.get(role, {
        "inertia_streak": 0, "inertia_total": 0.0,
        "last_dominant_drive": None, "dominant_since": None,
        "aggression_trend": 0.0, "defend_trend": 0.0, "chaos_trend": 0.0
    }).copy()
    tokens = []

    # 1. 历史兼容字段归零。旧版曾把“相邻消息的状态相似”按消息数累计，并在
    # 第 3/5 条消息制造 [压抑极值]/[濒临爆发]；它既不是持续时间，也不是压力，
    # 因而不能进入生成层。真实持续效应由下面 dominant_since 的挂钟路径产生。
    role_state["inertia_streak"] = 0
    role_state["inertia_total"] = 0.0

    # 2. 主导驱动力追踪（挂钟）：dominant_since 为时间戳（毫秒）
    dom = max(drives, key=drives.get)
    if dom == role_state.get("last_dominant_drive"):
        if role_state.get("dominant_since") is None:
            role_state["dominant_since"] = now_ms
    else:
        role_state["last_dominant_drive"] = dom
        role_state["dominant_since"] = now_ms

    if now_ms is not None and role_state.get("dominant_since") is not None:
        dur_s = (now_ms - role_state["dominant_since"]) / 1000.0
        if dur_s >= p["dominant_extreme_seconds"]:
            extreme = True
        elif dur_s >= p["dominant_warn_seconds"]:
            extreme = False
        else:
            extreme = None
        if extreme is not None:
            label = {"D_aggro": "持续施压", "D_defend": "持续逃避", "D_chaos": "持续失控"}[dom]
            tokens.append(f"[{label}·濒临爆发]" if extreme else f"[{label}]")

    # 3. 方向性累积追踪（逐字符继承）
    if self_change.get("delta") is not None:
        role_state["aggression_trend"] += self_change["delta"]["delta_aggro"]
        role_state["defend_trend"] += self_change["delta"]["delta_defend"]
        role_state["chaos_trend"] += self_change["delta"]["delta_chaos"]

    if abs(role_state["aggression_trend"]) > 0.8:
        direction = "爆发" if role_state["aggression_trend"] > 0 else "退缩"
        tokens.append(f"[侵略性累积{direction}]")
        role_state["aggression_trend"] = 0.0

    if abs(role_state["defend_trend"]) > 1.0:
        direction = "筑墙" if role_state["defend_trend"] > 0 else "瓦解"
        tokens.append(f"[防御性累积{direction}]")
        role_state["defend_trend"] = 0.0

    if abs(role_state["chaos_trend"]) > 0.6:
        direction = "恶化" if role_state["chaos_trend"] > 0 else "平复"
        tokens.append(f"[崩溃性累积{direction}]")
        role_state["chaos_trend"] = 0.0

    return {"tokens": tokens, "updated_role_state": role_state}


# =====================================================================
# 冰山分拣（L0~L3 行为词元撕裂为表/里/势）—— 逐字符继承
# =====================================================================
def build_iceberg_profile(atomic_tags, trend_tokens):
    """
    🧊 三维冰山分拣器：将一锅炖的词元，在代码层强制撕裂为"表、里、势"三层动能架构
    """
    surface_tokens = []
    hidden_tokens = []
    momentum_tokens = []

    # 1. 显性防线 (表) & 隐性暗流 (里) - 处理 L0 与 L1 的静态空间词元
    for tag in atomic_tags:
        if classify_layer(tag) == "表":
            surface_tokens.append(tag)
        else:
            hidden_tokens.append(tag)

    # 2. 动能裂变 (势) - 将所有 L2 (趋势) 与 L3 (累积) 词元打包，构成时间轴上的加速度
    for tag in trend_tokens:
        momentum_tokens.append(tag)

    return {
        "Surface_Mask": surface_tokens,
        "Hidden_Truth": hidden_tokens,
        "Momentum_Development": momentum_tokens
    }


# =====================================================================
# 3D 特有词元（操作数替换：D_user/E_user 顶替角色 B）
# =====================================================================
def generate_3d_geometry_tokens(D_NPC, D_user, E_NPC, E_user):
    """行为空间的 3D 特有词元：对抗轴 / 旋向 / 离奇度 / 风格指纹。"""
    tokens = []

    # 1. 对抗轴（叉积）
    w = cross_product(D_NPC, D_user)
    w_mag = (w["x"] ** 2 + w["y"] ** 2 + w["z"] ** 2) ** 0.5
    if w_mag > 0.05:
        axis = max(("x", "y", "z"), key=lambda k: abs(w[k]))
        axis_zh = {"x": "侵略轴", "y": "防御轴", "z": "崩溃轴"}[axis]
        tokens.append({"text": f"[{axis_zh}对抗]", "layer": "表",
                       "category": "tactics", "strength": round(min(1.0, w_mag), 3)})

    # 2. 旋向（标量三重积：NPC 的行为相对用户天性的"左右手"）
    h = scalar_triple_product(D_NPC, D_user, E_user)
    if h > 0.05:
        tokens.append({"text": "[顺着对方天性施压]", "layer": "里",
                       "category": "tactics", "strength": round(min(1.0, abs(h)), 3)})
    elif h < -0.05:
        tokens.append({"text": "[逆着对方天性硬切]", "layer": "里",
                       "category": "tactics", "strength": round(min(1.0, abs(h)), 3)})

    # 3. 离奇度（平面外投影：行为超出天性解释域的比例）
    odd_npc = plane_out_projection(D_NPC, E_NPC, E_user)["oddity"]
    odd_user = plane_out_projection(D_user, E_user, E_NPC)["oddity"]
    if odd_npc > 0.4:
        tokens.append({"text": "[NPC行为超出天性解释域]", "layer": "势",
                       "category": "tactics", "strength": odd_npc})
    if odd_user > 0.4:
        tokens.append({"text": "[用户行为超出拟合解释域]", "layer": "势",
                       "category": "tactics", "strength": odd_user})

    # 4. 风格指纹（模长比）
    ratio_npc = magnitude_ratio(D_NPC, E_NPC)
    if ratio_npc > 1.3:
        tokens.append({"text": "[NPC超水平发挥]", "layer": "势",
                       "category": "tactics", "strength": round(min(1.0, (ratio_npc - 1.3) / 1.0), 3)})
    elif 0 < ratio_npc < 0.7:
        tokens.append({"text": "[NPC压抑得低于天性]", "layer": "里",
                       "category": "tactics", "strength": round(1.0 - ratio_npc, 3)})
    ratio_user = magnitude_ratio(D_user, E_user)
    if ratio_user > 1.3:
        tokens.append({"text": "[用户超水平发挥]", "layer": "势",
                       "category": "tactics", "strength": round(min(1.0, (ratio_user - 1.3) / 1.0), 3)})
    elif 0 < ratio_user < 0.7:
        tokens.append({"text": "[用户压抑得低于天性]", "layer": "里",
                       "category": "tactics", "strength": round(1.0 - ratio_user, 3)})

    return tokens


# =====================================================================
# 全量行为词元编排（单 NPC + 用户伪向量，实现设计 §3.2）
# =====================================================================
def _tok(text, layer, category, strength=1.0):
    return {"text": text, "layer": layer, "category": category, "strength": strength}


def _source(lane, owner, expression_eligible):
    """词元来源的瞬时旁路收据。

    原始 token 契约仍只有 text/layer/category/strength；这里不把来源写回 token，
    只让时间总线在本轮保留“这是谁的比较结果、能否进入对白”的必要语义。
    """
    return {"lane": lane, "owner": owner,
            "expression_eligible": bool(expression_eligible)}


def analyze_behavior_space(constants, variables, weights,
                           weights_prev=None, dt_hours=0.0,
                           D_prev=None, D_user_prev=None, D_end=None,
                           accum_state=None, now_ms=None, params=None,
                           e_weights=None):
    """
    单 NPC 全量行为词元编排。

    输入：
      constants   NPC 7 常量
      variables   NPC 6 变量
      weights     用户拟合权重（5 模板）
      weights_prev/dt_hours   用户拟合上窗口值（速度项）
      D_prev / D_user_prev    上轮驱动力 / 用户上轮伪向量（dict x/y/z）
      D_end       上次消息结束时刻的 NPC 驱动力快照（时间桥）
      accum_state / now_ms    L3 累积状态与当前时间戳（毫秒）
    输出：raw_values + analysis（含 flat_tokens 供 rank_tokens 消费）。
    """
    # 1. 基础数据计算
    drives = calculate_drives(constants, variables)
    baseline = calculate_baseline(constants)

    D_NPC = get_drive_vector(drives)
    E_NPC = get_baseline_vector(baseline)
    E_user, D_user, purity = compute_user_vectors(weights, weights_prev, dt_hours,
                                                  e_weights=e_weights)

    D_prev_vec = get_drive_vector(D_prev) if D_prev else None

    results = {
        "atomic_tags": generate_atomic_tags(constants, variables),
        "meta_cognition": {},
        "gameplay": {},
        "compatibility_npc_to_user": {},
        "compatibility_user_to_npc": {},
        "self_change": {},
        "chase": {},
        "time_bridge": {},
        "accum_tokens": [],
        "tactics_3d": [],
        "iceberg": {},
        "updated_accum_state": None,
        "flat_tokens": [],
        "flat_token_sources": [],
    }

    # 2. 空间型对比分析（L1）
    meta_sim = cosine_similarity(D_NPC, E_NPC)
    meta_delta = component_difference(D_NPC, E_NPC)
    results["meta_cognition"] = {
        "universal_tokens": generate_universal_tokens(meta_sim, 0, meta_delta)
    }

    game_sim = cosine_similarity(D_NPC, D_user)
    game_delta = component_difference(D_NPC, D_user)
    results["gameplay"] = {
        "universal_tokens": generate_universal_tokens(game_sim, 0, game_delta)
    }

    # 相性 NPC→用户（贴合用户喜好的内环测量，架构文档 §16）
    comp_sim = cosine_similarity(D_NPC, E_user)
    comp_satisfy = satisfy_score(D_NPC, E_user)
    comp_delta = component_difference(D_NPC, E_user)
    results["compatibility_npc_to_user"] = {
        "universal_tokens": generate_universal_tokens(comp_sim, comp_satisfy, comp_delta),
        "satisfy": comp_satisfy,
    }

    # 相性 用户→NPC（用户行为是否顺应 NPC 天性）
    compu_sim = cosine_similarity(D_user, E_NPC)
    compu_satisfy = satisfy_score(D_user, E_NPC)
    compu_delta = component_difference(D_user, E_NPC)
    results["compatibility_user_to_npc"] = {
        "universal_tokens": generate_universal_tokens(compu_sim, compu_satisfy, compu_delta),
        "satisfy": compu_satisfy,
    }

    # 3. 时间型对比分析（L2）
    self_change = analyze_self_change(D_NPC, D_prev_vec)
    results["self_change"] = self_change

    chase = analyze_chase(D_NPC, D_user_prev)
    results["chase"] = chase

    bridge = analyze_time_bridge(D_NPC, D_end)
    results["time_bridge"] = bridge

    # 4. L3 累积效应（挂钟）
    if accum_state is not None:
        accum_res = analyze_accumulation(accum_state, "npc", self_change, drives,
                                         now_ms=now_ms, params=params)
        results["accum_tokens"] = accum_res["tokens"]
        results["updated_accum_state"] = accum_res["updated_role_state"]

    # 5. 3D 战术词元
    tactics = generate_3d_geometry_tokens(D_NPC, D_user, E_NPC, E_user)
    results["tactics_3d"] = tactics

    # 6. 冰山分拣（静态 = 原子 + 元认知 + 博弈 + 相性；势 = 自变 + 追赶 + 时间桥 + 累积）
    static_texts = (list(results["atomic_tags"])
                    + list(results["meta_cognition"]["universal_tokens"])
                    + list(results["gameplay"]["universal_tokens"])
                    + list(results["compatibility_npc_to_user"]["universal_tokens"])
                    + list(results["compatibility_user_to_npc"]["universal_tokens"]))
    trend_texts = (list(self_change["tokens"]) + list(chase["tokens"])
                   + [t["text"] for t in bridge["tokens"]]
                   + list(results["accum_tokens"]))
    results["iceberg"] = build_iceberg_profile(static_texts, trend_texts)

    # 6.5 用户心向词元（偏好通道第一公民）：主导图式直接进候选池——
    # 数值不进生成层，只给中文方向名。它是方向事实（非幅度信号）→ 强度恒 1.0，
    # 并在相性块最前插入：同分时靠类别 tie-break + 插入序取胜（回放一致）。
    # 置信门槛：主导权重 ≤ user_dominant_min_w 不宣称方向（均匀先验=最大不确定度）。
    # 期②-4：主导图式来自稳定寄存器 B₀（e_weights）——心向跟声明走，不跟脸色走。
    ew = e_weights if e_weights is not None else weights
    user_top = None
    if ew:
        user_top = max(ew, key=lambda k: float(ew.get(k, 0.0) or 0.0))
        if float(ew.get(user_top, 0.0) or 0.0) <= FITTING["user_dominant_min_w"]:
            user_top = None
    _ZH = {"Counter_Phobic": "逆恐", "Mirroring": "镜映", "Avoidance": "逃避",
           "Trauma_Recon": "重构", "Pragmatism": "实用"}
    results["user_dominant"] = user_top
    if user_top:
        results["user_dominant_token"] = _tok(
            f"[用户心向·{_ZH[user_top]}]", "里", "compat",
            FITTING["heart_token_strength"])

    # 7. 扁平候选池（rank_tokens 消费）
    flat, flat_sources = [], []

    def extend(tokens, source):
        for token in tokens:
            flat.append(token)
            flat_sources.append(dict(source))

    # L0 原子标签由人格常量生成，是“角色计算画像”而不是本轮新发生的心理事件。
    # 人格卡已承担其长期表达职责；若每回合把这些 MANE 原始标签直送写作层，会把
    # “极端自毁/核弹把柄”等旧叙事语义带入日常聊天并形成固定借口。它们仍完整参与
    # 排序和时间场，只不具备本轮对白素材资格。
    extend((_tok(tag, classify_layer(tag), "atomic") for tag in results["atomic_tags"]),
           _source("npc_persona_measurement", "npc", False))
    # MANE 的“顺应/未满足对方天性”在此处其实是 NPC 与自身基线的比较，原字面
    # 没有足够主语，不能直达生成层；它仍保留在原 token 流参与排序/时间场。
    extend((_tok(t, "里", "nature")
            for t in results["meta_cognition"]["universal_tokens"]),
           _source("npc_self_comparison", "npc", False))
    extend((_tok(t, "表", "gameplay")
            for t in results["gameplay"]["universal_tokens"]),
           _source("npc_user_measurement", "relation", False))
    if results.get("user_dominant_token"):
        extend((results["user_dominant_token"],),
               _source("user_preference", "user", False))
    # 两条相性计算保留为独立来源。它们过去都压扁为 compat，导致“谁顺应谁”
    # 在输出端丢失；现在仍同类排序，但只能作为总线调度/评估读数。
    extend((_tok(t, "里", "compat")
            for t in results["compatibility_npc_to_user"]["universal_tokens"]),
           _source("npc_to_user_fit", "relation", False))
    extend((_tok(t, "里", "compat")
            for t in results["compatibility_user_to_npc"]["universal_tokens"]),
           _source("user_to_npc_relation", "relation", False))
    extend((_tok(t, "势", "trend") for t in self_change["tokens"]),
           _source("npc_time_change", "npc", True))
    extend((_tok(t, "势", "trend") for t in chase["tokens"]),
           _source("npc_user_adjustment", "relation", False))
    extend(bridge["tokens"], _source("npc_time_change", "npc", True))
    extend((_tok(t, "势", "trend") for t in results["accum_tokens"]),
           _source("npc_time_change", "npc", True))
    extend(tactics, _source("mane_interaction_strategy", "relation", False))
    results["flat_tokens"] = flat
    results["flat_token_sources"] = flat_sources

    raw_values = {
        "D_NPC": drives,
        "E_NPC": baseline,
        "D_user": D_user,
        "E_user": E_user,
        "purity": purity,
    }

    return {
        "raw_values": raw_values,
        "analysis": results
    }
