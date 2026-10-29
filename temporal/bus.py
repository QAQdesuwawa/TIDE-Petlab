# temporal/bus.py
"""采样协议（架构文档 §11.5）：纯函数编排，可回放。

advance   ：挂钟推进（curve → fatigue → recovery → rebound → clamp，闭式）
on_message：一次消息到达的完整时间+事件结算（零 LLM；词元几何复用 P0 模块）

只记消息时间戳，不记"离开事件"——用户没打字之前，离开/回归在系统里不存在。
"""
import copy
import math

from core.drives import calculate_drives
from core.params import DRIVES, TIME, FALLBACK_VARS
from core.tokens.behavior import analyze_behavior_space, analyze_time_bridge
from core.tokens.priority import score_token_resources, select_field_tokens
from core.tokens.variable import compute_variable_tokens
from core.vectors import get_drive_vector
from model_packages.cx_six_seven_v1.initialization_schema import (
    normalize_initial_state as normalize_initial_vars,
)
from temporal.fields import AXES, compile_field
from temporal.gate import tau0_minutes

HOUR_MS = 3_600_000.0
CAPACITY_ROOT_ORDER = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")
CAPACITY_CONSTANT_ORDER = ("a1", "a2", "b1", "b2", "b3", "g1", "g2")


def _root_delta_snapshot(values, *, label):
    """校验一份六轴变化向量，拒绝用缺轴零值掩盖 P4 接线遗漏。"""
    if not isinstance(values, dict):
        raise ValueError(f"{label} 必须是六轴映射")
    missing = [axis for axis in FALLBACK_VARS if axis not in values]
    if missing:
        raise ValueError(f"{label} 缺少根轴: " + ", ".join(missing))
    out = {}
    for axis in FALLBACK_VARS:
        try:
            value = float(values[axis])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{label}.{axis} 必须是数值") from exc
        if not math.isfinite(value):
            raise ValueError(f"{label}.{axis} 必须是有限数值")
        out[axis] = value
    return out


def _memory_delta_receipt(event):
    """读取 P4 已冻结的 ``Delta_X_memory``，但不选择、重算或生成它。

    F08 只能消费已经由 selected RAG receipt、年龄/轨迹测量与融合器产出的批次
    receipt。这里不导入 memory 模块，避免 temporal 反向依赖 recall/writer；也不
    接受裸 ``memory_delta``，避免调用方把词元、候选或 UI 数字伪装成根状态变化。
    """
    supplied = event.get("memory_delta")
    receipt = event.get("memory_state_receipt")
    if supplied is None and receipt is None:
        return None, None
    if supplied is None or not isinstance(receipt, dict):
        raise ValueError("P4 memory_delta 必须与正式 memory_state_receipt 同时提供")
    if receipt.get("ready_to_commit") is not True:
        raise ValueError("P4 memory_state_receipt 未获得提交资格")
    declared = _root_delta_snapshot(receipt.get("Delta_X_memory"),
                                    label="memory_state_receipt.Delta_X_memory")
    requested = _root_delta_snapshot(supplied, label="memory_delta")
    for axis in FALLBACK_VARS:
        if not math.isclose(requested[axis], declared[axis], rel_tol=0.0, abs_tol=1e-9):
            raise ValueError("P4 memory_delta 必须与冻结 receipt 的 Delta_X_memory 一致")
    return requested, receipt


def new_state(initial_vars=None):
    """全新状态（首条消息前）；可显式指定经过确认的关系初态 X0。"""
    return {
        "vars": normalize_initial_vars(initial_vars),
        "prev_vars": None,
        "P": 0.0,
        "E": 0.0,
        "H_max": 0.0,
        "exploded": False,
        "exploded_at": None,
        # 旧存档兼容键：M1b 后不再写入或参与 F08；下个 state schema 迁移再清理。
        "last_dominant": None,
        "dominant_since": None,
        "last_ts": None,
        # 最近一条已提交用户事件的不可变开口锚。它是事件 receipt，不是心理根量：
        # NPC 输出会改变 X，却绝不能重置这条用户互动曲线。
        "opening_anchor": None,
        "last_advance_ms": None,   # 上次时间积分点（增量积分锚：轮询/重启不重复算恢复场）
        # 旧 field 时代的兼容键；M1b 的 Φ 仅使用 last_advance_ms 与当前 X。
        "time_anchor_vars": None,
        "time_anchor_ms": None,
        "last_drives": None,        # 上轮驱动力（自变对比）
        "last_user_vec": None,      # 用户上轮伪向量（追赶对比）
        "last_end_drives": None,    # 上次结束时刻 NPC 驱动力快照（时间桥）
        "pending_reply": None,      # 延迟回复挂起：{reply, send_at, request_ts}
        "accum": {},                # L3 累积状态（{"npc": {...}}，挂钟口径）
        "seen_counts": {},          # 词元新鲜度历史计数（跨轮持久化，防静态词元吃满 Top-N）
        "field": None,              # 当前活跃漂移场（编译产物）
        "field_start_ms": None,
        "field_tau0_min": None,
        # 旧 field 续接兼容键；新输出事件直接写 X，F08 不再读取此项。
        "output_field_offset": None,
        # T0.1 单角色串行提交水位：只标记最后一条已完整写入时间状态的 action。
        # 后续输出必须在前一条完成后才会开始，故不需要无界 action 历史。
        "last_output_state_action_id": None,
        # E4：只保存 F16 已提交的表现发放余量。它不是心理根态，不能由草稿、
        # typing、UI 或候选编排写入；下一 action 的纯发放器只读这个 receipt。
        "expression_emission_remainders": {},
        "last_expression_emission_action_id": None,
    }


def advance(state, now_ms, params=None, events=None, *,
            effective_constants=None, effective_constants_version=None,
            effective_time_params=None):
    """挂钟推进：把 x 从上一个推进点推进到 now_ms（闭式单段结算）。

    推进锚 = last_advance_ms（缺省回落 last_ts）——轮询/重启多次调用同一 gap
    不会重复积分（增量语义），每次推进后更新 last_advance_ms。
    """
    p = params or TIME
    events = events if events is not None else []
    x = state["vars"]
    # 先校验接口，再判断 Δt。否则首条消息/零时间调用会悄悄吞掉不完整的 live
    # 常量绑定，下一次才以另一种公式结算，破坏同签名迁移。
    uses_effective = effective_constants is not None or effective_constants_version is not None
    if uses_effective and (effective_constants is None
                           or not str(effective_constants_version or "").strip()):
        raise ValueError("F08 有效常量必须同时提供数值与不可变版本")

    since = state.get("last_advance_ms") or state.get("last_ts")
    dt_ms = 0.0
    if since is not None:
        dt_ms = max(0.0, now_ms - since)
    dt_h = dt_ms / HOUR_MS
    if dt_h <= 0:
        return state

    # P2-3c：live 调用若声明采用有效常量，必须把数值和不可变版本成对传入。
    # 这里不读 profile/B0/UI，也不以 C_base 或裸 prior 回退；调用者应在进入 F08 前
    # 已经完成会话绑定解析。未传这对参数仍保留给旧档/纯 oracle，不能被正式 Runner 使用。
    uses_effective = effective_constants is not None or effective_constants_version is not None
    if uses_effective:
        from temporal.root_time import settle_root_time_vector
        receipt = settle_root_time_vector(
            effective_constants, x, dt_ms,
            constants_version=str(effective_constants_version), params=effective_time_params,
        )
        state["exploded"] = False
        state["exploded_at"] = None
        for axis in AXES:
            x[axis] = min(1.0, max(0.0, float(receipt["after"][axis])))
        events.append({
            "rule": "root_time_advanced",
            "formula": "F_time_root_vector_v2",
            "dt_ms": int(round(dt_ms)),
            "rebound_ms": 0,
            "effective_constants_version": str(effective_constants_version),
            "root_time_version": receipt["version"],
            # F08 的完整前后态只作为本次正式墙钟结算的 receipt 证据。它不是
            # 额外状态槽、词元或开口输入；Runner 只有在同一 state 真正落盘时才可
            # 将其冻结为表现层只读锚。
            "time_transition": {
                "X_before": dict(receipt["before"]),
                "Delta_X_time": dict(receipt["root_delta"]),
                "X_after": dict(receipt["after"]),
                "elapsed_ms": int(receipt["inputs"]["elapsed_ms"]),
            },
        })
        state["last_advance_ms"] = now_ms
        return state

    # Alpha 0.2-M1b：F08 只做根量状态推进。field/Top3、主导驱动力和输出残量
    # 都是旧投影或旧兼容产物，不能再决定 X。每轴向 rest_point 的指数恢复可按
    # 任意合法时间切分相乘，天然保持回放等价。旧 ``exploded/rebound`` 布尔窗口
    # 不再参与时间推进：它会把一次压力读数伪装成固定数小时耐火期。
    rest = p["rest_point"]
    # 兼容旧档键但撤销它们的运行时权力。新的降温只由本轮根态、已提交输出
    # 和墙钟增量组成；不再有“过线后强制回落 N 小时”的派生阶段。
    state["exploded"] = False
    state["exploded_at"] = None
    total_rate = float(p["recovery_rho"]) * dt_h
    factor = math.exp(-total_rate)
    resolved = dict(x)
    for ax in AXES:
        # 固定精度不是新的状态量；它只消除同一闭式公式因不同合法分段产生的
        # 机器浮点末位差，从而让重放 receipt 可以逐位比较。
        resolved[ax] = round(
            float(rest[ax]) + (float(resolved[ax]) - float(rest[ax])) * factor, 8
        )

    # 这是时间结算的可追溯 receipt，不是新的心理变量，也不被任何决策公式读取。
    # 它让回放可区分「没有经过时间」和「经过时间但没有爆发」两种状态。
    events.append({
        "rule": "root_time_advanced",
        "dt_ms": int(round(dt_ms)),
        "rebound_ms": 0,
    })

    # 旧 archive 仍可携带 field/time_anchor/offset；它们由迁移层保留以便读取旧
    # receipt，但从本版本起不再参与计算。新输出事件直接写 X，随后由上述 Φ 衰减。

    # 钳制 + 推进锚更新
    for ax in AXES:
        x[ax] = min(1.0, max(0.0, resolved[ax]))
    state["last_advance_ms"] = now_ms
    return state


def _sigmoid(value):
    value = max(-60.0, min(60.0, float(value)))
    return 1.0 / (1.0 + math.exp(-value))


def _logit(value):
    bounded = min(1.0 - 1e-6, max(1e-6, float(value)))
    return math.log(bounded / (1.0 - bounded))


def _capacity_weights(matrix, constants):
    """将 F06 容量矩阵从 C 行投影到六条根轴。"""
    if not isinstance(matrix, (tuple, list)) or len(matrix) != len(CAPACITY_CONSTANT_ORDER):
        raise ValueError("F06 hidden_capacity_matrix 必须是 7×6")
    rows = []
    for row in matrix:
        if not isinstance(row, (tuple, list)) or len(row) != len(CAPACITY_ROOT_ORDER):
            raise ValueError("F06 hidden_capacity_matrix 必须是 7×6")
        rows.append(tuple(float(value) for value in row))
    c = tuple(min(1.0, max(0.0, float((constants or {}).get(key, 0.0) or 0.0)))
              for key in CAPACITY_CONSTANT_ORDER)
    return c, tuple(sum(c[index] * rows[index][axis]
                        for index in range(len(CAPACITY_CONSTANT_ORDER)))
                    for axis in range(len(CAPACITY_ROOT_ORDER)))


def _capacity_root_delta(values, params):
    """F06 容量相对静息参考的 X 偏移；不借用 P 或任何驱动力。"""
    rest = params["rest_point"]
    reference = {
        "E_env": float(rest["E_env"]),
        "L_self": 0.0,
        "L_opponent": 0.0,
        "P_need": float(rest["P_need"]),
        "V_attr": float(rest["V_attr"]),
        "V_rep": float(rest["V_rep"]),
    }
    return tuple(min(1.0, max(0.0, float((values or {}).get(axis, 0.0) or 0.0)))
                 - reference[axis] for axis in CAPACITY_ROOT_ORDER)


def pressure_readout(constants, variables, params=None, drive_params=None):
    """F06 的纯根量读数：势能、静息势能与容量。

    ``D_chaos`` 原本是 ``F05(C, X)`` 的写作投影，却被 F06 作为输入再次消费；
    这里保留其非线性形状，但将它直接展开为 ``C/X`` 的局部代数项。返回值都是
    本轮读数，不写 state，也不创建新的心理轴。

    容量以当前杠杆不衰减、四轴回到静息点时的 ``P_rest`` 为下界；其人格静息
    基线再由一条独立 ``C^T A_capacity · (X-X_rest)`` 投影连续调整。故无新事件
    时系统不会只因自身静息势能仍高于旧容量而反复决堤，也不会把容量冻结为角色
    常量。
    """
    p = params or TIME
    dcfg = drive_params or DRIVES
    v = variables or {}

    def unit(key):
        return min(1.0, max(0.0, float(v.get(key, 0.0) or 0.0)))

    def constant(key):
        return min(1.0, max(0.0, float((constants or {}).get(key, 0.0) or 0.0)))

    def potential(values):
        env = unit_from(values, "E_env")
        need = unit_from(values, "P_need")
        attraction = unit_from(values, "V_attr")
        reputation = unit_from(values, "V_rep")
        leverage_total = min(
            float(dcfg["threat_total_cap"]),
            unit_from(values, "L_self") + unit_from(values, "L_opponent"),
        )
        mismatch = abs(attraction - reputation)
        avalanche = math.exp((env + leverage_total) * mismatch) - 1.0
        chaos = (constant("g1") * avalanche
                 + constant("g2") * math.pow(need * mismatch,
                                               float(dcfg["chaos_need_power"])))
        return min(1.0, max(0.0, 0.25 * (env + need + attraction * reputation
                                          + min(1.0, chaos))))

    def unit_from(values, key):
        return min(1.0, max(0.0, float((values or {}).get(key, 0.0) or 0.0)))

    current = {key: unit(key) for key in FALLBACK_VARS}
    P = potential(current)
    resting = dict(current)
    resting.update({key: float(p["rest_point"][key]) for key in AXES})
    P_rest = potential(resting)
    capacity_base = min(1.0, max(0.0,
                                  constant("b2") * float(p["hidden_capacity_w_b2"])
                                  + constant("b3") * float(p["hidden_capacity_w_b3"])))
    capacity_constants, capacity_weights = _capacity_weights(
        p["hidden_capacity_matrix"], constants)
    capacity_delta = _capacity_root_delta(current, p)
    capacity_terms = tuple(weight * delta for weight, delta in zip(capacity_weights, capacity_delta))
    raw_capacity = _sigmoid(_logit(capacity_base) + sum(capacity_terms))
    H_max = P_rest + (1.0 - P_rest) * raw_capacity
    return {
        "P": round(P, 4),
        "P_rest": round(P_rest, 4),
        "H_max": round(min(1.0, max(P_rest, H_max)), 4),
        "capacity_raw": round(raw_capacity, 4),
        "capacity_base": round(capacity_base, 4),
        "capacity_root_delta": dict(zip(CAPACITY_ROOT_ORDER,
                                          (round(value, 6) for value in capacity_delta))),
        "capacity_weights": dict(zip(CAPACITY_ROOT_ORDER,
                                       (round(value, 6) for value in capacity_weights))),
        "capacity_terms": dict(zip(CAPACITY_ROOT_ORDER,
                                     (round(value, 6) for value in capacity_terms))),
        "capacity_constants": dict(zip(CAPACITY_CONSTANT_ORDER,
                                         (round(value, 6) for value in capacity_constants))),
    }


def update_hidden(state, constants, params, events, now_ms):
    """将 F06 根量读数提交到 temporal state；唯一状态写入点。"""
    readout = pressure_readout(constants, state["vars"], params)
    P = readout["P"]
    H_max = readout["H_max"]

    # 压力越线只是当前读数，不再变成一个会支配随后数小时的布尔阶段。E 保留为
    # 可视化/诊断的连续超额量，任何调度仍只读取 C、X、e_user、Delta_t。
    state["exploded"] = False
    state["exploded_at"] = None
    E = round(max(0.0, P - H_max), 4)
    state["P"] = P
    state["E"] = E
    state["H_max"] = H_max
    return state


def on_message(state, constants, event, now_ms, params=None, *,
               effective_constants=None, effective_constants_version=None,
               effective_time_params=None):
    """纯函数：一次消息到达的完整结算。原 state 不变，返回 (new_state, report)。

    event = {
      "var_deltas":    dict(6 键),     # 本轮变量增量（词典快速提取，P5 接真源）
      "lever_deltas":  {"self": x, "opponent": y},  # 三档私密分级（异步滞后一轮）
      "weights":       dict(5 键),     # 瞬时状态 D(t)（期②-4 起；此前 = 融合权重）
      "stable_weights": dict | None,   # 基准锚 B₀（期②-4：E_user/纯度/心向词元走它）
      "weights_prev":  dict | None,    # 上窗口 D(t)（速度项）
      "semantic_tokens": list[dict],   # 可替换语义投影的通用词元（可为空）
    }
    """
    p = params or TIME
    s = copy.deepcopy(state)
    events = []

    # ① 时间结算（挂钟推进）
    advance(s, now_ms, p, events,
            effective_constants=effective_constants,
            effective_constants_version=effective_constants_version,
            effective_time_params=effective_time_params)
    # P4-2：事件状态印痕的 ``X_before`` 是墙钟已追平、但本条已提交输入尚未写入的
    # 六轴状态。它不是调用前的陈旧快照，因而不会把数小时自然回落混进 Delta_X_event。
    event_before = dict(s["vars"])

    # ② 事件结算：变量增量 + 杠杆增量（仅 L，事件性）。P4 的记忆影响
    # 不是另一条消息、更不是下一次墙钟：它只能携带上游已冻结的批次 receipt，
    # 在这一次纯结算内紧跟原始事件变化写入最终 X。
    v = s["vars"]
    deltas = event.get("var_deltas") or {}
    for k in v:
        v[k] = min(1.0, max(0.0, v[k] + float(deltas.get(k, 0.0))))
    lever = event.get("lever_deltas") or {}
    v["L_self"] = min(1.0, max(0.0, v["L_self"] + float(lever.get("self", 0.0))))
    v["L_opponent"] = min(1.0, max(0.0, v["L_opponent"] + float(lever.get("opponent", 0.0))))
    # 使用裁剪后的实际变化，不能只存请求的输入 delta：触到 0/1 边界时两者并不相同。
    event_after = dict(v)
    event_delta = {axis: event_after[axis] - event_before[axis]
                   for axis in FALLBACK_VARS}
    memory_delta, memory_receipt = _memory_delta_receipt(event)
    memory_transition = None
    if memory_delta is not None:
        memory_before = dict(v)
        for axis in v:
            v[axis] = min(1.0, max(0.0, v[axis] + memory_delta[axis]))
        memory_after = dict(v)
        actual_memory_delta = {
            axis: memory_after[axis] - memory_before[axis]
            for axis in FALLBACK_VARS
        }
        # Delta_X_event 与 Delta_X_memory 必须分开留痕：前者仍可作为这条用户/NPC
        # 消息的历史印痕；后者只说明本批已选记忆怎样改变了今天的最终状态。
        memory_transition = {
            "version": str(memory_receipt.get("version") or ""),
            "X_before": memory_before,
            "Delta_X_memory": actual_memory_delta,
            "X_after": memory_after,
            "selected_memory_ids": list(memory_receipt.get("selected_memory_ids") or ()),
            "single_state_commit": True,
            "derived_only": True,
        }

    # ③ 驱动力只供表达投影；不得持久化其 argmax 再回灌 F08。
    drives = calculate_drives(constants, v)

    # ④ 显隐势能 + 决堤。F05 表现驱动力仍读取 C_base；F06 则与本次 F08
    # 使用同一份显式 C_effective。二者不得因为共用 state 而静默混成一套常量。
    hidden_constants = effective_constants if effective_constants is not None else constants
    update_hidden(s, hidden_constants, p, events, now_ms)

    # ⑤ 时间桥词元（仅当沉默漂移场真实生效才桥接——gate 未开/无场时，
    #   姿态差来自事件层而非时间层，不产生"沉默后"词元）
    bridge = []
    if s["last_end_drives"] is not None and now_ms > (state["last_ts"] or now_ms):
        bridge = analyze_time_bridge(get_drive_vector(drives), s["last_end_drives"])["tokens"]

    # ⑥ 词元几何 → rank → 结束词元 → 编译新场
    dt_h = (now_ms - (s["last_ts"] or now_ms)) / HOUR_MS
    weights = event.get("weights") or {}
    weights_prev = event.get("weights_prev")
    # 期②-4：stable_weights = 基准锚 B₀（E_user/纯度/心向词元走它）；
    # weights = 瞬时状态 D(t)（D_user/博弈/追赶走它）。缺省 None = 旧行为。
    stable = event.get("stable_weights")
    bh = analyze_behavior_space(
        constants, v, weights,
        weights_prev=weights_prev, dt_hours=dt_h,
        D_prev=s["last_drives"], D_user_prev=s["last_user_vec"],
        D_end=s["last_end_drives"],
        accum_state=s["accum"], now_ms=now_ms, params=p,
        e_weights=stable if stable else None)
    if bh["analysis"]["updated_accum_state"] is not None:
        s["accum"] = {"npc": bh["analysis"]["updated_accum_state"]}

    var_tokens = compute_variable_tokens(v, s["prev_vars"])
    behavior_candidates = list(bh["analysis"]["flat_tokens"])
    behavior_sources = list(bh["analysis"].get("flat_token_sources") or [])
    npc_var_candidates = list(var_tokens["npc"])
    relation_var_candidates = list(var_tokens["relation"])
    semantic_candidates = list(event.get("semantic_tokens") or [])
    candidates = (behavior_candidates + npc_var_candidates + relation_var_candidates
                  + semantic_candidates)
    # 来源旁路不改变候选词元。它只把 MANE 迁移中原先丢失的主语/方向交给
    # 输出适配器，避免 compat 与 tactics 被误当作 NPC 的行动要求。
    provenance = (behavior_sources
                  + [{"lane": "npc_variable_state", "owner": "npc",
                      "expression_eligible": True} for _ in npc_var_candidates]
                  + [{"lane": "relationship_context", "owner": "relation",
                      "expression_eligible": False} for _ in relation_var_candidates]
                  + [{"lane": "external_semantic_projection", "owner": "user",
                      "expression_eligible": False} for _ in semantic_candidates])
    # 一次资源结算，两个投影：全量排序账本供总线消费者只读；时间场继续只取 Top-3。
    # 不把后者误当成表达、审计等消费者的完整资源池。
    resource_ranked = score_token_resources(
        candidates, potential=s["P"], seen_counts=s["seen_counts"], provenance=provenance)
    ranked = select_field_tokens(resource_ranked, potential=s["P"])
    # 新鲜度历史持久化（rank 内部用自己的副本；状态侧权威计数）
    for t in candidates:
        text = t.get("text", "")
        s["seen_counts"][text] = s["seen_counts"].get(text, 0) + 1
    field_tokens = ranked["all"]
    s["field"] = compile_field(
        field_tokens, {ax: v[ax] for ax in AXES},
        "dominate" if s["P"] >= 0.5 else "blend", p)
    s["field_start_ms"] = now_ms
    s["field_tau0_min"] = tau0_minutes(s["P"], p)
    # field 仍暂存给 M2 前的旧 schedule/表达兼容读取，但不能重设 F08 的时间锚。

    # ⑦ 快照更新（全量落盘由 P2 storage 承担）
    s["prev_vars"] = dict(v)
    s["last_ts"] = now_ms
    s["last_advance_ms"] = now_ms
    s["last_drives"] = drives
    s["last_user_vec"] = bh["raw_values"]["D_user"]
    s["last_end_drives"] = get_drive_vector(drives)

    report = {
        "dt_ms": max(0.0, now_ms - (state["last_ts"] or now_ms)),
        "events": events,
        # 这是一个只读状态结算 receipt。现有调用方可以忽略它，P4 的 message
        # 存证层则必须显式消费它；不得从 UI／writer／recall 候选另造状态印痕。
        "state_transition": {
            "version": "event-state-transition-v1",
            "X_before": event_before,
            "Delta_X_event": event_delta,
            "X_after": event_after,
            "time_settled_before_event": True,
            "derived_only": True,
        },
        # 没有已提交 recall batch 时刻意为 None；调用方不能因空候选/表达卡或
        # prompt 已见到旧事就补造一条记忆状态转换。
        "memory_state_transition": memory_transition,
        "bridge_tokens": bridge,
        # 本轮不可变资源账本：已按既有公式全量排序、已过弱新闻沉底线，尚未施加时间场
        # Top-3 带宽。它不持久化、不改变状态，只供同一轮消费者做边界明确的投影。
        "resource_ranked": resource_ranked,
        "resource_token_sources": resource_ranked["source_receipt"],
        "ranked": ranked,
        "token_sources": ranked["source_receipt"],
        "field_tokens": field_tokens,
        "field": s["field"],
        "P": s["P"],
        "E": s["E"],
        "H_max": s["H_max"],
        "drives": drives,
        "purity": bh["raw_values"]["purity"],
        # 供表达层的临时三向量读数；不写入 state，不创建第二总线。表达消费者只能
        # 用它重排已具表达资格的 NPC 资源，不能反向改时间场或用户参数。
        "expression_vectors": {
            "D_NPC": dict(bh["raw_values"]["D_NPC"]),
            "E_user": dict(bh["raw_values"]["E_user"]),
            "D_user": dict(bh["raw_values"]["D_user"]),
        },
    }
    return s, report
