"""TIDE 公式注册表：参数治理与数学 trace 的只读目录。

它不参与任何运行计算、状态写入或调度。唯一职责是让同一份公式元数据可被
参数审计、trace 和定向测试共同读取，避免把“公式在哪、读哪些根输入、哪些参数
可调”散落在文档和调用点中。

``inline:`` 前缀表示当前仍嵌在公式实现中的行为超参数；R1 收编前它必须显式
暴露，但不能假装成已经存在的 ``core.params`` 路径。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from core import params as _params


FORMULA_REGISTRY_VERSION = "alpha03-p4-v1"


@dataclass(frozen=True)
class FormulaSpec:
    """一条公式的静态、可序列化元数据，不保存公式函数或闭包。"""

    formula_id: str
    owner_module: str
    root_inputs: tuple[str, ...]
    derived_outputs: tuple[str, ...]
    config_paths: tuple[str, ...]
    write_boundary: str
    runtime_mode: str
    targeted_tests: tuple[str, ...]
    formula_version: str = "v1"
    # M0 不改变运行公式：这四项只声明迁移目标，使 current runtime inputs 和
    # Alpha 0.2 的根量签名能够同时被 trace / 测试读取。
    target_root_inputs: tuple[str, ...] = ()
    target_receipt_inputs: tuple[str, ...] = ()
    migration_status: str = "stable"
    migration_notes: str = ""

    def as_record(self) -> dict:
        return {
            "formula_id": self.formula_id,
            "owner_module": self.owner_module,
            "root_inputs": list(self.root_inputs),
            "derived_outputs": list(self.derived_outputs),
            "config_paths": list(self.config_paths),
            "write_boundary": self.write_boundary,
            "runtime_mode": self.runtime_mode,
            "targeted_tests": list(self.targeted_tests),
            "formula_version": self.formula_version,
            "target_root_inputs": list(self.target_root_inputs),
            "target_receipt_inputs": list(self.target_receipt_inputs),
            "migration_status": self.migration_status,
            "migration_notes": self.migration_notes,
        }


# ID 对应 docs/ALGORITHM_FORMULA_ATLAS.md 的 F01--F17。使用 tuple 保持顺序稳定，
# 便于 trace 阅读与 snapshot 比较；这里不能导入 Runner / temporal / agents。
FORMULAS: tuple[FormulaSpec, ...] = (
    FormulaSpec(
        "F01", "core.schema_matrix.generate_constants", ("W_npc",), ("C",),
        ("DEFAULTS.weight_tolerance", "inline:core.schema_matrix.base_matrix"),
        "仅角色创建期写 profile.weights/profile.constants", "active",
        ("tests/test_core_math.py", "tests/test_ammo.py")),
    FormulaSpec(
        "F02", "ops.fitting.extract_features", ("user_message.text",),
        ("features", "tier_hint"), ("FITTING",),
        "纯输入测量，不写状态", "active", ("tests/test_fitting.py",)),
    FormulaSpec(
        "F03", "ops.turn_signals.derive_turn_signals",
        ("features", "tier_hint", "ammo", "semantic_projection", "pending_extraction"),
        ("Delta_X", "Delta_L_self", "Delta_X_sources"),
        ("DICT_DELTAS", "AMMO_HIT_DELTAS", "SALIENCE.tier_lever_delta",
         "FITTING.extractor_correction"),
        "输入侧永不写 L_opponent；事件提交前只生成增量；来源收据不回流", "active",
        ("tests/test_turn_signals.py", "tests/test_virtual_tick_coupled_harness.py"),
        formula_version="alpha02-m4-f03-v2",
        migration_status="stable",
        migration_notes="词典、语义、弹药与延迟 extractor 的已准入贡献逐轴可审计；总和必须等于进入 F06 的 Delta_X。",
    ),
    FormulaSpec(
        "F04", "ops.fitting.coarse_fit",
        ("recent_user_messages", "user_baseline"), ("D(t)",), ("FITTING",),
        "纯近期测量，不直接写 B0", "active", ("tests/test_fitting.py",)),
    FormulaSpec(
        "F05", "core.drives.calculate_drives", ("C", "X"),
        ("S", "T", "R_mag", "D_aggro", "D_defend", "D_chaos"),
        ("DRIVES",),
        "纯状态几何，不写状态", "active", ("tests/test_core_math.py",)),
    FormulaSpec(
        "F06", "temporal.bus.pressure_readout + update_hidden + ops.output_receipt",
        ("C_effective(version)", "X", "committed_user_anchor", "t_sent"),
        ("P", "E", "H_max", "output_event_residual"),
        ("TIME.hidden_capacity_w_b2", "TIME.hidden_capacity_w_b3",
         "TIME.hidden_capacity_matrix",
         "DRIVES.chaos_need_power", "DRIVES.threat_total_cap",
         "inline:temporal.root_time.ROOT_TIME_VECTOR_V2"),
        "仅正式输出提交可把 F06 后效写入 X；残量不决定开口", "active",
        ("tests/test_temporal.py", "tests/test_event_residual.py"),
        formula_version="alpha02-m10-f06-root-vector-v3",
        target_root_inputs=("C_effective(version)", "X", "committed_user_anchor", "t_sent"),
        target_receipt_inputs=("effective_constants_version", "committed_user_anchor", "t_sent"),
        migration_status="stable",
        migration_notes="已移除 D_chaos 旁路；P 与容量均直接读 C_effective/X。已送达输出的 e_user(t_sent)\n        直接复用 F08 root-time-vector-v2 的分轴恢复率（P_need 快、V_attr 慢、杠杆不衰减），不读取旧 opening、P、词元、场或计数，也不产生开口资格。",
    ),
    FormulaSpec(
        "F07", "core.tokens.priority.score_token_resources", ("token_candidates", "P", "seen_counts"),
        ("token_resources", "field_top3"),
        (
            "TOKEN_PRIORITY.energy_band_thresholds", "TOKEN_PRIORITY.strength_floor",
            "TOKEN_PRIORITY.strength_ceiling", "TOKEN_PRIORITY.weak_sink",
            "TOKEN_PRIORITY.novelty_first_bonus", "TOKEN_PRIORITY.novelty_repeat_penalty",
            "TOKEN_PRIORITY.novelty_repeat_cap",
        ),
        "只更新词元新鲜度计数；不写人格或记忆", "active", ("tests/test_core_math.py",)),
    FormulaSpec(
        "F08", "temporal.bus.advance", ("C_effective(version)", "X", "Delta_t"),
        ("X_after_time", "root_time_receipt"), (
            "inline:temporal.root_time.ROOT_TIME_VECTOR_V2",
        ),
        "仅 temporal state 写四连续轴；L 轴不随时间衰减", "active",
        ("tests/test_temporal.py", "tests/test_temporal_schedule.py"),
        formula_version="root-time-vector-v2",
        target_root_inputs=("C_effective(version)", "X", "Delta_t"),
        target_receipt_inputs=("effective_constants_version",),
        migration_status="stable",
        migration_notes="正式 v2 以会话绑定的 C_effective、X 与挂钟增量直接解析四轴回落；field、驱动力、旧爆发阶段、输出残量、P3、词元和记忆候选均不写入 F08。",
    ),
    FormulaSpec(
        "F09", "core.salience.compute_salience_parts",
        ("Delta_V_attr", "Delta_V_rep", "pain", "tier", "user_message.text", "P_need"),
        ("S", "salience_parts"), ("SALIENCE", "MEMORY.entropy_scale_chars"),
        "只写归档事件显著性，不决定消息发送", "active", ("tests/test_memory.py",)),
    FormulaSpec(
        "F10", "ops.turn_recall.resolve_turn_recall + ops.memory_state", (
            "query", "archive", "ledger", "wall_clock", "C_effective(version)",
            "X_after", "Delta_X_event", "selected_recall_receipt", "actual_recall_trajectory"),
        ("recall_candidates", "recall_eligibility", "recall_selection", "Delta_X_memory"),
        ("MEMORY", "LEDGER", "RECALL", "RECALL_EVIDENCE", "RECALL_GATE",
         "inline:ops.memory_state.P4_TAU_REFERENCE_MS",
         "inline:ops.memory_state.P4_MEMORY_BASIS_VERSION"),
        "RAG 独占选择；family-v2 仅在已选 receipt 后、F03/F16 单次提交中写 Delta_X_memory", "mixed",
        ("tests/test_turn_recall.py", "tests/test_memory.py", "tests/test_memory_state.py",
         "tests/test_runner_stub.py", "tests/test_peer_response.py",
         "tests/test_proactive_memory_timeline.py"),
        formula_version="recall-state-readout-v2",
        target_root_inputs=("A", "event", "Delta_t", "evidence", "C_effective(version)",
                            "X_after", "Delta_X_event"),
        target_receipt_inputs=("selected_rag_receipt", "state_imprint", "policy_family_anchor",
                               "actual_recall_trajectory", "effective_constants_version"),
        migration_status="stable",
        migration_notes="RAG 只负责候选/选择；family-v2 只从同版 C_effective、当前根事件、历史印痕、真实年龄与既成 recall 轨迹形成 P4 readout。P4 不重排记忆、不读 P3/M.A.N.E./词元/writer/UI，也不创建 tick 或消息路径；旧会话无 anchor、旧档无印痕时零写入。",
    ),
    FormulaSpec(
        "F11", "interaction_extensions.preference_calibration.cx_six_seven.response_intensity.compute_reward_parts",
        ("user_message.text", "features", "arrival_delay", "user_baseline", "tier"),
        ("R", "R_parts"), ("REWARD",),
        "只读用户反应观测，不写偏好或状态", "active",
        ("tests/test_reward_parts.py", "tests/test_reward_tuner.py")),
    FormulaSpec(
        "F12", "ops.drift_gate.drift_update", ("B0", "D(t)", "user_evidence_history"),
        ("B0_candidate",), ("DRIFT", "FITTING.user_dominant_min_w"),
        "仅产生 B0 版本替换候选；Runner 只落 proposal receipt，不能写已绑定 B0", "shadow",
        ("tests/test_drift_gate.py", "tests/test_fitting.py")),
    FormulaSpec(
        "F13", "ops.*_projection", ("B0", "D(t)", "settled_state", "selection_receipt"),
        ("calibration_shadow",), ("CADENCE", "FORM", "RECALL_CALIBRATION"),
        "只生成版本化离线参数提案／receipt；不得写当前会话的调度、状态或消息", "shadow",
        ("tests/test_cadence_projection.py", "tests/test_form_projection.py",
         "tests/test_recall_projection.py")),
    FormulaSpec(
        "F14", "ops.peer_response.peer_response_eligibility",
        ("C_effective(version)", "X", "Delta_X_event"),
        ("peer_reactive_candidate",), ("PEER_REACTIVE",),
        "只判断已提交 peer 输入的因果反应；不得产生自主计划或投递", "active",
        ("tests/test_peer_response.py", "tests/test_math_bus_lineage.py"),
        formula_version="peer-reactive-root-vector-v1",
        target_root_inputs=("C_effective(version)", "X", "Delta_X_event"),
        target_receipt_inputs=("committed_peer_event", "effective_constants_version"),
        migration_status="stable",
        migration_notes="F14 只保留 peer 的即时因果反应。用户主回复走 F16；自主续话、追发与沉默开口不读取旧 opening_margin，而由正式 tick 的 P3 F15 authority receipt 唯一裁定。peer 仍只读 C_effective/X/本轮 e，不读 P、关系几何、B0/cadence、词元、草稿或 writer。",
    ),
    FormulaSpec(
        "F15", "core.interaction_geometry.opening_authority_receipt",
        ("C_effective(version)", "X_before", "X_after", "Delta_X", "Delta_G", "event_identity"),
        ("B", "R", "Z", "I_open", "Theta_open", "margin", "Q_tick", "h_next_ms"),
        ("inline:core.interaction_geometry.SCHEDULE_VECTOR_AUTHORITY_V2",
         "inline:core.interaction_geometry.OPENING_AUTHORITY_V5"),
        "只在正式 dynamic tick 以同一 receipt 裁定 crossing；F16 仅提交已经取得资格的输出", "active",
        ("tests/test_interaction_geometry.py", "tests/test_math_bus_lineage.py",
         "tests/test_temporal_schedule.py", "tests/test_actor.py"),
        formula_version="opening-authority-receipt-v1",
        target_root_inputs=("C_effective(version)", "X_before", "X_after", "Delta_X", "Delta_G", "event_identity"),
        target_receipt_inputs=("settled_tick", "effective_constants_version"),
        migration_status="stable",
        migration_notes="F15 从同一 Z 读 I_open、Theta_open 和 Q_tick；margin 只在正式 dynamic tick 经离散 edge 创建一次自主输出资格，Q_tick 只映射下一观察间隔。F16 output receipt 只重设下一个 tick，不能预消费 crossing。旧 opening_margin/tick_readout 仅限显式 legacy、fixture 或 peer 兼容，不能参与已切 P3 计划、观察间隔或发送。",
    ),
    FormulaSpec(
        "F16", "temporal.delay + ops.form_projection", ("C", "X", "Delta_X_event", "user_baseline", "B0", "D(t)", "seed"),
        ("cadence_delay", "form_attention"), (
            "DELAY", "FORM",
            "FORM.activity_matrix", "FORM.level_mid_ratio",
            "FORM.level_strong_ratio",
        ),
        "两条只读呈现投影；不写心理状态或主动计划", "mixed",
        ("tests/test_delay.py", "tests/test_output_time.py", "tests/test_form_projection.py"),
        formula_version="alpha02-m10-f16-root-vector-v3",
        target_root_inputs=("C", "X", "Delta_X_event", "user_baseline", "seed"),
        migration_status="stable",
        migration_notes="delay 是 C/X/e/个人节奏基线/seed 的只读投影；form_attention 保留 B0/D(t) 的用户偏好方向，但强度 gate 已迁为 C/X/e 投影。两者均不读取 P、未回应账本或消息数量，不写心理状态或主动计划。",
    ),
    FormulaSpec(
        "F17", "ops.length_mode", ("C", "X", "Delta_t", "delivered_memory_receipt", "seed"),
        ("length_mode", "past_event_weight"),
        ("LENGTH_MODE", "LENGTH_MODE.state_matrix"),
        "只选择表达长度模式；不决定召回资格", "active", ("tests/test_length_mode.py",),
        formula_version="alpha02-m10-f17-root-vector-v2",
        target_root_inputs=("C", "X", "Delta_t", "seed"),
        target_receipt_inputs=("delivered_memory_receipt",),
        migration_status="stable",
        migration_notes="F17 用 C/X/墙钟和本轮已实际交付的记忆数连续投影篇幅概率；recent_S_max 仅为归档排序证据，不能越线成为表现层行为门。",
    ),
)


def registry() -> tuple[FormulaSpec, ...]:
    """稳定返回注册表；调用方不得修改全局元数据。"""
    return FORMULAS


def get(formula_id: str) -> FormulaSpec:
    """按稳定 ID 读取一条公式；未知 ID 显式失败，避免 trace 静默漂移。"""
    wanted = str(formula_id)
    for spec in FORMULAS:
        if spec.formula_id == wanted:
            return spec
    raise KeyError("未知公式 ID: " + wanted)


def resolve_config_path(path: str, params_module: object = _params):
    """解析 ``PARAM_FAMILY.key``；inline 引用只供审计，不存在运行配置值。"""
    ref = str(path)
    if ref.startswith("inline:"):
        return None
    pieces = ref.split(".")
    if not pieces or not pieces[0]:
        raise KeyError("非法参数路径: " + ref)
    current = getattr(params_module, pieces[0])
    for piece in pieces[1:]:
        if not isinstance(current, Mapping) or piece not in current:
            raise KeyError("参数路径不存在: " + ref)
        current = current[piece]
    return current


def validate() -> list[str]:
    """验证 registry 自身，不导入运行时模块也不修改任何全局对象。"""
    errors: list[str] = []
    seen: set[str] = set()
    allowed_modes = {"active", "shadow", "mixed"}
    allowed_migrations = {"stable", "root_isolation_pending", "boundary_frozen"}
    for spec in FORMULAS:
        if not spec.formula_id or spec.formula_id in seen:
            errors.append("formula_id 缺失或重复: " + spec.formula_id)
        seen.add(spec.formula_id)
        if spec.runtime_mode not in allowed_modes:
            errors.append(spec.formula_id + " runtime_mode 非法")
        if not spec.owner_module or not spec.root_inputs or not spec.derived_outputs:
            errors.append(spec.formula_id + " 缺少归属、输入或输出")
        if spec.migration_status not in allowed_migrations:
            errors.append(spec.formula_id + " migration_status 非法")
        if spec.migration_status != "stable" and not spec.target_root_inputs:
            errors.append(spec.formula_id + " 迁移中但缺少目标根量签名")
        for path in spec.config_paths:
            try:
                resolve_config_path(path)
            except (AttributeError, KeyError):
                errors.append(spec.formula_id + " 参数路径不存在: " + str(path))
    return errors


def snapshot() -> dict:
    """供未来 math trace / 工具读取的 JSON 原生元数据快照。"""
    return {
        "registry_version": FORMULA_REGISTRY_VERSION,
        "formulas": [spec.as_record() for spec in FORMULAS],
    }
