# ops/runner.py
"""编排层：同步/异步管线（实现设计 §5）。

同步（用户等待）：
  词典快速提取（三档粗分级 + 变量增量）→ 图式粗算 + 偏好融合
  → bus.on_message（时间积分 + 事件结算 + 词元几何 + 结束词元 → 漂移场）
  → 记忆唤起（情绪色彩污染）→ 回复生成（1 次 LLM）
异步（回复发出后，桩测试中同步执行以便检查）：
  extractor 精提取（延迟一轮校正 4 个非杠杆变量，pain 供显著性）
  → 显著性 → 消息入库（语义 emb 可选）→ 结束词元归档 → 全量快照

契约：agents 对象只需实现 call(system, user, temperature=..., require_json=...)。
杠杆增量只来自词典三档粗分级——extractor 的 tier 只用于显著性/情绪标签，防重复计数。
"""
import copy
from collections.abc import Mapping
from dataclasses import replace
from types import SimpleNamespace
import hashlib
import logging
import json
import random
import threading
import time as _time

from agents.extractor import build_extractor_messages, parse_extractor
from agents.labor import build_labor_messages, parse_labor
from agents.ledger import build_ledger_messages, parse_ledger
from agents.preference import build_preference_messages, parse_preference
from agents.responder import (clean_reply, parse_responder,
                              validate_recall_expression)
from core.drives import calculate_drives
from core.params import (DEFAULTS, SALIENCE, MEMORY, FITTING, LEDGER,
                         CADENCE, FOLLOWUP, ACTOR)
from core.salience import compute_salience_parts
from core.schema_matrix import SchemaMatrix
from ops.breakpoints import behavior_signature, record_breakpoint
from ops.embeddings import BigramEmbedder
from interaction_extensions.preference_calibration.cx_six_seven.fitting import (
    PROTOTYPES,
    build_refine_messages,
    coarse_fit,
    parse_refine,
    update_baseline,
)
from interaction_extensions.preference_calibration.cx_six_seven.fit_bus import (
    emit_concentration_token,
    fit_row,
)
from interaction_extensions.preference_calibration.feedback import (
    attribute_feedback,
    expression_feedback_context,
    normalize_cadence_feedback,
    normalize_feedback,
)
from ops.interaction_events import (action_receipt, make_action_id, make_turn_id,
                                    turn_opened, input_received, input_aborted,
                                    input_measurement_committed as input_measurement_receipt,
                                    semantic_projected,
                                    output_semantic_measurement_committed,
                                    reference_frame_resolved,
                                    npc_self_expressed, extractor_observed,
                                    extractor_applied, extractor_failed, user_feedback,
                                    cadence_feedback, feedback_interpreted,
                                    classify_output_commit_recovery,
                                    output_commit_prepared, output_state_committed,
                                    output_expression_anchor,
                                    interaction_residual_expression_anchor,
                                    memory_expression_anchor,
                                    event_expression_anchor,
                                    time_expression_anchor,
                                    output_state_recovery_skipped, npc_output_discharged)
from ops.length_mode import length_mode, memory_supplement
from ops.manifest import build_manifest, params_snapshot, sha256_text
from ops.memory import Memory
from ops.outcome_observer import DEFAULT_HORIZON_MS, observe_actions
from ops.ledger import Ledger
from ops.deny import adjudicate_denials, r_parts_history
from ops.persona_pack import parse_persona_blocks, assemble_persona_blocks
                                 # 模块保留供历史等价测试；runner 不再消费
from ops.response_pack import assemble_response_blocks
from ops.reward import compute_reward, personal_ranges
from ops.turn_recall import (commit_turn_recall, prepare_semantic_recall_pool,
                             selected_rag_receipts, recall_expression_reference,
                             recall_receipt_reference)
from ops.legacy_recall_compat import (select_compat_autonomous_recall,
                                      select_compat_received_recall)
from ops.memory_state import (append_memory_state_policy_version,
                              current_memory_state_policy,
                              derive_recall_state_measures,
                              derive_dynamic_recall_state_measures,
                              normalize_recall_state_policy,
                              prepare_recall_state_batch,
                              append_recall_policy_family_anchor,
                              current_recall_policy_family_anchor,
                              normalize_recall_policy_family)
from ops.memory_product_family import (
    append_explicit_memory_product_family_version,
    append_memory_product_family_anchor,
    current_memory_product_family_anchor,
    normalize_memory_product_family,
    validate_memory_product_family_transaction,
)
from ops.turn_prompt import recall_selection_summary
from ops.legacy_prompt_compat import (assemble_compat_autonomous_prompt,
                                      assemble_compat_received_prompt)
from ops.turn_signals import derive_turn_signals
from interaction_extensions.preference_calibration.cx_six_seven.tuner import (
    load_tuned_weights,
)
from ops.semantic_projection import blend_behavior_weights, coerce_projection
from ops.stream_draft import ReplyDraftExtractor
from ops.expression_attention import compile_expression_attention
from core.tokens.expression import compile_expression_tokens
from core.tokens.expression_emission import (
    commit_expression_emission_plan,
    compile_expression_emission_plan,
)
from ops.expression_delivery import compile_expression_delivery
from interaction_extensions.preference_calibration.cx_six_seven.fit_expression_projection import (
    compile_fit_expression_projection,
)
from interaction_extensions.preference_calibration.cx_six_seven.form_projection import (
    compile_form_projection,
)
from interaction_extensions.preference_calibration.cx_six_seven.recall_projection import (
    compile_recall_projection,
)
from ops.recall_query import assemble_recall_query_tokens
from interaction_extensions.preference_calibration.cx_six_seven.cadence_projection import (
    cadence_projection,
)
from ops.scheduler_context import build_scheduler_context
from ops.legacy_runner_ingress import LegacyRunnerIngressMixin
from ops.peer_response import peer_response_eligibility
from interaction_extensions.preference_calibration.feedback_intent import (
    coerce_feedback_intent,
)
from ops.telemetry import build_turn_telemetry
from ops.fixture_provenance import is_non_dialogue_seed, is_synthetic_fixture
from ops.agent_usage import bind_usage_sink
from ops.wall_clock import WallClockPlanner
from temporal.bus import new_state, on_message, advance, update_hidden
from temporal.delay import decide_delay_seconds
from temporal.output_time import (advance_to_output_time, commit_output_vector,
                                  mark_output_state_committed, output_state_transition,
                                  resolve_output_timeline)
from ops.delivery_planning import plan_reply_delivery
from temporal.proactive import proactive_params, replay_tick_lifecycle
from temporal.opening import opening_anchor, opening_margin
from temporal.schedule import plan_next_wake
from core.interaction_geometry import (OPENING_AUTHORITY_V5,
                                       SCHEDULE_VECTOR_AUTHORITY_V2,
                                       opening_authority_receipt)

class Runner(LegacyRunnerIngressMixin):
    # Actor 只据此选择性订阅无正文的输入侧展示快照；它不是业务能力开关。
    presentation_preview_supported = True
    # 与 ``presentation_preview_supported`` 一样，这只是 Actor 选择性订阅的能力
    # 标记。草稿从不进入 Runner 的消息、状态或 receipt 路径。
    stream_draft_supported = True
    # Actor 只有在 Runner 明确声明了提交前 gate 时才可以撤销主回复；第三方
    # Runner/兼容包装器保持“串行后提交”的旧语义，不能被 UI 误判为安全可抢占。
    main_reply_preemption_supported = True

    def __init__(self, store, labor_agent=None, extractor_agent=None,
                 responder_agent=None, preference_agent=None, embed_fn=None,
                 rng=None, memory_params=None, seed=None, prompt_budget_chars=None,
                 reply_delay=False, ledger_agent=None, ledger_every=None,
                 async_extractor=False, recall_strategy="unified",
                 outcome_horizon_ms=DEFAULT_HORIZON_MS, semantic_projector=None,
                 feedback_interpreter=None, semantic_recaller=None,
                 semantic_recall_config=None, expression_attention=True,
                 responder_core_prompt=None, response_pack_enabled=False,
                 auto_preference_refine=False, math_trace_enabled=False,
                 cadence_params=None, memory_state_policy=None,
                 memory_state_policy_family=None, memory_product_family=None,
                 memory_product_stage_producer=None,
                 model_shadow_runtime=None, formal_model_runtime=None,
                 formal_temporal_runtime=None, formal_runtime_variant=None):
        self.store = store
        # F2 仅绑定影子模型身份；旧公式仍拥有唯一状态／发送权。宿主必须先经
        # registry 绑定显式 session ID，Runner 不从角色目录或 conversation 猜。
        if model_shadow_runtime is not None:
            from framework.model_runtime import BoundInteractionModel
            if not isinstance(model_shadow_runtime, BoundInteractionModel):
                raise ValueError("model binding requires a registry-bound runtime")
        self._model_shadow_runtime = model_shadow_runtime
        # Alpha 0.4 正式 runtime 必须由 application composition root 成对注入。
        # Runner 不选择包、不从旧 profile/constants/state 猜初始化 payload；没有
        # v3 anchor 时仅保留依赖，直到宿主显式调用 bootstrap 方法。
        formal_values = (
            formal_model_runtime,
            formal_temporal_runtime,
            formal_runtime_variant,
        )
        if any(value is not None for value in formal_values) and not all(
                value is not None for value in formal_values):
            raise ValueError(
                "formal model, temporal, and variant bindings must be provided together")
        if formal_model_runtime is not None:
            from framework.model_runtime import BoundInteractionModel
            from framework.runtime_variant import RuntimeVariantBinding
            from framework.temporal_runtime import BoundTemporalDomain
            if not isinstance(formal_model_runtime, BoundInteractionModel):
                raise ValueError(
                    "formal_model_runtime must be registry-bound")
            if not isinstance(formal_temporal_runtime, BoundTemporalDomain):
                raise ValueError(
                    "formal_temporal_runtime must be registry-bound")
            if not isinstance(formal_runtime_variant, RuntimeVariantBinding):
                raise ValueError(
                    "formal_runtime_variant must be registry-bound")
            formal_runtime_variant.verify_for(
                model_binding=formal_model_runtime.binding,
                temporal_binding=formal_temporal_runtime.binding,
            )
        self._formal_model_runtime = formal_model_runtime
        self._formal_temporal_runtime = formal_temporal_runtime
        self._formal_runtime_variant = formal_runtime_variant
        self.formal_runtime_session = None
        # AUD-125：每次 writer 调用的输出归属只来自显式绑定的 Participant；绝不由
        # store ID、persona prose、会话标题或输入 sender 猜测。
        self._output_identity = None
        self.labor = labor_agent
        self.extractor = extractor_agent
        self.responder = responder_agent
        self.preference = preference_agent or labor_agent
        self.embed_fn = embed_fn if embed_fn is not None else BigramEmbedder()
        # ``seed`` 既然写进 manifest，就必须真正初始化本地随机路径；显式传入 rng
        # 仍拥有最高优先级，以保留测试夹具/宿主对随机流的完全控制。
        self.rng = rng if rng is not None else random.Random(seed)
        self.seed = seed
        self.prompt_budget_chars = prompt_budget_chars
        self.reply_delay = reply_delay
        # 提取 Agent 真异步（承 MANE G6e 思路的延迟优化）：它本来就是"下轮校正"
        # 的延迟一轮设计，同步跑 = 回复延迟被它串行叠加（实测 25~55s）。异步后
        # 回复延迟只剩 responder 一次调用；引擎默认 False（等价锁确定性），
        # 产品入口传 True。校正结果在锁内读清，未完成则该轮跳过（可延迟语义）。
        self.async_extractor = async_extractor
        # 可替换的同回合语义投影器。None = 逐值保留词典 fallback；该对象只产数据，
        # 不读 Store、不推进总线、不直接生成回复。
        self.semantic_projector = semantic_projector
        # 候选重排不是输入投影的隐式副作用：只有两个对象都被入口显式提供时才启用。
        # 配置由测试/产品入口版本化，避免在 Runner 内写业务阈值。
        self.semantic_recaller = semantic_recaller
        self.semantic_recall_config = self._validate_semantic_recall_config(
            semantic_recall_config, semantic_recaller)
        # 独立于通用语义投影：它只给出已确认的反馈标签或 None，不读 Store、不改状态。
        self.feedback_interpreter = feedback_interpreter
        # E0.1 是 TIDE 的默认输出适配：它不改变状态、召回或持久化，只阻止 MANE
        # 双角色行动词元被原样误读为 NPC 对用户的指令。False 仅用于冻结旧路径 A/B。
        self.expression_attention = bool(expression_attention)
        # 仅用于显式 legacy prompt A/B；正式 expression writer 默认不得加载旧的
        # 固定响应卡。它不参与总线、记忆和持久状态。
        self.responder_core_prompt = responder_core_prompt
        self.response_pack_enabled = bool(response_pack_enabled)
        # 自动精修只产生 B0 候选，但仍是额外模型调用；默认运行时关闭，
        # 标定任务可显式开启，不把“每五轮”伪装成主链路刚需。
        self.auto_preference_refine = bool(auto_preference_refine)
        # R0.5 观测开关：只控制提交后无正文 math span 的落盘，绝不改变本轮
        # 状态、计划、随机流、提示词或模型调用。默认关闭，待等价测试收口后再由
        # 产品入口显式启用，而不是把日志设施伪装成新的算法开关。
        self.math_trace_enabled = bool(math_trace_enabled)
        # 仅供受控回放/试验为某个 Runner 注入一份节律参数副本。正常产品入口不传
        # 此参数，仍逐位读取全局 CADENCE；绝不在这里改写全局 dict。这样双 Runner
        # 才能在同一进程分别复现 baseline/shadow/active，而不会互相污染。
        self.cadence_params = self._coerce_cadence_params(cadence_params)
        # P4 必须由会话/受控夹具显式提供版本化政策；没有默认半衰期、容量或轨迹
        # 常数。此处只保存不可变副本，真正校验/消费留给状态桥，避免构造 Runner
        # 时把未使用的候选公式误称为已启用。
        if memory_state_policy is not None and not isinstance(memory_state_policy, dict):
            raise ValueError("memory_state_policy 必须是对象或 None")
        if (memory_state_policy is not None
                and memory_state_policy_family is not None):
            raise ValueError("静态 memory_state_policy 与 Alpha 0.3 policy family 不能同时注入")
        # 注入值不是运行期可写配置：只有 setup 显式绑定、或重启读到已绑定版本后，
        # 才会成为本会话可消费的 policy。保留副本只是为了新会话创建事务，不让调用方
        # 之后改 dict 影响会话。
        self._injected_memory_state_policy = copy.deepcopy(memory_state_policy)
        self.memory_state_policy = None
        self.memory_state_policy_binding_version = None
        # Alpha 0.3 策略族是新会话身份，不是旧 P4 v1 的静态数值 policy。具体
        # dynamic readout 尚未定标前，它不启用状态桥、更不改变 RAG 或开口。
        if memory_state_policy_family is not None and not isinstance(memory_state_policy_family, dict):
            raise ValueError("memory_state_policy_family 必须是对象或 None")
        self._injected_memory_state_policy_family = copy.deepcopy(memory_state_policy_family)
        self.memory_state_policy_family_anchor = None
        # Alpha 0.4 family 只是 C1--C5/D1 合同身份。未绑定会话保持 family-off；
        # 这里不启用记忆公式、不创建候选，也不触碰 tick、writer 或 UI。
        if memory_product_family is not None and not isinstance(
                memory_product_family, dict):
            raise ValueError("memory_product_family 必须是对象或 None")
        self._injected_memory_product_family = copy.deepcopy(memory_product_family)
        self.memory_product_family_anchor = None
        # D3c 只注入一个已版本化的 C1--C5 生产器；Runner 不拥有公式参数，
        # 不会把 Alpha 0.3 recall receipt 强转为 Alpha 0.4 DTO。缺失时正式入口闭锁。
        self._memory_product_stage_producer = memory_product_stage_producer
        self._memory_product_stage_producer_identity = None
        if memory_product_stage_producer is not None:
            from ops.memory_product_runtime import (
                memory_product_stage_producer_identity,
            )
            self._memory_product_stage_producer_identity = (
                memory_product_stage_producer_identity(
                    memory_product_stage_producer))
        if recall_strategy not in {"legacy", "unified", "unified_evidence_v2",
                                   "unified_evidence_additive_v1",
                                   "unified_suppressed"}:
            raise ValueError("recall_strategy 无效")
        # P3 feature flag：默认保留历史多通道路径；统一候选必须显式开启。
        self.recall_strategy = recall_strategy
        self.outcome_horizon_ms = max(0, int(outcome_horizon_ms))
        self._ext_lock = threading.Lock()
        self.ledger_agent = ledger_agent
        self.ledger_every = ledger_every if ledger_every is not None else LEDGER["every"]
        self.ledger = Ledger(store, rng=self.rng)
        self.ledger_pending = []
        # 挂起释放锁：GUI 主线程（揭示回调）与轮询线程可能同时 release_pending，
        # 并发下必须"先摘牌再落盘"，否则同一条回复双写（真实会话已实证双份）
        self._release_lock = threading.Lock()
        self.memory = Memory(store, params=memory_params or dict(MEMORY), rng=self.rng)
        self.wall_clock = WallClockPlanner(store)
        self._wire_usage_sinks()
        self.state = store.load_state() or new_state()
        self.last_ranked = None
        self.last_expression_attention = None
        # 不是新的记忆状态：只缓存本轮已装配的事件卡，供紧随其后的追发继承，
        # 防止它另起一次查询而跳到无关旧事。
        self.last_recall_expression_events = ()
        self.profile = store.load_profile()
        # 重启必须在 B0/schema migration 或任何模型阶段调用前核对冻结版本。
        from ops.model_session_binding import validate_shadow_model_binding
        self.model_shadow_binding = validate_shadow_model_binding(
            self.profile, runtime=self._model_shadow_runtime)
        profile_identity = (self.profile or {}).get("product_participant_identity")
        if isinstance(profile_identity, Mapping):
            self.bind_output_identity(profile_identity)
        # B₀ schema migration：只能读取旧档已经持久化的“明确声明且已成功保存”事实；
        # 不能从裸 prior、沉默、行为、LLM 或 drift 猜测启用。它只补不可变版本锚，
        # 尚不让任何实时调度路径读取 C_effective。
        if self.profile:
            from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
                current_b0,
                migrate_legacy_b0_binding,
            )
            migration_now = int(_time.time() * 1000)
            migrated_profile, did_migrate = migrate_legacy_b0_binding(
                self.profile, at_ms=migration_now)
            if did_migrate:
                binding = current_b0(migrated_profile)
                self.store.save_profile(migrated_profile)
                self.store.append_event({
                    "kind": "b0_schema_migration", "ts": migration_now,
                    "detail": {"to_version": binding["version"],
                               "enabled": bool(binding["enabled"]),
                               "source": binding["source"]},
                })
            self.profile = migrated_profile
        if (
            self.profile
            and "runtime_session_binding" in self.profile
            and self._formal_model_runtime is not None
        ):
            from ops.runtime_session_bootstrap import (
                restore_bootstrapped_runtime_session,
            )
            self.formal_runtime_session = restore_bootstrapped_runtime_session(
                profile=self.profile,
                model_runtime=self._formal_model_runtime,
                temporal_runtime=self._formal_temporal_runtime,
                runtime_variant=self._formal_runtime_variant,
            )
            from framework.history import (
                FRAMEWORK_HISTORY_PROFILE_KEY,
                HistoryAuthority,
            )
            stored_history = self.profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
            if stored_history is not None:
                history = HistoryAuthority.from_record(stored_history)
                if history.session_binding_fingerprint != (
                    self.formal_runtime_session.session_binding.fingerprint
                ):
                    raise ValueError("framework history binding is invalid")
            if "runtime_full_memory_lineage" in self.profile:
                from ops.runtime_full_memory_profile_commit import (
                    read_runtime_full_memory_node_journal,
                    read_runtime_full_memory_lineage_journal,
                )
                read_runtime_full_memory_lineage_journal(
                    self.profile,
                    session_binding_fingerprint=(
                        self.formal_runtime_session.session_binding.fingerprint),
                )
                read_runtime_full_memory_node_journal(
                    self.profile,
                    session_binding_fingerprint=(
                        self.formal_runtime_session.session_binding.fingerprint),
                )
        # P4 policy 和 B₀ 一样属于会话身份，而非 Runner 进程参数。已有绑定只能读取；
        # 宿主若同时传入不同 policy，必须显式迁移而不是静默把历史回放改成另一套公式。
        policy_binding = current_memory_state_policy(self.profile) if self.profile else None
        if policy_binding is not None:
            bound_policy = dict(policy_binding["policy"])
            if self._injected_memory_state_policy is not None:
                injected_policy = normalize_recall_state_policy(
                    self._injected_memory_state_policy)
                if injected_policy != bound_policy:
                    raise ValueError("已绑定的 memory_state_policy 与外部注入不一致")
            self.memory_state_policy = bound_policy
            self.memory_state_policy_binding_version = str(policy_binding["version"])
        family_anchor = current_recall_policy_family_anchor(self.profile) if self.profile else None
        if policy_binding is not None and family_anchor is not None:
            raise ValueError("同一会话不能同时绑定静态 P4 policy 与 Alpha 0.3 policy family")
        if family_anchor is not None:
            if self._injected_memory_state_policy_family is not None:
                injected_family = normalize_recall_policy_family(
                    self._injected_memory_state_policy_family)
                if injected_family != family_anchor["family"]:
                    raise ValueError("已绑定的 memory_state_policy_family 与外部注入不一致")
            self.memory_state_policy_family_anchor = dict(family_anchor)
        product_family_anchor = (
            current_memory_product_family_anchor(self.profile)
            if self.profile else None)
        if product_family_anchor is not None:
            if self._injected_memory_product_family is not None:
                injected_product_family = normalize_memory_product_family(
                    self._injected_memory_product_family)
                if injected_product_family != product_family_anchor["family"]:
                    raise ValueError(
                        "已绑定的 memory_product_family 与外部注入不一致")
            self.memory_product_family_anchor = dict(product_family_anchor)
        self.recovered_memory_product_commit = (
            self._recover_memory_product_root_commit())
        self.prior = self.profile.get("prior") if self.profile else None
        self.persona_blocks = parse_persona_blocks(store.load_persona_card() or "")
        self.weights_prev = None
        # extractor 是下一回合才消费的测量，必须与墙钟状态一起恢复；旧状态没有该字段
        # 时自然降级为 None，保持向后兼容。
        pending = self.state.get("pending_extraction")
        self.pending_extraction = pending if isinstance(pending, dict) else None
        self.baseline = {}
        self.turn = 0
        self._followup_threads = []   # 追发链线程（测试 join 用）
        # 用户卡 v1（T6）：散装用户侧状态的统一容器（与人格卡对称）——
        # 基线重启持久化 + prior 双写；禁手 = 跨对聚合（无任何跨 store 函数）
        from interaction_extensions.preference_calibration.cx_six_seven.user_card import (
            load as _load_card,
            new_card,
            sync_from_profile,
        )
        self.user_card = _load_card(store)
        if self.user_card is None:
            self.user_card = new_card()
        self.user_card = sync_from_profile(self.user_card, self.profile)
        if self.user_card.get("baseline"):
            self.baseline = dict(self.user_card["baseline"])   # 重启恢复基线
        # A1/B1/B3：消息落盘统一入口的版本与归因状态
        snapshot = params_snapshot()
        if self.cadence_params is not None:
            snapshot = dict(snapshot, runner_cadence_override=self.cadence_params)
        self.params_sha = sha256_text(snapshot)
        self._npc_actions = []        # 未归因的 NPC 动作（下轮用户回合的 R 认领它们）
        # P1 因果链：序号由 append-only 历史恢复，避免重启后 ID 碰撞；Actor 已保证
        # 同一角色事务串行，因此无需额外并发锁。
        # S0.4：若上次进程在 NPC message 与 action receipt 两个 append 之间中断，
        # 先以消息中的 action_id 幂等补齐最小归因收据，再恢复序号；绝不猜测或重放
        # 输出侧状态回流，缺证据的后续副作用保持未结算。
        self.recovered_output_receipts = store.recover_npc_action_receipts()
        # T0.1：收据可证明“已经说出口”后，才允许重放其已验证输出向量。prepared
        # 载荷本身不是发送事实，故不会令未发送草稿改变状态。
        self.recovered_output_state_commits = self._recover_output_state_commits()
        self._turn_sequence = sum(1 for m in store.read_messages()
                                  if m.get("role") == "user")
        self._action_sequence = self._restore_action_sequence()
        self._drift_state = None      # 偏移卡状态机（期②-3，纯函数持有）
        # 用户线观察（T7-3）：上次用户消息时刻 + 已观测沉默带（跨带才记账）
        self._last_user_ts = None
        self._last_gap_band = -1

    @staticmethod
    def _coerce_cadence_params(value):
        """构造一份私有 cadence 参数副本，不允许试验修改模块级默认值。"""
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("cadence_params 必须是对象或 None")
        unknown = set(value) - set(CADENCE)
        if unknown:
            raise ValueError("cadence_params 含未知字段：" + ",".join(sorted(unknown)))
        merged = copy.deepcopy(CADENCE)
        for key, raw in value.items():
            if key == "control_directions":
                if not isinstance(raw, dict):
                    raise ValueError("cadence_params.control_directions 必须是对象")
                merged[key] = copy.deepcopy(raw)
            else:
                merged[key] = copy.deepcopy(raw)
        # projection 会消费这些边界；在构造阶段失败比在墙钟 worker 内半途失败可审计。
        if not isinstance(merged.get("enabled"), bool):
            raise ValueError("cadence_params.enabled 必须是布尔值")
        if not isinstance(merged.get("version"), str) or not merged["version"]:
            raise ValueError("cadence_params.version 必须是非空字符串")
        return merged

    def _restore_action_sequence(self):
        """从所有已分配 action 事实恢复序号，prepared 空洞也绝不复用。"""
        ids = []
        for row in self.store.read_messages():
            ids.append(row.get("action_id"))
        for row in self.store.read_action_receipts():
            ids.append(row.get("action_id"))
        for row in self.store.read_events():
            if row.get("kind") in ("output_commit_prepared", "output_state_committed"):
                ids.append(row.get("action_id"))
        highest = 0
        for action_id in ids:
            parts = str(action_id or "").rsplit(":", 1)
            if len(parts) != 2:
                continue
            try:
                highest = max(highest, int(parts[1]))
            except ValueError:
                continue
        return highest

    def _recover_memory_product_root_commit(self):
        """Complete one durable Alpha 0.4 state-first marker on restart."""
        marker = self.state.get("_pending_memory_product_commit")
        if marker is None:
            return {"status": "no_pending"}
        from ops.memory_product_transaction import (
            MemoryProductTransaction,
            MemoryProductTransactionJournal,
        )

        transaction = MemoryProductTransaction.from_record(marker)
        anchor = self.memory_product_family_anchor
        if not isinstance(anchor, Mapping):
            raise RuntimeError(
                "pending Alpha 0.4 memory product has no bound family")
        if transaction.family_binding_version != str(anchor.get("version") or ""):
            raise ValueError("pending memory product family binding does not match session")
        validate_memory_product_family_transaction(
            anchor.get("family") or {}, transaction)
        identity = self._writer_output_identity()
        if identity is None or transaction.owner_participant_id != identity[
                "participant_id"]:
            raise ValueError("pending memory product owner does not match Runner identity")
        result = MemoryProductTransactionJournal(
            self.store).recover_pending_root_commit()
        self.state = self.store.load_state() or self.state
        return result

    @staticmethod
    def _is_preference_evidence(record):
        """兼容旧 human 历史，同时拒绝 peer 被 ``role=user`` 的遗留形状污染。

        ``role`` 仍服务本地对话显示和既有记忆索引；是否能观察真实用户偏好只能由
        committed received-event 的显式 policy 决定，不能从正文、source 字符串或 UI 猜。
        """
        return (isinstance(record, dict) and record.get("role") == "user"
                and bool(record.get("preference_evidence", True)))

    def _recover_output_state_commits(self):
        """重启时只补已发送且已收据化、但缺 state watermark 的输出时间提交。"""
        payloads = [row for row in self.store.read_events()
                    if row.get("kind") == "output_commit_prepared"]
        if not payloads:
            return []
        messages = [row.get("action_id") for row in self.store.read_messages()
                    if row.get("role") == "npc"]
        receipts = [row.get("action_id") for row in self.store.read_action_receipts()]
        state_ids = [row.get("action_id") for row in self.store.read_events()
                     if row.get("kind") == "output_state_committed"]
        semantic_by_action = {
            str(row.get("action_id")): row for row in self.store.read_events()
            if row.get("kind") == "output_semantic_measurement_committed"
            and row.get("action_id")
        }
        recall_ids = {str(row.get("action_id")) for row in self.store.read_events()
                      if row.get("kind") == "output_recall_committed"}
        skipped_ids = {str(row.get("action_id")) for row in self.store.read_events()
                       if row.get("kind") == "output_state_recovery_skipped"
                       and row.get("action_id")}
        marker = self.state.get("last_output_state_action_id")
        if marker is not None:
            state_ids.append(marker)
        constants = (self.profile or {}).get("constants") or {}
        recovered = []
        for payload in payloads:
            action_id = str(payload.get("action_id") or "")
            if action_id in skipped_ids:
                continue
            status = classify_output_commit_recovery(
                payload, message_action_ids=messages, receipt_action_ids=receipts,
                state_action_ids=state_ids)
            if status == "complete":
                semantic_row = semantic_by_action.get(action_id)
                recovered_payload = dict(payload)
                if semantic_row is not None:
                    # prepared 只保存 F16 前的时间基线；已存在的 output semantic
                    # receipt 才是恢复 P4/状态事实的权威，不可倒回旧 e_out。
                    recovered_payload["memory_state_receipt"] = semantic_row.get(
                        "memory_state_receipt")
                    recovered_payload["memory_delta"] = semantic_row.get("memory_delta")
                # state 已落盘但进程可能在 recall plan 落账前中断；这份 plan 是
                # prepared 时冻结的，补写不涉及检索或发送。
                if recovered_payload.get("memory_state_receipt") is not None:
                    self._record_output_memory_state_batch(
                        recovered_payload, ts=int((payload.get("timeline") or {})["t_sent"]),
                        recovery=True)
                if action_id not in recall_ids:
                    self._commit_output_recall_plan(
                        payload, now_ms=int((payload.get("timeline") or {})["t_sent"]))
                # 若进程恰在 output_state_committed 后中断，formal emission 的 F16
                # receipt 仍须补齐；helper 以 action 事件主键幂等，不会重扣配额。
                self._commit_expression_emission_after_f16(
                    self.state, output_commit=payload, action_id=action_id,
                    ts=int((payload.get("timeline") or {})["t_sent"]))
                continue
            if status != "sent_state_replay":
                continue
            semantic_row = semantic_by_action.get(action_id)
            # 新合同不能在恢复时重新请求模型或从 writer/self/e_out 猜输出语义。
            # F16 已存在但进程恰在 semantic receipt 前中断时，保持消息事实、明确
            # 隔离 state replay，待诊断而非偷写一份根量。
            if bool(payload.get("output_semantic_required")) and semantic_row is None:
                self.store.append_event({
                    "schema_version": 1, "kind": "output_semantic_recovery_pending",
                    "action_id": action_id,
                    "ts": int((payload.get("timeline") or {}).get("t_sent") or 0),
                    "reason": "f16_present_semantic_measurement_missing",
                    "params_sha": self.params_sha,
                })
                continue
            timeline = payload.get("timeline") or {}
            sent_ms = int(timeline["t_sent"])
            anchor_ms = self.state.get("last_advance_ms", self.state.get("last_ts"))
            # 历史版本曾允许 followup 在主回复视觉释放前入库。此时较早的主回复
            # 只有发送事实，却已不可能安全回放到单调状态锚；隔离其状态回流，绝不
            # 删除原消息，也不让一个坏会话阻断整个登录。
            if anchor_ms is not None and sent_ms < int(anchor_ms):
                self.store.append_event(output_state_recovery_skipped(
                    action_id=action_id, ts=sent_ms, state_anchor_ms=int(anchor_ms)))
                skipped_ids.add(action_id)
                continue
            time_binding = self._output_time_binding(payload)
            semantic_deltas = ((semantic_row or {}).get("delta_x")
                               if semantic_row is not None else payload.get("applied_deltas")) or {}
            memory_batch = ((semantic_row or {}).get("memory_state_receipt")
                            if semantic_row is not None else payload.get("memory_state_receipt"))
            memory_delta = ((semantic_row or {}).get("memory_delta")
                            if semantic_row is not None else payload.get("memory_delta"))
            if memory_batch is not None:
                restored, _receipt = output_state_transition(
                    self.state, constants, timeline=timeline,
                    deltas=semantic_deltas,
                    effective_constants=time_binding["effective_constants"],
                    effective_constants_version=time_binding["effective_constants_version"],
                    f06_constants=time_binding["effective_constants"],
                    memory_delta=memory_delta,
                    memory_state_receipt=memory_batch)
            else:
                restored, _receipt = commit_output_vector(
                    self.state, constants, sent_ms=sent_ms,
                    deltas=semantic_deltas,
                    effective_constants=time_binding["effective_constants"],
                    effective_constants_version=time_binding["effective_constants_version"],
                    f06_constants=time_binding["effective_constants"])
            mark_output_state_committed(restored, action_id)
            self.state = restored
            self.store.save_state(restored)
            expression_anchor = self._expression_anchor_from_output_commit(
                payload, state=restored, action_id=action_id, sent_ms=sent_ms)
            recovered_turn_id = str(payload.get("turn_id") or "")
            input_anchor = (
                self._committed_event_expression_anchor_for_turn(
                    recovered_turn_id, before_or_at_ms=sent_ms)
                if str(payload.get("source") or "") == "reply" and recovered_turn_id
                else None)
            interaction_anchor = interaction_residual_expression_anchor(
                action_id=action_id, turn_id=recovered_turn_id, sent_ms=sent_ms,
                event_anchor=input_anchor, output_anchor=expression_anchor)
            self.store.append_event(output_state_committed(
                action_id=action_id, ts=sent_ms, recovery=True,
                expression_anchor=expression_anchor,
                interaction_residual_anchor=interaction_anchor))
            if memory_batch is not None:
                self._record_output_memory_state_batch(
                    payload, ts=sent_ms, recovery=True)
            # recall 的事实资格来自 F16 已送达，而不是 P4 是否启用。没有
            # memory batch 的旧会话也必须在恢复时消费 prepared 内同一冻结 plan。
            self._commit_output_recall_plan(payload, now_ms=sent_ms)
            self._commit_expression_emission_after_f16(
                self.state, output_commit=payload, action_id=action_id, ts=sent_ms)
            state_ids.append(action_id)
            recovered.append(action_id)
        return recovered

    def _effective_time_binding(self):
        """只读解析本会话的 F08 常量版本；绝不从 prior/state/UI 回退。"""
        if not self.profile:
            raise RuntimeError("F08 结算前必须先完成角色 setup")
        constants = self.profile.get("constants") or {}
        from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
            resolve_session_effective_constants,
        )
        return resolve_session_effective_constants(constants, self.profile)

    def _output_time_binding(self, payload):
        """读取 output prepared 时冻结的 C_effective；旧事务才保守回退当前绑定。

        F16 的表达锚与状态回放必须使用当时参与 output transition 的同一份 C。旧
        prepared 没有 binding 时不伪造 expression anchor，但保留原有恢复兼容行为。
        """
        binding = (payload or {}).get("expression_binding")
        if not isinstance(binding, dict):
            return self._effective_time_binding()
        constants = binding.get("effective_constants")
        version = str(binding.get("effective_constants_version") or "").strip()
        from core.effective_constants import CONSTANT_ORDER
        if not version or not isinstance(constants, dict) or set(constants) != set(CONSTANT_ORDER):
            raise ValueError("output prepared 的 C_effective 表现绑定无效")
        normalized = {axis: float(constants[axis]) for axis in CONSTANT_ORDER}
        return {
            "effective_constants": normalized,
            "effective_constants_version": version,
            "source": "output_prepared_expression_binding",
        }

    @staticmethod
    def _expression_anchor_from_output_commit(payload, *, state, action_id, sent_ms):
        """只从已冻结 F16 binding 和已提交后的 X 建立表现锚。"""
        return output_expression_anchor(
            action_id=action_id, sent_ms=sent_ms, state=state,
            expression_binding=(payload or {}).get("expression_binding"))

    @staticmethod
    def _memory_expression_anchor_from_transition(*, memory_transition, time_binding,
                                                  committed_ms, event_kind):
        """把已结算 P4 transition 连同其冻结 C 封装为表现只读锚。

        调用方仍负责保证它只被追加到已经完成状态提交的事件中；这个 helper 不读
        recall candidate、正文、UI 或当前 profile，也不替 P4 计算任何数值。
        """
        binding = {
            "version": "expression-root-binding-v1",
            "effective_constants_version": time_binding["effective_constants_version"],
            "effective_constants": time_binding["effective_constants"],
        }
        return memory_expression_anchor(
            committed_ms=int(committed_ms), event_kind=str(event_kind),
            memory_transition=memory_transition, expression_binding=binding)

    @staticmethod
    def _event_expression_anchor_from_transition(*, event_transition, time_binding,
                                                 event_ms, event_kind):
        """将 F03 已结算外部事件封装为表现只读锚。

        这是从 temporal bus receipt 到 writer 的单向投影，不重新测量语义、不读正文，
        也不执行一次额外状态写入。外部输入在 responder 前已提交，因此它可作为其
        直接因果回复的材料；NPC 输出则没有这条同轮回灌通路。
        """
        binding = {
            "version": "expression-root-binding-v1",
            "effective_constants_version": time_binding["effective_constants_version"],
            "effective_constants": time_binding["effective_constants"],
        }
        return event_expression_anchor(
            event_ms=int(event_ms), event_kind=str(event_kind),
            event_transition=event_transition, expression_binding=binding)

    @staticmethod
    def _time_expression_anchor_from_f08_events(*, events, time_binding,
                                                settled_ms, event_kind):
        """从一次真实 F08 receipt 提取表现只读时间锚。

        ``events`` 必须来自刚刚执行的正式 ``advance``；本 helper 不会自行推进
        墙钟或根据 UI/模型耗时制造 transition。调用方仍须把同一 state 真正提交后
        才可持久化它。
        """
        transition = None
        for row in reversed(list(events or ())):
            if not isinstance(row, dict) or row.get("rule") != "root_time_advanced":
                continue
            candidate = row.get("time_transition")
            if isinstance(candidate, dict):
                transition = candidate
                break
        if transition is None:
            return None
        binding = {
            "version": "expression-root-binding-v1",
            "effective_constants_version": time_binding["effective_constants_version"],
            "effective_constants": time_binding["effective_constants"],
        }
        return time_expression_anchor(
            settled_ms=int(settled_ms), event_kind=str(event_kind),
            time_transition=transition, expression_binding=binding)

    def _latest_committed_expression_anchor(self, *, now_ms=None):
        """返回最后一条已完成输出的表现锚；没有完整 F16 事实即保持空白。"""
        latest = None
        for row in self.store.read_events():
            if row.get("kind") != "output_state_committed":
                continue
            anchor = row.get("expression_anchor")
            if not isinstance(anchor, dict):
                continue
            try:
                sent_ms = int(anchor.get("sent_ms"))
            except (TypeError, ValueError):
                continue
            if now_ms is not None and sent_ms > int(now_ms):
                continue
            if latest is None or sent_ms >= int(latest.get("sent_ms", -1)):
                latest = copy.deepcopy(anchor)
        return latest

    def _committed_interaction_residual_anchor_for_output(self, output_anchor, *, now_ms=None):
        """按 F16 action 精确读取其已冻结的互动残量锚。

        这不是“最近事件”查找：只能读取与当前 output anchor 同 action、同发送时刻
        的事实。这样主动 writer 不会因时间相邻而把另一轮用户输入重新解释成承接。
        """
        if not isinstance(output_anchor, dict):
            return None
        action_id = str(output_anchor.get("action_id") or "")
        try:
            sent_ms = int(output_anchor.get("sent_ms"))
        except (TypeError, ValueError):
            return None
        if not action_id or (now_ms is not None and sent_ms > int(now_ms)):
            return None
        # 残量只能描述这条 F03→F16 已完成互动的后效。若 F16 之后已有新的
        # 已提交外部输入，当前互动上下文已经换代；不能按“最近 output”把旧残量
        # 偷渡进下一次 writer。这里按正式 F03 的逻辑时间判断，不读正文，也不碰
        # P3/tick/状态或记忆选择。
        for event in self.store.read_events():
            if event.get("kind") != "input_measurement_committed":
                continue
            try:
                event_ms = int(event.get("ts"))
            except (TypeError, ValueError):
                continue
            if event_ms > sent_ms and (now_ms is None or event_ms <= int(now_ms)):
                return None
        for row in reversed(self.store.read_events()):
            if (row.get("kind") != "output_state_committed"
                    or str(row.get("action_id") or "") != action_id):
                continue
            anchor = row.get("interaction_residual_anchor")
            if not isinstance(anchor, dict):
                return None
            try:
                matches = (str(anchor.get("action_id") or "") == action_id
                           and int(anchor.get("sent_ms")) == sent_ms)
            except (TypeError, ValueError):
                return None
            return copy.deepcopy(anchor) if matches else None
        return None

    def _committed_event_expression_anchor_for_turn(self, turn_id, *, before_or_at_ms):
        """读出一条直接 F16 可精确归属的 F03 锚，不读取任何正文。"""
        identity = str(turn_id or "")
        if not identity:
            return None
        latest = None
        for row in self.store.read_events():
            if (row.get("kind") != "input_measurement_committed"
                    or str(row.get("turn_id") or "") != identity):
                continue
            anchor = row.get("expression_anchor")
            if not isinstance(anchor, dict):
                continue
            try:
                event_ms = int(anchor.get("event_ms"))
            except (TypeError, ValueError):
                continue
            if event_ms > int(before_or_at_ms):
                continue
            if latest is None or event_ms >= int(latest.get("event_ms", -1)):
                latest = copy.deepcopy(anchor)
        return latest

    def _latest_committed_memory_expression_anchor(self, *, now_ms=None,
                                                    after_output_ms=None,
                                                    before_event_ms=None):
        """读取最后一条已落盘、且尚未被后续输出吸收的 P4 表现锚。

        一条 NPC 输出完成后，它的 F16 表现锚已经包含此前 P4 影响；再次把同一
        memory delta 作为独立卡投递会重复强调旧事。因此只取严格晚于最后输出
        锚的已提交 P4 影响。这里不查询 RAG／archive，不把 batch candidate 当事实。
        """
        latest = None
        for row in self.store.read_events():
            if row.get("kind") != "memory_state_batch_committed":
                continue
            detail = row.get("detail") or {}
            anchor = detail.get("expression_anchor") if isinstance(detail, dict) else None
            if not isinstance(anchor, dict):
                continue
            try:
                committed_ms = int(anchor.get("committed_ms"))
            except (TypeError, ValueError):
                continue
            if now_ms is not None and committed_ms > int(now_ms):
                continue
            if after_output_ms is not None and committed_ms <= int(after_output_ms):
                continue
            # 当前 F03 里刚落盘的 P4 已经包含在本轮最终 X；直接回复只能将它
            # 作为根状态的正常结果看见，不能再塞一张“记忆余波”卡重复强调。
            if before_event_ms is not None and committed_ms >= int(before_event_ms):
                continue
            if latest is None or committed_ms >= int(latest.get("committed_ms", -1)):
                latest = copy.deepcopy(anchor)
        return latest

    def _latest_committed_event_expression_anchor(self, *, now_ms=None,
                                                   after_output_ms=None):
        """读取尚未被后续 F16 输出吸收的、已落盘外部事件表现锚。

        只读取 ``input_measurement_committed`` 的 F03 事实。若之后已有完整输出锚，
        当前 X 已经吸收该事件，不能把同一变化第二次作为卡包交给主动 writer；这
        与 P4 的 unabsorbed 规则平行，但不读取输入正文或 semantic measurement。
        """
        latest = None
        for row in self.store.read_events():
            if row.get("kind") != "input_measurement_committed":
                continue
            anchor = row.get("expression_anchor")
            if not isinstance(anchor, dict):
                continue
            try:
                event_ms = int(anchor.get("event_ms"))
            except (TypeError, ValueError):
                continue
            if now_ms is not None and event_ms > int(now_ms):
                continue
            if after_output_ms is not None and event_ms <= int(after_output_ms):
                continue
            if latest is None or event_ms >= int(latest.get("event_ms", -1)):
                latest = copy.deepcopy(anchor)
        return latest

    def _latest_committed_time_expression_anchor(self, *, now_ms=None,
                                                  after_output_ms=None):
        """读取尚未被后续 F16 输出吸收的正式 F08 表现锚。

        只接受 F03 提交附带的输入前 F08，或正式 online tick 的
        ``wall_clock_boundary_settled``。readonly preview 从不落盘，故天然不具备
        资格；若 F16 已发生，最终输出锚已经带有该时间推进后的根状态，不能重投。
        """
        latest = None
        for row in self.store.read_events():
            anchor = None
            if row.get("kind") == "input_measurement_committed":
                anchor = row.get("time_expression_anchor")
            elif row.get("kind") == "wall_clock_boundary_settled":
                detail = row.get("detail") or {}
                anchor = detail.get("time_expression_anchor") if isinstance(detail, dict) else None
            if not isinstance(anchor, dict):
                continue
            try:
                settled_ms = int(anchor.get("settled_ms"))
            except (TypeError, ValueError):
                continue
            if now_ms is not None and settled_ms > int(now_ms):
                continue
            if after_output_ms is not None and settled_ms <= int(after_output_ms):
                continue
            if latest is None or settled_ms >= int(latest.get("settled_ms", -1)):
                latest = copy.deepcopy(anchor)
        return latest

    def _advance_effective_time(self, state, now_ms, events):
        """Runner 全部正式 F08 入口的唯一适配器。"""
        binding = self._effective_time_binding()
        advance(state, now_ms, events=events,
                effective_constants=binding["effective_constants"],
                effective_constants_version=binding["effective_constants_version"])
        return binding

    def _wire_usage_sinks(self):
        """将调用收据绑定到本 Runner，不污染 Guild 共享的传输 client。"""
        def _sink(rec):
            try:
                self.store.append_llm_call(rec)
            except Exception as exc:
                logging.getLogger(__name__).error(
                    "usage_receipt_persistence_failed error_type=%s", type(exc).__name__)
        self.labor = bind_usage_sink(self.labor, _sink)
        self.extractor = bind_usage_sink(self.extractor, _sink)
        self.responder = bind_usage_sink(self.responder, _sink)
        self.preference = bind_usage_sink(self.preference, _sink)
        self.ledger_agent = bind_usage_sink(self.ledger_agent, _sink)

        def _bind_provider(provider):
            agent = getattr(provider, "agent", None)
            bound = bind_usage_sink(agent, _sink)
            if provider is None or bound is agent:
                return provider
            # provider 仅封装 agent/prompt；浅复制使各 Runner 拥有自己的调用归属，
            # 不复制网络 client、状态或记忆。
            cloned = copy.copy(provider)
            cloned.agent = bound
            return cloned

        self.semantic_projector = _bind_provider(self.semantic_projector)
        self.semantic_recaller = _bind_provider(self.semantic_recaller)

    @staticmethod
    def _validate_semantic_recall_config(config, recaller):
        if recaller is None:
            if config is not None:
                raise ValueError("未提供语义重排器时不能提供重排配置")
            return None
        if not isinstance(config, dict):
            raise ValueError("语义重排器需要显式实验配置")
        required = {"candidate_capacity", "exploration_budget",
                    "min_relevance", "min_confidence"}
        if not required <= set(config):
            raise ValueError("语义重排配置不完整")
        capacity = int(config["candidate_capacity"])
        exploration = int(config["exploration_budget"])
        min_relevance = float(config["min_relevance"])
        min_confidence = float(config["min_confidence"])
        if (capacity < 1 or exploration < 0 or exploration > capacity
                or not 0.0 <= min_relevance <= 1.0
                or not 0.0 <= min_confidence <= 1.0):
            raise ValueError("语义重排配置无效")
        return {"candidate_capacity": capacity, "exploration_budget": exploration,
                "min_relevance": min_relevance, "min_confidence": min_confidence}

    @staticmethod
    def _pending_extraction_key(pending):
        """待消费抽取的稳定身份；不依赖 dict 对象身份跨线程存活。"""
        if not isinstance(pending, dict):
            return None
        return (pending.get("source_turn_id"), pending.get("source_ts"),
                pending.get("source_sequence"))

    def _peek_pending_extraction(self):
        """读取下一回合可用的抽取测量，不清槽、不写消费事件。"""
        with self._ext_lock:
            return copy.deepcopy(self.pending_extraction)

    def _commit_pending_extraction(self, pending, consumer_turn_id, now_ms, *, state=None):
        """在主回复提交闸后消费此前快照的抽取测量。

        若生成期间有更晚的 extractor 结果抵达，旧快照不能把新观测一并清走；工作
        state 会同步到实际槽位。返回是否真的消费成功，供调用方只记一次归因事件。
        """
        if not pending:
            return None
        wanted = self._pending_extraction_key(pending)
        with self._ext_lock:
            current = self.pending_extraction
            if wanted is None or self._pending_extraction_key(current) != wanted:
                if state is not None:
                    if current is None:
                        state.pop("pending_extraction", None)
                    else:
                        state["pending_extraction"] = copy.deepcopy(current)
                return None
            self.pending_extraction = None
            self.state.pop("pending_extraction", None)
            if state is not None:
                state.pop("pending_extraction", None)
        if not pending:
            return None

        extraction = pending.get("extraction")
        if not isinstance(extraction, dict):
            # 兼容仅在内存中存在过的旧格式；不伪造缺失的历史归因事件。
            return pending
        source_turn_id = pending.get("source_turn_id")
        source_ts = pending.get("source_ts")
        if source_turn_id is not None and source_ts is not None:
            self.store.append_event(extractor_applied(
                source_turn_id=source_turn_id, source_ts=source_ts,
                consumer_turn_id=consumer_turn_id, ts=now_ms,
                params_sha=self.params_sha))
        return extraction

    def _sync_pending_extraction_to_state(self, state):
        """把 responder 期间异步抵达的最新抽取槽并回本轮即将保存的 state。

        这不是消费；它只避免慢主回复最后保存自己的工作 state 时覆盖掉后台刚落位的
        下一轮测量。来源序号仍由 ``_record_extraction`` 的既有水位线裁决。
        """
        with self._ext_lock:
            current = copy.deepcopy(self.pending_extraction)
            latest = self.state.get("extractor_latest_source_sequence")
        if current is None:
            state.pop("pending_extraction", None)
        else:
            state["pending_extraction"] = current
        if latest is not None:
            state["extractor_latest_source_sequence"] = latest

    def _consume_pending_extraction(self, consumer_turn_id, now_ms):
        """兼容入口：读取并立即提交一次已完成的 extractor 测量。"""
        pending = self._peek_pending_extraction()
        extraction = self._commit_pending_extraction(
            pending, consumer_turn_id, now_ms, state=self.state)
        if pending is not None:
            self.store.save_state(self.state)
        return extraction

    def _record_extraction(self, *, source_turn_id, source_ts, extraction,
                           source_sequence=None):
        """将完成的 extractor 测量作为下一回合待消费事实持久化。

        后台完成顺序不是用户事件顺序。单槽保留“最新来源回合”的观测，较旧调用
        即使更晚返回也只留下被拒收审计，不得覆盖尚未消费的新观测。
        """
        observed_at = int(_time.time() * 1000)
        sequence = int(source_sequence if source_sequence is not None else -1)
        pending = {
            "source_turn_id": str(source_turn_id),
            "source_ts": int(source_ts),
            "source_sequence": sequence,
            "extraction": dict(extraction or {}),
        }
        self.store.append_event(extractor_observed(
            source_turn_id=source_turn_id, source_ts=source_ts,
            observed_at_ms=observed_at, extraction=pending["extraction"],
            params_sha=self.params_sha))
        with self._ext_lock:
            watermark = int(self.state.get("extractor_latest_source_sequence", -1) or -1)
            if sequence >= 0 and sequence <= watermark:
                self.store.append_event({
                    "kind": "extractor_stale", "ts": observed_at,
                    "source_turn_id": str(source_turn_id), "source_ts": int(source_ts),
                    "source_sequence": sequence, "latest_source_sequence": watermark,
                    "params_sha": self.params_sha,
                })
                return None
            if sequence >= 0:
                self.state["extractor_latest_source_sequence"] = sequence
            self.pending_extraction = pending
            self.state["pending_extraction"] = pending
            self.store.save_state(self.state)
        return pending

    def _record_explicit_feedback(self, *, feedback, turn_id, now_ms, source="explicit",
                                  persist=True):
        """归档调用方或语义层已确认的反馈，不触发即时调参。"""
        label = normalize_feedback(feedback)
        attribution = attribute_feedback(
            self.store.read_action_receipts(), now_ms, self.outcome_horizon_ms)
        record = user_feedback(
            turn_id=turn_id, ts=now_ms, feedback=label, attribution=attribution,
            b0=self.prior or {}, params_sha=self.params_sha, source=source)
        if persist:
            self.store.append_event(record)
        return record

    def record_explicit_feedback(self, feedback, now_ms=None, source="ui_explicit"):
        """记录一条独立的用户显式反馈，供试用期人工归因回看。

        它不伪造一条用户对话、不推进墙钟、不调用模型，也不自动调整 B₀ 或参数。
        归因仍只会绑定时间窗内唯一的已发送 action；没有唯一行动时保留低/无归因，
        而不是猜测应怪哪一条回复。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        sequence = len(self.store.read_events()) + 1
        feedback_id = f"feedback:{now}:{sequence}"
        return self._record_explicit_feedback(
            feedback=feedback, turn_id=feedback_id, now_ms=now, source=source)

    def record_cadence_feedback(self, feedback, now_ms=None, source="ui_cadence",
                                presentation=None):
        """记录一次节奏体验标签，供人工反向标定，绝不修改当下计划或写作。"""
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        label = normalize_cadence_feedback(feedback)
        attribution = attribute_feedback(
            self.store.read_action_receipts(), now, self.outcome_horizon_ms)
        sequence = len(self.store.read_events()) + 1
        from ops.typing_presentation import presentation_snapshot
        record = cadence_feedback(
            feedback_id=f"cadence-feedback:{now}:{sequence}", ts=now, feedback=label,
            attribution=attribution, b0=self.prior or {},
            cadence=(self.store.load_clock_plan() or {}).get("cadence") or
                    self._cadence_projection(self.state),
            presentation=presentation_snapshot(presentation),
            params_sha=self.params_sha, source=source)
        self.store.append_event(record)
        return record

    def _reserve_npc_action(self, ts):
        """为一次已决定提交的 NPC 输出预分配单调 action ID。

        T0.1 的提交载荷必须在 message append 前引用同一 action；这里只分配身份，
        不写入消息、收据或状态。若随后进程中断留下编号空洞是可接受的，复用 ID
        才会破坏 append-only 因果链。
        """
        reserve = getattr(self.store, "reserve_npc_action_id", None)
        if callable(reserve):
            action_id = str(reserve(ts))
            # relationship Runner 的本地计数只用于旧接口兼容；正式身份由 owner
            # ledger 分配，防止两条 directed state 在同一 Actor/F16 时刻各自产生
            # ``action:ts:1``。
            try:
                self._action_sequence = max(self._action_sequence, int(action_id.rsplit(":", 1)[1]))
            except (IndexError, ValueError):
                pass
            return action_id
        self._action_sequence += 1
        return make_action_id(ts, self._action_sequence)

    def _remember(self, ts, role, text, source=None, turn_id=None, cause_id=None,
                  recall_mode=None, recall_refs=None, recall_strategy=None,
                  action_id=None, calibration_snapshot=None, **kw):
        """消息落盘统一入口：NPC 消息带 source 戳（A1）、全部带 params_sha（B3）；
        同时把 NPC 消息登记为"未归因动作"（B1：下轮用户回合的 R 认领）。
        """
        kw["params_sha"] = self.params_sha
        if source is not None:
            kw["source"] = source
        if turn_id is not None:
            kw["turn_id"] = turn_id
        if cause_id is not None:
            kw["cause_id"] = cause_id
        if role == "npc":
            action_id = action_id or self._reserve_npc_action(ts)
            kw["action_id"] = action_id
        rec = self.memory.remember_message(ts, role, text, **kw)
        if role == "npc":
            self.store.append_action_receipt(action_receipt(
                action_id=action_id, ts=ts, source=source or "reply",
                params_sha=self.params_sha, turn_id=turn_id, cause_id=cause_id,
                recall_mode=recall_mode, recall_refs=recall_refs,
                state_snapshot_turn=(self.turn + 1 if turn_id is not None else None),
                # recall_mode 是本轮的语义/情感召回模式；实验策略单独落盘，不能混用。
                recall_strategy=(recall_strategy or self.recall_strategy),
                calibration_snapshot=calibration_snapshot))
            self._npc_actions.append({"kind": source or "reply", "ts": int(ts),
                                      "action_id": action_id, "turn_id": turn_id})
        return rec

    @staticmethod
    def _event_state_imprint_kwargs(report):
        """只把 temporal bus 的正式事件回执交给已提交用户消息。

        P4 的历史印痕不得从 writer、UI、草稿或当前 state 反推。没有 temporal
        receipt 的兼容路径保持旧行形状；但若声称带有该 receipt，就必须是完整的
        ``on_message`` v1 合同，交由 ``Memory`` 再做逐轴守恒校验。
        """
        transition = (report or {}).get("state_transition")
        if transition is None:
            return {}
        if not isinstance(transition, dict):
            raise ValueError("正式事件状态回执必须为字典")
        required = ("version", "X_before", "Delta_X_event", "X_after")
        missing = [key for key in required if key not in transition]
        if missing:
            raise ValueError("正式事件状态回执缺少：" + ", ".join(missing))
        if transition["version"] != "event-state-transition-v1":
            raise ValueError("不支持的正式事件状态回执版本")
        return {
            "state_before": transition["X_before"],
            "event_delta": transition["Delta_X_event"],
            "state_after": transition["X_after"],
            "state_version": transition["version"],
        }

    @staticmethod
    def _output_conversation_context_from_received(provenance):
        """把已投递输入的会话范围带给由它因果生成的 own F16。

        这不是伪造一条反向 delivery：output 尚未经 Conversation bridge/relay 形成自己的
        source event。这里只记录它属于哪个冻结成员快照、由哪条已收到的事实触发，供
        actor-local memory 之后按会话过滤。
        """
        if not isinstance(provenance, dict):
            return None
        required = {
            "event_id", "conversation_id", "delivery_id", "sender_participant_id",
            "membership_version", "recipient_participant_id",
            "visible_recipient_ids", "member_snapshot_ids",
        }
        if set(provenance) != required:
            raise ValueError("received conversation provenance 字段不完整")
        owner = str(provenance["recipient_participant_id"])
        members = [str(item) for item in provenance["member_snapshot_ids"]]
        return {
            "conversation_id": str(provenance["conversation_id"]),
            "membership_version": int(provenance["membership_version"]),
            "owner_participant_id": owner,
            "sender_participant_id": owner,
            # 这是 direct F03 因果来源的精确 sender，不是 writer 猜测的受话人。
            # 真正 F16 提交后，conversation publication 才可把它作为默认 address
            # scope；草稿/typing 不读取、更不能据此形成 source。
            "reply_to_participant_id": str(provenance["sender_participant_id"]),
            "caused_by_event_id": str(provenance["event_id"]),
            "caused_by_delivery_id": str(provenance["delivery_id"]),
            "visible_recipient_ids": [item for item in members if item != owner],
            "member_snapshot_ids": members,
        }

    def _memory_temporal_readout(self, record, *, now_ms):
        """F10/P4 共用的 memory 墙钟读数入口（纯读，不选择、不写状态）。"""
        from ops.memory_state import derive_memory_temporal_readout
        binding = self._effective_time_binding()
        return derive_memory_temporal_readout(
            record=record, now_ms=now_ms,
            effective_constants=binding["effective_constants"])

    def _resolve_turn_recall_for_event(self, *, text, now_ms, report, query_tokens,
                                       turn_id, stage_event, stage_boundary):
        """执行一次 F10 实际选择；不写状态、不提交 recall，也不装配 writer。

        P4 需要在 F08 纯预结算后取得真实 selected receipt，再决定最终一次状态
        结算。把 resolver 放在这个窄方法中，避免“先落盘用户状态、后选 recall”的
        旧顺序变成第二条隐形状态 writer。普通未启用 P4 的路径仍在原提交点消费同一
        结果，选择算法/随机流不在此处改变。
        """
        semantic_pool, semantic_matches = (), ()
        config = self.semantic_recall_config
        if self.recall_strategy != "unified" and self.semantic_recaller is not None:
            try:
                semantic_pool = prepare_semantic_recall_pool(
                    store=self.store, memory=self.memory, ledger=self.ledger,
                    text=text, now_ms=now_ms, ranked_tokens=report["ranked"]["all"],
                    bridge_tokens=report["bridge_tokens"], embed_fn=self.embed_fn,
                    capacity=config["candidate_capacity"],
                    exploration_budget=config["exploration_budget"])
                if semantic_pool:
                    stage_event({
                        "kind": "semantic_recall_proposed", "turn_id": turn_id, "ts": now_ms,
                        "config": dict(config),
                        "candidates": [item.public() for item in semantic_pool],
                    })
                    candidates = [{"candidate_id": item.candidate_id,
                                   "summary": str(item.record.get("text") or "")}
                                  for item in semantic_pool]
                    # Reranker 只在 F10 已显式提出的 exact candidate 集内阅读当前输入；
                    # 不得把 writer 技术预算截出的 raw history 当作第二条检索上下文。
                    semantic_matches = tuple(self.semantic_recaller.rerank(
                        text=text, dialogue_tail="", candidates=candidates) or ())
                    stage_event({
                        "kind": "semantic_recall_reranked", "turn_id": turn_id, "ts": now_ms,
                        "matches": [{
                            "candidate_id": item.candidate_id,
                            "relevance": item.relevance,
                            "confidence": item.confidence,
                            "evidence_sha": hashlib.sha256(item.evidence.encode("utf-8")).hexdigest()[:16],
                        } for item in semantic_matches],
                    })
            except Exception as exc:
                # 可选语义重排不能否定本地 RAG 的既有选择资格。
                semantic_pool, semantic_matches = (), ()
                stage_event({
                    "kind": "semantic_recall_failed", "turn_id": turn_id,
                    "ts": now_ms, "error_type": type(exc).__name__,
                    "params_sha": self.params_sha,
                })
        return select_compat_received_recall(
            store=self.store, memory=self.memory, ledger=self.ledger,
            text=text, now_ms=now_ms, ranked_tokens=query_tokens,
            bridge_tokens=report["bridge_tokens"], embed_fn=self.embed_fn,
            turn_id=turn_id, strategy=self.recall_strategy, rng=self.rng,
            semantic_rerank_pool=semantic_pool,
            semantic_rerank_matches=semantic_matches,
            semantic_rerank_min_relevance=(config["min_relevance"] if config else None),
            semantic_rerank_min_confidence=(config["min_confidence"] if config else None),
            params_sha=self.params_sha, event_sink=stage_event,
            boundary_sink=stage_boundary, commit_selected=False,
            temporal_readout=lambda record: self._memory_temporal_readout(
                record, now_ms=now_ms),
            # 当前 direct input 已有独立、已提交的 current-input card；它只作为
            # memory-local 六维关系 anchor，不重复变成 recall writer card。
            include_memory_anchor=False)

    def _prepare_memory_state_batch(self, *, recall, report, now_ms,
                                    reference_frame=None, text=None):
        """把真实 selected RAG receipt 转为 P4 候选；无绑定时明确不产生 batch。"""
        policy = self.memory_state_policy
        selected = tuple(getattr(recall, "selected_records", ()) or ())
        # P4 读取的是已实际选中的 RAG receipt，但不能越过本轮已解析的主客体
        # 边界，把第三方叙述误当成当前 NPC 的共同关系状态。该 gate 不改 raw
        # selection 或 recall 提交计划，只生成供状态桥使用的合格副本。
        from ops.reference_frame import referent_state_eligibility_gate
        state_selected, state_suppressed = referent_state_eligibility_gate(
            selected, reference_frame, text=text)
        selection = selected_rag_receipts(
            selected_records=state_selected, shadow=getattr(recall, "shadow", None))
        trajectory = self.memory.recall_trajectory_history(state_selected, now_ms)
        transition = (report.get("state_transition") or {})
        current_delta = transition.get("Delta_X_event") or {}
        if policy is not None:
            measures = derive_recall_state_measures(
                selected_records=state_selected, now_ms=now_ms, policy=policy,
                trajectory_history=trajectory)
            batch = prepare_recall_state_batch(
                selected_records=state_selected, current_event_delta=current_delta,
                selection_receipts=selection, state_measures=measures["state_measures"],
                axis_caps=measures["axis_caps"])
            # 该字段是回放谱系，不是新的状态输入；F08 仍只接收冻结 batch 的向量和 receipt。
            # selection receipt 是 P4 的既成资格来源。将其无正文投影与 batch 一同
            # 固化，才能在 relation runtime 回放“本轮实际选中哪条连续历史、其来自
            # 哪个 directed relation”；它不是新的 P4 输入，更不能改变 selected
            # 排序、Delta_X_memory 或当前 relation 的状态写入位置。
            return dict(batch, policy_binding_version=self.memory_state_policy_binding_version,
                        selection_receipts=copy.deepcopy(selection),
                        state_suppressed=list(state_suppressed))

        anchor = self.memory_state_policy_family_anchor
        family = (anchor or {}).get("family") if isinstance(anchor, dict) else None
        if not isinstance(family, dict) or family.get("readout_contract") != "selected-recall-root-vector-v2":
            return None
        if not isinstance(transition.get("X_after"), dict):
            raise ValueError("Alpha 0.3 P4 readout 必须读取当前事件的 X_after receipt")
        # 只读当前会话同一版 C_effective；anchor 保留创建时身份，readout receipt 保留本轮版本。
        constant_binding = self._effective_time_binding()
        from core.params import TIME
        rest = {"E_env": TIME["rest_point"]["E_env"],
                "P_need": TIME["rest_point"]["P_need"],
                "V_attr": TIME["rest_point"]["V_attr"],
                "V_rep": TIME["rest_point"]["V_rep"],
                "L_self": 0.0, "L_opponent": 0.0}
        measures = derive_dynamic_recall_state_measures(
            selected_records=state_selected, now_ms=now_ms,
            effective_constants=constant_binding["effective_constants"],
            current_event_delta=current_delta, state_after_event=transition["X_after"],
            state_rest=rest, selection_receipts=selection,
            trajectory_history=trajectory)
        batch = prepare_recall_state_batch(
            selected_records=state_selected, current_event_delta=current_delta,
            selection_receipts=selection, state_measures=measures["state_measures"],
            axis_caps=None, fusion_mode=measures["fusion_mode"])
        return dict(batch,
                    policy_binding_version=str(anchor["version"]),
                    policy_family_version=str(family["version"]),
                    effective_constants_version=str(constant_binding["effective_constants_version"]),
                    dynamic_readout=measures,
                    # family-v2 与旧 policy 同样留下实际选择的无正文来源；P4 仍只
                    # 写 self.store（即当前 directed runtime），不会读取来源 relation
                    # 的 state 或把 provenance 当作额外状态变量。
                    selection_receipts=copy.deepcopy(selection),
                    state_suppressed=list(state_suppressed))

    @staticmethod
    def _memory_state_selection_decision(batch):
        """将一次 P4 资格评估投影为无正文、不可写状态的审计事实。

        此处刻意不查看 writer assembly/recall delivery。RAG 已实际选择的记录是否可
        形成状态影响，只由 ``prepare_recall_state_batch`` 的同源 P4 结论决定；这让
        legacy 无印痕、reference-frame 压制与真正待提交的 batch 在回执上可区分。
        """
        row = batch if isinstance(batch, dict) else {}
        selected = [str(value) for value in (row.get("selected_memory_ids") or ())
                    if str(value).strip()]
        missing = row.get("missing") if isinstance(row.get("missing"), list) else []
        suppressed = (row.get("state_suppressed")
                      if isinstance(row.get("state_suppressed"), list) else [])
        ready = bool(row.get("ready_to_commit"))
        reason = str(row.get("reason") or "")
        if ready:
            outcome = "state_impact_required"
        elif missing:
            outcome = "state_impact_blocked"
        elif suppressed:
            outcome = "state_suppressed"
        elif not selected:
            outcome = "no_state_selected_record"
        elif reason == "no_selected_state_imprint":
            outcome = "state_ineligible"
        else:
            # ``prepare_recall_state_batch`` 的所有正常非提交出口都应给出上面的
            # 原因；新出口若漏了分类，必须在 manifest 中 fail-closed，而不是猜测。
            outcome = "state_impact_blocked"
        return {
            "version": "memory-state-selection-evaluated-v1",
            "outcome": outcome,
            "ready_to_commit": ready,
            "selected_memory_ids": selected,
            "legacy_record_count": len(row.get("legacy_records") or ()),
            "missing_count": len(missing),
            "state_suppressed_count": len(suppressed),
            "reason": reason or None,
        }

    def _p4_state_bridge_enabled(self):
        """P4 只在旧静态 policy 或已启用 family-v2 readout 时进入现有 F03/F16 bridge。"""
        if self.memory_state_policy is not None:
            return True
        anchor = self.memory_state_policy_family_anchor
        family = (anchor or {}).get("family") if isinstance(anchor, dict) else None
        return isinstance(family, dict) and family.get("readout_contract") == "selected-recall-root-vector-v2"

    @staticmethod
    def _output_state_imprint_kwargs(payload):
        """仅从已冻结的 output prepared receipt 取 NPC 记忆印痕。"""
        transition = (payload or {}).get("state_transition")
        if transition is None:
            return {}
        if not isinstance(transition, dict):
            raise ValueError("输出状态回执必须为字典")
        required = ("version", "X_before", "Delta_X_event", "X_after")
        missing = [key for key in required if key not in transition]
        if missing:
            raise ValueError("输出状态回执缺少：" + ", ".join(missing))
        if transition["version"] != "output-state-transition-v1":
            raise ValueError("不支持的输出状态回执版本")
        return {
            "state_before": transition["X_before"],
            "event_delta": transition["Delta_X_event"],
            "state_after": transition["X_after"],
            "state_version": transition["version"],
        }

    def seed_historical_context(self, *, ts, text, topic, salience=0.5,
                                source="historical_context", ledger_fact=None):
        """以既有本地记忆层登记一条**已发生的用户侧前史**。

        它是导入／受控测试的窄入口，不是一次新对话、更不是 UI 伪造气泡：不做输入
        信号、prompt、状态演算、输出或墙钟刷新。调用方必须在角色 setup 后明确给出
        时间、主题和显著性；共同史由每一侧分别调用，私有史只调用目标侧，绝不共享
        store 或 Memory 实例。

        历史 NPC 输出须走其带 action receipt 的迁移路径，不能借此绕过输出提交边界；
        因而本接口刻意只接收已经发生的用户侧上下文。
        """
        if not self.profile:
            raise RuntimeError("尚未 setup()")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("历史上下文 text 不能为空")
        if not isinstance(topic, str) or not topic.strip():
            raise ValueError("历史上下文 topic 不能为空")
        try:
            historical_ts = int(ts)
            S = float(salience)
        except (TypeError, ValueError) as exc:
            raise ValueError("历史上下文 ts/salience 必须为数值") from exc
        if historical_ts < 0 or not 0.0 <= S <= 1.0:
            raise ValueError("历史上下文 ts 或 salience 越界")
        tokens = [{"text": topic.strip(), "strength": 1.0}]
        rec = self._remember(historical_ts, "user", text.strip(),
                             source=str(source or "historical_context"),
                             S=S, tokens=tokens, non_dialogue_seed=True)
        self.memory.archive_tokens(historical_ts, tokens, S=S, drift={"P": 0.0, "E": 0.0},
                                   source=str(source or "historical_context"),
                                   event_id=rec.get("event_id"))
        if ledger_fact is not None:
            if not isinstance(ledger_fact, str) or not ledger_fact.strip():
                raise ValueError("历史上下文 ledger_fact 必须为非空字符串")
            self.ledger.merge([ledger_fact.strip()], historical_ts, S=S, tokens=tokens,
                              topic=topic.strip(), source=str(source or "historical_context"),
                              event_id=rec.get("event_id"))
        return rec

    def _persist_npc_output(self, *, state, ts, text, source, turn_id=None, cause_id=None,
                            recall_mode=None, recall_refs=None, received_ms=None,
                            ready_ms=None, cadence_delay_ms=None, raw_deltas=None,
                            evidence=None, calibration_assembly_receipt=None,
                            memory_recall=None, **metadata):
        """S0 的 NPC 输出收据边界。

        此处只提交一条已经决定要发送的 NPC 文本及其 action receipt；它不改变
        墙钟状态、不结算心冷、也不处理自述回流或台账。后三类效果的时序在不同
        输出路径中并不相同：普通回复要先完成用户事件的测量，延迟回复必须先摘
        挂起牌，历史回放则要在历史发送时刻积分。因此它们仍由调用方在正确阶段
        显式结算，避免一个"万能提交器"重新耦合时间线。

        所有新的 NPC 文本出口必须经由此处；普通消息的通用 _remember 仍保留给
        用户输入和兼容的底层调用。
        """
        action_id = self._reserve_npc_action(ts)
        output_payload = None
        exact_evidence = []
        self_deltas = {}
        # e_self 只保留为 writer 自报审计。AUD-021 起，它和旧 ``e_out`` 都不是
        # output 的根状态来源；真正的 output Delta_X 必须等 F16 后由 Agent 1 测量。
        receipt_deltas = {}
        receipt_effect = {}
        if received_ms is not None or ready_ms is not None or cadence_delay_ms is not None:
            if received_ms is None or ready_ms is None or cadence_delay_ms is None:
                raise ValueError("输出时间提交必须同时给出 received/ready/cadence")
            from ops.response_feedback import validated_self_deltas
            self_deltas, exact_evidence = validated_self_deltas(text, raw_deltas, evidence)
            timeline = resolve_output_timeline(
                received_ms=received_ms, ready_ms=ready_ms,
                cadence_delay_ms=cadence_delay_ms)
            time_binding = self._effective_time_binding()
            pre_send, _time_receipt = advance_to_output_time(
                state, timeline,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"])
            # prepared 是 F16 前的可恢复时间事实，不能预先调用 Agent 1 或把
            # “说出口即释压”作为输出意义。因而这里的 state_transition 只冻结
            # t_sent 前的时间基线；最终 Delta_X 由 F16 后的 measurement receipt 给出。
            applied = {}
            _projected_state, state_transition = output_state_transition(
                state, (self.profile or {}).get("constants") or {},
                timeline=timeline, deltas=applied,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"],
                f06_constants=time_binding["effective_constants"])
            memory_batch = None
            memory_state_decision = None
            output_payload = output_commit_prepared(
                action_id=action_id, source=source, received_ms=received_ms,
                ready_ms=ready_ms, cadence_delay_ms=cadence_delay_ms,
                applied_deltas=applied, params_sha=self.params_sha, turn_id=turn_id,
                self_deltas=self_deltas, receipt_deltas=receipt_deltas,
                receipt_effect=receipt_effect, state_transition=state_transition,
                memory_delta=(memory_batch or {}).get("Delta_X_memory"),
                memory_state_receipt=memory_batch,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"],
                # recall 的 archive/ledger 事实只等待 F16；是否另外生成
                # Delta_X_memory 取决于 P4 policy，二者不可互相代理。
                recall_commit_plan=(getattr(memory_recall, "commit_plan", ())
                                    if memory_recall is not None else None))
            output_payload["output_semantic_required"] = True
            # 候选编排没有写状态资格：这里只将无正文、同 action 的 plan 暂存进
            # prepared 输出收据；只有该输出经过 F16 后，才可由下游结算余量。
            expression_attention = ((calibration_assembly_receipt or {})
                                    .get("expression_attention") or {})
            emission = (expression_attention.get("emission")
                        if isinstance(expression_attention, dict) else None)
            if isinstance(emission, dict) and isinstance(emission.get("plan"), dict):
                delivered = emission.get("delivered_token_keys")
                if isinstance(delivered, list):
                    output_payload["expression_emission"] = {
                        "plan": copy.deepcopy(emission["plan"]),
                        "delivered_token_keys": [str(key) for key in delivered
                                                 if str(key).strip()],
                    }
            if int(output_payload["timeline"]["t_sent"]) != int(ts):
                raise ValueError("输出收据时间必须等于 t_sent")
            # prepared 是可恢复的“待提交输出”事实，必须先于 message；但它本身
            # 不证明消息已发送，重启时不会独自触发状态回流。
            self.store.append_event(output_payload)
            if memory_state_decision is not None:
                self.store.append_event({
                    "schema_version": 1, "kind": "memory_state_selection_evaluated",
                    "action_id": action_id, "turn_id": turn_id,
                    "ts": int(timeline["t_sent"]),
                    "detail": self._memory_state_selection_decision(memory_state_decision),
                    "params_sha": self.params_sha,
                })
        from interaction_extensions.preference_calibration.cx_six_seven.receipt import (
            action_calibration_snapshot,
        )
        snapshot = action_calibration_snapshot(
            state=state, cadence=self._cadence_projection(state),
            recall_refs=recall_refs, text=text,
            baseline=self.prior or (self.profile or {}).get("prior"),
            assembly_receipt=calibration_assembly_receipt)
        output_imprint = self._output_state_imprint_kwargs(output_payload)
        if "output_identity" in metadata:
            raise ValueError("output identity 只能由绑定 Actor 注入")
        rec = self._remember(
            ts, "npc", text, source=source, turn_id=turn_id, cause_id=cause_id,
            recall_mode=recall_mode, recall_refs=recall_refs, action_id=action_id,
            calibration_snapshot=snapshot,
            output_identity=self._writer_output_identity(),
            **metadata, **output_imprint)
        manual_receipt = rec.get("test_manual_responder")
        if isinstance(manual_receipt, dict):
            # 只在已经成为 F16 的 action 上留审计事实；不让 arm 或草稿单独成为事件。
            self.store.append_event({
                "schema_version": 1,
                "kind": "test_manual_responder_f16_bound",
                "ts": int(ts),
                "action_id": str(action_id),
                "detail": dict(manual_receipt),
            })
        if output_payload is not None:
            # 仅供当前事务后续的状态/归档结算；_remember 已把真正 message 写入 JSONL，
            # 这些下划线键不会进入持久化消息正文。
            rec["_output_commit_payload"] = output_payload
            rec["_output_commit_evidence"] = exact_evidence
            rec["_output_receipt_deltas"] = dict(receipt_deltas)
            rec["_output_receipt_effect"] = dict(receipt_effect)
            rec["_output_memory_batch"] = memory_batch
            # 这是进程内的 frozen F10 selection，仅供已送达 F16 后的 P4 state bridge
            # 编译；不会进入 message、prepared receipt 或 prompt，也不能在恢复时重检索。
            rec["_output_memory_recall"] = memory_recall
        self._after_npc_output_receipt(
            state=state, receipt=rec, ts=ts, source=source, turn_id=turn_id)
        return rec

    def _commit_expression_emission_after_f16(self, state, *, output_commit,
                                              action_id, ts):
        """在实际 NPC 输出完成后，唯一地提交表现词元发放余量。

        prepared receipt 没有正式 plan、或同一 action 已结算时均空退。此处不读取
        正文、不重编候选、更不会改变根量、墙钟或 tick；只把实际交付 key 转为
        下一 action 可读取的连续余量。
        """
        if not isinstance(output_commit, dict):
            return None
        staged = output_commit.get("expression_emission")
        if not isinstance(staged, dict):
            return None
        if str(state.get("last_expression_emission_action_id") or "") == str(action_id):
            return {"committed": False, "reason": "already_committed"}
        if any(
            row.get("kind") == "expression_emission_committed"
            and str(row.get("action_id") or "") == str(action_id)
            for row in self.store.read_events()
        ):
            return {"committed": False, "reason": "already_receipted"}
        plan = staged.get("plan")
        delivered = staged.get("delivered_token_keys")
        if not isinstance(plan, dict) or not isinstance(delivered, list):
            return None
        commit = commit_expression_emission_plan(
            plan, f16_committed=True, delivered_token_keys=delivered,
            committed_remainders=state.get("expression_emission_remainders"))
        state["expression_emission_remainders"] = dict(
            commit.get("remainder_updates") or {})
        state["last_expression_emission_action_id"] = str(action_id)
        # state watermark 先落盘，恢复重进时不得二次扣除同一 action 的配额。
        self.store.save_state(state)
        self.store.append_event({
            "schema_version": 1,
            "kind": "expression_emission_committed",
            "ts": int(ts),
            "action_id": str(action_id),
            "detail": dict(commit),
        })
        return commit

    @staticmethod
    def _committed_output_deltas(receipt, fallback=None):
        """返回本次正式 payload 已冻结的 e_commit；未走输出时间入口时才回退。"""
        payload = (receipt or {}).get("_output_commit_payload") if isinstance(receipt, dict) else None
        if isinstance(payload, dict):
            values = payload.get("applied_deltas") or {}
            return {key: float(value) for key, value in values.items() if float(value) != 0.0}
        return {key: float(value) for key, value in (fallback or {}).items()
                if float(value) != 0.0}

    def _after_npc_output_receipt(self, *, state, receipt, ts, source, turn_id=None):
        """S0.3：输出时间提交的唯一预留点（当前刻意为零行为）。

        T0.1 只可在这里接入 ``t_ready -> t_sent`` 的 temporal 续接。``state``
        由调用方显式传入，因而墙钟回放可以传历史 ``replay_state``，不会把过去
        发生的输出错误写回当前 ``self.state``。S0 阶段绝不修改它。
        """
        del state, receipt, ts, source, turn_id

    def _record_output_memory_state_batch(self, payload, *, ts, recovery=False):
        """给已送达输出的冻结 P4 batch 留一条可幂等恢复的事实行。

        只读取 prepared payload；不会重新检索、重算记忆或施加状态。``action_id`` 是
        输出事务的发送事实主键，故正常提交与重启恢复只能留下同一条 batch receipt。
        """
        payload = payload or {}
        batch = payload.get("memory_state_receipt")
        action_id = str(payload.get("action_id") or "")
        if not isinstance(batch, dict) or not action_id:
            return False
        events = self.store.read_events()
        for row in events:
            if (row.get("kind") == "memory_state_batch_committed"
                    and str(row.get("action_id") or "") == action_id):
                return False
        self.store.append_event({
            "schema_version": 1, "kind": "memory_state_batch_committed",
            "action_id": action_id, "turn_id": payload.get("turn_id"), "ts": int(ts),
                "detail": {
                    "version": batch.get("version"),
                    "policy_binding_version": batch.get("policy_binding_version"),
                    "policy_family_version": batch.get("policy_family_version"),
                    "effective_constants_version": batch.get("effective_constants_version"),
                    "dynamic_readout": copy.deepcopy(batch.get("dynamic_readout")),
                    "selected_memory_ids": list(batch.get("selected_memory_ids") or ()),
                    # 与 input P4 receipt 同源的无正文 selection provenance。它只
                    # 解释实际选中的历史来自何处，不能扩展 output P4 的写入关系。
                    "selection_receipts": copy.deepcopy(batch.get("selection_receipts") or {}),
                    "Delta_X_memory": dict(batch.get("Delta_X_memory") or {}),
                    # prepared 已冻结 transition；只有本方法在 state watermark 后追加
                    # 事件，故这份锚才可供后续 writer 读取。
                    "expression_anchor": copy.deepcopy(
                        payload.get("memory_expression_anchor")),
                    "event_kind": "npc_output", "recovery": bool(recovery),
            }, "params_sha": self.params_sha,
        })
        return True

    def _commit_output_recall_plan(self, payload, *, now_ms):
        """只消费 prepared 内冻结 plan；用于送达后的正常/恢复路径，绝不重检索。"""
        plan = (payload or {}).get("recall_commit_plan")
        action_id = str((payload or {}).get("action_id") or "")
        if not plan or not action_id:
            return ()
        already = {str(row.get("action_id")) for row in self.store.read_events()
                   if row.get("kind") == "output_recall_committed"}
        if action_id in already:
            return ()
        committed = commit_turn_recall(
            memory=self.memory, ledger=self.ledger,
            recall=SimpleNamespace(commit_plan=tuple(plan)), now_ms=now_ms)
        self.store.append_event({
            "schema_version": 1, "kind": "output_recall_committed",
            "action_id": action_id, "ts": int(now_ms),
            "detail": {"committed": list(committed)}, "params_sha": self.params_sha,
        })
        return committed

    def _open_turn(self, ts, *, persist=True):
        """P1：单轮事务开始时记录因果锚；不参与时间场或召回判定。"""
        self._turn_sequence += 1
        turn_id = make_turn_id(ts, self._turn_sequence)
        if persist:
            self.store.append_event(turn_opened(
                turn_id=turn_id, ts=ts, params_sha=self.params_sha))
        return turn_id

    def observe_action_outcomes(self, now_ms=None):
        """P4：结算已关闭的行动窗口；只追加观测结果，绝不影响回复或状态场。

        timeout 之后出现迟到回复时追加一个更高 revision，而非覆盖旧判断，保留墙钟下
        "当时为何判超时"的可追溯性。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        observed = observe_actions(
            self.store.read_action_receipts(), self.store.read_messages(), now,
            horizon_ms=self.outcome_horizon_ms)
        receipts = {str(row.get("action_id")): row
                    for row in self.store.read_action_receipts()
                    if row.get("action_id")}
        latest = {}
        for row in self.store.read_outcomes():
            action_id = row.get("action_id")
            if action_id is not None:
                latest[action_id] = row
        written = []
        for row in observed:
            if row.get("status") == "open":
                continue
            action_id = row.get("action_id")
            previous = latest.get(action_id)
            if previous is not None and previous.get("status") == row.get("status"):
                continue
            revision = int((previous or {}).get("revision", 0)) + 1
            rec = dict(row)
            rec.update({"schema_version": 1, "revision": revision,
                        "observed_at": now})
            if previous is not None:
                rec["supersedes_revision"] = int(previous.get("revision", 0))
            self.store.append_outcome(rec)
            # 只把已关闭的自然结果窗口连回当时已发送动作；该事实不做即时调参。
            from interaction_extensions.preference_calibration.cx_six_seven.receipt import (
                outcome_calibration_observation,
            )
            calibration = outcome_calibration_observation(
                action=receipts.get(str(action_id)), outcome=rec)
            if calibration is not None:
                calibration["observed_at"] = now
                self.store.append_event(calibration)
            latest[action_id] = rec
            written.append(rec)
        return written

    def _bump_unanswered(self, state_obj, delta, ts, event="inc", *, persist=True):
        """心冷 n 簿记统一入口（A3）：每次真实变化（+1/清零）带时间戳落盘。"""
        if delta == 0:
            return None
        state_obj["unanswered_n"] = int(state_obj.get("unanswered_n", 0)) + delta
        record = {"ts": int(ts), "n": int(state_obj["unanswered_n"]), "event": event}
        if persist:
            self.store.append_cold(record)
        return record

    def _take_npc_actions(self):
        """摘走自上次用户回合以来的 NPC 动作（B1 归因戳的素材），返回摘要。"""
        acts = self._npc_actions
        self._npc_actions = []
        if not acts:
            return None, 0
        last = acts[-1]
        return {"kind": last["kind"], "ts": last["ts"]}, len(acts)

    def _peek_npc_actions(self):
        """取得待归因动作的只读快照；可抢占主回复不能在生成前把它们摘走。"""
        return tuple(dict(item) for item in self._npc_actions)

    def _commit_npc_actions(self, snapshot):
        """提交一次已经成功的用户回合对旧 NPC 动作的归因认领。

        只移走当前队首仍等于快照的动作；若未来接入其他生产者在此期间追加动作，
        它们会保留给下一次用户输入而不是被旧回合吞掉。
        """
        snapshot = tuple(snapshot or ())
        if not snapshot:
            return
        if (len(self._npc_actions) >= len(snapshot)
                and tuple(self._npc_actions[:len(snapshot)]) == snapshot):
            self._npc_actions = self._npc_actions[len(snapshot):]

    def _sync_user_card_prior(self):
        """用户卡仅镜像当前 B₀ 绑定；profile 的 append-only 版本档案为权威。"""
        try:
            from interaction_extensions.preference_calibration.cx_six_seven.user_card import (
                save as _save_card,
                sync_from_profile,
            )
            self.user_card = sync_from_profile(self.user_card, self.profile)
            _save_card(self.store, self.user_card)
        except Exception:
            pass

    def _ledger_from_reply(self, reply, now, facts=None, contradictions=None,
                           S=0.0, tokens=None, topic=None, event_id=None):
        """回复台账入账（承 MANE Agent3→Agent0）：responder 自产的 facts 就地合并
        （精确归因本条回复、盖当时 S/词元/话题）；facts 空 → 回落批量提取兜底。"""
        if facts or contradictions:
            self.ledger.merge(facts, now, contradictions,
                              S=S, tokens=tokens, topic=topic, event_id=event_id)
            return
        self._note_reply(reply, now, S=S, tokens=tokens, topic=topic,
                         event_id=event_id)

    def _note_reply(self, reply, now, S=0.0, tokens=None, topic=None, event_id=None):
        """NPC 回复定稿 → 陈述台账批量提取（异步内容；桩测试中同步执行）。

        定稿时刻盖章该轮 S（情感浓度）、field_tokens（话题词元，剔除准常驻类别
        防相关性污染）与 topic（当时话题的用户原话节选）——AI 的话产生本身就是
        参数化的；批内多轮时取情感峰值盖章（LLM 提取无法逐句归因）。
        """
        if self.ledger_agent is None or not reply:
            return
        exclude = set(LEDGER["stamp_exclude_categories"])
        raw_tokens = tokens if isinstance(tokens, list) else []
        tokens = [t for t in raw_tokens
                  if str(t.get("category", "")) not in exclude]
        self.ledger_pending.append({"reply": reply, "ts": now,
                                    "S": float(S or 0.0),
                                    "tokens": list(tokens or []),
                                    "topic": str(topic or ""),
                                    "event_id": (str(event_id).strip()
                                                 if event_id is not None else None)})
        if len(self.ledger_pending) >= self.ledger_every:
            batch = list(self.ledger_pending)
            self.ledger_pending = []
            ls, lu = build_ledger_messages([b["reply"] for b in batch],
                                           self.ledger.recent_texts())
            raw = self.ledger_agent.call(ls, lu,
                                         temperature=DEFAULTS["refine_temperature"],
                                         require_json=True)
            parsed = parse_ledger(raw)
            s_max = max((b["S"] for b in batch), default=0.0)
            tokens = [t for b in batch for t in b["tokens"]][:LEDGER["batch_tokens_cap"]]
            topic = max((b["topic"] for b in batch), key=len, default="")
            # 批量抽取无法把一条 fact 可靠地分回多条 output；只有单 output batch
            # 才保留 exact annotation identity。多条 batch 明确保持 legacy，绝不
            # 用时间／文本猜 parent event。
            batch_event_id = (batch[0].get("event_id") if len(batch) == 1 else None)
            self.ledger.merge(parsed["facts"], now, parsed["contradictions"],
                              S=s_max, tokens=tokens, topic=topic,
                              event_id=batch_event_id)

    # ------------------------------------------------------------------
    # 开局管线（一次性）
    # ------------------------------------------------------------------
    def bind_output_identity(self, participant):
        """绑定本 Actor 的 canonical Participant identity，供所有 writer 路径只读使用."""
        from ops.participants import Participant

        if isinstance(participant, Participant):
            record = participant.as_record()
        elif isinstance(participant, Mapping):
            record = dict(participant)
        else:
            raise TypeError("output identity 必须是 Participant 或其 canonical record")
        try:
            canonical = Participant.from_record(record)
        except Exception as exc:  # noqa: BLE001 -- identity 不得用兼容猜测修复
            raise ValueError("output identity 必须是完整 canonical Participant record") from exc
        if not canonical.capabilities.has_writer:
            raise ValueError("没有 writer capability 的 Participant 不能绑定输出 identity")
        self._output_identity = {
            "participant_id": canonical.participant_id,
            "actor_kind": canonical.kind,
        }
        return dict(self._output_identity)

    def _writer_output_identity(self):
        """返回本 Actor 已绑定的只读身份；缺失时不从其他材料推断。"""
        return None if self._output_identity is None else dict(self._output_identity)

    def formal_owner_participant_identity(self):
        """Return the exact canonical product owner for formal host wiring.

        This is a read-only composition capability.  It deliberately refuses
        to infer identity from a Store directory, character name, session, or
        conversation.  The durable product anchor and the already-bound writer
        identity must describe the same participant, and the active formal
        session must expose the same profile snapshot.
        """
        from ops.participants import Participant

        session = self.formal_runtime_session
        if session is None:
            raise RuntimeError(
                "formal owner identity requires an active formal runtime")
        profile = self.profile
        if not isinstance(profile, Mapping):
            raise ValueError(
                "formal owner requires a canonical product participant")
        record = profile.get("product_participant_identity")
        try:
            participant = Participant.from_record(record)
        except Exception as exc:  # noqa: BLE001 -- no identity repair/inference
            raise ValueError(
                "formal owner requires a canonical product participant"
            ) from exc
        bound = self._writer_output_identity()
        if (
            not isinstance(bound, Mapping)
            or bound.get("participant_id") != participant.participant_id
            or bound.get("actor_kind") != participant.kind
        ):
            raise ValueError(
                "product participant and bound writer identity do not match")
        session_profile = getattr(session, "profile", None)
        if (
            not isinstance(session_profile, Mapping)
            or session_profile.get("product_participant_identity") != record
        ):
            raise ValueError(
                "active formal runtime and product participant do not match")
        return participant

    def migrate_memory_product_family(
            self, family, *, effective_constants_version, b0_version,
            at_ms, expected_parent_version):
        """Atomically append and publish one explicit Alpha 0.4 family upgrade.

        This changes only the contract identity for future transactions. It
        does not run a memory stage, advance time, create an event, or grant
        Writer/dynamic-tick/send authority.
        """
        if not isinstance(self.profile, Mapping):
            raise RuntimeError(
                "Alpha 0.4 memory product family migration requires a profile")
        migrated = append_explicit_memory_product_family_version(
            self.profile,
            family,
            effective_constants_version=effective_constants_version,
            b0_version=b0_version,
            at_ms=at_ms,
            expected_parent_version=expected_parent_version,
        )
        anchor = dict(current_memory_product_family_anchor(migrated) or {})
        if not anchor:
            raise RuntimeError(
                "Alpha 0.4 memory product family migration produced no anchor")
        migrated_session = (
            None
            if self.formal_runtime_session is None
            else replace(self.formal_runtime_session, profile=migrated)
        )
        # save_profile is the only fallible publication boundary.  Both legacy
        # and formal session views switch only after the complete profile lands.
        self.store.save_profile(migrated)
        self.profile = migrated
        self.memory_product_family_anchor = anchor
        if migrated_session is not None:
            self.formal_runtime_session = migrated_session
        return dict(anchor)

    def commit_memory_product_transaction(self, transaction):
        """Commit one already-complete Alpha 0.4 owner-local memory product.

        The gate validates only session/owner authority and delegates exact-event
        and idempotency checks to the D1 journal. It never builds C1--C5 products.
        """
        from ops.memory_product_transaction import (
            MemoryProductTransaction,
            MemoryProductTransactionJournal,
        )

        if not isinstance(transaction, MemoryProductTransaction):
            raise ValueError("transaction must be a MemoryProductTransaction")
        anchor = self.memory_product_family_anchor
        if not isinstance(anchor, Mapping):
            raise RuntimeError("Alpha 0.4 memory product family is not bound")
        if transaction.family_binding_version != str(anchor.get("version") or ""):
            raise ValueError("memory product family binding does not match session")
        validate_memory_product_family_transaction(
            anchor.get("family") or {}, transaction)
        identity = self._writer_output_identity()
        if identity is None:
            raise RuntimeError("memory product commit requires bound output identity")
        if transaction.owner_participant_id != identity["participant_id"]:
            raise ValueError("memory product owner does not match Runner identity")
        journal = MemoryProductTransactionJournal(self.store)
        if transaction.root_transition is None:
            return journal.commit(transaction)
        committed = journal.committed_status(transaction)
        if committed is not None:
            return committed
        from ops.memory_root_transition import prepare_actual_recall_root_state

        time_binding = self._effective_time_binding()
        prepared_state = prepare_actual_recall_root_state(
            state=self.state,
            transition=transaction.root_transition,
            base_constants=(self.profile or {}).get("constants") or {},
            effective_constants=time_binding["effective_constants"],
        )
        result = journal.commit(transaction, prepared_state=prepared_state)
        self.state = self.store.load_state() or prepared_state
        return result

    def commit_memory_product_from_settled_stages(
            self, *, transaction_id, trigger_event, canonical_batch,
            impact_batch=None, root_transition=None, influence_plan=None, attention_plan=None,
            hydration_batch=None, stage_production_receipt=None):
        """Build and commit one product transaction from settled C1--C5 outputs.

        Peer input and own direct/proactive output deliberately share this entry.
        The method does not select recall, calculate any stage, hydrate content,
        inspect a path label, or grant send authority.  It only binds the already
        settled products to this Runner's owner/family identity and delegates to
        the exact same D1/D2 commit gate.
        """
        from ops.memory_product_transaction import build_memory_product_transaction

        anchor = self.memory_product_family_anchor
        if not isinstance(anchor, Mapping):
            raise RuntimeError("Alpha 0.4 memory product family is not bound")
        identity = self._writer_output_identity()
        if identity is None:
            raise RuntimeError("memory product commit requires bound output identity")
        transaction = build_memory_product_transaction(
            transaction_id=transaction_id,
            owner_participant_id=identity["participant_id"],
            trigger_event=trigger_event,
            canonical_batch=canonical_batch,
            impact_batch=impact_batch,
            root_transition=root_transition,
            influence_plan=influence_plan,
            attention_plan=attention_plan,
            hydration_batch=hydration_batch,
            family_binding_version=str(anchor.get("version") or ""),
            stage_production_receipt=stage_production_receipt,
        )
        return self.commit_memory_product_transaction(transaction)

    def commit_memory_product_for_committed_event(
            self, *, transaction_id, trigger_event):
        """Produce and commit one Alpha 0.4 product for an exact event.

        The injected producer owns all versioned C1--C5 runtime packages.  This
        entry supplies only canonical event/owner/family identity, verifies the
        result still belongs to that event, and delegates to the existing D3a
        settled-stage gate.  It has no path label and no send authority.
        """
        from ops.memory_product_runtime import (
            memory_product_stage_producer_identity,
            produce_memory_product_stages,
        )

        anchor = self.memory_product_family_anchor
        if not isinstance(anchor, Mapping):
            raise RuntimeError("Alpha 0.4 memory product family is not bound")
        identity = self._writer_output_identity()
        if identity is None:
            raise RuntimeError("memory product commit requires bound output identity")
        producer = self._memory_product_stage_producer
        if producer is None:
            raise RuntimeError(
                "Alpha 0.4 memory product stage producer is not configured")
        current_producer_identity = memory_product_stage_producer_identity(producer)
        if current_producer_identity != self._memory_product_stage_producer_identity:
            raise RuntimeError("memory product stage producer identity changed at runtime")
        family_version = str(anchor.get("version") or "")
        stages = produce_memory_product_stages(
            producer=producer,
            trigger_event=trigger_event,
            owner_participant_id=identity["participant_id"],
            family_binding_version=family_version,
        )
        return self.commit_memory_product_from_settled_stages(
            transaction_id=transaction_id,
            trigger_event=trigger_event,
            canonical_batch=stages.canonical_batch,
            impact_batch=stages.impact_batch,
            root_transition=stages.root_transition,
            influence_plan=stages.influence_plan,
            attention_plan=stages.attention_plan,
            hydration_batch=stages.hydration_batch,
            stage_production_receipt=stages.production_receipt(
                owner_participant_id=identity["participant_id"],
                family_binding_version=family_version,
            ),
        )

    def setup(self, background_text, preference_text=None, preference_declared=None,
              role_weights_override=None, initial_state=None):
        """劳动 Agent 开局：背景文本 → 三件套。"""
        if self.labor is None:
            raise RuntimeError("labor_agent 未注入：先 setup() 再对话")
        system, user = build_labor_messages(background_text)
        raw = self.labor.call(system, user,
                              temperature=DEFAULTS["labor_temperature"], require_json=True)
        parsed = parse_labor(raw)
        if role_weights_override is not None:
            # 用户在创建器中显式调整的角色五维，优先于背景解析的初始建议；仍复用
            # labor 的同一归一化/非法值过滤，不允许 UI 另造一套常量语义。
            parsed["weights"] = parse_labor(
                json.dumps({"weights": role_weights_override}, ensure_ascii=False)
            )["weights"]
        return self._apply_setup(parsed, background_text, preference_text,
                                 preference_declared=preference_declared,
                                 initial_state=initial_state)

    def setup_from_card(self, card, background_text="", preference_text=None,
                        preference_declared=None):
        """角色卡直接开局（跳过劳动 Agent）。card = 劳动 Agent 输出同构 dict。"""
        import json as _json
        raw_card = card if isinstance(card, dict) else {}
        parsed = parse_labor(_json.dumps(raw_card, ensure_ascii=False))
        return self._apply_setup(parsed, background_text, preference_text,
                                 preference_declared=preference_declared,
                                 initial_state=raw_card.get("initial_state"))

    def attach_formal_runtimes(
        self, *, model_runtime, temporal_runtime, runtime_variant,
    ):
        """One-time explicit late binding after the product profile exists.

        Package choice and session identity remain the composition root's
        responsibility.  A v3 anchor is restored if present; otherwise this
        only attaches dependencies and initialization remains explicit.
        """
        if not isinstance(self.profile, Mapping):
            raise ValueError(
                "formal runtime attachment requires an existing profile")
        if (
            self._formal_model_runtime is not None
            or self._formal_temporal_runtime is not None
            or self._formal_runtime_variant is not None
            or self.formal_runtime_session is not None
        ):
            raise ValueError("formal runtimes are already attached; replace is forbidden")
        from framework.model_runtime import BoundInteractionModel
        from framework.runtime_variant import RuntimeVariantBinding
        from framework.temporal_runtime import BoundTemporalDomain
        if not isinstance(model_runtime, BoundInteractionModel):
            raise ValueError("formal model runtime must be registry-bound")
        if not isinstance(temporal_runtime, BoundTemporalDomain):
            raise ValueError("formal temporal runtime must be registry-bound")
        if not isinstance(runtime_variant, RuntimeVariantBinding):
            raise ValueError("formal runtime variant must be explicitly bound")
        runtime_variant.verify_for(
            model_binding=model_runtime.binding,
            temporal_binding=temporal_runtime.binding,
        )

        restored = None
        if "runtime_session_binding" in self.profile:
            from ops.runtime_session_bootstrap import (
                restore_bootstrapped_runtime_session,
            )
            restored = restore_bootstrapped_runtime_session(
                profile=self.profile,
                model_runtime=model_runtime,
                temporal_runtime=temporal_runtime,
                runtime_variant=runtime_variant,
            )
        # Publish only after every validation/restore step succeeds.
        self._formal_model_runtime = model_runtime
        self._formal_temporal_runtime = temporal_runtime
        self._formal_runtime_variant = runtime_variant
        self.formal_runtime_session = restored
        return restored

    def export_formal_diagnostic_snapshot(self):
        """Return the current committed formal diagnostic export, if available.

        This boundary deliberately exposes neither bound runtime nor session.
        The product host performs all lineage validation and owns the exact
        definition of an available committed diagnostic slice.
        """
        if (
            self._formal_model_runtime is None
            or self.formal_runtime_session is None
        ):
            return None
        from ops.product_diagnostic_snapshot import (
            export_latest_product_diagnostic_snapshot,
        )
        return export_latest_product_diagnostic_snapshot(
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
        )

    def bootstrap_formal_runtime_session(
        self, *, initial_coordinate, parameter_payload, state_payload,
    ):
        """Explicitly create and persist one Alpha 0.4 schema-v3 session.

        The composition root must supply package-owned payloads.  This method
        never derives them from the legacy Runner profile or state.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
        ):
            raise ValueError(
                "formal runtimes are required before session bootstrap")
        if not isinstance(self.profile, Mapping):
            raise ValueError(
                "formal session bootstrap requires an existing profile")
        from ops.runtime_session_bootstrap import bootstrap_runtime_session
        created = bootstrap_runtime_session(
            profile=self.profile,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            runtime_variant=self._formal_runtime_variant,
            initial_coordinate=initial_coordinate,
            parameter_payload=parameter_payload,
            state_payload=state_payload,
        )
        # save_profile is the sole mutation.  Publish the in-memory binding only
        # after the complete v3 profile succeeds, so a failed write cannot make
        # this Runner believe it owns an unpersisted formal session.
        self.store.save_profile(dict(created.profile))
        self.profile = dict(created.profile)
        self.formal_runtime_session = created
        return created

    def activate_formal_runtimes(
        self, *, model_runtime, temporal_runtime, runtime_variant,
        initial_coordinate, parameter_payload, state_payload,
    ):
        """Atomically bind dependencies and create one new schema-v3 session."""
        if not isinstance(self.profile, Mapping):
            raise ValueError(
                "formal runtime activation requires an existing profile")
        if "runtime_session_binding" in self.profile:
            raise ValueError(
                "existing formal session must be restored without initialization")
        if (
            self._formal_model_runtime is not None
            or self._formal_temporal_runtime is not None
            or self._formal_runtime_variant is not None
            or self.formal_runtime_session is not None
        ):
            raise ValueError("formal runtimes are already attached; replace is forbidden")
        from framework.model_runtime import BoundInteractionModel
        from framework.runtime_variant import RuntimeVariantBinding
        from framework.temporal_runtime import BoundTemporalDomain
        if not isinstance(model_runtime, BoundInteractionModel):
            raise ValueError("formal model runtime must be registry-bound")
        if not isinstance(temporal_runtime, BoundTemporalDomain):
            raise ValueError("formal temporal runtime must be registry-bound")
        if not isinstance(runtime_variant, RuntimeVariantBinding):
            raise ValueError("formal runtime variant must be explicitly bound")
        runtime_variant.verify_for(
            model_binding=model_runtime.binding,
            temporal_binding=temporal_runtime.binding,
        )

        from ops.runtime_session_bootstrap import bootstrap_runtime_session
        created = bootstrap_runtime_session(
            profile=self.profile,
            model_runtime=model_runtime,
            temporal_runtime=temporal_runtime,
            runtime_variant=runtime_variant,
            initial_coordinate=initial_coordinate,
            parameter_payload=parameter_payload,
            state_payload=state_payload,
        )
        # Persistence is the final fallible step before in-memory publication.
        self.store.save_profile(dict(created.profile))
        self._formal_model_runtime = model_runtime
        self._formal_temporal_runtime = temporal_runtime
        self._formal_runtime_variant = runtime_variant
        self.profile = dict(created.profile)
        self.formal_runtime_session = created
        return created

    def commit_formal_runtime_event(
        self, *, conversation_id, participant_id, event_id, action_id,
        measurement_payload, context_metadata=None,
    ):
        """Atomically persist and publish one explicit formal event transaction.

        This is an integration seam, not the default product event path.  The
        caller owns the already committed event fact and its package-owned
        measurement payload; this method neither infers semantics from text nor
        advances time, polls dynamic tick, writes a message, or invokes Writer.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "formal runtime event requires an active v3 runtime session")
        from ops.runtime_event_transaction import (
            execute_committed_event_transaction,
        )
        transaction = execute_committed_event_transaction(
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            scope_ref=conversation_id,
            subject_ref=participant_id,
            event_ref=event_id,
            action_ref=action_id,
            measurement_payload=measurement_payload,
            context_metadata=context_metadata,
        )
        # Persistence remains the last fallible operation before publication.
        self.store.save_profile(dict(transaction.session.profile))
        self.profile = dict(transaction.session.profile)
        self.formal_runtime_session = transaction.session
        return transaction

    def prepare_formal_runtime_full_memory_input(
        self, *, conversation_id, participant_id, event_id, action_id,
        candidate_authorization=None, context_metadata=None,
    ):
        """Build one signed Full Memory input from this session's profile.

        Candidate completeness is established by the owner-local authorization
        product, not by a caller-supplied list. Runner does not interpret model
        dimensions; the bound package converts committed nodes and lineage.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "formal full memory input requires an active v3 session")
        from framework.runtime_kernel import RuntimeProgressAuthority
        from ops.runtime_full_memory_input_product import (
            prepare_runtime_full_memory_input_product_from_profile,
        )
        from ops.runtime_full_memory_candidate_authorization import (
            authorize_runtime_full_memory_candidates,
        )

        authority = RuntimeProgressAuthority(
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self._formal_temporal_runtime,
        )
        context = authority.invocation_context_v2(
            self.formal_runtime_session.position,
            scope_ref=conversation_id,
            subject_ref=participant_id,
            event_ref=event_id,
            action_ref=action_id,
            metadata=context_metadata,
        )
        if candidate_authorization is None:
            candidate_authorization = (
                authorize_runtime_full_memory_candidates(
                    profile=self.formal_runtime_session.profile,
                    session_binding_fingerprint=(
                        self.formal_runtime_session.session_binding.fingerprint),
                    context=context,
                )
            )
        return prepare_runtime_full_memory_input_product_from_profile(
            model_runtime=self._formal_model_runtime,
            context=context,
            profile=self.formal_runtime_session.profile,
            candidate_authorization=candidate_authorization,
        )

    def commit_formal_runtime_full_memory_event(
        self, *, conversation_id, participant_id, event_id, action_id,
        measurement_payload, memory_input_product=None,
        candidate_authorization=None, context_metadata=None,
    ):
        """Persist accepted Full state and its lineage in one profile write.

        This explicit seam is not a default Actor path.  It neither retrieves
        candidate content nor invokes Writer, dynamic tick, routing, or send.
        Callers may pass a previously signed input product or an independently
        verified candidate authorization. With neither, Runner derives the
        complete owner-local authorization from its committed node journal.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "formal full memory event requires an active v3 session")
        from ops.runtime_full_memory_event_transaction import (
            execute_prepared_full_memory_event_transaction,
        )
        from ops.runtime_full_memory_profile_commit import (
            commit_runtime_full_memory_profile,
        )

        if (
            memory_input_product is not None
            and candidate_authorization is not None
        ):
            raise ValueError(
                "provide either memory_input_product or candidate authorization")
        if memory_input_product is None:
            memory_input_product = self.prepare_formal_runtime_full_memory_input(
                conversation_id=conversation_id,
                participant_id=participant_id,
                event_id=event_id,
                action_id=action_id,
                candidate_authorization=candidate_authorization,
                context_metadata=context_metadata,
            )

        transaction = execute_prepared_full_memory_event_transaction(
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            scope_ref=conversation_id,
            subject_ref=participant_id,
            event_ref=event_id,
            action_ref=action_id,
            measurement_payload=measurement_payload,
            memory_input_product=memory_input_product,
            context_metadata=context_metadata,
        )
        committed = commit_runtime_full_memory_profile(
            transaction=transaction,
            model_runtime=self._formal_model_runtime,
        )
        # The state snapshot and lineage journal become durable together.
        self.store.save_profile(dict(committed.session.profile))
        self.profile = dict(committed.session.profile)
        self.formal_runtime_session = committed.session
        return committed

    def advance_formal_runtime_progress(
        self, *, target_coordinate, conversation_id, participant_id,
        event_id, action_id, context_metadata=None,
    ):
        """Atomically persist and publish one explicit formal progress fact.

        This method does not read the host clock, schedule a timer, run dynamic
        tick, or send.  The caller supplies the target coordinate and committed
        fact identity; the bound TemporalDomain alone interprets that coordinate.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "formal runtime progress requires an active v3 runtime session")
        from ops.runtime_progress_transaction import (
            execute_runtime_progress_transaction,
        )
        transaction = execute_runtime_progress_transaction(
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            target_coordinate=target_coordinate,
            scope_ref=conversation_id,
            subject_ref=participant_id,
            event_ref=event_id,
            action_ref=action_id,
            context_metadata=context_metadata,
        )
        self.store.save_profile(dict(transaction.session.profile))
        self.profile = dict(transaction.session.profile)
        self.formal_runtime_session = transaction.session
        return transaction

    def commit_formal_runtime_autonomous_observation(
        self, *, observation_target, target_coordinate, participant_id,
        tick_event_id, assessment_payload,
    ):
        """Persist one complete due-tick assessment before publishing it.

        This explicit seam does not choose a Conversation, prepare a Writer,
        commit an output, route a delivery, or send.  A failed profile write
        leaves the Runner's published session unchanged.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "formal autonomous observation requires an active v3 runtime session")
        from ops.runtime_autonomous_observation import (
            execute_runtime_autonomous_observation_transaction,
        )
        transaction = execute_runtime_autonomous_observation_transaction(
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            observation_target=observation_target,
            target_coordinate=target_coordinate,
            participant_id=participant_id,
            tick_event_id=tick_event_id,
            assessment_payload=assessment_payload,
        )
        self.store.save_profile(dict(transaction.session.profile))
        self.profile = dict(transaction.session.profile)
        self.formal_runtime_session = transaction.session
        return transaction

    def commit_formal_runtime_fact(
        self, *, target_coordinate, conversation_id, participant_id,
        event_id, action_id, measurement_payload, context_metadata=None,
    ):
        """Persist one externally committed progress/event fact exactly once.

        This explicit integration seam neither observes a clock nor commits a
        message.  Equal canonical positions skip progress; a forward position
        is settled before the event, and only the final profile is visible to
        Store or Runner memory.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "formal runtime fact requires an active v3 runtime session")
        from ops.runtime_committed_fact_transaction import (
            execute_runtime_committed_fact_transaction,
        )
        from ops.runtime_full_memory_committed_fact_transaction import (
            execute_runtime_full_memory_committed_fact_transaction,
            supports_full_memory_fact_transaction,
        )
        transaction_host = (
            execute_runtime_full_memory_committed_fact_transaction
            if supports_full_memory_fact_transaction(
                self._formal_model_runtime)
            else execute_runtime_committed_fact_transaction
        )
        transaction = transaction_host(
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            target_coordinate=target_coordinate,
            scope_ref=conversation_id,
            subject_ref=participant_id,
            event_ref=event_id,
            action_ref=action_id,
            measurement_payload=measurement_payload,
            context_metadata=context_metadata,
        )
        self.store.save_profile(dict(transaction.session.profile))
        self.profile = dict(transaction.session.profile)
        self.formal_runtime_session = transaction.session
        return transaction

    def commit_verified_formal_runtime_fact(self, evidence):
        """Commit one product fact only after its receipt lineage was verified.

        This remains an explicit integration seam.  It does not read Store
        tails, infer semantics from text, observe time, poll dynamic tick, call
        Writer/provider, or switch the default product path.
        """
        from ops.runtime_product_fact_bridge import RuntimeCommittedFactEvidence
        if not isinstance(evidence, RuntimeCommittedFactEvidence):
            raise ValueError(
                "verified formal fact requires RuntimeCommittedFactEvidence")
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "verified formal fact requires an active v3 runtime session")
        evidence.verify_for(
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
        )
        if (
            evidence.expected_runtime_ordinal is not None
            and self.formal_runtime_session.position.runtime_ordinal
            != evidence.expected_runtime_ordinal
        ):
            from framework.runtime_kernel import RuntimeKernelContractError
            raise RuntimeKernelContractError(
                "verified formal fact expected runtime ordinal is stale")
        return self.commit_formal_runtime_fact(
            target_coordinate=evidence.target_coordinate,
            conversation_id=evidence.conversation_id,
            participant_id=evidence.participant_id,
            event_id=evidence.event_id,
            action_id=evidence.action_id,
            measurement_payload=evidence.measurement_payload,
            context_metadata=evidence.context_metadata,
        )

    def commit_formal_product_recipient_ingress(
        self, *, route, conversation_registry, recipient_participant_id,
        semantic_projection, measurement_evidence,
    ):
        """Persist one exact route delivery and its optional F03 atomically.

        The pure ingress host returns a complete profile value.  Runner writes
        that value once and publishes it only after storage succeeds; it never
        calls the legacy received-event flow or starts a responder.
        """
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self._formal_runtime_variant is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "recipient ingress requires an active formal runtime session")
        identity = self._writer_output_identity()
        recipient_id = str(recipient_participant_id or "").strip()
        if (
            not isinstance(identity, Mapping)
            or identity.get("participant_id") != recipient_id
        ):
            raise ValueError(
                "recipient ingress must target this Runner participant")
        from ops.runtime_product_recipient_ingress import (
            execute_product_recipient_ingress,
        )
        from ops.runtime_expression_state import (
            restore_runtime_expression_state,
        )
        expression_state = restore_runtime_expression_state(
            self.formal_runtime_session.profile,
            session_binding=self.formal_runtime_session.session_binding,
        )
        result = execute_product_recipient_ingress(
            route=route,
            conversation_registry=conversation_registry,
            recipient_participant_id=recipient_id,
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            runtime_variant=self._formal_runtime_variant,
            semantic_projection=semantic_projection,
            measurement_evidence=measurement_evidence,
            committed_remainders=expression_state.committed_remainders,
        )
        if not result.already_committed:
            self.store.save_profile(dict(result.session.profile))
            self.profile = dict(result.session.profile)
            self.formal_runtime_session = result.session
        return result

    def project_formal_runtime_fact_expression(
        self, transaction, *, committed_remainders,
    ):
        """Read expression resources only after this fact is current.

        Projection deliberately has no persistence path.  A projection error
        therefore cannot undo the already committed interaction fact.
        """
        from ops.runtime_committed_fact_transaction import (
            RuntimeCommittedFactTransaction,
        )
        from ops.runtime_full_memory_committed_fact_transaction import (
            RuntimeFullMemoryCommittedFactTransaction,
        )
        if not isinstance(transaction, (
            RuntimeCommittedFactTransaction,
            RuntimeFullMemoryCommittedFactTransaction,
        )):
            raise ValueError(
                "expression projection requires a committed fact transaction")
        if (
            self._formal_model_runtime is None
            or self.formal_runtime_session is None
            or self.formal_runtime_session != transaction.session
            or self.profile != dict(transaction.session.profile)
        ):
            raise ValueError(
                "expression projection requires the current committed fact")
        from ops.model_expression_projection_shadow import (
            produce_expression_projection_shadow,
        )
        event = transaction.event_transaction
        previous = transaction.event_input_session
        return produce_expression_projection_shadow(
            runtime=self._formal_model_runtime,
            session_binding=transaction.session.session_binding,
            previous_state=previous.state,
            previous_position=transaction.settlement_input_position,
            model_parameters=previous.parameters,
            settlement_context=event.context,
            settlement_execution=event.settlement_execution,
            settlement_acceptance=event.acceptance,
            committed_remainders=committed_remainders,
        )

    def complete_formal_product_recipient_expression(self, *, delivery_id):
        """Resume and persist one recipient's post-commit expression only."""
        if (
            self._formal_model_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "expression completion requires an active formal runtime session")
        from ops.runtime_product_expression_continuation import (
            commit_product_expression_completion,
            load_product_expression_continuation,
            restore_product_expression_projection,
        )
        continuation = load_product_expression_continuation(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        expression = restore_product_expression_projection(
            continuation=continuation,
            session=self.formal_runtime_session,
            runtime=self._formal_model_runtime,
        )
        session, completion = commit_product_expression_completion(
            self.formal_runtime_session,
            continuation=continuation,
            expression=expression,
        )
        if not completion.already_completed:
            self.store.save_profile(dict(session.profile))
            self.profile = dict(session.profile)
            self.formal_runtime_session = session
        return completion

    def recover_formal_product_recipient_lifecycle(self, *, delivery_id):
        """Recover one complete lifecycle without re-submitting its fact."""
        completion = self.complete_formal_product_recipient_expression(
            delivery_id=delivery_id)
        from framework.model_api import model_data_fingerprint
        from ops.runtime_product_expression_continuation import (
            load_product_expression_continuation,
            restore_product_fact_products,
        )
        from ops.runtime_product_fact_lifecycle import (
            complete_product_fact_lifecycle,
        )
        continuation = load_product_expression_continuation(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        evidence, transaction = restore_product_fact_products(
            continuation=continuation,
            session=self.formal_runtime_session,
            runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
        )
        lifecycle = complete_product_fact_lifecycle(
            evidence=evidence,
            transaction=transaction,
            expression=completion.expression,
        )
        if completion.receipt.get("expression_receipt_fingerprint") != (
            model_data_fingerprint(lifecycle.expression.receipt)
        ):
            raise ValueError(
                "recovered lifecycle differs from expression completion")
        return lifecycle

    def plan_formal_product_recipient_observation(
        self, *, delivery_id, planning_payload,
    ):
        """Persist one post-event observation target without scheduling it."""
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "observation planning requires an active formal runtime session")
        from ops.runtime_product_observation_plan import (
            build_product_observation_plan,
            commit_product_observation_plan,
            load_product_observation_plan,
        )
        existing = load_product_observation_plan(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self._formal_temporal_runtime,
        )
        if existing is not None:
            return existing
        lifecycle = self.recover_formal_product_recipient_lifecycle(
            delivery_id=delivery_id)
        plan = build_product_observation_plan(
            delivery_id=delivery_id,
            lifecycle=lifecycle,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            planning_payload=planning_payload,
        )
        session, committed = commit_product_observation_plan(
            self.formal_runtime_session,
            plan=plan,
            temporal_runtime=self._formal_temporal_runtime,
        )
        self.store.save_profile(dict(session.profile))
        self.profile = dict(session.profile)
        self.formal_runtime_session = session
        return committed

    def is_formal_product_recipient_work_complete(
        self, *, delivery_id, delivery_scope,
        observation_planning_required,
    ):
        """Derive dispatch completion from existing owner-local products only."""
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "recipient completion check requires an active formal session")
        scope = str(delivery_scope or "").strip().lower()
        if scope not in {"addressed", "observed"}:
            raise ValueError("recipient completion check scope is invalid")
        from ops.runtime_product_recipient_ingress import (
            committed_product_recipient_delivery_ids,
        )
        if delivery_id not in committed_product_recipient_delivery_ids(
            self.formal_runtime_session
        ):
            return False
        if scope == "observed":
            return True
        from ops.runtime_product_expression_continuation import (
            load_product_expression_completion_receipt,
            load_product_expression_continuation,
        )
        continuation = load_product_expression_continuation(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        completion = load_product_expression_completion_receipt(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
        )
        if completion is None:
            return False
        response_policy = continuation.fact_evidence.get("response_policy")
        if response_policy not in {"required", "eligible"}:
            raise ValueError("recipient completion response policy is invalid")
        if response_policy != "eligible" or not observation_planning_required:
            return True
        from ops.runtime_product_observation_plan import (
            load_product_observation_plan,
        )
        return load_product_observation_plan(
            self.formal_runtime_session.profile,
            delivery_id=delivery_id,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self._formal_temporal_runtime,
        ) is not None

    def plan_formal_product_output_observation(
        self, *, lifecycle, action_id, planning_payload,
    ):
        """Replace the active target from one committed owner output state."""
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "output observation planning requires an active formal session")
        from ops.runtime_product_observation_plan import (
            build_product_observation_plan_from_output,
            commit_product_observation_plan,
            load_product_observation_plan_by_id,
        )
        plan_id = f"observation-plan:committed_output_action:{action_id}"
        existing = load_product_observation_plan_by_id(
            self.formal_runtime_session.profile,
            plan_id=plan_id,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self._formal_temporal_runtime,
        )
        if existing is not None:
            return existing
        plan = build_product_observation_plan_from_output(
            action_id=action_id,
            lifecycle=lifecycle,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            planning_payload=planning_payload,
        )
        session, committed = commit_product_observation_plan(
            self.formal_runtime_session,
            plan=plan,
            temporal_runtime=self._formal_temporal_runtime,
        )
        self.store.save_profile(dict(session.profile))
        self.profile = dict(session.profile)
        self.formal_runtime_session = session
        return committed

    def read_formal_active_observation_plan(self):
        """Restore the exact durable active plan without advancing anything."""
        if (
            self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "active observation plan requires an active formal session")
        from ops.runtime_product_observation_plan import (
            load_active_product_observation_plan,
        )
        return load_active_product_observation_plan(
            self.formal_runtime_session.profile,
            session_binding=self.formal_runtime_session.session_binding,
            temporal_runtime=self._formal_temporal_runtime,
        )

    def probe_formal_product_observation_due(self, *, target_coordinate):
        """Read the durable active plan and ask TemporalDomain if it is due."""
        if (
            self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "observation due probe requires an active formal runtime session")
        from ops.runtime_product_observation_due import (
            probe_active_product_observation_due,
        )
        return probe_active_product_observation_due(
            self.formal_runtime_session,
            temporal_runtime=self._formal_temporal_runtime,
            target_coordinate=target_coordinate,
        )

    def commit_due_formal_product_observation(
        self, *, target_coordinate, participant_id, tick_event_id,
    ):
        """Atomically consume the active target and rotate its successor."""
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "product observation tick requires an active formal session")
        from ops.runtime_product_observation_tick import (
            execute_product_observation_tick,
        )
        committed = execute_product_observation_tick(
            session=self.formal_runtime_session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            target_coordinate=target_coordinate,
            participant_id=participant_id,
            tick_event_id=tick_event_id,
        )
        if committed.already_committed:
            return committed
        self.store.save_profile(dict(committed.session.profile))
        self.profile = dict(committed.session.profile)
        self.formal_runtime_session = committed.session
        return committed

    def recover_pending_formal_product_autonomous_ticks(self):
        """Read exact committed grants; do not progress time or invoke models."""
        if (
            self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            return ()
        from ops.runtime_product_observation_tick import (
            recover_pending_product_observation_ticks,
        )
        return recover_pending_product_observation_ticks(
            session=self.formal_runtime_session,
            temporal_runtime=self._formal_temporal_runtime,
        )

    def commit_verified_formal_runtime_lifecycle(
        self, evidence, *, committed_remainders,
    ):
        """Commit one verified fact, then project its expression resources.

        This is an explicit composition-root entry only.  Once the first call
        returns, that fact is authoritative.  A later projection or lineage
        failure is therefore reported with the committed transaction and is
        never converted into a rollback or a second fact submission.
        """
        from ops.runtime_product_fact_lifecycle import (
            PostCommitExpressionProjectionError,
            complete_product_fact_lifecycle,
        )
        transaction = self.commit_verified_formal_runtime_fact(evidence)
        try:
            expression = self.project_formal_runtime_fact_expression(
                transaction,
                committed_remainders=committed_remainders,
            )
            return complete_product_fact_lifecycle(
                evidence=evidence,
                transaction=transaction,
                expression=expression,
            )
        except Exception as exc:
            raise PostCommitExpressionProjectionError(
                transaction=transaction,
                evidence=evidence,
                cause=exc,
            ) from exc

    def commit_recoverable_verified_formal_runtime_lifecycle(
        self, evidence, *, committed_remainders, continuation_id,
        anchor_receipt,
    ):
        """Atomically persist one fact with its read-only retry continuation."""
        from ops.runtime_product_fact_bridge import RuntimeCommittedFactEvidence
        from ops.runtime_committed_fact_transaction import (
            execute_runtime_committed_fact_transaction,
        )
        from ops.runtime_full_memory_committed_fact_transaction import (
            execute_runtime_full_memory_committed_fact_transaction,
            supports_full_memory_fact_transaction,
        )
        from ops.runtime_product_expression_continuation import (
            attach_product_expression_continuation,
            build_product_expression_continuation,
        )
        if not isinstance(evidence, RuntimeCommittedFactEvidence):
            raise ValueError(
                "recoverable formal lifecycle requires verified evidence")
        if (
            self._formal_model_runtime is None
            or self._formal_temporal_runtime is None
            or self.formal_runtime_session is None
        ):
            raise ValueError(
                "recoverable formal lifecycle requires active v3 session")
        evidence.verify_for(
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
        )
        session = self.formal_runtime_session
        if (
            evidence.expected_runtime_ordinal is not None
            and session.position.runtime_ordinal
            != evidence.expected_runtime_ordinal
        ):
            from framework.runtime_kernel import RuntimeKernelContractError
            raise RuntimeKernelContractError(
                "recoverable formal lifecycle expected ordinal is stale")
        transaction_host = (
            execute_runtime_full_memory_committed_fact_transaction
            if supports_full_memory_fact_transaction(
                self._formal_model_runtime)
            else execute_runtime_committed_fact_transaction
        )
        transaction = transaction_host(
            session=session,
            model_runtime=self._formal_model_runtime,
            temporal_runtime=self._formal_temporal_runtime,
            target_coordinate=evidence.target_coordinate,
            scope_ref=evidence.conversation_id,
            subject_ref=evidence.participant_id,
            event_ref=evidence.event_id,
            action_ref=evidence.action_id,
            measurement_payload=evidence.measurement_payload,
            context_metadata=evidence.context_metadata,
        )
        continuation = build_product_expression_continuation(
            transaction=transaction,
            evidence=evidence,
            continuation_id=continuation_id,
            anchor_receipt=anchor_receipt,
            committed_remainders=committed_remainders,
        )
        next_session = attach_product_expression_continuation(
            transaction.session, continuation=continuation)
        self.store.save_profile(dict(next_session.profile))
        self.profile = dict(next_session.profile)
        self.formal_runtime_session = next_session
        return self.recover_formal_product_recipient_lifecycle(
            delivery_id=continuation_id)

    def _apply_setup(self, parsed, background_text, preference_text,
                     preference_declared=None, initial_state=None):
        if self._model_shadow_runtime is not None and self.profile:
            raise ValueError("model binding cannot silently replace an existing session")
        constants = SchemaMatrix().generate_constants(parsed["weights"])
        # ``None`` preserves the legacy meaning: no text means the UI may make a
        # one-time fallback inquiry.  ``True`` means the creation flow did show
        # the field and the user deliberately left it blank; B0 stays uniform.
        declared = bool(preference_text) if preference_declared is None else bool(preference_declared)
        neutral_prior = {k: 0.2 for k in PROTOTYPES}   # 最大不确定度

        # 角色能否进入会话不能依赖可选的偏好模型。UserStore 会在 Runner 构造时
        # 建目录；过去 preference 调用若超时，目录就被遗留为没有 profile/persona/
        # state 的“幽灵角色”。先把可恢复的基础三件套写齐，再把偏好解析作为可
        # 降级的后续事务。它不改变卡片、墙钟或 B0 的含义，只保证失败时仍是中性 B0。
        x0 = new_state(initial_state)["vars"]
        created_at = int(_time.time() * 1000)
        profile = {"weights": parsed["weights"], "constants": constants,
                   "ammo": parsed["ammo"], "prior": neutral_prior,
                   "background": background_text,
                   # X0 与 C（人格常量）分开落盘：它只是双方在开局已确认的关系
                   # 起点，不能由背景文案或 writer 人设隐式改写。
                   "initial_state": {"vars": x0,
                                     "source": "explicit" if initial_state is not None
                                     else "default"},
                   "preference_declared": declared,
                   "preference_status": ("pending" if preference_text
                                         else "declared_blank" if declared
                                         else "not_requested"),
                   "manifest": build_manifest(parsed, seed=self.seed),
                   "created_at": created_at}
        # 即使用户尚未填写偏好，也创建一个明确关闭的会话 B₀ 锚。它不产生校正，
        # 但使后续显式保存可作为 append-only 新版本，而不是覆盖一份裸 prior。
        from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
            append_explicit_b0_version,
        )
        profile = append_explicit_b0_version(
            profile, neutral_prior, enabled=False, source="initial_unspecified", at_ms=created_at,
        )
        if self._model_shadow_runtime is not None:
            from ops.model_session_binding import append_shadow_model_binding
            profile = append_shadow_model_binding(
                profile, runtime=self._model_shadow_runtime, at_ms=created_at)
        if self.cadence_params is not None:
            # `params_sha` 与 action/event 使用同一份带 override 的快照；manifest 不可
            # 继续误标为模块级默认参数。这里只留 hash/mode，不把测试参数正文扩散到
            # 消息或 prompt。
            profile["manifest"]["params_sha"] = self.params_sha
            profile["manifest"]["cadence_mode"] = "runner_override"
            profile["manifest"]["cadence_override_sha"] = sha256_text(self.cadence_params)
        if self._injected_memory_state_policy is not None:
            # 政策没有默认值；显式新建会话时才校验、版本化并绑定。此后重启只读取该档案，
            # 不能由 Runner 参数覆盖它。
            profile = append_memory_state_policy_version(
                profile, self._injected_memory_state_policy, at_ms=created_at,
                source="initial_explicit_runner")
            policy_binding = current_memory_state_policy(profile)
            self.memory_state_policy = dict(policy_binding["policy"])
            self.memory_state_policy_binding_version = str(policy_binding["version"])
        if self._injected_memory_state_policy_family is not None:
            # 先解析同一会话的 C_effective/B₀ 身份，再绑定策略族。此处不读取 X、
            # 文本、RAG 候选或 UI；它只建立 Alpha 0.3 的可回放起点。
            from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
                resolve_session_effective_constants,
            )
            constant_binding = resolve_session_effective_constants(constants, profile)
            profile = append_recall_policy_family_anchor(
                profile, self._injected_memory_state_policy_family,
                effective_constants_version=constant_binding["effective_constants_version"],
                b0_version=constant_binding["b0_version"], at_ms=created_at,
                source="initial_new_session")
            self.memory_state_policy_family_anchor = dict(
                current_recall_policy_family_anchor(profile) or {})
        if self._injected_memory_product_family is not None:
            # Alpha 0.4 是独立 append-only family identity；新会话绑定只引用同一
            # C_effective/B0 版本，不改变 Alpha 0.3 family 或任何实时调度。
            from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
                resolve_session_effective_constants,
            )
            constant_binding = resolve_session_effective_constants(constants, profile)
            profile = append_memory_product_family_anchor(
                profile, self._injected_memory_product_family,
                effective_constants_version=constant_binding[
                    "effective_constants_version"],
                b0_version=constant_binding["b0_version"], at_ms=created_at,
                source="initial_new_session")
            self.memory_product_family_anchor = dict(
                current_memory_product_family_anchor(profile) or {})
        self.store.save_profile(profile)
        if self._model_shadow_runtime is not None:
            self.model_shadow_binding = dict(profile["interaction_model_binding"])
        self.store.save_persona_card(parsed["persona_card"])
        self.profile = profile
        self.prior = neutral_prior
        self.persona_blocks = parse_persona_blocks(parsed["persona_card"])
        # 新角色在 setup 时才提交 X0。已有会话绝不能因为再次加载卡片被重置；
        # 这保持“创建态 → 事件/时间态”的单向流动。
        if self.store.load_state() is None:
            self.state = new_state(initial_state)
            self.store.save_state(self.state)
        # 开局声明也要同步到关系层用户卡；否则只有后续重声明才会双写，重启后两处不一致。
        self._sync_user_card_prior()

        if not preference_text:
            self.store.append_event({
                "kind": "preference_initialization", "ts": int(_time.time() * 1000),
                "detail": {"status": profile["preference_status"],
                           "provider_attempted": False,
                           "preference_declared": declared,
                           "b0_version": profile["session_b0_version"], "b0_enabled": False},
            })
            return parsed

        try:
            ps, pu = build_preference_messages(preference_text)
            praw = self.preference.call(ps, pu,
                                        temperature=DEFAULTS["refine_temperature"], require_json=True)
            prior = parse_preference(praw)
        except Exception as exc:  # noqa: BLE001 -- 外部能力失败只能降级，不能毁掉角色创建。
            profile["preference_status"] = "degraded"
            profile["preference_error"] = type(exc).__name__
            self.store.save_profile(profile)
            self.profile = profile
            self.store.append_event({
                "kind": "preference_initialization", "ts": int(_time.time() * 1000),
                "detail": {"status": "degraded", "provider_attempted": True,
                           "error_type": type(exc).__name__,
                           "preference_declared": declared,
                           "b0_version": profile["session_b0_version"], "b0_enabled": False},
            })
            return parsed

        profile = append_explicit_b0_version(
            profile, prior, enabled=True, source="initial_explicit_text",
            at_ms=int(_time.time() * 1000),
        )
        profile["preference_status"] = "applied"
        profile.pop("preference_error", None)
        self.store.save_profile(profile)
        self.profile = profile
        self.prior = prior
        self._sync_user_card_prior()
        self.store.append_event({
                "kind": "preference_initialization", "ts": int(_time.time() * 1000),
                "detail": {"status": "applied", "provider_attempted": True,
                       "preference_declared": declared,
                       "b0_version": profile["session_b0_version"], "b0_enabled": True},
        })
        return parsed

    def apply_preference(self, text):
        """事后声明陪伴体验（开局漏问的补问 / 随时重声明）：
        偏好 Agent → prior 更新进 profile。空文本返回 False。"""
        if not (text or "").strip():
            return False
        if self.preference is None:
            return False
        ps, pu = build_preference_messages(str(text).strip())
        praw = self.preference.call(ps, pu,
                                    temperature=DEFAULTS["refine_temperature"], require_json=True)
        prior = parse_preference(praw)
        profile = self.store.load_profile() or {}
        previous_b0_version = profile.get("session_b0_version")
        from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
            append_explicit_b0_version,
        )
        profile = append_explicit_b0_version(
            profile, prior, enabled=True, source="explicit_text", at_ms=int(_time.time() * 1000),
        )
        profile["preference_declared"] = True
        self.store.save_profile(profile)
        self.profile = profile
        self.prior = prior
        self._sync_user_card_prior()
        self.store.append_event({
            "kind": "b0_explicit_migration", "ts": int(_time.time() * 1000),
            "detail": {"from_version": previous_b0_version,
                       "to_version": profile["session_b0_version"],
                       "enabled": True, "source": "explicit_text"},
        })
        return True

    def apply_preference_weights(self, weights):
        """直接应用用户主动设置的五维占比（供滑块/标签 UI 和确定性测试使用）。

        它与 ``apply_preference(text)`` 同属声明入口：只更新 B₀/profile/user_card，
        不读取会话行为，也不启动调参或漂移。调用前必须完成角色 ``setup``。
        """
        if not self.profile:
            raise RuntimeError("尚未 setup()")
        from interaction_extensions.preference_calibration.cx_six_seven.preference import (
            normalize_declared_weights,
        )
        prior = normalize_declared_weights(weights)
        profile = self.store.load_profile() or {}
        previous_b0_version = profile.get("session_b0_version")
        from interaction_extensions.preference_calibration.cx_six_seven.b0_versions import (
            append_explicit_b0_version,
        )
        profile = append_explicit_b0_version(
            profile, prior, enabled=True, source="explicit_weights", at_ms=int(_time.time() * 1000),
        )
        profile["preference_declared"] = True
        self.store.save_profile(profile)
        self.profile = profile
        self.prior = prior
        self._sync_user_card_prior()
        self.store.append_event({
            "kind": "b0_explicit_migration", "ts": int(_time.time() * 1000),
            "detail": {"from_version": previous_b0_version,
                       "to_version": profile["session_b0_version"],
                       "enabled": True, "source": "explicit_weights"},
        })
        return dict(prior)

    def mark_preference_declared(self):
        """记录用户已跳过首次偏好声明，保持 profile 与用户卡一致。

        这是界面流程事实，不解释用户心理、不创建偏好方向、更不进入时间或记忆计算。
        仍须经 Actor 调用，避免 GUI 在角色事务之外直接写档。
        """
        if not self.profile:
            return False
        profile = self.store.load_profile() or {}
        profile["preference_declared"] = True
        self.store.save_profile(profile)
        self.profile = profile
        self._sync_user_card_prior()
        return True

    # ------------------------------------------------------------------
    # 回复生成：responder 的唯一非结构化材料入口是已经装配完成的 prompt。
    # ------------------------------------------------------------------
    def _generate_reply(self, system, user, temperature, recall_event_ids=(),
                        on_draft=None, should_continue=None):
        """生成一份候选并解析其结构化回执。

        输出含回复、台账、自我表达及召回表达审计声明：承 MANE Agent3→Agent0 洞察——
        responder 自己就地提交台账（facts 精确归因本条回复）；解析失败回落
        纯文本（facts 空 → 批量提取兜底）。
        """
        def cancelled_result():
            # 旧输入被新 Enter 抢占：返回无副作用空候选，使 on_user_message 在既有
            # 提交闸处归档 input_aborted，而不是解析/重试一份已经失效的流内容。
            return ("", [], [], {}, [], [], [], {})

        def call_responder(current_system, *, stream_draft=False):
            """调用 responder；可选草稿只是本次网络流的临时展示订阅。

            完整 ``raw`` 仍是唯一交给 ``parse_responder`` 的输入。网络中断或
            协议不完整时，候选不会获得互动历史／记忆资格；AUD-219 产品切换后仍须
            进入独立 WriterAttempt 审计档案。
            """
            if callable(should_continue) and not should_continue():
                return None
            stream_call = getattr(self.responder, "stream_call", None)
            if not stream_draft or not callable(on_draft) or not callable(stream_call):
                raw = self.responder.call(current_system, user, temperature=temperature,
                                          require_json=True)
                return None if callable(should_continue) and not should_continue() else raw
            extractor = ReplyDraftExtractor()

            def receive_delta(delta):
                if extractor.feed(delta):
                    # 只给 UI 最新稳定前缀，永远不转发原始 JSON delta。
                    on_draft({"text": extractor.visible})

            kwargs = {"temperature": temperature, "require_json": True,
                      "on_delta": receive_delta}
            if (callable(should_continue)
                    and getattr(self.responder, "supports_stream_cancellation", False)):
                kwargs["should_continue"] = should_continue
            if getattr(self.responder, "supports_stream_restarts", False):
                def restart_stream():
                    extractor.reset()
                    on_draft({"text": "", "reset": True})
                kwargs["on_restart"] = restart_stream
            return stream_call(current_system, user, **kwargs)

        raw = call_responder(system, stream_draft=True)
        if raw is None or (callable(should_continue) and not should_continue()):
            return cancelled_result()
        parsed = parse_responder(raw)
        reply = parsed["reply"] or "……"
        facts, cons = parsed["facts"], parsed["contradictions"]
        self_deltas, self_evidence = (parsed["self_state_delta"],
                                      parsed["self_state_evidence"])
        recall_used, recall_evidence = (parsed["used_recall_event_ids"],
                                        parsed["recall_expression_evidence"])
        response_protocol = parsed["response_protocol"]
        from agents.responder import redact_recall_audit_identifiers
        reply, redacted_event_ids = redact_recall_audit_identifiers(reply, recall_event_ids)
        response_protocol = dict(response_protocol)
        # 测试 responder 可在**已经被正常调用**时留下无调度权限的 provenance。
        # 它不影响解析、anti-loop、F03/P3/F15/F16 或 Writer；唯一用途是让已提交
        # F16 message 以后可按 exact action_id 审计“本次正文由测试端提供”。
        take_test_receipt = getattr(self.responder, "take_consumed_receipt", None)
        if callable(take_test_receipt):
            try:
                manual_receipt = take_test_receipt()
            except Exception:
                manual_receipt = None
            if isinstance(manual_receipt, dict):
                response_protocol["test_manual_responder"] = dict(manual_receipt)
        response_protocol["reply_safety"] = {
            "internal_audit_identifier_redacted": list(redacted_event_ids),
        }
        return (reply or "……", facts, cons, self_deltas, self_evidence, recall_used,
                recall_evidence, response_protocol)

    def _measure_committed_npc_output(self, *, reply, action_id, turn_id, ts):
        """在 F16 message/action 事实存在后，测量该条输出的唯一 Agent 1 根增量。

        缺少/失败 provider 时保留显式零增量的 ``unavailable`` receipt；这不是本地
        词典替代，更不会把 writer 的自述、草稿或 UI 预览伪装成语义测量。
        """
        projection = None
        failure = None
        if self.semantic_projector is not None:
            try:
                projection = coerce_projection(self.semantic_projector.project(
                    text=reply, dialogue_tail="", recall_hint=None,
                    state_vars=self.state.get("vars") or {}, event_kind="npc_output"),
                    event_kind="npc_output")
            except Exception as exc:  # provider 是可观测降级，绝不改走 writer/self fallback。
                failure = type(exc).__name__
        if projection is None:
            delta_x = {}
            measurement = {
                "mode": "unavailable",
                "reason": failure or "semantic_projector_not_configured",
                "event_kind": "npc_output",
            }
        else:
            delta_x = dict(projection.variable_deltas or {})
            measurement = {
                "mode": "semantic_projector",
                "event_kind": "npc_output",
                "projection": projection.as_record(),
            }
        return delta_x, measurement

    def _apply_npc_self_expression(self, state, *, reply, raw_deltas, evidence,
                                   ts, source, action_id, turn_id=None, output_commit=None,
                                   output_evidence=None, output_memory_recall=None):
        """把 NPC 定稿中有逐字证据的自述，作为独立、极小的输出侧状态事件。

        它刻意不重跑用户事件、不重排词元，也不改 breakpoint 的用户输入测量；这是
        “NPC 说了什么”在发送后的时间线影响，供下一轮连续状态读取。
        """
        from ops.response_feedback import apply_self_deltas, validated_self_deltas
        before_vars = None
        self_applied = {}
        semantic_applied = {}
        receipt_applied = {}
        receipt_effect = {}
        if output_commit is not None:
            payload = output_commit
            # 主动 F16 的调用点可以不显式传入 turn_id，但 prepared receipt 已冻结
            # 它的 identity。后续 Agent 1 receipt 必须沿用同一 identity；不能因为
            # 它不是 direct reply 就写成 None，致使 action manifest 无法精确配对。
            effective_turn_id = str(turn_id or payload.get("turn_id") or "").strip() or None
            timeline = payload.get("timeline") or {}
            # 再次在纯层取得“发送点、尚未施加输出向量”的变量快照；归档只描述
            # 定稿自述的微量变化，不能把期间的墙钟回落误记成 NPC 说话造成的事件。
            time_binding = self._output_time_binding(payload)
            pre_output, _ = advance_to_output_time(
                state, timeline,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"])
            before_vars = dict(pre_output.get("vars") or {})
            # 此时 _remember 已写入同 action 的 message 与 F16 action receipt；现在才
            # 有资格调用 Agent 1。语义结果是这条输出的唯一正式 Delta_X_event。
            semantic_delta, semantic_measurement = self._measure_committed_npc_output(
                reply=reply, action_id=action_id, turn_id=effective_turn_id,
                ts=int(timeline["t_sent"]))
            semantic_applied = {
                key: float(value) for key, value in semantic_delta.items()
                if abs(float(value or 0.0)) > 0.0
            }
            committed, state_transition = output_state_transition(
                state, (self.profile or {}).get("constants") or {},
                timeline=timeline, deltas=semantic_applied,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"],
                f06_constants=time_binding["effective_constants"])
            memory_batch = None
            if output_memory_recall is not None and self._p4_state_bridge_enabled():
                memory_decision = self._prepare_memory_state_batch(
                    recall=output_memory_recall,
                    report={"state_transition": state_transition},
                    now_ms=int(timeline["t_sent"]))
                if memory_decision.get("ready_to_commit") is True:
                    memory_batch = memory_decision
                    committed, state_transition = output_state_transition(
                        state, (self.profile or {}).get("constants") or {},
                        timeline=timeline, deltas=semantic_applied,
                        effective_constants=time_binding["effective_constants"],
                        effective_constants_version=time_binding["effective_constants_version"],
                        f06_constants=time_binding["effective_constants"],
                        memory_delta=memory_batch["Delta_X_memory"],
                        memory_state_receipt=memory_batch)
            # prepared 行是 F16 前时间事实；内存 payload 现在被当前一次提交补齐，供
            # 同一事务的 P4/表现 anchor 消费。持久的正式权威是下列语义 receipt。
            payload["applied_deltas"] = dict(semantic_applied)
            payload["state_transition"] = dict(state_transition)
            payload["memory_delta"] = (dict(memory_batch.get("Delta_X_memory") or {})
                                       if memory_batch is not None else None)
            payload["memory_state_receipt"] = memory_batch
            if memory_batch is not None:
                payload["memory_expression_anchor"] = memory_expression_anchor(
                    committed_ms=int(timeline["t_sent"]), event_kind="npc_output",
                    memory_transition=state_transition.get("memory_state_transition"),
                    expression_binding=payload.get("expression_binding"))
            self.store.append_event(output_semantic_measurement_committed(
                action_id=action_id, turn_id=effective_turn_id, ts=int(timeline["t_sent"]),
                delta_x=semantic_applied, measurement=semantic_measurement,
                state_transition=state_transition,
                memory_delta=(memory_batch or {}).get("Delta_X_memory"),
                memory_state_receipt=memory_batch, params_sha=self.params_sha))
            if memory_batch is not None:
                self.store.append_event({
                    "schema_version": 1, "kind": "memory_state_selection_evaluated",
                    "action_id": str(action_id), "turn_id": turn_id,
                    "ts": int(timeline["t_sent"]),
                    "detail": self._memory_state_selection_decision(memory_batch),
                    "params_sha": self.params_sha,
                })
            state.clear()
            state.update(committed)
            mark_output_state_committed(state, action_id)
            # state watermark 先落盘；审计行只用于旧 action 的完成证明。若此后中断，
            # marker 仍足以阻止同一 action 在重启时二次回流。
            self.store.save_state(state)
            expression_anchor = self._expression_anchor_from_output_commit(
                payload, state=state, action_id=action_id,
                sent_ms=int(timeline["t_sent"]))
            # F03 event card 不能在 F16 后原样重投；但这个直接输出完成后可以冻结
            # 一份无正文、同 C 的“本次互动尚存何种根量残量”事实，供后续经 tick
            # 授权的 writer 只读。自主输出没有对应外部 turn，天然不会形成此锚。
            associated_turn_id = str(effective_turn_id or "")
            input_anchor = (self._committed_event_expression_anchor_for_turn(
                associated_turn_id, before_or_at_ms=int(timeline["t_sent"]))
                if str(source) == "reply" and associated_turn_id else None)
            interaction_anchor = interaction_residual_expression_anchor(
                action_id=action_id, turn_id=associated_turn_id,
                sent_ms=int(timeline["t_sent"]), event_anchor=input_anchor,
                output_anchor=expression_anchor)
            self.store.append_event(output_state_committed(
                action_id=action_id, ts=int(timeline["t_sent"]),
                expression_anchor=expression_anchor,
                interaction_residual_anchor=interaction_anchor))
            if memory_batch is not None:
                self._record_output_memory_state_batch(
                    payload, ts=int(timeline["t_sent"]))
            # 送达状态 watermark 已先落盘；无论 P4 是否启用，才可将这份
            # prepared 内冻结 recall plan 追加为历史事实。不得在此重检索。
            self._commit_output_recall_plan(
                payload, now_ms=int(timeline["t_sent"]))
            # E4：仅在 output_state_committed 之后消费同 action 的 emission plan。
            # 未发送 draft、typing/UI 预览与被取消输出均不会抵达这一提交边界。
            self._commit_expression_emission_after_f16(
                state, output_commit=payload, action_id=action_id,
                ts=int(timeline["t_sent"]))
            # F16 输出的 Agent 1 Delta_X 与 F03 输入处在同一事件口径：它既结算
            # 根状态，也必须以同一份已提交事实进入记忆。此前此处提前 return，令
            # output semantic 虽写入状态却没有自己的 archive 印痕，后续召回会把
            # “刚刚说出口造成的变化”遗漏掉。这里不重检索、不读取 writer 草稿，
            # 只由已提交回复和已测量的 Delta_X 生成 archive token。
            if semantic_applied:
                from core.salience import compute_salience_parts
                from core.tokens.variable import compute_variable_tokens
                from ops.recall_geometry import token_direction
                token_pack = compute_variable_tokens(state.get("vars") or {}, before_vars)
                event_tokens = [
                    token for token in token_pack["npc"]
                    if token.get("var") in semantic_applied or token.get("category") == "pain"
                ]
                if event_tokens:
                    event_s, _parts = compute_salience_parts(
                        before_vars, state.get("vars") or {}, text=reply)
                    drift = {
                        "P": float(state.get("P", 0.0)),
                        "E": float(state.get("E", 0.0)),
                    }
                    direction = token_direction(event_tokens)
                    if direction is not None:
                        drift["dir"] = direction
                    self.memory.archive_tokens(
                        int(timeline["t_sent"]), event_tokens, S=event_s, drift=drift,
                        source="npc:%s" % str(source), action_id=action_id,
                        **self._output_state_imprint_kwargs(payload))
            # e_self 仍作为可追溯的 writer 自述，但不再是根状态来源；旧通用 e_out
            # 已从正式路径移除。它们不能与 Agent 1 再次叠加。
            self_applied = dict(payload.get("self_deltas") or {})
            receipt_applied = {}
            receipt_effect = {}
            exact_evidence = list(output_evidence or [])
        else:
            self_applied, exact_evidence = validated_self_deltas(reply, raw_deltas, evidence)
        self_applied = {key: value for key, value in self_applied.items() if abs(value) > 0.0}
        receipt_applied = {key: value for key, value in receipt_applied.items()
                           if abs(value) > 0.0}
        if output_commit is not None:
            # F16 path returns the actual semantic root delta even when e_self is empty.
            return semantic_applied
        if not self_applied and not receipt_applied:
            return {}
        if before_vars is None:
            before_vars = dict(state.get("vars") or {})
            time_binding = self._effective_time_binding()
            events, _drives = apply_self_deltas(
                state, (self.profile or {}).get("constants") or {}, self_applied, ts,
                f06_constants=time_binding["effective_constants"])
        else:
            events = []
        if self_applied:
            self.store.append_event(npc_self_expressed(
                action_id=action_id, ts=ts, source=source, turn_id=turn_id,
                applied_deltas=self_applied, evidence=exact_evidence,
                params_sha=self.params_sha))
        if receipt_applied:
            self.store.append_event(npc_output_discharged(
                action_id=action_id, ts=ts, source=source, turn_id=turn_id,
                applied_deltas=receipt_applied, receipt_effect=receipt_effect,
                params_sha=self.params_sha))
        for event in events:
            self.store.append_event({"ts": int(ts), "kind": "npc_self_expression_bus",
                                     "action_id": str(action_id), "event": event,
                                     "params_sha": self.params_sha})
        # 这不是第二套“NPC 记忆”：只把已经通过逐字证据校验、并确实改变状态的
        # 输出，投影成同一 archive 格式。词元由既有变量空间生成，显著性也复用 S
        # 的既有分量；普通台词、模型无证据自报和未改变状态的输出均不会入档。
        from core.salience import compute_salience_parts
        from core.tokens.variable import compute_variable_tokens
        from ops.recall_geometry import token_direction
        token_pack = compute_variable_tokens(state.get("vars") or {}, before_vars)
        event_tokens = [token for token in token_pack["npc"]
                        if token.get("var") in self_applied or token.get("category") == "pain"]
        if self_applied and event_tokens:
            event_s, _parts = compute_salience_parts(
                before_vars, state.get("vars") or {}, text=reply)
            drift = {"P": float(state.get("P", 0.0)), "E": float(state.get("E", 0.0))}
            direction = token_direction(event_tokens)
            if direction is not None:
                drift["dir"] = direction
            self.memory.archive_tokens(
                int(ts), event_tokens, S=event_s, drift=drift,
                source="npc:%s" % str(source),
                action_id=action_id,
                **self._output_state_imprint_kwargs(output_commit))
        # 对外保留“模型明确说出口的自述”语义；通用 e_out 可从同一 action 的
        # prepared receipt 或 npc_output_discharged 审计行读取，不能被混成模型自报。
        return self_applied

    def _p3_tick_opening_candidate(self, state, now_ms, authority_receipt, *,
                                   effective_constants, effective_constants_version):
        """把已结算 tick 的 P3 receipt 投影为唯一可消费的 F15 候选。

        这里不重算 ``Z/I/Theta/Q``，也不读取旧 ``opening_margin``。用户锚只是一条
        离散事件身份，防止没有正式用户输入的状态凭空获得自主开口资格；它不参与
        P3 的数学读数。只有 ``settle_wall_clock``（正式 dynamic tick）调用本方法。
        """
        receipt = dict(authority_receipt) if isinstance(authority_receipt, dict) else {}
        anchor = opening_anchor(state)
        try:
            margin = float(receipt["margin"])
            impulse = float(receipt["I_open"])
            gate = float(receipt["Theta_open"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("P3 tick 必须消费完整 opening_authority_receipt") from exc
        event = receipt.get("event") if isinstance(receipt.get("event"), dict) else {}
        sources = {
            "formula_version": "opening-authority-receipt-v1",
            "authority_receipt_version": str(receipt.get("version") or ""),
            "schedule_version": str((receipt.get("formula_versions") or {}).get("schedule_vector") or ""),
            "opening_version": str((receipt.get("formula_versions") or {}).get("opening_projection") or ""),
            "impulse": impulse,
            "gate": gate,
            "margin": margin,
            "root_delta": dict(receipt.get("Delta_X") or {}),
            "event_delta": dict(receipt.get("Delta_X") or {}),
            "effective_constants_version": str(effective_constants_version),
            "authority_event_kind": str(event.get("kind") or ""),
            "authority_event_id": str(event.get("id") or ""),
        }
        if anchor is None:
            sources["anchor_ts"] = None
            sources["anchor_turn_id"] = ""
            signal = {
                "eligible": False, "qualified_at_ms": None, "score": margin,
                "base": impulse, "base_raw": impulse, "observation_source": "wall_tick",
                "sources": sources, "opening": receipt,
            }
            return signal
        sources["anchor_ts"] = int(anchor["ts"])
        sources["anchor_turn_id"] = str(anchor.get("turn_id") or "")
        eligible = bool(margin >= 0.0)
        signal = {
            "eligible": eligible,
            "qualified_at_ms": (int(now_ms) if eligible else None),
            "score": margin, "base": impulse, "base_raw": impulse,
            "observation_source": "wall_tick", "sources": sources,
            "opening": receipt,
        }
        if eligible:
            # F16 只安排已经由本次 tick 准入的可见投递，不反向参与 P3 crossing。
            delay_rng = random.Random(
                f"{self.seed or 0}:opening:{int(now_ms)}:opening-authority-v1")
            delay_sec, delay_receipt = decide_delay_seconds(
                effective_constants, (state or {}).get("vars") or {},
                receipt.get("Delta_X") or {}, self.baseline, delay_rng, include_receipt=True)
            signal["delay_ms"] = int(delay_sec * 1000)
            signal["at_ms"] = int(now_ms) + int(signal["delay_ms"])
            signal["delay_receipt"] = {"mode": "opening_authority_v1", **delay_receipt}
        return signal

    def _scheduler_context(self, state, now_ms):
        """一次候选的只读快照；不得保存到 profile、state、prompt 或 clock plan。"""
        return build_scheduler_context(state=state, now_ms=now_ms)

    def _cadence_projection(self, state=None):
        """把当前 B₀/D(t) 编译为只读校准 shadow；它不参与调度。"""
        s = state or self.state or {}
        baseline = self.prior or (self.profile or {}).get("prior") or {}
        projection = cadence_projection(
            baseline=baseline,
            dynamic=s.get("weights") or baseline,
            params=self.cadence_params)
        return projection

    @staticmethod
    def _cadence_trace_summary(projection):
        """将校准 shadow 压成无正文 trace 读数。

        这是观测适配器，不是新的调度层：它只读取已经供 wall-clock 使用的
        ``cadence_projection`` 结果。字段保持无正文，避免数学 trace 递归地保存
        参数副本、词元或写作材料。
        """
        row = projection if isinstance(projection, dict) else {}
        controls = row.get("control_coefficients") or {}
        shadow = controls.get("shadow") or {}
        active = controls.get("active") or {}
        return {
            "version": str(row.get("version") or "unknown"),
            "active": bool(row.get("active", False)),
            "guard": row.get("guard"),
            "alignment": round(float(row.get("alignment", 0.0) or 0.0), 6),
            "mode": str(row.get("mode") or "unknown"),
            "calibration_shadow": {str(key): round(float(value or 0.0), 6)
                                   for key, value in sorted(shadow.items())},
            "calibration_active": {str(key): round(float(value or 0.0), 6)
                                   for key, value in sorted(active.items())},
        }

    @staticmethod
    def _opening_trace_summary(candidate):
        """压缩正式 opening／peer 候选的无正文分量，供只读 math trace 使用。

        既可读取自主 opening，也可读取 peer reactive candidate；两者都由同一 C/X/e
        快照即时推出。只保留数值与布尔条件，避免把回复正文、弹药内容或角色标签写进观测流。
        """
        signal = candidate if isinstance(candidate, dict) else {}
        sources = signal.get("sources") or {}

        # 延迟主回复尚未提交时，F14 不得伪造一个旧结构的空候选；保留最小的
        # “尚未计算” receipt 即可，真正的双投影将在 release 后生成。
        if not signal:
            return {"candidate_present": False, "eligible": False,
                    "formula_version": "f14-root-vector-v1"}

        def number(value):
            try:
                return round(float(value or 0.0), 6)
            except (TypeError, ValueError):
                return 0.0

        # M9 F14 receipt：保持 trace 的有界形状，同时完整保留六轴贡献。旧结构的
        # P/geometry/character 字段不再被塞入同一条 span，避免把历史读数伪装成
        # 当前资格输入。
        if sources.get("formula_version"):
            return {
                "candidate_present": bool(signal),
                "eligible": bool(signal.get("eligible", False)),
                "score": number(signal.get("score")),
                "due_at_ms": (int(signal["at_ms"]) if signal.get("at_ms") is not None else None),
                "delay_ms": (int(signal["delay_ms"]) if signal.get("delay_ms") is not None else None),
                "impulse": number(sources.get("impulse")),
                "gate": number(sources.get("gate")),
                "margin": number(sources.get("margin")),
                "attenuation": number(signal.get("attenuation")),
                "formula_version": str(sources.get("formula_version")),
                "mode": (str(sources.get("mode")) if sources.get("mode") else None),
                "event_present": bool(sources.get("event_present", True)),
                "projection": {
                    "root_delta": copy.deepcopy(sources.get("root_delta") or {}),
                    "event_delta": copy.deepcopy(sources.get("event_delta") or {}),
                    "impulse_state_terms": copy.deepcopy(sources.get("impulse_state_terms") or {}),
                    "impulse_event_terms": copy.deepcopy(sources.get("impulse_event_terms") or {}),
                    "gate_state_terms": copy.deepcopy(sources.get("gate_state_terms") or {}),
                    "gate_event_terms": copy.deepcopy(sources.get("gate_event_terms") or {}),
                    "direction_cosine": number(sources.get("direction_cosine")),
                    "direction_warn": bool(sources.get("direction_warn", False)),
                },
            }

        # peer reactive 尚在独立迁移队列中；它不应因保留旧计算器就超出 math
        # trace 的有界协议。这里只记录结果与交付语义，不把旧局部中间量扩散成
        # 新 F14 的伪输入。
        if sources.get("mode") == "reactive_peer":
            return {
                "candidate_present": True,
                "eligible": bool(signal.get("eligible", False)),
                "score": number(signal.get("score")),
                "attenuation": number(signal.get("attenuation")),
                "mode": "reactive_peer",
                "event_magnitude": number(sources.get("event_magnitude")),
            }

        return {
            "candidate_present": bool(signal),
            "eligible": bool(signal.get("eligible", False)),
            "score": number(signal.get("score")),
            "due_at_ms": (int(signal["at_ms"]) if signal.get("at_ms") is not None else None),
            "delay_ms": (int(signal["delay_ms"]) if signal.get("delay_ms") is not None else None),
            "base": number(signal.get("base")),
            "base_raw": number(signal.get("base_raw")),
            "attenuation": number(signal.get("attenuation")),
            "pressure": number(sources.get("pressure")),
            "event_delta": number(sources.get("event_delta")),
            "event_energy": number(sources.get("event_energy")),
            "event_forward": number(sources.get("event_forward")),
            "event_magnitude": number(sources.get("event_magnitude")),
            "relationship": number(sources.get("relationship")),
            "character_approach": number(sources.get("character_approach")),
            "character_withdrawal": number(sources.get("character_withdrawal")),
            "character_impulse": number(sources.get("character_impulse")),
            "character_push": number(sources.get("character_push")),
            "character_brake": number(sources.get("character_brake")),
            "character_engagement": number(sources.get("character_engagement")),
            # M9 F14：正式资格的可回放双投影。旧 P/geometry 字段仅可能出现在
            # peer reactive 的历史适配器；自主 opening 不再读取它们。
            "impulse": number(sources.get("impulse")),
            "gate": number(sources.get("gate")),
            "margin": number(sources.get("margin")),
            "root_delta": copy.deepcopy(sources.get("root_delta") or {}),
            "event_root_delta": copy.deepcopy(sources.get("event_delta") or {}),
            "impulse_state_terms": copy.deepcopy(sources.get("impulse_state_terms") or {}),
            "impulse_event_terms": copy.deepcopy(sources.get("impulse_event_terms") or {}),
            "gate_state_terms": copy.deepcopy(sources.get("gate_state_terms") or {}),
            "gate_event_terms": copy.deepcopy(sources.get("gate_event_terms") or {}),
            "direction_cosine": number(sources.get("direction_cosine")),
            "direction_warn": bool(sources.get("direction_warn", False)),
            "formula_version": str(sources.get("formula_version") or ""),
        }

    @staticmethod
    def _candidate_trace_formula_params(candidate):
        """F14/peer trace 只标识本候选矩阵版本，不携带旧 fixed-line。"""
        signal = candidate if isinstance(candidate, dict) else {}
        sources = signal.get("sources") or {}
        return {
            "version": str(sources.get("formula_version") or "f14-root-vector-v1"),
            "route": str(sources.get("mode") or "autonomous_opening"),
        }

    @staticmethod
    def _root_state_trace_summary(state, constants):
        """把 C/X 与即时压力读数压成统一、无正文的数学 receipt。"""
        from temporal.bus import pressure_readout

        s = state or {}
        x = s.get("vars") or {}
        c = constants or {}
        return {
            "C": {key: round(float(c.get(key, 0.0) or 0.0), 6)
                  for key in ("a1", "a2", "b1", "b2", "b3", "g1", "g2")},
            "X": {key: round(float(x.get(key, 0.0) or 0.0), 6)
                  for key in ("E_env", "P_need", "V_attr", "V_rep",
                              "L_self", "L_opponent")},
            "P": round(float(pressure_readout(c, x)["P"]), 6),
        }

    @classmethod
    def _f15_trace_input_summary(cls, state, constants, now_ms, *,
                                 effective_constants_version=None):
        """旧档／peer trace 的 root-vector 输入摘要，正式 P3 trace 不得调用。"""
        return cls._opening_trace_input_summary(
            state, constants, now_ms,
            effective_constants_version=effective_constants_version)

    @classmethod
    def _opening_trace_input_summary(cls, state, constants, now_ms, *,
                                     effective_constants_version=None):
        """为 legacy root-vector 对照输出无正文输入摘要。"""
        s = state or {}
        readout = opening_margin(s, constants or {}, now_ms)
        anchor_ts = readout.get("anchor_ts")
        try:
            anchor_age_ms = max(0, int(now_ms) - int(anchor_ts))
        except (TypeError, ValueError):
            anchor_age_ms = 0
        root = cls._root_state_trace_summary(s, constants)
        # P 是展示/诊断投影，不是 opening_margin 的输入；不得因 trace 方便而
        # 伪装成公式依赖。原始 e0 与当前 e(t) 必须同时可见，才能审计自然衰减。
        root.pop("P", None)
        summary = {
            **root,
            "Delta_X_event": dict(readout.get("event_delta_original") or {}),
            "Delta_X_effective": dict(readout.get("event_delta") or {}),
            "delta_t_ms": int(anchor_age_ms),
        }
        if effective_constants_version:
            summary["effective_constants_version"] = str(effective_constants_version)
        return summary

    @classmethod
    def _f14_trace_input_summary(cls, state, constants, variable_deltas=None):
        """F14 receipt：根状态与已提交事件增量，零 writer 信息。"""
        deltas = variable_deltas or {}
        normalized = {
            key: round(float(deltas.get(key, 0.0) or 0.0), 6)
            for key in ("E_env", "P_need", "V_attr", "V_rep", "L_self", "L_opponent")
        }
        return {
            # 不复用通用状态摘要：后者为了 F06/F08/F15 可读性附带临时 P，
            # 但 P 不是 F14 的输入。F14 receipt 必须能从表面就回放到 C/X/e。
            "C": {key: round(float((constants or {}).get(key, 0.0) or 0.0), 6)
                  for key in ("a1", "a2", "b1", "b2", "b3", "g1", "g2")},
            "X": {key: round(float(((state or {}).get("vars") or {}).get(key, 0.0) or 0.0), 6)
                  for key in ("E_env", "P_need", "V_attr", "V_rep",
                              "L_self", "L_opponent")},
            "Delta_X_event": normalized,
            "event_energy": round(sum(abs(value) for value in normalized.values()), 6),
        }

    @classmethod
    def _f08_trace_input_summary(cls, state, constants, now_ms, *,
                                 effective_constants_version=None):
        """F08 receipt：时间推进前根状态与该结算段的真实墙钟差。"""
        s = state or {}
        anchor = s.get("last_advance_ms") or s.get("last_ts")
        summary = {
            **cls._root_state_trace_summary(s, constants),
            "delta_t_ms": (0 if anchor is None
                           else max(0, int(now_ms) - int(anchor))),
            "exploded_before": bool(s.get("exploded", False)),
        }
        if effective_constants_version:
            summary["effective_constants_version"] = str(effective_constants_version)
        return summary

    @classmethod
    def _f08_trace_output_summary(cls, state, constants, advanced, *,
                                  effective_constants_version=None):
        """F08 receipt：时间推进后的六轴与当下压力读数。"""
        settled = cls._root_state_trace_summary(state, constants)
        summary = {
            "X_after": settled["X"],
            "P": settled["P"],
            "E": round(float((state or {}).get("E", 0.0) or 0.0), 6),
            "advanced": bool(advanced),
        }
        if effective_constants_version:
            summary["effective_constants_version"] = str(effective_constants_version)
        return summary

    @staticmethod
    def _p3_authority_receipt_for_event(before_state, after_state, effective_constants,
                                        effective_constants_version, *, event_kind,
                                        event_id, observed_at_ms):
        """从一个已提交状态转换生成 P3 receipt；不裁定 crossing 或创建计划。"""
        return opening_authority_receipt(
            (before_state or {}).get("vars") or {},
            (after_state or {}).get("vars") or {}, effective_constants or {},
            constants_version=str(effective_constants_version or ""),
            event_kind=str(event_kind or ""), event_id=str(event_id or ""),
            observed_at_ms=int(observed_at_ms),
            min_interval_ms=ACTOR["tick_min_ms"],
            max_interval_ms=ACTOR["tick_max_ms"],
            schedule_params=SCHEDULE_VECTOR_AUTHORITY_V2,
            opening_params=OPENING_AUTHORITY_V5)

    def _publish_p3_authority_receipt(self, before_state, after_state, *,
                                      effective_constants, effective_constants_version,
                                      event_kind, event_id, observed_at_ms,
                                      turn_id=None, conversation_provenance=None,
                                      output_conversation_context=None):
        """追加已提交事件的 P3 receipt；receipt 本身不形成调度副作用。"""
        receipt = self._p3_authority_receipt_for_event(
            before_state, after_state, effective_constants, effective_constants_version,
            event_kind=event_kind, event_id=event_id, observed_at_ms=observed_at_ms)
        record = {
            "schema_version": 1, "kind": "opening_authority_receipt",
            "action_id": (str(event_id) if event_kind == "npc_output" else None),
            "turn_id": turn_id, "ts": int(observed_at_ms), "detail": dict(receipt),
            "params_sha": self.params_sha,
        }
        if conversation_provenance is not None:
            # 只透传 Router 已提交 delivery 的来源，不把 source event ID 冒充
            # 本地 F03/P3 turn ID，也不让 conversation metadata 参与公式。
            required = (
                "event_id", "conversation_id", "delivery_id",
                "sender_participant_id", "membership_version",
                "recipient_participant_id",
            )
            if (not isinstance(conversation_provenance, Mapping)
                    or any(key not in conversation_provenance for key in required)):
                raise ValueError("P3 canonical source requires exact delivery provenance")
            record["delivery_reference"] = {
                key: conversation_provenance[key] for key in required[:-1]
            }
            record["recipient_participant_id"] = conversation_provenance[
                "recipient_participant_id"]
        if output_conversation_context is not None:
            # 只引用同一 action 已提交 F16 message 的归属；caused-by input 不是
            # output 自己的 source event，故不转写成 delivery_reference。
            if event_kind != "npc_output" or not isinstance(output_conversation_context, Mapping):
                raise ValueError("P3 output context requires committed npc_output")
            record["conversation_context"] = copy.deepcopy(dict(output_conversation_context))
        self.store.append_event(record)
        return receipt

    @staticmethod
    def _p3_shadow_schedule_summary(before_state, after_state, effective_constants,
                                    effective_constants_version, *, event_kind, event_id,
                                    observed_at_ms, receipt=None, mode="shadow_only"):
        """在正式 tick 里留存 P3 三维调度投影，但绝不参与本轮发信裁定。

        P3 的 ``X/ΔX -> R, Ceff -> B, B⊙R -> Z`` 纯函数已经存在；在其候选参数完成
        冻结前，Runner 只能把它作为可回放观测写进 F15 receipt。尤其不能产生
        ``eligible``、clock plan 或任何 writer 副作用，避免和当前唯一权威 F15 并轨。
        """
        receipt = (dict(receipt) if isinstance(receipt, dict) else
                   Runner._p3_authority_receipt_for_event(
                       before_state, after_state, effective_constants, effective_constants_version,
                       event_kind=event_kind, event_id=event_id, observed_at_ms=observed_at_ms))
        # math trace 的摘要层数有限；这里刻意保存“可反算的最小平面 receipt”，
        # 而不是把纯函数的展示型嵌套对象原样塞入 trace。R/B/Z、前后根量与 ΔG
        # 已足以定位每个读数的来源，完整 components 仍由同版本纯函数重建。
        return {
            "mode": str(mode),
            "event": dict(receipt["event"]),
            "effective_constants_version": receipt["effective_constants_version"],
            "geometry_version": receipt["formula_versions"]["relationship_geometry"],
            "schedule_version": receipt["formula_versions"]["schedule_vector"],
            "opening_version": receipt["formula_versions"]["opening_projection"],
            "X_before": dict(receipt["X_before"]),
            "X_after": dict(receipt["X_after"]),
            "Delta_X": dict(receipt["Delta_X"]),
            "Delta_G": dict(receipt["Delta_G"]),
            "B": dict(receipt["B"]), "R": dict(receipt["R"]),
            "Z": dict(receipt["Z"]), "I_open": float(receipt["I_open"]),
            "Theta_open": float(receipt["Theta_open"]),
            "opening_margin": float(receipt["margin"]),
            "Q_tick": float(receipt["Q_tick"]),
            "H_projection": copy.deepcopy(receipt.get("H_projection") or {}),
            "h_next_ms": int(receipt["h_next_ms"]),
            "h_next_bounds_ms": {"min": int(ACTOR["tick_min_ms"]),
                                 "max": int(ACTOR["tick_max_ms"])},
            "h_next_version": receipt["formula_versions"]["observation_interval"],
        }

    @staticmethod
    def _p3_trace_input_summary(authority_receipt):
        """把一份已提交的 P3 receipt 原样投影到 trace。

        这不是第二次开口计算：trace 只复述该次状态结算已产生的根量、几何和
        ``I/Theta/Q``。新 P3 通路不得为了填充 trace 再调用旧
        :func:`temporal.opening.opening_margin`。
        """
        receipt = dict(authority_receipt) if isinstance(authority_receipt, dict) else {}
        if not receipt:
            return {"authority_receipt_present": False}
        return {
            "authority_receipt_present": True,
            "authority_receipt_version": str(receipt.get("version") or ""),
            "event": copy.deepcopy(receipt.get("event") or {}),
            "effective_constants_version": str(receipt.get("effective_constants_version") or ""),
            "X_before": copy.deepcopy(receipt.get("X_before") or {}),
            "X_after": copy.deepcopy(receipt.get("X_after") or {}),
            "Delta_X": copy.deepcopy(receipt.get("Delta_X") or {}),
            "Delta_G": copy.deepcopy(receipt.get("Delta_G") or {}),
            "B": copy.deepcopy(receipt.get("B") or {}),
            "R": copy.deepcopy(receipt.get("R") or {}),
            "Z": copy.deepcopy(receipt.get("Z") or {}),
            "I_open": float(receipt.get("I_open", 0.0) or 0.0),
            "Theta_open": float(receipt.get("Theta_open", 0.0) or 0.0),
            "margin": float(receipt.get("margin", 0.0) or 0.0),
            "Q_tick": float(receipt.get("Q_tick", 0.0) or 0.0),
            "H_projection": copy.deepcopy(receipt.get("H_projection") or {}),
            "h_next_ms": int(receipt.get("h_next_ms", 0) or 0),
        }

    def _record_output_f06(self, trace, *, state_before, state_after, constants,
                           applied_deltas, source, parent_ref=None,
                           effective_constants_version=None):
        """为一条已提交 NPC 输出追加 F06 receipt；零增量不伪造状态事件。"""
        if trace is None or not applied_deltas:
            return
        before = self._root_state_trace_summary(state_before, constants)
        after = self._root_state_trace_summary(state_after, constants)
        input_summary = {
            **before,
            "Delta_X_event": {key: round(float((applied_deltas or {}).get(key, 0.0) or 0.0), 6)
                              for key in ("E_env", "P_need", "V_attr", "V_rep",
                                          "L_self", "L_opponent")},
        }
        if effective_constants_version:
            input_summary["effective_constants_version"] = str(effective_constants_version)
        trace.record(
            phase="state_settled", formula_id="F06",
            input_summary=input_summary,
            output_summary={
                "P": after["P"], "E": round(float((state_after or {}).get("E", 0.0) or 0.0), 6),
                "H_max": round(float((state_after or {}).get("H_max", 0.0) or 0.0), 6),
                "exploded": bool((state_after or {}).get("exploded", False)),
            }, decision_reason=str(source) + "_output_state_committed", parent_ref=parent_ref)

    @staticmethod
    def _projection_trace_summary(projection):
        """保持 W_recall / W_form trace 为紧凑数值 receipt，绝不携带写作文本。"""
        if projection is None:
            return {}
        record = projection.as_record() if hasattr(projection, "as_record") else dict(projection)
        summary = {
            "version": str(record.get("version") or "unknown"),
            "active": bool(record.get("active", False)),
            "guard": record.get("guard"),
        }
        for key in ("candidate_gate", "alignment"):
            if key in record:
                summary[key] = round(float(record.get(key, 0.0) or 0.0), 6)
        for group_name in ("shadow", "controls"):
            for key, value in (record.get(group_name) or {}).items():
                summary[group_name + "_" + str(key)] = round(float(value or 0.0), 6)
        return summary

    def _new_math_trace(self, *, root_kind, root_id, now_ms, root_refs):
        """创建本事务的只读观测器；关闭时不分配任何 trace 对象或 RNG。"""
        if not self.math_trace_enabled:
            return None
        from ops.math_trace import MathTraceCollector
        return MathTraceCollector(
            root_kind=root_kind, root_id=root_id, params_sha=self.params_sha,
            logical_ts=int(now_ms), root_refs=dict(root_refs))

    def _commit_math_trace(self, collector):
        """只在既有事务已经成功落盘后追加 span；观测流永不参与后续决策。"""
        if collector is None:
            return
        for record in collector.records():
            self.store.append_math_trace(record)

    def _trace_autonomous_action(self, *, receipt, source, ts, recall_events=(),
                                  opening=None, clock_plan=None, form_projection=None,
                                  variable_deltas=None, state_before=None,
                                  authority_receipt=None):
        """记录一条已提交自主输出的本地因果节点，不接触其文本或 prompt。"""
        action_id = str((receipt or {}).get("action_id") or "action_unknown")
        trace = self._new_math_trace(
            root_kind="action", root_id=action_id, now_ms=ts,
            root_refs={"action_id": action_id, "action_ts": int(ts),
                       "action_source": str(source)})
        if trace is None:
            return
        time_binding = self._effective_time_binding()
        effective_constants = time_binding["effective_constants"]
        effective_constants_version = time_binding["effective_constants_version"]
        trace.record(
            phase="memory_resolved", formula_id="F10",
            output_summary={"selected_count": len(recall_events),
                            "delivered_count": len(recall_events)},
            decision_reason=("memory_selected" if recall_events else "memory_not_selected"))
        trace.record(
            phase="output_committed", formula_id="F16",
            output_summary={"committed": True, "delay_ms": 0,
                            "form_projection": self._projection_trace_summary(form_projection)},
            decision_reason=str(source) + "_output_committed", parent_ref=action_id)
        # 输出侧状态变化也必须以已经校验、已经提交的实际增量入 trace；不能把
        # responder 自报的原始候选当成状态事实。
        if variable_deltas is not None:
            root = self._root_state_trace_summary(state_before or self.state, effective_constants)
            settled = self._root_state_trace_summary(self.state, effective_constants)
            trace.record(
                phase="state_settled", formula_id="F06",
                input_summary={
                    **root,
                    "Delta_X_event": {key: round(float((variable_deltas or {}).get(key, 0.0) or 0.0), 6)
                                      for key in ("E_env", "P_need", "V_attr", "V_rep",
                                                  "L_self", "L_opponent")},
                    "effective_constants_version": effective_constants_version,
                },
                output_summary={
                    "P": settled["P"], "E": round(float(self.state.get("E", 0.0) or 0.0), 6),
                    "H_max": round(float(self.state.get("H_max", 0.0) or 0.0), 6),
                    "exploded": bool(self.state.get("exploded", False)),
                }, decision_reason=str(source) + "_output_state_committed",
                parent_ref=action_id)
        if opening is not None:
            # F06 的 ``variable_deltas`` 是本条已提交输出的 e_out／自述后效；
            # 它不能被展示成 F14 的外部事件 e。F14 receipt 必须复用候选实际
            # 读取的 event_delta（followup 后通常为空），否则 trace 会伪造
            # “角色刚说的话等于对方又施压了一次”。
            f14_event_delta = ((opening.get("f14") or {}).get("event_delta")
                               if isinstance(opening, dict) else None)
            trace.record(
                phase="schedule_evaluated", formula_id="F14",
                input_summary=self._opening_trace_input_summary(
                    self.state, effective_constants, ts,
                    effective_constants_version=effective_constants_version),
                output_summary=self._opening_trace_summary(opening),
                formula_params=self._candidate_trace_formula_params(opening),
                decision_reason=("opening_eligible" if opening.get("eligible", False)
                                 else "opening_not_eligible"))
        if clock_plan is not None:
            cadence = self._cadence_projection(self.state)
            trace.record(
                phase="schedule_evaluated", formula_id="F15",
                input_summary=(self._p3_trace_input_summary(authority_receipt)
                               if authority_receipt is not None else
                               self._f15_trace_input_summary(
                                   self.state, effective_constants, ts,
                                   effective_constants_version=effective_constants_version)),
                output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                "plan_version": int(clock_plan.get("version", 0)),
                                "reason_count": len(clock_plan.get("reasons") or ()),
                                "cadence_projection": self._cadence_trace_summary(cadence)},
                decision_reason=("clock_scheduled" if clock_plan.get("at_ms") is not None
                                 else "clock_idle"))
        self._commit_math_trace(trace)

    def commit_opening_message(self, *, now_ms=None, source="opening",
                               _should_continue=None, _fixture_legacy=False):
        """提交一条已经由正式 opening crossing 授权的自主输出。

        这是在线 ``opening_due`` 的唯一 composer/commit：回复后的追发和墙钟命中
        的主动开口只可在表达审计标签上不同，不能再各自进入另一条 writer、扫描或
        计划路径。历史 catch-up replay 是另一种离线回放事务，不调用本方法。
        """
        opening_source = str(source or "opening")
        should_continue = (_should_continue if callable(_should_continue)
                           else (lambda: True))
        stage = "entry"
        try:
            # 用户已在 Actor 总线上发来新输入时，未定稿的自主续话没有发生资格。
            if not should_continue():
                return None
            # 在线 Actor 不传 ``now_ms``，仍以定稿时的真实宿主时钟为准。虚拟墙钟
            # 夹具必须显式传入已调度的逻辑时刻，不能把今天的宿主时间写入可回放
            # 的 followup receipt。
            now = int(now_ms if now_ms is not None else _time.time() * 1000)
            # 自主 output 与其 prompt/P4/dispatch 使用同一回合身份。它是已经
            # 存在于主动 prompt/P4 的事务主键，此处只补到 F16 action 与输出 P3，
            # 让 receipt-first 审计能按主键而非时间邻近连线。
            proactive_turn_id = "proactive:%s:%d" % (opening_source, now)
            stage = "prepare_expression"
            form_plan = compile_form_projection(
                baseline=self.prior or {k: 0.2 for k in PROTOTYPES},
                dynamic=self.state.get("weights") or self.prior,
                constants=self.profile.get("constants") or {},
                variables=self.state.get("vars") or {})
            if _fixture_legacy:
                # 仅历史夹具仍可验证“上回合已装配卡的继承”。它不是 Actor writer，
                # 也不伪称一次新的 F10 selection 或 P4 状态影响。
                proactive_recall = None
                recall_events = self.last_recall_expression_events
            else:
                # F10 只在已由 dynamic tick 授权的这次 output 事务中冻结；它
                # 不能继承上一轮 writer 卡包，也不能在草稿/typing 时先写 recall。
                query = self._proactive_recall_query(now)
                proactive_recall = self._prepare_proactive_recall(
                    now, query["query_tokens"], "online_proactive", query_receipt=query)
                recall_events = (() if proactive_recall is None
                                 else proactive_recall.expression_plan.events)
            expression_cards, expression_plan, expression_receipt = \
                self._proactive_expression_context(
                    state=self.state, now_ms=now, source=opening_source)
            prompt = assemble_compat_autonomous_prompt(
                persona_blocks=self.persona_blocks, turn=self.turn,
                field_pressure=self.state.get("P", 0.0),
                ranked_tokens=[],
                # 正式主动 writer 与 direct 一样只能消费 formal delivery。当前
                # descriptor 卡碰巧都声明 ``prompt_bridge=False`` 不能成为边界：
                # 若未来新增卡误带该旧字段，也不得绕过 plan 直接抵达 writer。
                bridge_tokens=(() if self.expression_attention else [
                    item for item in expression_cards if item.get("prompt_bridge")]),
                attraction=float(self.state["vars"].get("V_attr", 0.5)),
                dialogue_tail="",
                state_hint=None,
                budget_chars=self.prompt_budget_chars,
                responder_core_prompt=self.responder_core_prompt,
                expression_attention_plan=expression_plan,
                now_ms=now,
                recall_expression_events=recall_events,
                form_projection=form_plan,
                temporal_expression_receipt=expression_receipt,
                # 这条 writer 只会由在线 Actor 消费已经由 dynamic tick 冻结的
                # opening。它不是离线 replay；必须把“没有新输入、交付新对白”的
                # 正式边界传到表现层，不能让旧 replay 委托把短尾当待答问题。
                proactive_mode="online_boundary",
                canonical_memory_sequence=True,
                output_identity=self._writer_output_identity())
            self._trace_proactive_prompt(opening_source, now, prompt)
            stage = "generate_reply"
            f_reply, f_facts, f_cons, f_deltas, f_evidence, _used, _recall_evidence, _protocol = self._generate_reply(
                prompt.system, prompt.user, DEFAULTS["responder_temperature"])
            # 网络调用不可强杀，但定稿后、写入任何消息/状态之前必须再过一次提交闸。
            if not should_continue():
                return None
            ready_ms = (now if now_ms is not None else int(_time.time() * 1000))
            timeline = resolve_output_timeline(
                received_ms=now, ready_ms=ready_ms, cadence_delay_ms=0)
            ts = timeline["t_sent"]
            stage = "persist_output"
            rec = self._persist_npc_output(
                state=self.state, ts=ts, text=f_reply, source=opening_source, S=0.0, tokens=[],
                turn_id=proactive_turn_id,
                recall_refs=[recall_expression_reference(event)
                             for event in prompt.recall_expression_events],
                received_ms=now, ready_ms=ready_ms, cadence_delay_ms=0,
                raw_deltas=f_deltas, evidence=f_evidence,
                calibration_assembly_receipt=prompt.assembly_receipt,
                memory_recall=proactive_recall)
            state_before_output = copy.deepcopy(self.state)
            stage = "commit_output_state"
            applied_deltas = self._apply_npc_self_expression(
                self.state, reply=f_reply, raw_deltas=f_deltas, evidence=f_evidence,
                ts=ts, source=opening_source, action_id=rec.get("action_id"),
                output_commit=rec.get("_output_commit_payload"),
                output_evidence=rec.get("_output_commit_evidence"),
                output_memory_recall=rec.get("_output_memory_recall"))
            committed_deltas = self._committed_output_deltas(rec, applied_deltas)
            # 自主输出同样等待回应；这只是历史统计，不是下一次 opening 的资格输入。
            self._bump_unanswered(self.state, 1, ts, "inc_opening")
            self.store.save_state(self.state)
            self._ledger_from_reply(
                f_reply, ts, f_facts, f_cons, event_id=rec.get("event_id"))
            stage = "refresh_next_observation"
            time_binding = self._effective_time_binding()
            p3_output_receipt = self._publish_p3_authority_receipt(
                state_before_output, self.state,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"],
                event_kind="npc_output", event_id=str(rec.get("action_id") or ""),
                observed_at_ms=int(ts), turn_id=proactive_turn_id,
                output_conversation_context=rec.get("conversation_context"))
            # F16 自主输出已经是一次已消费的 crossing。输出自身只能回写 F06 根状态
            # 并重设下一次 P3 observation；不得在同一个提交点重新求一个旧 F14 候选，
            # 更不得把 self delta 伪装成新的外部刺激。
            opening = None
            clock_plan = self.wall_clock.refresh(
                self.state, (self.profile or {}).get("constants") or {}, ts,
                cause="followup_committed", opening=None,
                cadence=self._cadence_projection(self.state),
                authority_receipt=p3_output_receipt)
            self._trace_autonomous_action(
                receipt=rec, source=opening_source, ts=ts,
                recall_events=prompt.recall_expression_events,
                opening=opening, clock_plan=clock_plan,
                form_projection=form_plan, variable_deltas=committed_deltas,
                state_before=state_before_output,
                authority_receipt=p3_output_receipt)
            return f_reply
        except Exception as exc:
            # 失败候选仍不是消息：它不能留下 action、状态、记忆、P3、tick 或 UI
            # 事实。但以前的宽泛吞错使预跑只能看见“已越线却没消息”，无法区分
            # 预算拒绝、provider 异常或提交问题。仅追加无正文失败观测，供
            # Runner/预跑回溯；不记录异常文本，避免泄露 provider 或 prompt 内容。
            try:
                self.store.append_event({
                    "ts": int(locals().get("now", now_ms or 0) or 0),
                    "kind": "opening_commit_exception",
                    "detail": {
                        "source": opening_source,
                        "stage": str(stage),
                        "error_type": type(exc).__name__,
                    },
                })
            except Exception:
                pass
            return None

    def generate_followup(self, prev_reply, _should_continue=None, now_ms=None,
                          *, _fixture_legacy=False):
        """只供显式 fixture 使用的旧 writer 包装。

        正式在线路径没有调用它的资格；没有明确 fixture 标记时只留下拒绝审计，
        防止旧脚本/嵌入式宿主悄悄恢复第二个自主 writer。
        """
        del prev_reply
        if not _fixture_legacy:
            now = int(now_ms if now_ms is not None else _time.time() * 1000)
            self.store.append_event({
                "ts": now, "kind": "opening_dispatch_rejected",
                "detail": {"rule": "legacy_followup_fixture_required"},
            })
            return None
        return self.commit_opening_message(
            now_ms=now_ms, source="followup", _should_continue=_should_continue,
            _fixture_legacy=True)

    def _proactive_bridge(self, now_ms):
        """把当前墙钟状态投影为主动线可查询的既有桥接词元。

        没有时间桥就返回空：主动分享不应仅因“档案里有旧事”而随机翻旧账。
        """
        previous = self.state.get("last_end_drives")
        if previous is None:
            return []
        from core.tokens.behavior import analyze_time_bridge
        from core.vectors import get_drive_vector
        constants = (self.profile or {}).get("constants") or {}
        current = get_drive_vector(calculate_drives(constants, self.state["vars"]))
        return analyze_time_bridge(current, previous)["tokens"]

    def _proactive_expression_context(self, *, state, now_ms, source,
                                      previous_drives=None, sent_ms=None):
        """为一次无新用户输入的写作重新编译表达卡。

        不能沿用上一条主回复的 attention plan：其中可能有针对旧输入的应对卡，
        会让追发继续逐词辩护。这里仅使用已提交输出时间锚与当前根量投影；它不
        重排时间场、不查询记忆、更不决定 F15 是否开口。
        """
        # 旧签名保留给 replay/fixture 调用；新的根量词元绝不读取它们，因为
        # ``last_end_drives``/``prev_vars`` 不是可证明同 C 的输出锚。
        del previous_drives, sent_ms
        active = state or self.state
        time_binding = self._effective_time_binding()
        output_anchor = self._latest_committed_expression_anchor(now_ms=now_ms)
        interaction_residual_anchor = self._committed_interaction_residual_anchor_for_output(
            output_anchor, now_ms=now_ms)
        memory_anchor = self._latest_committed_memory_expression_anchor(
            now_ms=now_ms,
            after_output_ms=(output_anchor or {}).get("sent_ms"))
        event_anchor = self._latest_committed_event_expression_anchor(
            now_ms=now_ms,
            after_output_ms=(output_anchor or {}).get("sent_ms"))
        time_anchor = self._latest_committed_time_expression_anchor(
            now_ms=now_ms,
            after_output_ms=(output_anchor or {}).get("sent_ms"))
        transition = compile_expression_tokens(
            current_constants=time_binding["effective_constants"],
            current_constants_version=time_binding["effective_constants_version"],
            current_variables=active.get("vars") or {}, now_ms=int(now_ms),
            output_anchor=output_anchor, memory_anchor=memory_anchor,
            event_anchor=event_anchor, time_anchor=time_anchor,
            interaction_residual_anchor=interaction_residual_anchor, source=source)
        cards = list(transition.get("cards") or ())
        if self.expression_attention:
            # 正式主动线与 direct 线消费完全相同的 descriptor -> plan -> delivery
            # 契约。它不读取旧 F07 分数、bridge 或固定 Top-N；历史余量也只能是
            # 此前 F16 已提交的 receipt。
            emission_plan = compile_expression_emission_plan(
                transition.get("candidates") or (),
                committed_remainders=active.get("expression_emission_remainders"),
            )
            plan = compile_expression_delivery(cards=cards, emission_plan=emission_plan)
        else:
            # 仅供显式 A/B 的旧通路；默认产品路径绝不回退到这里。
            sources = []
            for item in cards:
                expression_source = str(item.get("expression_source") or "")
                sources.append({"source": {
                    "lane": ("npc_drive_state" if expression_source == "current_drive_state"
                             else "npc_time_state" if expression_source == "committed_time_transition"
                             else "npc_event_state" if expression_source == "committed_event_transition"
                             else "npc_memory_state" if expression_source == "committed_memory_transition"
                             else "npc_interaction_residual" if expression_source == "committed_interaction_residual"
                             else "npc_output_transition"),
                    "owner": "npc",
                    "expression_eligible": True,
                }})
            plan = compile_expression_attention(
                ranked={"all": cards}, bridge_tokens=(), source_receipt=sources,
                max_writer_tokens=len(cards))
        return cards, plan, dict(transition.get("receipt") or {})

    @staticmethod
    def _active_field_recall_tokens(state):
        """读出已生效时间场的词元，作为主动检索的当前状态证据。

        这里不重新 rank、不改变场，也不把 ``last_ranked`` 这类上一轮语言资源重放为
        当前状态。``field`` 是墙钟已经在推进的编译产物；其中的 token 以既有 ``w``
        作为检索权重，恰好描述“此刻仍在作用的场”。没有活动场则保持空。
        """
        field = (state or {}).get("field") or {}
        out = []
        for raw in field.get("tokens") or ():
            text = str(raw.get("text") or "")
            if not text:
                continue
            try:
                strength = float(raw.get("w", raw.get("strength", 1.0)))
            except (TypeError, ValueError):
                strength = 1.0
            out.append({"text": text, "category": raw.get("category", "other"),
                        "strength": max(0.0, strength)})
        return out

    @staticmethod
    def _merge_proactive_recall_tokens(bridge_tokens, field_tokens):
        """同一 selector 的两类既有查询证据去重合并，不建立新词元流。"""
        merged = {}
        for token in list(bridge_tokens or ()) + list(field_tokens or ()):
            text = str(token.get("text") or "")
            if not text:
                continue
            try:
                strength = float(token.get("strength", 1.0))
            except (TypeError, ValueError):
                strength = 1.0
            current = merged.get(text)
            if current is None or strength > float(current.get("strength", 0.0)):
                merged[text] = {"text": text, "category": token.get("category", "other"),
                                "strength": max(0.0, strength)}
        return list(merged.values())

    def _proactive_recall_query(self, now_ms, *, state=None, bridge_tokens=None):
        """主动线的查询收据：时间桥 + 已生效时间场，写作输入仍只见时间桥。"""
        active_state = self.state if state is None else state
        bridge = (list(bridge_tokens) if bridge_tokens is not None
                  else self._proactive_bridge(now_ms))
        field_tokens = self._active_field_recall_tokens(active_state)
        return {"bridge_tokens": bridge, "field_tokens": field_tokens,
                "query_tokens": self._merge_proactive_recall_tokens(bridge, field_tokens)}

    def _prepare_proactive_recall(self, now_ms, query_tokens, source, query_receipt=None):
        """冻结主动 recall；只有正式 output 落地后，调用方才能提交其 plan。"""
        receipt = dict(query_receipt or {})
        # opening anchor 只保存 F03 已提交事件的精确 identity。它不创建第二张
        # “当前输入卡”，也不合并 Actor：本 owner 到自己的 tick 时，selector
        # 仍须从本 Store 读回这条已归档 event。
        anchor = self.state.get("opening_anchor") or {}
        opening_anchor_event_id = (str(anchor.get("event_id") or "").strip()
                                   if str(source) == "online_proactive" else "")
        if self.recall_strategy != "legacy" and not opening_anchor_event_id:
            return None
        self.store.append_event({
            "ts": int(now_ms), "kind": "proactive_recall_query",
            "turn_id": "proactive:%s:%d" % (str(source), int(now_ms)),
            "detail": {
                "source": str(source),
                "bridge_token_texts": [str(t.get("text")) for t in receipt.get("bridge_tokens") or ()],
                "field_token_texts": [str(t.get("text")) for t in receipt.get("field_tokens") or ()],
                "query_token_texts": [str(t.get("text")) for t in query_tokens],
                "opening_anchor_event_id": opening_anchor_event_id or None,
                "memory_selector_inputs": ["committed_event_id", "Delta_X_event", "wall_clock"],
                "excluded_context_timestamps": [],
            }, "params_sha": self.params_sha,
        })
        recall = select_compat_autonomous_recall(
            store=self.store, memory=self.memory, ledger=self.ledger,
            now_ms=now_ms, bridge_tokens=query_tokens,
            strategy=self.recall_strategy, rng=self.rng,
            params_sha=self.params_sha,
            turn_id="proactive:%s:%d" % (str(source), int(now_ms)),
            commit_selected=False,
            temporal_readout=lambda record: self._memory_temporal_readout(
                record, now_ms=now_ms),
            opening_anchor_event_id=opening_anchor_event_id)
        return recall

    def _resolve_proactive_recall_events(self, now_ms, query_tokens, source, query_receipt=None):
        """兼容只读装配入口：选择不等于已想起，绝不在此处落账。"""
        recall = self._prepare_proactive_recall(
            now_ms, query_tokens, source, query_receipt=query_receipt)
        return (() if recall is None else recall.expression_plan.events)

    def _trace_proactive_prompt(self, source, ts, prompt):
        """主动线保留与普通回合同口径的本地装配收据。"""
        self.store.append_prompt_trace({
            "schema_version": 1, "kind": "responder",
            "turn_id": "proactive:%s:%d" % (str(source), int(ts)),
            "source": str(source), "ts": int(ts),
            "system": prompt.system, "user": prompt.user,
            "system_sha": sha256_text(prompt.system), "user_sha": sha256_text(prompt.user),
            "persona_block_ids": list(prompt.persona_block_ids),
            "response_block_ids": list(prompt.response_block_ids),
            "recall_event_ids": list(prompt.recall_event_ids),
            "excluded_ledger_ts": list(prompt.excluded_ledger_ts),
            "history_sources": list(prompt.history_sources),
            "history_exclusions": list(prompt.history_exclusions),
            "assembly_receipt": dict(prompt.assembly_receipt or {}),
            "model": str(getattr(self.responder, "model", "unknown"))[:160],
            "length_mode": prompt.length_mode,
        })

    def generate_share(self, now_ms=None, _should_continue=None, *, _fixture_legacy=False):
        """只供显式 fixture 使用的旧分享 writer。

        产品没有 share Actor 任务；若未显式声明 fixture，宁可留下拒绝审计，也不让
        工具/嵌入式调用重新获得一条绕开 ``opening_due`` 的自主投递权。
        """
        should_continue = (_should_continue if callable(_should_continue)
                           else (lambda: True))
        try:
            if not should_continue():
                return None
            now = int(now_ms if now_ms is not None else _time.time() * 1000)
            if not _fixture_legacy:
                self.store.append_event({
                    "ts": now, "kind": "opening_dispatch_rejected",
                    "detail": {"rule": "legacy_share_fixture_required"},
                })
                return None
            form_plan = compile_form_projection(
                baseline=self.prior or {k: 0.2 for k in PROTOTYPES},
                dynamic=self.state.get("weights") or self.prior,
                constants=self.profile.get("constants") or {},
                variables=self.state.get("vars") or {})
            query = self._proactive_recall_query(now)
            bridge = query["bridge_tokens"]
            proactive_recall = self._prepare_proactive_recall(
                now, query["query_tokens"], "share", query_receipt=query)
            recall_events = (() if proactive_recall is None
                             else proactive_recall.expression_plan.events)
            prompt = assemble_compat_autonomous_prompt(
                persona_blocks=self.persona_blocks, turn=self.turn,
                field_pressure=self.state.get("P", 0.0), ranked_tokens=[],
                bridge_tokens=bridge, attraction=float(self.state["vars"].get("V_attr", 0.5)),
                dialogue_tail="",
                state_hint=None,
                budget_chars=self.prompt_budget_chars,
                responder_core_prompt=self.responder_core_prompt, now_ms=now,
                recall_expression_events=recall_events, form_projection=form_plan,
                canonical_memory_sequence=True,
                output_identity=self._writer_output_identity())
            self._trace_proactive_prompt("share", now, prompt)
            f_reply, f_facts, f_cons, f_deltas, f_evidence, _used, _recall_evidence, _protocol = self._generate_reply(
                prompt.system, prompt.user, DEFAULTS["responder_temperature"])
            if not should_continue():
                return None
            # 显式 now_ms 只用于离线/测试时间线：不能把宿主今天的生成耗时写回
            # 伪造的历史主动事件。正常 Actor 入口仍在定稿后采样真实 ready。
            ready_ms = (now if now_ms is not None else int(_time.time() * 1000))
            timeline = resolve_output_timeline(
                received_ms=now, ready_ms=ready_ms, cadence_delay_ms=0)
            ts = timeline["t_sent"]
            rec = self._persist_npc_output(
                state=self.state, ts=ts, text=f_reply, source="share", S=0.0, tokens=[],
                recall_refs=[recall_expression_reference(event)
                             for event in prompt.recall_expression_events],
                received_ms=now, ready_ms=ready_ms, cadence_delay_ms=0,
                raw_deltas=f_deltas, evidence=f_evidence,
                calibration_assembly_receipt=prompt.assembly_receipt,
                memory_recall=proactive_recall)
            state_before_output = copy.deepcopy(self.state)
            applied_deltas = self._apply_npc_self_expression(
                self.state, reply=f_reply, raw_deltas=f_deltas, evidence=f_evidence,
                ts=ts, source="share", action_id=rec.get("action_id"),
                output_commit=rec.get("_output_commit_payload"),
                output_evidence=rec.get("_output_commit_evidence"),
                output_memory_recall=rec.get("_output_memory_recall"))
            committed_deltas = self._committed_output_deltas(rec, applied_deltas)
            self._ledger_from_reply(
                f_reply, ts, f_facts, f_cons, event_id=rec.get("event_id"))
            s = self.state
            self._bump_unanswered(s, 1, ts, "inc_share")
            self.store.save_state(s)
            clock_plan = self.wall_clock.refresh(
                s, (self.profile or {}).get("constants") or {}, ts,
                cause="share_committed", opening=None,
                cadence=self._cadence_projection(s))
            self._trace_autonomous_action(
                receipt=rec, source="share", ts=ts,
                recall_events=prompt.recall_expression_events,
                clock_plan=clock_plan, form_projection=form_plan,
                variable_deltas=committed_deltas, state_before=state_before_output)
            return f_reply
        except Exception:
            return None

    def refine_preference(self):
        """偏好精修：生成候选观测，不直接改写稳定基准 B₀。

        ``prior`` 是用户声明的稳定基准；自动路径（包括 ``drift_gate``）只能留下
        append-only proposal receipt，不能写入它。这里的 LLM 输出只是对近期消息的
        第二份、可追溯观测，供后续审核和新版本绑定使用。
        """
        if self.preference is None:
            return False
        msgs = [m for m in self.store.read_messages() if self._is_preference_evidence(m)]
        if len(msgs) < 3:
            return False
        rs, ru = build_refine_messages(msgs)
        raw = self.preference.call(rs, ru,
                                   temperature=DEFAULTS["refine_temperature"],
                                   require_json=True)
        refined = parse_refine(raw)
        observed_through = max((int(m.get("ts", 0) or 0) for m in msgs), default=0)
        self.store.append_event({
            "ts": int(_time.time() * 1000),
            "kind": "preference_candidate",
            "detail": {
                "source": "refiner_llm",
                "weights": dict(refined),
                "message_count": len(msgs),
                "observed_through_ts": observed_through,
                "writes_b0": False,
            },
            "params_sha": self.params_sha,
        })
        return True

    # ------------------------------------------------------------------
    # 用户消息（同步路径）
    # ------------------------------------------------------------------
    def discard_pending_reply(self, now_ms=None):
        """丢弃尚未发送的延迟回复，供 Actor 的 Enter 抢占边界调用。

        这不是“撤回”任何已发生消息：只有还停留在 ``pending_reply``、没有
        message/action receipt 的草稿才会被摘掉。保留无正文的 interrupt 事实，
        仅用于后续节律回放；它不进入记忆、台账或状态积分。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        with self._release_lock:
            prev = self.state.get("pending_reply")
            if not prev:
                return None
            self.state["pending_reply"] = None
            self.store.save_state(self.state)
            self.store.append_interrupt({
                "ts": now,
                "dropped_send_at": prev.get("send_at"),
                "reason": "user_submit_preempt",
            })
            return {"dropped_send_at": prev.get("send_at")}

    def _commit_peer_observation(self, *, now, text, state, report, turn_id, staged_events,
                                 staged_cold, staged_boundaries, pending_actions, pending_slot,
                                 profile, profile_dirty, behavior, deltas, delay_ms, tier_hint,
                                 pain, b0, eligibility, reaction_parts, recall_query_tokens,
                                 recall=None, p4_recall_commit_due=False, math_trace=None,
                                 conversation_provenance=None, p3_before_input=None,
                                 time_binding=None, incoming_event_kind="peer_input",
                                 participant_source_kind="npc", preference_evidence=False):
        """提交未获主回复资格的角色来信。

        这不是测试器吞消息：输入照常进入时间总线、档案与墙钟，唯独不会构造 writer
        prompt、冷却一次召回，或把未说出的草稿记成 NPC 输出。这样后续 `catch_up()`
        仍只面对已发生的状态与记忆。
        """
        # 此刻不说话不等于不存在内部唤起。P4 已在同一输入事件中冻结的 selected
        # receipt 必须原样复用；旧路径才在这里做一次只读选择。两者均不构造 writer。
        if recall is None:
            recall = select_compat_received_recall(
                store=self.store, memory=self.memory, ledger=self.ledger, text=text,
                now_ms=now, ranked_tokens=recall_query_tokens,
                bridge_tokens=report["bridge_tokens"], embed_fn=self.embed_fn,
                turn_id=turn_id, strategy=self.recall_strategy, rng=self.rng,
                params_sha=self.params_sha, event_sink=staged_events.append,
                boundary_sink=staged_boundaries.append, commit_selected=False,
                temporal_readout=lambda record: self._memory_temporal_readout(
                    record, now_ms=now), include_memory_anchor=False)
        recall_preflight = {
            "mode": recall.mode,
            "evidence": round(float(recall.evidence), 6),
            "selected": [{"source": source, "record_ts": int(record.get("ts", now))}
                         for source, record in recall.selected_records],
            # legacy 概率门的候选裁决与本次真正命中必须分开呈现：前者说明“相关旧事
            # 被考虑”，后者才说明“本轮真的想起”。两者都不等价于已交给 writer。
            "shadow_selected": [{"source": str(item.get("source")),
                                 "record_ts": int(item.get("record_ts", now))}
                                for item in ((recall.shadow or {}).get("selected_bundles") or [])
                                if isinstance(item, dict)],
            "delivery": "not_attempted_no_peer_reply",
        }
        for record in staged_events:
            self.store.append_event(record)
        for record in staged_cold:
            self.store.append_cold(record)
        for record in staged_boundaries:
            self.store.append_boundary(record)
        self._commit_npc_actions(pending_actions)
        self._commit_pending_extraction(pending_slot, turn_id, now, state=state)
        if profile_dirty:
            self.store.save_profile(profile)
            self.profile = profile
        # 对端 NPC 的已提交输入是本 Actor 的共同互动事实，但不是“用户又出现”或
        # 用户偏好证据；不能借 peer 分支悄悄重置 user-silence / B0 观测。
        if preference_evidence:
            self._last_user_ts = now
            self._last_gap_band = -1
        salience_params = dict(SALIENCE)
        tuned = load_tuned_weights(self.profile)
        if tuned:
            for key, value in tuned.items():
                if key in salience_params:
                    salience_params[key] = float(value)
        S, s_parts = compute_salience_parts(
            self.state.get("prev_vars"), state["vars"], pain=pain, tier=tier_hint,
            text=text, params=salience_params)
        self._remember(now, "user", text, source="peer", S=S,
                       tokens=report["ranked"]["all"], delay_ms=delay_ms,
                       s_parts=s_parts, reward=reaction_parts["R"], turn_id=turn_id,
                       conversation_provenance=conversation_provenance,
                       participant_source_kind=participant_source_kind,
                       preference_evidence=preference_evidence,
                       **self._event_state_imprint_kwargs(report))
        from ops.recall_geometry import token_direction
        drift = {"P": report["P"], "E": report["E"]}
        field_dir = token_direction(report["field_tokens"])
        if field_dir is not None:
            drift["dir"] = field_dir
        self.memory.archive_tokens(
            now, report["field_tokens"], S=S, drift=drift, source="peer",
            turn_id=turn_id,
            **self._event_state_imprint_kwargs(report))
        adjudicate_denials(store=self.store, parts=reaction_parts["components"], now_ms=now,
                           turn=self._turn_sequence, turn_id=turn_id,
                           history=r_parts_history(self.store))
        self._sync_pending_extraction_to_state(state)
        self.state = state
        self.store.save_state(state)
        self.store.save_snapshot(state, self.turn + 1)
        if p4_recall_commit_due:
            # peer 没有 writer 也不改变该 recall 的事实顺序：F03 共同历史先落盘，
            # 随后只提交已冻结的旧账本 plan。
            commit_turn_recall(memory=self.memory, ledger=self.ledger,
                               recall=recall, now_ms=now)
        # peer 无回复同样拥有已完成的选择 receipt；W_recall 只能观察该 receipt，
        # 不能因本轮不说话而改写选择或伪造一次 writer 交付。
        recall_plan = compile_recall_projection(
            baseline=self.prior or b0, dynamic=behavior,
            selection=recall_selection_summary(recall))
        # 未直接回复的 peer 输入仍须生成 P3 authority，再交给 wall-clock 形成下一
        # 次正式 tick 的计划。过去这里直接 replace，导致“同一 F03 链”在无回复
        # 分支断在 P3 前，前端/测试只能看到状态而无法追溯开口来源。
        binding = time_binding or self._effective_time_binding()
        p3_input_receipt = self._publish_p3_authority_receipt(
            (p3_before_input if p3_before_input is not None else self.state), state,
            effective_constants=binding["effective_constants"],
            effective_constants_version=binding["effective_constants_version"],
            event_kind=incoming_event_kind, event_id=turn_id, observed_at_ms=now,
            turn_id=turn_id, conversation_provenance=conversation_provenance)
        cadence = self._cadence_projection(state)
        clock_plan = self.wall_clock.replace(
            state, (self.profile or {}).get("constants") or {}, now,
            cause=incoming_event_kind + "_measurement_committed",
            opening=None, cadence=cadence, authority_receipt=p3_input_receipt)
        if math_trace is not None:
            time_binding = self._effective_time_binding()
            math_trace.record(
                phase="memory_resolved", formula_id="F10",
                output_summary={"selected_count": len(recall.selected_records),
                                "delivered_count": 0,
                                "recall_projection": self._projection_trace_summary(recall_plan)},
                decision_reason=("memory_selected_not_delivered"
                                 if recall.selected_records else "memory_not_selected"))
            signal = eligibility.get("signal") or {}
            math_trace.record(
                phase="schedule_evaluated", formula_id="F14",
                input_summary=self._f14_trace_input_summary(
                    state, time_binding["effective_constants"], deltas),
                output_summary={
                    **self._opening_trace_summary(signal),
                    # peer eligibility is the authoritative decision for this root.
                    "eligible": bool(eligibility.get("eligible", False)),
                },
                formula_params=self._candidate_trace_formula_params(signal),
                decision_reason="peer_reactive_reply_not_eligible")
            math_trace.record(
                phase="schedule_evaluated", formula_id="F15",
                input_summary=self._f15_trace_input_summary(
                    state, time_binding["effective_constants"], now,
                    effective_constants_version=time_binding["effective_constants_version"]),
                output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                "plan_version": int(clock_plan.get("version", 0)),
                                "reason_count": len(clock_plan.get("reasons") or ()),
                                "cadence_projection": self._cadence_trace_summary(cadence)},
                decision_reason=("clock_scheduled" if clock_plan.get("at_ms") is not None
                                 else "clock_idle"))
            self._commit_math_trace(math_trace)
        # peer 来信与用户来信一样是已提交输入，精提取仍应成为下一轮的校正；它
        # 只记录输入，不会凭空生成回复或改写本轮资格。
        if self.extractor is not None:
            es, eu = build_extractor_messages(text, state["vars"])
            source_sequence = self._turn_sequence

            def _do_extract():
                try:
                    raw_ext = self.extractor.call(
                        es, eu, temperature=DEFAULTS["extractor_temperature"], require_json=True)
                    self._record_extraction(
                        source_turn_id=turn_id, source_ts=now,
                        extraction=parse_extractor(raw_ext), source_sequence=source_sequence)
                except Exception as exc:
                    self.store.append_event(extractor_failed(
                        source_turn_id=turn_id, source_ts=now,
                        observed_at_ms=int(_time.time() * 1000),
                        error_type=type(exc).__name__, params_sha=self.params_sha))

            if self.async_extractor:
                threading.Thread(target=_do_extract, daemon=True).start()
            else:
                _do_extract()
        self.weights_prev = behavior
        self.turn += 1
        return {"peer_observed": True, "reply_planned": False, "turn_id": turn_id,
                "eligibility": eligibility, "S": S, "R": reaction_parts["R"],
                "clock_plan": clock_plan, "send_at": None, "reply": None,
                "response_deferred": False, "recall_preflight": recall_preflight}

    def on_received_participant_event(self, received_event, now_ms=None, delay_ms=None,
                                      explicit_feedback=None, _on_prepared=None, _on_draft=None,
                                      _should_continue=None, _begin_commit=None,
                                      _ready_clock=None):
        """唯一新式输入入口：消费已验证的 participant delivery，再走原 F03→F16。

        这里不创建 Conversation、查询成员、重算 B₀ 或写 source event；它只把已抵达本
        Runner 的正文和 exact delivery lineage 交给已有的输入事务。直接回复仍可经原 F16
        时间线提交；自主消息的发送权仍只属于 dynamic tick。
        """
        prepared = self.prepare_received_participant_event(
            received_event, now_ms=now_ms, delay_ms=delay_ms,
            explicit_feedback=explicit_feedback, _on_prepared=_on_prepared,
            _on_draft=_on_draft, _should_continue=_should_continue,
            _begin_commit=_begin_commit, _ready_clock=_ready_clock)
        if not prepared.get("complete"):
            prepared = self.generate_prepared_user_message(prepared)
        return self.complete_prepared_user_message(prepared)

    def prepare_received_participant_event(self, received_event, now_ms=None, delay_ms=None,
                                           explicit_feedback=None, _on_prepared=None,
                                           _on_draft=None, _should_continue=None,
                                           _begin_commit=None, _ready_clock=None):
        """执行 canonical received-event 的 F03 输入事务并返回本进程 prepared turn。

        该对象的 ``_flow`` 不是可持久化状态，也不会泄露给 UI；它只保留局部的
        prompt/召回工作态，使 responder 网络调用能在另一个 Actor draft worker
        中恢复。F03 已经在 yield 前提交；yield 后若被取消，只能得到无输出草稿。
        """
        from ops.received_participant_events import ReceivedParticipantEvent
        if not isinstance(received_event, ReceivedParticipantEvent):
            raise TypeError("Runner 只能消费 ReceivedParticipantEvent")
        response_mode = ("required" if received_event.response_policy == "required"
                         else "peer")
        flow = self._on_user_message_flow(
            received_event.text, now_ms=now_ms, delay_ms=delay_ms,
            explicit_feedback=explicit_feedback, _on_prepared=_on_prepared,
            _on_draft=_on_draft, _should_continue=_should_continue,
            _begin_commit=_begin_commit, _ready_clock=_ready_clock,
            _response_mode=response_mode, _received_event=received_event)
        try:
            prepared = next(flow)
        except StopIteration as stopped:
            # peer 未越线等合法早退没有 responder 草稿；它已经完成自己的输入
            # 观察事务，因此调用者无需再创建一个空 worker。
            return {"complete": True, "result": stopped.value}
        ticket = dict(prepared or {})
        ticket["complete"] = False
        ticket["phase"] = "prepared"
        ticket["_flow"] = flow
        return ticket

    def generate_prepared_user_message(self, prepared):
        """只恢复 responder 草稿阶段，禁止提交 state／memory／output receipt。"""
        if not isinstance(prepared, dict):
            raise TypeError("prepared turn 必须是字典")
        if prepared.get("complete"):
            return prepared
        if prepared.get("phase") != "prepared":
            raise RuntimeError("prepared turn 不在 draft 生成阶段")
        flow = prepared.get("_flow")
        if flow is None:
            raise RuntimeError("prepared turn 已被消费")
        try:
            draft = next(flow)
        except StopIteration as stopped:
            # 防御性分支：当前 required/peer 正常路径都会在草稿后再次暂停；
            # 若未来出现无需 output commit 的早退，仍保持 ticket 的一次性语义。
            prepared.pop("_flow", None)
            prepared["complete"] = True
            prepared["phase"] = "complete"
            prepared["result"] = stopped.value
            return prepared
        if not isinstance(draft, dict) or not draft.get("draft_ready"):
            raise RuntimeError("prepared turn 未在 draft 边界返回")
        prepared.update(draft)
        prepared["phase"] = "draft_ready"
        return prepared

    def complete_prepared_user_message(self, prepared):
        """在 F03 后恢复一次 draft/commit 流；同一 ticket 只能消费一次。"""
        if not isinstance(prepared, dict):
            raise TypeError("prepared turn 必须是字典")
        if prepared.get("complete"):
            return prepared.get("result")
        if prepared.get("phase") == "prepared":
            prepared = self.generate_prepared_user_message(prepared)
            if prepared.get("complete"):
                return prepared.get("result")
        if prepared.get("phase") != "draft_ready":
            raise RuntimeError("prepared turn 不在输出提交阶段")
        flow = prepared.pop("_flow", None)
        if flow is None:
            raise RuntimeError("prepared turn 已被消费")
        prepared["complete"] = True
        try:
            next(flow)
        except StopIteration as stopped:
            return stopped.value
        raise RuntimeError("prepared turn 在 draft 边界后意外再次暂停")

    def _on_user_message_flow(self, text, now_ms=None, delay_ms=None, explicit_feedback=None,
                              _on_prepared=None, _on_draft=None, _should_continue=None,
                              _begin_commit=None, _ready_clock=None, _response_mode="required",
                              _received_event=None):
        if _response_mode not in {"required", "peer"}:
            raise ValueError("未知回复入口模式")
        if _received_event is not None:
            from ops.received_participant_events import ReceivedParticipantEvent
            if not isinstance(_received_event, ReceivedParticipantEvent):
                raise TypeError("_received_event 必须为 ReceivedParticipantEvent")
            if _received_event.text != str(text):
                raise ValueError("received event 正文与 F03 输入不一致")
            expected_policy = ("required" if _response_mode == "required" else "eligible")
            if _received_event.response_policy != expected_policy:
                raise ValueError("received event policy 与输入事务不一致")
            delivery_reference = _received_event.receipt_reference()
            conversation_provenance = _received_event.memory_provenance()
            conversation_frame = _received_event.conversation_frame
            incoming_event_kind = _received_event.interaction_event_kind
            participant_source_kind = _received_event.sender_participant_kind
            preference_evidence = _received_event.preference_evidence
        else:
            delivery_reference = None
            conversation_provenance = None
            conversation_frame = None
            incoming_event_kind = ("peer_input" if _response_mode == "peer" else "human_input")
            participant_source_kind = ("npc" if _response_mode == "peer" else "human")
            preference_evidence = _response_mode != "peer"
        output_conversation_context = self._output_conversation_context_from_received(
            conversation_provenance)
        writer_identity = self._writer_output_identity()
        if output_conversation_context is not None and writer_identity is not None:
            if (str(output_conversation_context["owner_participant_id"])
                    != str(writer_identity["participant_id"])):
                raise ValueError("本次输出 Actor identity 与 received delivery owner 不一致")
        # 角色来信即使宿主为了 CLI/fixture 关闭了用户延迟，也必须先挂起到统一时间
        # 线；否则一个刚获得资格的 peer reply 又会退回同刻互答。用户入口保持其
        # 原始配置，只有 peer 入口强制走既有 pending/release 合同。
        delayed_response = bool(self.reply_delay or _response_mode == "peer")
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        # I1：主回复在 responder 返回前只构造局部工作态。被新 Enter 抢占时，
        # 输入正文、语义/召回诊断和反馈解释均不能遗留在 append-only 档案。
        staged_events = []
        staged_cold = []
        staged_boundaries = []

        def stage_event(record):
            staged_events.append(dict(record))
            return record

        def stage_boundary(record):
            staged_boundaries.append(dict(record))
            return record

        profile = copy.deepcopy(self.profile or {})
        constants = profile.get("constants") or {}
        if not constants:
            raise RuntimeError("尚未 setup()")
        turn_id = self._open_turn(now, persist=False)
        # AUD-124：当前 F16 来信和 recall event 同属已提交 interaction event；
        # 不能在 writer 表示时丢掉 sender/time/identity。这里仅透传 canonical
        # delivery lineage，不创建 memory、状态或新的 source。
        if delivery_reference is not None and _received_event is not None:
            projection = _received_event.projection
            current_input_event = {
                "speaker": "participant:%s" % str(delivery_reference["sender_participant_id"]),
                "committed_at_ms": int(projection.committed_at_ms),
                "event_id": str(delivery_reference["event_id"]),
                "delivery_id": str(delivery_reference["delivery_id"]),
            }
        else:
            current_input_event = {
                "speaker": str(participant_source_kind),
                "committed_at_ms": int(now),
                "event_id": "turn:%s" % str(turn_id),
            }
        math_trace = self._new_math_trace(
            root_kind="turn", root_id=turn_id, now_ms=now,
            root_refs={"turn_id": turn_id, "turn_ts": int(now)})
        stage_event(turn_opened(turn_id=turn_id, ts=now, params_sha=self.params_sha))
        if delivery_reference is not None:
            # delivery 已在 Router 侧提交；这里仅把它与本 Actor 的 F03 turn 精确关联。
            # 不能从这里反向创建 source event，更不能借此写状态、改成员或调度。
            stage_event(_received_event.delivery_receipt(
                turn_id=turn_id, received_at_ms=now, params_sha=self.params_sha))
        stage_event(input_received(
            turn_id=turn_id, ts=now, text=text, params_sha=self.params_sha))
        # 若上一条延迟回复已到发送点，必须先把它作为 NPC 的真实历史动作结算，
        # 再读取本条用户输入。否则“NPC 已说出口”的微量变化会被错误排到用户
        # 事件之后，破坏双轴时间顺序。
        released_before_input = self.release_pending(now)
        # 这份快照是本次 F03 外部输入前的已提交根状态；只用于随后生成一次 P3
        # receipt，不会写回或替换用户事件自己的原始 Delta_X。
        p3_before_input = copy.deepcopy(self.state)
        # 当前输入在 responder 返回前还不是历史。DIR-132/AUD-016：不再把任意
        # raw ordered message sequence（更不能按 writer budget 截断）提供给语义、
        # feedback 或 F10 rerank。历史影响只能经 F10 exact-ID selected receipt 前向
        # 进入 P4／writer；本处的模型输入只有 current source 和 explicit candidate。
        feedback_record = None
        if explicit_feedback is not None:
            # 在本轮新 NPC 回复生成前归因，避免把用户对上一动作的反馈错误挂到本轮回复。
            feedback_record = stage_event(self._record_explicit_feedback(
                feedback=explicit_feedback, turn_id=turn_id, now_ms=now, persist=False))
        elif self.feedback_interpreter is not None:
            # 解释器仅可返回已确认标签；None/异常均不影响当前回复事务。
            try:
                intent = coerce_feedback_intent(self.feedback_interpreter.interpret(
                    text=text, dialogue_tail=""))
                if intent is not None:
                    intent_record = intent.as_record()
                    stage_event(feedback_interpreted(
                        turn_id=turn_id, ts=now, intent=intent_record, params_sha=self.params_sha))
                    feedback_record = stage_event(self._record_explicit_feedback(
                        feedback=intent.label, turn_id=turn_id, now_ms=now,
                        source="semantic:" + intent.source, persist=False))
            except Exception as exc:
                stage_event({"kind": "feedback_interpretation_failed", "turn_id": turn_id,
                             "ts": now, "error_type": type(exc).__name__,
                             "params_sha": self.params_sha})

        # 热路径语义投影：不使用业务事件硬编码，结果只投影到既有总线的连续输入。
        # 失败降级到词典路径，同时追加失败事实，绝不阻断用户当前回复。
        projection = None
        if self.semantic_projector is not None:
            try:
                projection = coerce_projection(self.semantic_projector.project(
                    text=text, dialogue_tail="", recall_hint=None,
                    state_vars=self.state.get("vars") or {},
                    event_kind=incoming_event_kind), turn_id=turn_id,
                    event_kind=incoming_event_kind)
                if projection is not None:
                    stage_event(semantic_projected(
                        turn_id=turn_id, ts=now, projection=projection.as_record(),
                        params_sha=self.params_sha))
                    if projection.reference_frame is not None:
                        frame_record = projection.reference_frame.as_record()
                        frame_record["turn_id"] = turn_id
                        stage_event(reference_frame_resolved(
                            turn_id=turn_id, ts=now, reference_frame=frame_record,
                            params_sha=self.params_sha))
            except Exception as exc:
                stage_event({
                    "kind": "semantic_projection_failed", "turn_id": turn_id,
                    "ts": now, "error_type": type(exc).__name__,
                    "params_sha": self.params_sha,
                })

        # ⓪ 归因快照（B1）：本轮准备认领“自上次用户回合以来的 NPC 动作”。
        #    可抢占事务在提交闸通过后才真正摘牌；本轮生成的新回复/释放仍属于下一轮。
        pending_actions = self._peek_npc_actions()
        if pending_actions:
            last = pending_actions[-1]
            last_action, n_actions = ({"kind": last["kind"], "ts": last["ts"]},
                                      len(pending_actions))
        else:
            last_action, n_actions = None, 0
        attribution = None
        if last_action is not None:
            attribution = dict(last_action)
            attribution["gap_ms"] = max(0, now - last_action["ts"])
            attribution["actions_since"] = n_actions

        # ① 输入信号：词典、弹药与上一轮 extractor 校正在内聚组件中编译。
        #    主回复只先快照 extractor 槽；旧回复被新 Enter 撤销时，该测量仍留给
        #    下一条真正提交的用户消息消费。
        pending_slot = self._peek_pending_extraction()
        pending = (pending_slot.get("extraction") if isinstance(pending_slot, dict)
                   else pending_slot)
        signals = derive_turn_signals(
            text=text, ammo=profile.get("ammo") or [],
            now_state_vars=self.state["vars"],
            pending_extraction=pending, semantic_projection=projection,
            event_kind=incoming_event_kind)
        features = signals.features
        tier_hint = signals.tier_hint
        deltas = signals.variable_deltas
        lever_self = signals.lever_self
        lever_opponent = signals.lever_opponent
        ammo_hits = signals.ammo_hits
        pain = signals.extractor_pain
        if math_trace is not None:
            math_trace.record(
                phase="input_observed", formula_id="F02",
                input_summary={"has_semantic_projection": projection is not None},
                output_summary={"feature_count": len(features),
                                "ammo_hit_count": len(ammo_hits)},
                decision_reason="features_extracted")
            # F02 只说明“看到了什么”；F03 才是把输入测量编译为六轴事件增量的
            # 正式边界。过去没有这条 receipt，导致测试虽会跑到 F03，却无法证明
            # Delta_X/杠杆没有在中途丢失。该观测不参与任何状态或调度决策。
            math_trace.record(
                phase="input_observed", formula_id="F03",
                input_summary={
                    "has_pending_extraction": pending is not None,
                    "has_semantic_projection": projection is not None,
                },
                output_summary={
                    "Delta_X": {key: round(float(deltas.get(key, 0.0) or 0.0), 6)
                                for key in ("E_env", "P_need", "V_attr", "V_rep",
                                            "L_self", "L_opponent")},
                    "Delta_X_sources": {
                        source: {key: round(float(values.get(key, 0.0) or 0.0), 6)
                                 for key in ("E_env", "P_need", "V_attr", "V_rep",
                                             "L_self", "L_opponent")}
                        for source, values in signals.source_contributions.items()
                    },
                    "measurement_receipt": dict(signals.measurement_receipt or {}),
                    "Delta_L_self": round(float(lever_self or 0.0), 6),
                    "Delta_L_opponent": round(float(lever_opponent or 0.0), 6),
                }, decision_reason="turn_signals_derived")
        if ammo_hits:
            for entry in ammo_hits:
                entry["exposed"] = True
                entry["exposed_ms"] = now
        profile_dirty = bool(ammo_hits)

        # ③ 图式粗算（含本轮消息）——期②-4/5 接线切换：α 逐轮混合退役，
        #    D(t)（行为拟合）与 B₀（基准锚）直接分路进总线：
        #    weights = D(t)（瞬时，驱动 D_user/博弈/追赶）；stable_weights = B₀
        #    （稳定，驱动 E_user/纯度/心向词元）——"开局体验由声明主导"从此
        #    由结构保证（B₀ 永稀释不动），不再是 α=1 的轮数特权
        msgs = [{"ts": m.get("ts", now), "text": m.get("text", ""),
                 "delay_ms": m.get("delay_ms")}
                for m in self.store.read_messages()
                if self._is_preference_evidence(m) and not is_non_dialogue_seed(m)]
        # D(t) 是声明用户喜好的当下测量，不是“任意参与者话语”的混合画像。
        # peer 仍走同一 F03/F08/P3/P4/F16 链并影响本 Actor 根状态；但不得借
        # 这一处 transient append 偷写 B0 / 用户偏好。模拟用户的可选授权会在
        # 独立 policy 到位后显式开启，不能把 NPC peer 静默当作 human。
        if preference_evidence:
            msgs.append({"ts": now, "text": text, "delay_ms": delay_ms})
        behavior = coarse_fit(msgs, baseline=self.baseline)
        behavior = blend_behavior_weights(behavior, projection)
        gap_h = None
        if self.state.get("last_ts") is not None:
            gap_h = (now - self.state["last_ts"]) / 3_600_000.0
        b0 = self.prior or {k: 0.2 for k in PROTOTYPES}
        if math_trace is not None:
            # B0（声明锚）和 D(t)（近期行为测量）须并列可见，不能只在 event
            # 字典里悄悄交给 temporal bus；二者职责不同，后续回归才能查出错接。
            math_trace.record(
                phase="input_observed", formula_id="F04",
                input_summary={"recent_user_message_count": len(msgs),
                               "baseline_available": bool(self.baseline)},
                output_summary={
                    "D_t": {key: round(float(behavior.get(key, 0.0) or 0.0), 6)
                            for key in PROTOTYPES},
                    "B0": {key: round(float(b0.get(key, 0.0) or 0.0), 6)
                           for key in PROTOTYPES},
                }, decision_reason="behavior_and_baseline_bound")

        # ④ 时间总线：积分 + 事件 + 词元几何 + 结束词元 → 新场
        event = {"var_deltas": deltas,
                 "lever_deltas": {"self": lever_self, "opponent": lever_opponent},
                 "weights": behavior, "stable_weights": b0,
                 "weights_prev": self.weights_prev,
                 "semantic_tokens": list(projection.tokens) if projection else []}
        time_binding = self._effective_time_binding()
        # P4 覆盖两类已提交外部输入：普通用户与 peer。二者都从同一原 state 结算；
        # 已送达 NPC 输出和自主线仍未迁移，不能借此半接入。无 policy 保持原 F08 路径。
        p4_enabled = (self._p4_state_bridge_enabled()
                      and _response_mode in {"required", "peer"})
        p4_recall_committed = False
        p4_recall_commit_due = False
        if p4_enabled:
            # 预结算是纯函数，只为 resolver 提供当前事件的 RAG 查询证据；它绝不
            # 写 state/store。真正持久化的最终 state 仍从同一个原状态重算一次。
            provisional_state, provisional_report = on_message(
                self.state, constants, event, now,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"])
            recall_query_tokens = assemble_recall_query_tokens(
                provisional_report["ranked"]["all"],
                list(projection.tokens) if projection else ())
            recall = self._resolve_turn_recall_for_event(
                text=text, now_ms=now, report=provisional_report,
                query_tokens=recall_query_tokens, turn_id=turn_id,
                stage_event=stage_event,
                stage_boundary=stage_boundary)
            p4_batch = self._prepare_memory_state_batch(
                recall=recall, report=provisional_report, now_ms=now,
                reference_frame=(projection.reference_frame if projection else None), text=text)
            stage_event({
                "schema_version": 1, "kind": "memory_state_selection_evaluated",
                "turn_id": turn_id, "ts": now,
                "detail": self._memory_state_selection_decision(p4_batch),
                "params_sha": self.params_sha,
            })
            if p4_batch.get("state_suppressed"):
                stage_event({
                    "schema_version": 1, "kind": "memory_state_recall_suppressed",
                    "turn_id": turn_id, "ts": now,
                    "detail": {
                        "reference_scope": str((projection.reference_frame.delivery_scope
                                                if projection and projection.reference_frame
                                                else "none")),
                        "suppressed": list(p4_batch["state_suppressed"]),
                    },
                    "params_sha": self.params_sha,
                })
            if p4_batch.get("ready_to_commit") is True:
                final_event = dict(event,
                                   memory_delta=p4_batch["Delta_X_memory"],
                                   memory_state_receipt=p4_batch)
                state, report = on_message(
                    self.state, constants, final_event, now,
                    effective_constants=time_binding["effective_constants"],
                    effective_constants_version=time_binding["effective_constants_version"])
                memory_expression = self._memory_expression_anchor_from_transition(
                    memory_transition=report.get("memory_state_transition"),
                    time_binding=time_binding, committed_ms=now,
                    event_kind=incoming_event_kind)
                if memory_expression is None:
                    raise ValueError("已提交 P4 batch 必须生成完整记忆表现锚")
                stage_event({
                    "schema_version": 1, "kind": "memory_state_batch_committed",
                    "turn_id": turn_id, "ts": now,
                    "detail": {
                        "version": p4_batch["version"],
                        "policy_binding_version": p4_batch["policy_binding_version"],
                        "policy_family_version": p4_batch.get("policy_family_version"),
                        "effective_constants_version": p4_batch.get("effective_constants_version"),
                        "dynamic_readout": copy.deepcopy(p4_batch.get("dynamic_readout")),
                        "selected_memory_ids": list(p4_batch["selected_memory_ids"]),
                        # selected 记忆的 relation source 只能随正式 P4 receipt
                        # 留痕；此字段不参与向量计算，也不会给来源 relation 写状态。
                        "selection_receipts": copy.deepcopy(
                            p4_batch.get("selection_receipts") or {}),
                        "Delta_X_memory": dict(p4_batch["Delta_X_memory"]),
                        # 这仍是 stage 内的事实；仅当本轮 F03 用户事件连同最终 X
                        # 一起完成提交后，才成为下一次 writer 可读的表现锚。
                        "expression_anchor": memory_expression,
                    },
                    "params_sha": self.params_sha,
                })
                # 状态 batch 本身是本轮真正的“想起”事实；旧 recall 账本仍需同一份
                # commit plan，但必须等 F03 用户输入已随最终 X 落盘，不能反向抢跑。
                p4_recall_commit_due = True
            else:
                state, report = provisional_state, provisional_report
        else:
            state, report = on_message(
                self.state, constants, event, now,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"])
        # 新用户消息才会开始一段新的开口轨迹。这个 receipt 不属于 X，后续 NPC
        # 输出只可回写 X，不能覆盖它；因此追发与沉默后开口共享同一 Delta_t。
        state["opening_anchor"] = {"ts": int(now), "turn_id": str(turn_id),
                                   # 与随后的 F03 message/archive 相同的既有稳定
                                   # 身份；不是由 ts/text 猜出来的另一条 event。
                                   "event_id": "turn:" + str(turn_id),
                                   "delta": dict(deltas)}
        # F03 的前后态由 temporal bus 在上面刚刚一次性结算；把它冻结成无正文的
        # 表现锚，既让直接因果回复可看见“当前变化”，也让 peer 未回应时保留给
        # 将来的自主 writer。P4 如存在则另有自己的 Delta_X_memory 锚，不能用
        # 最终 X 替换这里的 X_after。
        current_event_expression_anchor = self._event_expression_anchor_from_transition(
            event_transition=report.get("state_transition"), time_binding=time_binding,
            event_ms=now, event_kind=incoming_event_kind)
        if current_event_expression_anchor is None:
            raise ValueError("已结算 F03 输入必须生成完整事件表现锚")
        current_time_expression_anchor = self._time_expression_anchor_from_f08_events(
            events=report.get("events"), time_binding=time_binding, settled_ms=now,
            event_kind=incoming_event_kind + "_pre_event")
        if math_trace is not None:
            math_trace.record(
                phase="state_settled", formula_id="F05",
                input_summary=self._root_state_trace_summary(state, constants),
                output_summary={
                    key: round(float((report.get("drives") or {}).get(key, 0.0) or 0.0), 6)
                    for key in ("S", "T", "R_mag", "D_aggro", "D_defend", "D_chaos")
                }, decision_reason="drive_geometry_observed")
            math_trace.record(
                phase="state_settled", formula_id="F06",
                input_summary={
                    **self._root_state_trace_summary(
                        state, time_binding["effective_constants"]),
                    "Delta_X_event": {key: round(float(deltas.get(key, 0.0) or 0.0), 6)
                                      for key in ("E_env", "P_need", "V_attr", "V_rep",
                                                  "L_self", "L_opponent")},
                    "effective_constants_version": time_binding["effective_constants_version"],
                },
                output_summary={
                    "P": round(float(report.get("P", 0.0)), 6),
                    "E": round(float(report.get("E", 0.0)), 6),
                    "H_max": round(float(report.get("H_max", 0.0)), 6),
                    "exploded": bool(report.get("exploded", False)),
                }, decision_reason="message_settled")
        # 用户消息到达 = 回暖：未回复计数清零（心冷机制，双线架构 §四·补）
        cold_reset = self._bump_unanswered(
            state, -int(state.get("unanswered_n", 0)), now, "reset", persist=False)
        if cold_reset is not None:
            staged_cold.append(cold_reset)

        # R 的各分量先于 writer 产生：它们本来就是输入测量，而非输出奖励。因此
        # peer 入口可以在不构造 prompt/草稿的情况下走同一连续资格公式。
        from ops.reward import compute_reward_parts, reaction_tokens
        R_parts = compute_reward_parts(text, features=features, delay_ms=delay_ms,
                                       baseline=self.baseline, tier_hint=tier_hint,
                                       ranges=personal_ranges(msgs[:-1]))
        R = R_parts["R"]
        R_tokens = reaction_tokens(R_parts)
        if math_trace is not None:
            math_trace.record(
                phase="input_observed", formula_id="F11",
                input_summary={"arrival_delay_ms": int(delay_ms or 0),
                               "tier_present": tier_hint is not None},
                output_summary={
                    "R": round(float(R or 0.0), 6),
                    "component_count": len(R_parts.get("components") or {}),
                }, decision_reason="reaction_observed")
        peer_eligibility = None
        if not p4_enabled:
            recall_query_tokens = assemble_recall_query_tokens(
                report["ranked"]["all"], list(projection.tokens) if projection else ())
        if _response_mode == "peer":
            peer_eligibility = peer_response_eligibility(
                state=state, constants=time_binding["effective_constants"],
                variable_deltas=deltas, ammo_hits=ammo_hits,
                reaction_parts=R_parts, cadence=self._cadence_projection(state))
            stage_event({"schema_version": 1, "kind": "peer_response_eligibility",
                         "turn_id": turn_id, "ts": now, "detail": peer_eligibility})
            # AUD-109：peer eligibility 是这条 F03 的可回放读数，不是一次 direct
            # writer/pending/F16 授权。无论读数是否越线，peer source 都先以同一
            # P3 authority 结束；只有 owner-local dynamic tick 才能在 F08→F15 后
            # 获得并消费 opening。否则 eligible 分支会在 F03 后写 pending reply，
            # 覆盖刚形成的 h_next_ms，让测试和产品都退回 timer 发送。
            stage_event(input_measurement_receipt(
                turn_id=turn_id, ts=now, delta_x=deltas,
                measurement=signals.measurement_receipt,
                source_contributions=signals.source_contributions,
                state_anchor_ms=state.get("last_advance_ms"),
                params_sha=self.params_sha,
                expression_anchor=current_event_expression_anchor,
                time_expression_anchor=current_time_expression_anchor,
                delivery_reference=delivery_reference,
                participant_source_kind=participant_source_kind,
                preference_evidence=preference_evidence,
                response_policy=_received_event.response_policy))
            return self._commit_peer_observation(
                now=now, text=text, state=state, report=report, turn_id=turn_id,
                staged_events=staged_events, staged_cold=staged_cold,
                staged_boundaries=staged_boundaries, pending_actions=pending_actions,
                pending_slot=pending_slot, profile=profile, profile_dirty=profile_dirty,
                behavior=behavior, deltas=deltas, delay_ms=delay_ms,
                tier_hint=tier_hint, pain=pain, b0=b0, eligibility=peer_eligibility,
                reaction_parts=R_parts,
                recall_query_tokens=(None if p4_enabled else recall_query_tokens),
                recall=(recall if p4_enabled else None),
                p4_recall_commit_due=p4_recall_commit_due,
                math_trace=math_trace,
                conversation_provenance=conversation_provenance,
                p3_before_input=p3_before_input,
                time_binding=time_binding,
                incoming_event_kind=incoming_event_kind,
                participant_source_kind=participant_source_kind,
                preference_evidence=preference_evidence)

        # F03 已经结算的用户事件先成为共同历史，再进入慢 responder。这里提交的
        # 只有输入、根状态和时间计划；召回“实际交付”及 NPC output receipt 仍必须
        # 等后面的正式提交点，不能把未发送草稿伪装成已经发生的 NPC 行为。
        input_measurement_committed = False
        input_salience = None
        input_salience_parts = None
        input_clock_plan = None
        # staged 流会在随后 recall/prompt 阶段继续追加。F03 先提交后，必须记住
        # 已经落盘的前缀长度；不能在主回复定稿时把同一条输入 receipt 再 append 一次。
        committed_event_count = 0
        committed_cold_count = 0
        committed_boundary_count = 0
        input_embedding = None
        try:
            # F03 提交的用户事实需要留下给**未来** local RAG 的向量索引；它不是
            # 本轮 recall、根状态或任何开口条件。此前早提交分支硬写 ``emb=None``，
            # 使无 provider 的本地历史根本无法被后续问句检索。
            input_embedding = self.embed_fn(text) if self.embed_fn is not None else None
            salience_params = dict(SALIENCE)
            tuned = load_tuned_weights(self.profile)
            for key, value in (tuned or {}).items():
                if key in salience_params:
                    salience_params[key] = float(value)
            input_salience, input_salience_parts = compute_salience_parts(
                self.state.get("prev_vars"), state["vars"], pain=pain,
                tier=tier_hint, text=text, params=salience_params)
        except Exception:
            # 只有纯计算失败才允许退回旧的同步提交路径；一旦发生落盘副作用，
            # 绝不能吞异常后再执行一次，造成用户输入/记忆/事实的双重提交。
            input_measurement_committed = False
        else:
            # F03 不是展示回调里的瞬态 bool：先把已采用的根增量和状态锚写成独立
            # receipt，随后再写用户消息。这样 responder 草稿被 Enter 取消后，
            # 仍可证明“哪条输入已经作用于哪份状态”。
            staged_events.append(input_measurement_receipt(
                turn_id=turn_id, ts=now, delta_x=deltas,
                measurement=signals.measurement_receipt,
                source_contributions=signals.source_contributions,
                state_anchor_ms=state.get("last_advance_ms"),
                params_sha=self.params_sha,
                expression_anchor=current_event_expression_anchor,
                time_expression_anchor=current_time_expression_anchor,
                delivery_reference=delivery_reference,
                participant_source_kind=participant_source_kind,
                preference_evidence=preference_evidence,
                response_policy=_received_event.response_policy))
            for record in staged_events:
                self.store.append_event(record)
            for record in staged_cold:
                self.store.append_cold(record)
            for record in staged_boundaries:
                self.store.append_boundary(record)
            self._commit_npc_actions(pending_actions)
            self._commit_pending_extraction(pending_slot, turn_id, now, state=state)
            if profile_dirty:
                self.store.save_profile(profile)
                self.profile = profile
            self._remember(now, "user", text,
                           source=("peer" if _response_mode == "peer" else None), S=input_salience,
                           tokens=report["ranked"]["all"], emb=input_embedding,
                           delay_ms=delay_ms, blocks=[], s_parts=input_salience_parts,
                           reward=R, turn_id=turn_id,
                           conversation_provenance=conversation_provenance,
                           participant_source_kind=participant_source_kind,
                           preference_evidence=preference_evidence,
                           **self._event_state_imprint_kwargs(report))
            from ops.recall_geometry import token_direction
            input_drift = {"P": report["P"], "E": report["E"]}
            direction = token_direction(report["field_tokens"])
            if direction is not None:
                input_drift["dir"] = direction
            self.memory.archive_tokens(now, report["field_tokens"], S=input_salience,
                                       drift=input_drift,
                                       source=("peer" if _response_mode == "peer" else "user"),
                                       turn_id=turn_id,
                                       **self._event_state_imprint_kwargs(report))
            if preference_evidence:
                self._last_user_ts = now
                self._last_gap_band = -1
            self.weights_prev = behavior
            self.state = state
            self._sync_pending_extraction_to_state(state)
            self.store.save_state(state)
            self.store.save_snapshot(state, self.turn + 1)
            if p4_recall_commit_due:
                # 不重新查询、不改时间、不重掷随机数：只把已冻结且现在已拥有共同历史
                # 的 recall plan 写进原有 append-only 账本。即使 responder 之后取消，
                # 已提交的输入与未说出口的内部想起都保持为事实。
                commit_turn_recall(memory=self.memory, ledger=self.ledger,
                                   recall=recall, now_ms=now)
                p4_recall_committed = True
            p3_input_receipt = self._publish_p3_authority_receipt(
                p3_before_input, state,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"],
                event_kind=incoming_event_kind, event_id=turn_id, observed_at_ms=now,
                turn_id=turn_id, conversation_provenance=conversation_provenance)
            input_clock_plan = self.wall_clock.replace(
                state, constants, now, cause=incoming_event_kind + "_measurement_committed",
                opening=None, cadence=self._cadence_projection(state),
                authority_receipt=p3_input_receipt)
            input_measurement_committed = True
            committed_event_count = len(staged_events)
            committed_cold_count = len(staged_cold)
            committed_boundary_count = len(staged_boundaries)

        # 这一回调只把**已经结算并已提交**的 F03 事件向量投给展示层。它发生在
        # responder 调用前，因此慢模型期间 Actor 可以基于同一 state 做 wall-clock
        # 观察；它不读草稿，更不能把未完成回复当作消息。
        if callable(_on_prepared):
            try:
                from ops.typing_presentation import (presentation_snapshot,
                                                     typing_presentation_plan)
                _on_prepared({
                    "turn_id": turn_id,
                    # F03 的正式提交时刻；观测层不得用回调抵达时刻代替它，
                    # 否则冻结回放／慢 worker 会把演示曲线起点画到错误墙钟。
                    "ts": int(now),
                    "measurement_committed": input_measurement_committed,
                    "clock_plan": copy.deepcopy(input_clock_plan),
                    "presentation": presentation_snapshot(typing_presentation_plan({
                        "presentation_receipt": {
                            "version": "typing-receipt-v2",
                            "event_delta": dict(deltas or {}),
                        },
                    })),
                })
            except Exception:
                # 展示订阅者不得阻断用户事务或污染既有错误出口。
                pass

        # ⑤ 召回上下文：实际选择在一个只读、可复用的 resolver 边界完成。P4 政策
        # 启用时它会在纯预结算后提前执行，以便最终 F08 一次性写入记忆影响；未启用
        # 时仍在既有 F03 提交后执行，保持原运行时顺序。
        if not p4_enabled:
            recall = self._resolve_turn_recall_for_event(
                text=text, now_ms=now, report=report, query_tokens=recall_query_tokens,
                turn_id=turn_id,
                stage_event=stage_event, stage_boundary=stage_boundary)
        r_evidence = recall.evidence
        rmode = recall.mode
        recall_rec = recall.memory_record
        hint = recall.memory_hint
        emb = recall.embedding
        semantic_hit = recall.semantic_hit
        ledger_rec = recall.ledger_record
        ledger_hint = recall.ledger_hint
        episode_text = recall.episode
        selected_recall_refs = [recall_receipt_reference(name, record, now_ms=now)
                                for name, record in recall.selected_records]
        # W_recall 只读取同一份已经完成的选择 receipt；它不重排候选，也不参与
        # 本轮选择。提前编译使 F10 可以记录这次真正交给 writer 的影子映射。
        recall_plan = compile_recall_projection(
            baseline=self.prior or {k: 0.2 for k in PROTOTYPES}, dynamic=behavior,
            selection=recall_selection_summary(recall))
        if math_trace is not None:
            math_trace.record(
                phase="memory_resolved", formula_id="F10",
                input_summary={"query_token_count": len(recall_query_tokens)},
                output_summary={
                    "selected_count": len(recall.selected_records),
                    "selection_receipt_count": len(selected_recall_refs),
                    "writer_delivery": "not_evaluated_at_F10",
                    "semantic_hit": bool(semantic_hit),
                    "ledger_hit": ledger_rec is not None,
                    "recall_projection": self._projection_trace_summary(recall_plan),
                }, decision_reason=("memory_selected" if selected_recall_refs
                                    else "memory_not_selected"),
                active_or_shadow=("shadow" if self.recall_strategy == "unified_suppressed"
                                  else "active"))

        # ⑥ 回复生成（同步，1 次 LLM）：上下文装配收敛在 turn_prompt；
        #    Runner 只执行生成并保留卡包标签用于随后落盘归因。
        # 期②-1 浓度卡：情感浓度 → 当轮语境材料（低浓度不产）。场 Top-3 只供
        # 浓度读数；表达投递订阅同轮的全量有序资源账本，二者不得再互相截流。
        conc_tok = emit_concentration_token(report["ranked"]["all"])
        intuition_tokens = list(report["ranked"]["all"])
        if conc_tok is not None:
            intuition_tokens.append(conc_tok)
        attention_ranked = dict(report["resource_ranked"])
        attention_ranked["all"] = list(attention_ranked.get("all", ()))
        # 旧 six-axis phase/pain 与旧 time bridge 仍可在符号/检索账本存在，但本轮
        # writer 不再直接消费它们。直接回复改由同一 C_effective + 当前 X 编译的
        # 三驱动力底色卡表达，避免旧 C_base/prev_vars 词元与新卡并列污染表现层。
        attention_sources = []
        for raw_source in (report.get("resource_token_sources") or ()):
            copied_source = copy.deepcopy(raw_source)
            source_info = copied_source.get("source") if isinstance(copied_source, dict) else None
            if (isinstance(source_info, dict)
                    and source_info.get("lane") in {"npc_variable_state", "npc_time_change"}):
                source_info["expression_eligible"] = False
                source_info["expression_retired_reason"] = "root_expression_vector_v2"
            attention_sources.append(copied_source)
        prior_output_anchor = self._latest_committed_expression_anchor(now_ms=now)
        prior_memory_anchor = self._latest_committed_memory_expression_anchor(
            now_ms=now,
            after_output_ms=(prior_output_anchor or {}).get("sent_ms"),
            before_event_ms=now)
        root_expression = compile_expression_tokens(
            current_constants=time_binding["effective_constants"],
            current_constants_version=time_binding["effective_constants_version"],
            current_variables=state.get("vars") or {}, now_ms=now,
            # 直接回复以当前用户输入为因果上下文；不把上一条 NPC 输出的时间差分
            # 混成“正在答复用户”的材料。当前回合刚 selected 的 P4 batch 也仍在
            # stage 内；只有此前已经提交、且未被 NPC 输出吸收的记忆影响可入词元。
            output_anchor=None, memory_anchor=prior_memory_anchor,
            # 这不是暂存的 draft：F03 外部输入与其 Delta_X_event 已在 responder
            # 前成为共同历史。只读卡可以参与这条因果回复；NPC 自己的输出变化则
            # 必须等 F16 之后才有资格出现在下一轮。
            event_anchor=current_event_expression_anchor,
            time_anchor=current_time_expression_anchor,
            source="direct_reply")
        attention_ranked["all"].extend(root_expression.get("cards") or ())
        for root_card in root_expression.get("cards") or ():
            expression_source = root_card.get("expression_source")
            lane = ("npc_event_state"
                    if expression_source == "committed_event_transition"
                    else "npc_time_state"
                    if expression_source == "committed_time_transition"
                    else "npc_memory_state"
                    if expression_source == "committed_memory_transition"
                    else "npc_drive_state")
            attention_sources.append({
                "source": {"lane": lane, "owner": "npc",
                           "expression_eligible": True},
            })
        if conc_tok is not None:
            attention_ranked["all"] = list(attention_ranked.get("all", ())) + [conc_tok]
            attention_sources.append({
                "candidate_index": None,
                "source": {"lane": "user_emotional_context", "owner": "user",
                           "expression_eligible": True},
            })
        if self.expression_attention:
            # E3：writer 只能消费同一 action 的正式 descriptor plan。这里不重读
            # 根量、不按 F07 rank 重新选、不添加 fit/bridge/feedback 作为隐形权重。
            emission_plan = compile_expression_emission_plan(
                root_expression.get("candidates") or (),
                committed_remainders=state.get("expression_emission_remainders"),
            )
            attention_plan = compile_expression_delivery(
                cards=root_expression.get("cards") or (), emission_plan=emission_plan)
            writer_bridge_tokens = ()
            writer_intuition_tokens = ()
        else:
            expression_vectors = report.get("expression_vectors") or {}
            fit_expression = compile_fit_expression_projection(
                npc_drive=expression_vectors.get("D_NPC"),
                user_baseline=expression_vectors.get("E_user"),
                user_dynamic=expression_vectors.get("D_user"))
            attention_plan = compile_expression_attention(
                ranked=attention_ranked, field_pressure=report["P"],
                bridge_tokens=report["bridge_tokens"],
                feedback_context=expression_feedback_context(feedback_record),
                source_receipt=attention_sources, fit_projection=fit_expression)
            writer_bridge_tokens = report["bridge_tokens"]
            writer_intuition_tokens = intuition_tokens
        # W_form 是表达词元的影子编译：当前默认不向 writer 注入任何形式指令，
        # 但 receipt 已能回放本轮的连续方向与其可能的语言接口。
        form_plan = compile_form_projection(
            baseline=self.prior or {k: 0.2 for k in PROTOTYPES}, dynamic=behavior,
            constants=constants, variables=state.get("vars") or {},
            event_delta=report.get("delta") or report.get("event_delta"))
        prompt = assemble_compat_received_prompt(
            text=text, features=features, tier_hint=tier_hint, ammo_hits=ammo_hits,
            persona_blocks=self.persona_blocks, turn=self.turn,
            field_pressure=report["P"], bridge_tokens=writer_bridge_tokens,
            intuition_tokens=writer_intuition_tokens,
            attraction=state["vars"].get("V_attr", 0.5), recall=recall,
            ledger=self.ledger, store=self.store,
            now_ms=now, gap_hours=gap_h, constants=constants, rng=self.rng,
            # 正式 writer 不持有 raw message sequence；历史只经 actual F10 delivery。
            dialogue_tail="", budget_chars=self.prompt_budget_chars,
            semantic_tokens=list(projection.tokens) if projection else None,
            reference_frame=(projection.reference_frame if projection else None),
            conversation_frame=conversation_frame,
            feedback_record=feedback_record,
            expression_attention_plan=attention_plan,
            form_projection=form_plan,
            recall_projection=recall_plan,
            responder_core_prompt=self.responder_core_prompt,
            response_pack_enabled=self.response_pack_enabled,
            canonical_memory_sequence=True,
            current_input_event=current_input_event,
            output_identity=writer_identity)
        # F10 的 selected receipt 不等于 writer delivery。F16/action 只能记录在
        # prompt budget 与 reference gate 后真正保留的 exact event cards；被压制、
        # 无正文或被预算裁掉的 selected recall 留在 selection/assembly receipt，
        # 不得伪装成已影响本条输出。
        recall_refs = [recall_expression_reference(event)
                       for event in prompt.recall_expression_events]
        resp_ids = prompt.response_block_ids
        persona_ids = prompt.persona_block_ids
        lmode = prompt.length_mode

        def commit_input_without_npc_output(reason):
            """提交已经发生的用户事件，但不让未发送 NPC 草稿污染历史。

            这条窄路径只用于 responder 返回前被新 Enter 抢占的旧回合。用户的
            文本、F03 根状态与墙钟锚已经在现实中发生，不能随着旧草稿一起撤销；
            recall 的“已交付”账本和任何 NPC output receipt 则仍不得提交。
            """
            salience_params = dict(SALIENCE)
            tuned = load_tuned_weights(self.profile)
            if tuned:
                for key, value in tuned.items():
                    if key in salience_params:
                        salience_params[key] = float(value)
            S, s_parts = compute_salience_parts(
                self.state.get("prev_vars"), state["vars"], pain=pain,
                tier=tier_hint, text=text, params=salience_params)
            for record in staged_events:
                self.store.append_event(record)
            for record in staged_cold:
                self.store.append_cold(record)
            for record in staged_boundaries:
                self.store.append_boundary(record)
            self._commit_npc_actions(pending_actions)
            self._commit_pending_extraction(pending_slot, turn_id, now, state=state)
            if profile_dirty:
                self.store.save_profile(profile)
                self.profile = profile
            self._remember(now, "user", text,
                           source=("peer" if _response_mode == "peer" else None), S=S,
                           tokens=report["ranked"]["all"], emb=emb,
                           delay_ms=delay_ms,
                           blocks=resp_ids + ["persona:" + p for p in persona_ids],
                           s_parts=s_parts, reward=R, turn_id=turn_id,
                           conversation_provenance=conversation_provenance,
                           participant_source_kind=participant_source_kind,
                           preference_evidence=preference_evidence,
                           **self._event_state_imprint_kwargs(report))
            from ops.recall_geometry import token_direction
            archived_drift = {"P": report["P"], "E": report["E"]}
            field_dir = token_direction(report["field_tokens"])
            if field_dir is not None:
                archived_drift["dir"] = field_dir
            self.memory.archive_tokens(now, report["field_tokens"], S=S,
                                       drift=archived_drift,
                                       source=("peer" if _response_mode == "peer" else "user"),
                                       turn_id=turn_id,
                                       **self._event_state_imprint_kwargs(report))
            self.last_ranked = report["ranked"]["all"]
            self.last_expression_attention = attention_plan
            self.last_recall_expression_events = prompt.recall_expression_events
            if preference_evidence:
                self._last_user_ts = now
                self._last_gap_band = -1
            self.weights_prev = behavior
            self.turn += 1
            self.state = state
            self._sync_pending_extraction_to_state(state)
            self.store.save_state(state)
            self.store.save_snapshot(state, self.turn + 1)
            if delay_ms is not None and preference_evidence:
                self.baseline = update_baseline(self.baseline, delay_ms, length=len(text))
                self.user_card["baseline"] = dict(self.baseline)
                from interaction_extensions.preference_calibration.cx_six_seven.user_card import (
                    save as _save_card,
                )
                _save_card(self.store, self.user_card)
            clock_plan = self.wall_clock.replace(
                state, constants, now,
                cause=incoming_event_kind + "_committed_no_npc_output",
                opening=None, cadence=self._cadence_projection(state))
            self.store.append_event({
                "schema_version": 1, "kind": "npc_draft_cancelled_input_committed",
                "turn_id": turn_id, "ts": now, "detail": {"reason": reason},
            })
            if math_trace is not None:
                math_trace.record(
                    phase="schedule_evaluated", formula_id="F15",
                    input_summary=self._f15_trace_input_summary(
                        state, time_binding["effective_constants"], now,
                        effective_constants_version=time_binding["effective_constants_version"]),
                    output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                    "plan_version": int(clock_plan.get("version", 0)),
                                    "reason_count": len(clock_plan.get("reasons") or ())},
                    decision_reason="user_input_committed_npc_draft_cancelled")
                self._commit_math_trace(math_trace)
            return S, clock_plan

        # R4 的唯一暂停点：至此 F03 已成为共同历史，prompt/recall 均已冻结在
        # 当前 generator frame；尚未调用 responder，也未写 NPC output。Actor 后续
        # 可把这个仅进程内 ticket 交给可取消 draft worker，而新 Enter 只需废弃
        # 该 ticket，绝不能回滚 yield 前已经发生的用户事实。
        yield {
            "turn_id": turn_id,
            "measurement_committed": bool(input_measurement_committed),
            "clock_plan": copy.deepcopy(input_clock_plan),
            "prompt_ready": True,
        }

        reply, reply_facts, reply_cons, reply_deltas, reply_evidence, recall_used, recall_evidence, response_protocol = self._generate_reply(
            prompt.system, prompt.user, DEFAULTS["responder_temperature"],
            recall_event_ids=prompt.recall_event_ids, on_draft=_on_draft,
            should_continue=_should_continue)
        # 第二个暂停点位于网络草稿和任何 output 副作用之间。Actor 的 draft
        # worker 只能把 generator 推到这里；只有 Actor 串行循环恢复它，才会进入
        # 提交闸、F16、pending/release、F06 和下一版墙钟计划。
        yield {
            "turn_id": turn_id,
            "draft_ready": True,
            "measurement_committed": bool(input_measurement_committed),
        }
        # 主回复的网络调用不能总能物理中止；它的结果也绝不能因此获得提交资格。
        # Actor 在新的 Enter 到达时撤销本轮 gate。这里位于任何 message/state/output
        # receipt 提交之前，保证迟到的生成结果不能变成共同历史。
        # Actor 与新 Enter 在这里争夺唯一线性化点。``_begin_commit`` 成功后，
        # 本轮可作为先发生的共同历史提交；新输入若先到则它返回 False，旧稿只留
        # abort 审计。兼容直调/旧 Actor 时保持原 gate 行为。
        may_commit = (bool(_begin_commit()) if callable(_begin_commit) else True)
        if (not may_commit
                or (callable(_should_continue) and not _should_continue())):
            if input_measurement_committed:
                self.turn += 1
                self.store.append_event({
                    "schema_version": 1, "kind": "npc_draft_cancelled_input_committed",
                    "turn_id": turn_id, "ts": now,
                    "detail": {"reason": "user_submit_preempt"},
                })
                S, clock_plan = input_salience, input_clock_plan
            else:
                S, clock_plan = commit_input_without_npc_output("user_submit_preempt")
            return {"aborted": True, "input_committed": True, "turn_id": turn_id,
                    "S": S, "clock_plan": clock_plan,
                    "reason": "user_submit_preempt"}
        # 提交闸通过后，才把本轮输入侧事实变成可见的本地档案。顺序保持
        # turn_opened → input_received → 语义/召回/反馈，便于回放而不泄露取消稿。
        # F03 前缀已在 responder 前提交；这里只写 recall/prompt 阶段新追加的
        # 观察行。未走早提交降级路径时起点仍为 0，保持原同步语义。
        event_start = committed_event_count if input_measurement_committed else 0
        cold_start = committed_cold_count if input_measurement_committed else 0
        boundary_start = committed_boundary_count if input_measurement_committed else 0
        for record in staged_events[event_start:]:
            self.store.append_event(record)
        for record in staged_cold[cold_start:]:
            self.store.append_cold(record)
        for record in staged_boundaries[boundary_start:]:
            self.store.append_boundary(record)
        if not input_measurement_committed:
            self._commit_npc_actions(pending_actions)
            self._commit_pending_extraction(pending_slot, turn_id, now, state=state)
        # 召回在 prompt 组装时已确定；这里仅提交同一份计划，绝不再次检索或抽样。
        # 因而真正进入回复的记忆与冷却/频率账本的来源一一对应。
        if not p4_recall_committed:
            commit_turn_recall(memory=self.memory, ledger=self.ledger,
                               recall=recall, now_ms=now)
        if profile_dirty and not input_measurement_committed:
            self.store.save_profile(profile)
            self.profile = profile
        # 用户线观察（T7-3）同样只在提交成功时更新，取消稿不能把沉默带清零。
        self._last_user_ts = now
        self._last_gap_band = -1
        # 冻结/离线回放会显式给出完整逻辑时间，因而 ``now_ms`` 仍可作为 t_ready。
        # 桌面 Actor 虽也必须传入接收时刻来保持同一运行时钟，却会额外提供一个
        # 仅表示宿主“定稿完成”的只读时钟。若仍把 t_ready 固定在 t_received，慢
        # responder 的挂起回复就可能在 UI 释放前被在线 tick 越过，随后无法倒插。
        # 该回调只记录时间事实，不参与 C/X、门限、词元或任何调度判定。
        if callable(_ready_clock):
            try:
                reply_ready_ms = max(now, int(_ready_clock()))
            except Exception:
                reply_ready_ms = now
        else:
            reply_ready_ms = (now if now_ms is not None else int(_time.time() * 1000))
        # 完整 prompt 只写入本地 append-only trace；run manifest、盲评包均不读取它。
        requested_temperature = DEFAULTS["responder_temperature"]
        override_temperature = getattr(self.responder, "temperature_override", None)
        effective_temperature = (requested_temperature if override_temperature is None
                                 else override_temperature)
        self.store.append_prompt_trace({
            "schema_version": 1, "kind": "responder", "turn_id": turn_id,
            "ts": now, "system": prompt.system, "user": prompt.user,
            "system_sha": sha256_text(prompt.system), "user_sha": sha256_text(prompt.user),
            "persona_block_ids": list(persona_ids),
            "response_block_ids": list(resp_ids),
            "recall_event_ids": list(prompt.recall_event_ids),
            "excluded_ledger_ts": list(prompt.excluded_ledger_ts),
            "history_sources": list(prompt.history_sources),
            "history_exclusions": list(prompt.history_exclusions),
            "expression_feedback": (dict(prompt.expression_feedback)
                                    if prompt.expression_feedback is not None else None),
            "assembly_receipt": dict(prompt.assembly_receipt or {}),
            "requested_temperature": requested_temperature,
            "effective_temperature": effective_temperature,
            "model": str(getattr(self.responder, "model", "unknown"))[:160],
            "length_mode": lmode,
        })
        recall_expression = validate_recall_expression(
            reply, recall_used, recall_evidence, prompt.recall_event_ids)
        recall_expression["response_protocol"] = response_protocol
        from ops.recall_expression_observation import observe_recall_expression
        recall_expression["local_observation"] = observe_recall_expression(
            prompt.recall_expression_events, reply)
        self.store.append_event({
            "schema_version": 1, "kind": "recall_expression_declared", "turn_id": turn_id,
            "ts": now, "detail": recall_expression,
        })
        self.last_ranked = report["ranked"]["all"]
        self.last_expression_attention = attention_plan
        self.last_recall_expression_events = prompt.recall_expression_events

        # ⑥.4 输出侧检查卡（T1）：observe 默认——只 log 触发，零行为漂移
        #      （repair/inject 关，承 MANE G1 纪律：先实测触发率再谈注入）
        from ops.constraints.bus import run_checks
        from ops.constraints.log import log_row
        _check_ctx = {
            "reply": reply,
            "tail": [m.get("text", "")
                     for m in self.store.read_messages()[-8:]] + [text],
            "persona": self.persona_blocks,
            "ledger": self.ledger.top_recent(now=now),
            "turn": self.turn,
        }
        _check_res = run_checks(_check_ctx)
        for _r in _check_res["results"]:
            if _r["pass"]:
                continue                      # v1 只记 fail 行（触发台账）
            log_row(self.store, {
                "ts": now, "turn": self.turn, "kind": "check",
                "card_id": _r["card_id"], "event": "fail",
                "verdict": _r["verdict"], "action": _r["action"],
                "text_before_sha": None, "text_after_sha": None,
                "golden_key": None})
        _check_violations = [_r["card_id"] for _r in _check_res["violations"]]
        manual_responder_receipt = response_protocol.get("test_manual_responder")

        # ⑥.5 延迟回复（reply_delay=True）：先挂起、到点才落盘——时间戳 = 真实发送时刻；
        #      新消息在发送前到达 → 作废并记入打断台账（反向侧写近况：被晾时急不急）
        delivery_plan = plan_reply_delivery(
            delayed=delayed_response, constants=constants, variables=state["vars"],
            event_delta=deltas, baseline=self.baseline, rng=self.rng,
            received_ms=now, ready_ms=reply_ready_ms)
        delay_sec = delivery_plan["delay_sec"]
        delay_receipt = delivery_plan["delay_receipt"]
        send_at = delivery_plan["send_at"]
        reply_timeline = delivery_plan["timeline"]
        superseded = False
        released = released_before_input
        if delayed_response:
            prev = state.get("pending_reply")
            if prev:
                if now >= prev["send_at"]:
                    released = prev["reply"]
                    prev_record = self._persist_npc_output(
                        state=state, ts=prev["send_at"], text=prev["reply"],
                        source="reply", S=0.0, tokens=[],
                        turn_id=prev.get("turn_id"),
                        recall_mode=prev.get("recall_mode"),
                        recall_refs=prev.get("recall_refs"),
                        calibration_assembly_receipt=prev.get("calibration_assembly_receipt"),
                        received_ms=prev.get("request_ts", prev["send_at"]),
                        ready_ms=prev.get("ready_ms", prev["send_at"]),
                        cadence_delay_ms=prev.get("cadence_delay_ms", 0),
                        raw_deltas=prev.get("self_state_delta"),
                        evidence=prev.get("self_state_evidence"),
                        conversation_context=prev.get("conversation_context"),
                        test_manual_responder=prev.get("test_manual_responder"))
                    self._apply_npc_self_expression(
                        state, reply=prev["reply"],
                        raw_deltas=prev.get("self_state_delta"),
                        evidence=prev.get("self_state_evidence"),
                        ts=prev["send_at"], source="reply",
                        action_id=prev_record.get("action_id"),
                        turn_id=prev.get("turn_id"),
                        output_commit=prev_record.get("_output_commit_payload"),
                        output_evidence=prev_record.get("_output_commit_evidence"),
                        output_memory_recall=prev_record.get("_output_memory_recall"))
                    # 释放的回复同样等待用户回应 → 心冷计数（A3 修复：此前漏计）
                    self._bump_unanswered(state, 1, prev["send_at"], "inc_release")
                    self._ledger_from_reply(
                        prev["reply"], now, prev.get("facts"),
                        prev.get("contradictions"), S=prev.get("S", 0.0),
                        tokens=prev.get("tokens"), topic=prev.get("topic"),
                        event_id=prev_record.get("event_id"))
                else:
                    superseded = True
                    self.store.append_interrupt({"ts": now,
                                                 "dropped_send_at": prev["send_at"]})
            state["pending_reply"] = {"reply": reply, "send_at": send_at,
                                      "request_ts": now,
                                      "ready_ms": reply_ready_ms,
                                      "cadence_delay_ms": delay_sec * 1000,
                                      "facts": reply_facts,
                                      "contradictions": reply_cons,
                                      "self_state_delta": reply_deltas,
                                      "self_state_evidence": reply_evidence,
                                      "turn_id": turn_id,
                                      "recall_mode": rmode,
                                      "recall_refs": recall_refs,
                                      "conversation_context": output_conversation_context,
                                      "test_manual_responder": manual_responder_receipt,
                                      "calibration_assembly_receipt": dict(prompt.assembly_receipt or {})}

        # ⑦ 记账（异步内容；桩测试中同步执行以便检查）——含外环观察样本（S 分量 + R）；
        # R 是响应强度而非奖励，只有后续独立反馈/盲评可让它参与参数建议。
        salience_params = dict(SALIENCE)
        tuned = load_tuned_weights(self.profile)
        if tuned:
            for k, v in tuned.items():
                if k in salience_params:
                    salience_params[k] = float(v)
        if input_measurement_committed:
            S, s_parts = input_salience, input_salience_parts
        else:
            S, s_parts = compute_salience_parts(self.state.get("prev_vars"), state["vars"],
                                                pain=pain, tier=tier_hint, text=text,
                                                params=salience_params)
        # 延迟挂起补盖章：这轮生成的回复在挂起前 S 未知，现在补上（台账触发用它）
        if state.get("pending_reply") and state["pending_reply"].get("request_ts") == now:
            state["pending_reply"]["S"] = S
            state["pending_reply"]["tokens"] = report["field_tokens"]
            state["pending_reply"]["topic"] = text
        # M1 向量化：分量 + 反应词元族 + 标量投影（投影 = 旧 compute_reward 逐位一致）
        # 延迟回复尚未成为消息，不能提前排追发；但本轮用户的 R 卡观测必须随
        # 草稿进入后续的正式 release，避免即时/延迟两条产品路径不等价。
        if state.get("pending_reply") and state["pending_reply"].get("request_ts") == now:
            state["pending_reply"]["reaction_parts"] = R_parts
            state["pending_reply"]["event_energy"] = sum(
                abs(float(value or 0.0)) for value in deltas.values())
            state["pending_reply"]["variable_deltas"] = dict(deltas)
        # M4 否认 R 方向裁决：用本轮反应词元族对未决挂账改口（confirmed/falsified/stale），
        # 裁决行只进边界流——零选择影响，有效否认的几何压制是 M5 的事。
        adjudicate_denials(store=self.store, parts=R_parts["components"],
                           now_ms=now, turn=self._turn_sequence, turn_id=turn_id,
                           history=r_parts_history(self.store))
        emb2 = emb
        if not input_measurement_committed:
            self._remember(now, "user", text,
                           source=("peer" if _response_mode == "peer" else None), S=S,
                           tokens=report["ranked"]["all"], emb=emb2,
                           delay_ms=delay_ms,
                           blocks=resp_ids + ["persona:" + p for p in persona_ids],
                           s_parts=s_parts, reward=R, turn_id=turn_id,
                           conversation_provenance=conversation_provenance,
                           participant_source_kind=participant_source_kind,
                           preference_evidence=preference_evidence,
                           **self._event_state_imprint_kwargs(report))
        reply_record = None
        if not delayed_response:
            reply_record = self._persist_npc_output(
                state=state, ts=send_at, text=reply, source="reply", S=0.0, tokens=[],
                turn_id=turn_id, recall_mode=rmode, recall_refs=recall_refs,
                received_ms=now, ready_ms=reply_ready_ms, cadence_delay_ms=0,
                raw_deltas=reply_deltas, evidence=reply_evidence,
                calibration_assembly_receipt=prompt.assembly_receipt,
                conversation_context=output_conversation_context,
                test_manual_responder=manual_responder_receipt)
            self._bump_unanswered(state, 1, send_at, "inc")
            self._ledger_from_reply(reply, send_at, reply_facts, reply_cons,
                                    S=S, tokens=report["field_tokens"], topic=text,
                                    event_id=reply_record.get("event_id"))
        # M2 死向量读回：归档 drift 从此附带本事件场方向（四轴家族 δ 加权均值，
        # 与冻结区 lexicon 同源）——检索层观察卡消费它；旧 {"P","E"} 行保持不变，
        # 观察卡对无四轴数据的旧行如实标 None。
        from ops.recall_geometry import token_direction
        _field_dir = token_direction(report["field_tokens"])
        _archived_drift = {"P": report["P"], "E": report["E"]}
        if _field_dir is not None:
            _archived_drift["dir"] = _field_dir
        if not input_measurement_committed:
            self.memory.archive_tokens(now, report["field_tokens"], S=S,
                                       drift=_archived_drift,
                                       source=("peer" if _response_mode == "peer" else "user"),
                                       turn_id=turn_id,
                                       **self._event_state_imprint_kwargs(report))
        # 高响应签名观察台账（被动，零刺激：行为签名 × R × 好感度）。只有明示为
        # human preference evidence 的 received event 才能进入 B₀/拟合外环；peer 仍
        # 已完整走过 F03/P4/P3/F16，不能因而被当成真人偏好样本。
        compat = None
        compat_token = False
        if preference_evidence:
            from core.tokens.behavior import analyze_behavior_space
            compat_analysis = analyze_behavior_space(constants, state["vars"], behavior,
                                                     e_weights=b0)
            compat = round(float(compat_analysis["analysis"]
                                 ["compatibility_npc_to_user"]["satisfy"]), 4)
            compat_token = any("[用户心向" in str(t.get("text", ""))
                               for t in report["ranked"]["all"])
        # B2 召回归因：这轮 R 是否发生在唤起之后、哪种模式、证据多强
        recall_sources = recall.sources
        # 期① 偏好拟合总线：双寄存器 + 差距/浓度读数——只算只落盘，
        # 消费端接线不变（零行为变化，承 docs/基准锚定与调动监测.md §八）；
        # 期②-1 浓度卡读数（conc_token）同行走落盘
        if preference_evidence:
            fit = fit_row(self.prior or {k: 0.2 for k in PROTOTYPES}, behavior,
                          report["P"], self.state.get("P", 0.0),
                          state["vars"].get("P_need", 0.0),
                          ranked_tokens=report["ranked"]["all"])
            # F12 只产生“可能需要新 B₀ 版本”的候选。已绑定的 prior 是只读先验：
            # 它不能被用户证据、D(t)、LLM 或本轮 Runner 直接覆盖。
            from interaction_extensions.preference_calibration.cx_six_seven.drift_gate import (
                drift_update,
            )
            self._drift_state, drift_ev = drift_update(
                getattr(self, "_drift_state", None),
                {"user_evidence_conc": fit["user_evidence_conc"], "d_sim": fit["d_sim"],
                 "D": fit["D"], "evidence_ts": now},
                self.prior or {k: 0.2 for k in PROTOTYPES})
            fit["drift"] = False
            fit["drift_proposed"] = False
            if drift_ev is not None:
                self.store.append_event({"ts": now, "kind": "fit_drift_proposal",
                                         "detail": {"l1": drift_ev["l1"],
                                                    "streak": drift_ev["streak"],
                                                    "candidate_b0": dict(drift_ev["b0_new"]),
                                                    "writes_b0": False}})
                fit["drift_proposed"] = True
        else:
            fit = {"evidence_policy": "excluded_non_human_participant",
                   "drift": False, "drift_proposed": False}
        extra_bp = {"V_attr": state["vars"].get("V_attr", 0.5),
                    "compat": compat,
                    "compat_token": compat_token,
                    "params_sha": self.params_sha,
                    "recall_mode": rmode,
                    "recall_used": bool(recall_sources),
                    "recall_sources": recall_sources,
                    "recall_evidence": round(float(r_evidence), 4),
                    "R_parts": R_parts["components"],
                    "reaction_tokens": R_tokens,
                    "fit": fit,
                    "participant_source_kind": participant_source_kind,
                    "preference_evidence": preference_evidence}
        if attribution is not None:
            extra_bp["last_action"] = attribution
        record_breakpoint(self.store, now,
                          behavior_signature(resp_ids, persona_ids, report["P"]), R,
                          extra=extra_bp)
        reply_self_deltas = {}
        p3_output_receipt = None
        if not delayed_response and reply_record is not None:
            state_before_output = copy.deepcopy(state)
            reply_self_deltas = self._apply_npc_self_expression(
                state, reply=reply, raw_deltas=reply_deltas,
                evidence=reply_evidence, ts=send_at, source="reply",
                action_id=reply_record.get("action_id"), turn_id=turn_id,
                output_commit=reply_record.get("_output_commit_payload"),
                output_evidence=reply_record.get("_output_commit_evidence"),
                output_memory_recall=reply_record.get("_output_memory_recall"))
            reply_committed_deltas = self._committed_output_deltas(
                reply_record, reply_self_deltas)
            self._record_output_f06(
                math_trace, state_before=state_before_output, state_after=state,
                constants=time_binding["effective_constants"],
                applied_deltas=reply_committed_deltas, source="reply",
                parent_ref=str(reply_record.get("action_id") or "action_unknown"),
                effective_constants_version=time_binding["effective_constants_version"])
            output_binding = self._effective_time_binding()
            p3_output_receipt = self._publish_p3_authority_receipt(
                state_before_output, state,
                effective_constants=output_binding["effective_constants"],
                effective_constants_version=output_binding["effective_constants_version"],
                event_kind="npc_output", event_id=str(reply_record.get("action_id") or ""),
                observed_at_ms=int(send_at), turn_id=turn_id,
                output_conversation_context=reply_record.get("conversation_context"))
        self._sync_pending_extraction_to_state(state)
        self.store.save_state(state)
        self.store.save_snapshot(state, self.turn + 1)
        if delay_ms is not None and preference_evidence:
            self.baseline = update_baseline(self.baseline, delay_ms, length=len(text))
            # 用户卡（T6）：基线随轮落盘——修复"基线只在内存、重启即丢"的真缺口
            self.user_card["baseline"] = dict(self.baseline)
            from interaction_extensions.preference_calibration.cx_six_seven.user_card import (
                save as _save_card,
            )
            _save_card(self.store, self.user_card)

        # 当前用户输入可关闭此前的行动窗口；本轮新回复仍是 open，不会被误判。
        settled_outcomes = self.observe_action_outcomes(now)

        # 先让后台观察器面对本轮已提交的状态对象；否则 extractor 极快返回时，可能把
        # 待消费记录写进上一轮 state，随后又被这里的状态滚动覆盖。
        self.state = state

        # F16 用户因果回复并不消费 F15。无论即时还是延迟提交，输出后都只把本次
        # P3 output receipt 交给下一次正式 dynamic tick；不得在这里预建追发候选。
        opening = None
        if math_trace is not None:
            math_trace.record(
                phase="schedule_evaluated", formula_id="F14",
                input_summary=self._p3_trace_input_summary(p3_output_receipt),
                output_summary={"candidate_present": False,
                                "deferred_to_dynamic_tick": True},
                formula_params={"version": "opening-authority-receipt-v1",
                                "route": "output_rearm_only"},
                decision_reason="output_does_not_consume_opening")

        # 这条 direct F16 只替换旧计划。F15 越线资格必须留给下一次正式 dynamic
        # tick；主回复事务不得先消费 opening edge。canonical participant ingress
        # 记录真实对端来源，legacy adapter 才保留旧 ``user_message`` 兼容标签；
        # 这只改变 receipt 可读性，不改变 wall-clock/P3 公式或发送资格。
        cadence = self._cadence_projection(state)
        clock_cause = (incoming_event_kind + "_reply_committed"
                       if delivery_reference is not None else "user_message")
        clock_plan = self.wall_clock.replace(
            state, constants, max(now, int(send_at)), cause=clock_cause,
            opening=None, cadence=cadence, authority_receipt=p3_output_receipt)
        if math_trace is not None:
            math_trace.record(
                phase="schedule_evaluated", formula_id="F15",
                input_summary=self._p3_trace_input_summary(p3_output_receipt),
                output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                "plan_version": int(clock_plan.get("version", 0)),
                                "reason_count": len(clock_plan.get("reasons") or ()),
                                "cadence_projection": self._cadence_trace_summary(cadence)},
                decision_reason="opening_deferred_to_next_tick")

        # ⑧ 异步提取（下轮校正）：async_extractor=True 时后台线程跑，
        #    不再阻塞回复（回复延迟只剩 responder 一次调用）。extractor=None 是
        #    合法的低成本运行形态：词典 / 语义投影仍完整工作，只是没有下轮校正。
        if self.extractor is not None:
            es, eu = build_extractor_messages(text, state["vars"])
            source_sequence = self._turn_sequence

            def _do_extract():
                try:
                    raw_ext = self.extractor.call(
                        es, eu, temperature=DEFAULTS["extractor_temperature"],
                        require_json=True)
                    self._record_extraction(
                        source_turn_id=turn_id, source_ts=now,
                        extraction=parse_extractor(raw_ext),
                        source_sequence=source_sequence)
                except Exception as exc:
                    # 回复和用户原文已是既成事实；精提取失败只降级，不得反向击穿事务。
                    self.store.append_event(extractor_failed(
                        source_turn_id=turn_id, source_ts=now,
                        observed_at_ms=int(_time.time() * 1000),
                        error_type=type(exc).__name__, params_sha=self.params_sha))

            if self.async_extractor:
                threading.Thread(target=_do_extract, daemon=True).start()
            else:
                _do_extract()

        # ⑨ 状态滚动
        self.weights_prev = behavior          # 速度项 = D(t) 历史（期②-4）
        self.turn += 1
        refine_due = (self.auto_preference_refine
                      and self.turn % FITTING["refine_every_n"] == 0
                      and self.preference is not None)
        presentation_receipt = {
            "version": "typing-receipt-v2",
            "event_delta": dict(deltas or {}),
            "f14": dict((opening or {}).get("f14") or {}),
            "f16": dict((delay_receipt or {}).get("readout") or {}),
        }
        result = {"reply": reply, "turn_id": turn_id,
                "action_id": (reply_record or {}).get("action_id"),
                "report": report, "S": S, "R": R, "recall": hint or "",
                "semantic_projection": (projection.as_record() if projection else None),
                "recall_mode": rmode,
                "semantic_hit": semantic_hit or "",
                "ledger_recall": ledger_hint or "", "episode": episode_text,
                "response_blocks": resp_ids,
                "persona_blocks": persona_ids,
                "delay_sec": delay_sec, "send_at": send_at,
                "response_deferred": delayed_response,
                "superseded": superseded, "released": released,
                "opening": opening,
                "cadence_projection": self._cadence_projection(state),
                "length_mode": lmode,
                "compat": compat, "compat_token": compat_token,
                "refine_due": refine_due,
                "conc_token": (conc_tok or {}).get("strength", 0.0),
                "conc_emitted": conc_tok is not None,
                "constraint_violations": _check_violations,
                "R_parts": R_parts["components"],
                "clock_plan": clock_plan,
                "outcomes_settled": len(settled_outcomes),
                "npc_self_deltas": reply_self_deltas,
                "presentation_receipt": presentation_receipt,
                "feedback": feedback_record,
                "ammo_hits": [{"text": e["text"], "tier": e.get("tier", "私密")}
                              for e in ammo_hits]}
        telemetry = build_turn_telemetry(store=self.store, turn_result=result, state=state)
        self.store.append_telemetry(telemetry)
        if math_trace is not None:
            if reply_record is not None:
                math_trace.record(
                    phase="output_committed", formula_id="F16",
                    output_summary={"committed": True,
                                    "delay_ms": int(delay_sec * 1000),
                                    "cadence_delay": delay_receipt,
                                    "form_projection": self._projection_trace_summary(form_plan)},
                    decision_reason="output_receipt_committed",
                    parent_ref=str(reply_record.get("action_id") or "action_unknown"))
            else:
                math_trace.record(
                    phase="schedule_evaluated", formula_id="F16",
                    output_summary={"committed": False,
                                    "reply_deferred": bool(delayed_response),
                                    "delay_ms": int(delay_sec * 1000),
                                    "cadence_delay": delay_receipt,
                                    "form_projection": self._projection_trace_summary(form_plan)},
                    decision_reason="output_deferred")
            self._commit_math_trace(math_trace)
        result["telemetry"] = telemetry
        if _response_mode == "peer":
            result["peer_observed"] = True
            result["reply_planned"] = True
            result["eligibility"] = peer_eligibility
        return result

    def rebuild_wall_clock_plan(self, now_ms=None):
        """供运行时恢复入口调用：只由快照与绝对时间重建计划，零 LLM。"""
        if not self.profile:
            return None
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        return self.wall_clock.rebuild(
            self.state, self.profile.get("constants") or {}, now,
            opening=None, cadence=self._cadence_projection(self.state))

    # ------------------------------------------------------------------
    # 比赛演示时钟（仅显式 demo_scenario；不参与普通会话）
    # ------------------------------------------------------------------
    def runtime_now_ms(self, host_now_ms=None):
        """返回本会话的运行时钟。

        正常档案严格等于宿主墙钟。明确标注的比赛演示会话可保留一个单调的展示偏移，
        使“沉默一段时间后的 catch_up”能够在录屏时可复现地走正式时间总线，而无需
        改动时间方程或等待真实数小时。
        """
        now = int(host_now_ms if host_now_ms is not None else _time.time() * 1000)
        scenario = (self.profile or {}).get("demo_scenario")
        if not isinstance(scenario, dict):
            return now
        try:
            return now + max(0, int(scenario.get("clock_offset_ms", 0)))
        except (TypeError, ValueError):
            return now

    def advance_demo_clock(self, delta_ms, host_now_ms=None):
        """推进演示会话的可见时钟，并经既有 ``catch_up`` 结算。

        该入口拒绝普通会话；它不修改时间/召回参数、不给 responder 额外 prompt，
        也不会把草稿伪装成已发生事实。
        """
        demo_clock = self.stage_demo_clock(delta_ms, host_now_ms=host_now_ms)
        now = demo_clock["now_ms"]
        self.store.append_event({
            "kind": "competition_demo_clock_advanced", "ts": now,
            "detail": {"delta_ms": demo_clock["advanced_by_ms"],
                       "scene_id": demo_clock.get("scene_id")},
        })
        result = self.catch_up(now_ms=now, parallel=True)
        result["demo_clock"] = {"advanced_by_ms": demo_clock["advanced_by_ms"],
                                "now_ms": now}
        return result

    def stage_demo_clock(self, delta_ms, host_now_ms=None):
        """只推进演示档案逻辑时钟，把生成留给下一次正式 ``catch_up``。

        用于“关闭客户端 → 后台准备离线时段 → 重新登录”的录屏：不调用
        responder、不扫描开口点、不写 NPC 文本；普通会话仍被拒绝。
        """
        scenario = (self.profile or {}).get("demo_scenario")
        if not isinstance(scenario, dict):
            raise RuntimeError("只有明确标记的演示会话可以推进展示时间")
        try:
            delta = int(delta_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("演示时间增量必须是整数毫秒") from exc
        if delta <= 0:
            raise ValueError("演示时间只能向前推进")
        profile = self.store.load_profile() or {}
        current = dict(profile.get("demo_scenario") or {})
        if not current:
            raise RuntimeError("演示会话元数据不完整")
        current["clock_offset_ms"] = max(0, int(current.get("clock_offset_ms", 0))) + delta
        profile["demo_scenario"] = current
        self.store.save_profile(profile)
        self.profile = profile
        now = self.runtime_now_ms(host_now_ms)
        self.store.append_event({
            "kind": "competition_demo_clock_staged", "ts": now,
            "detail": {"delta_ms": delta, "scene_id": current.get("id")},
        })
        return {"advanced_by_ms": delta, "now_ms": now,
                "scene_id": current.get("id")}

    # ------------------------------------------------------------------
    # 延迟回复：到点释放（挂起的消息落盘，时间戳 = 原发送时刻）
    # ------------------------------------------------------------------
    def settle_pending_reply_ready(self, observed_ms, *, turn_id=None):
        """把 Actor 实际观察到定稿的时刻写回尚未发送的 F16 计划。

        网络 worker 看见定稿的时间不等于单写者可提交 pending 的时间。若这段
        间隙已被 F08 结算跨过，沿用旧 ``send_at`` 就会向已结算时间线倒插消息。
        此处仅校正这一条未发送输出的时间事实：不读取词元、不重算 F16 根量投影，
        也不改变已得出的延迟秒数。
        """
        pending = self.state.get("pending_reply")
        if not isinstance(pending, dict):
            return None
        if (turn_id is not None and pending.get("turn_id") is not None
                and str(pending.get("turn_id")) != str(turn_id)):
            return None
        try:
            observed = int(observed_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("pending reply observed_ms 必须是整数毫秒") from exc
        anchor = self.state.get("last_advance_ms", self.state.get("last_ts", observed))
        ready = max(observed, int(pending.get("ready_ms", observed)), int(anchor or observed))
        received = int(pending.get("request_ts", ready))
        cadence = int(pending.get("cadence_delay_ms", 0))
        timeline = resolve_output_timeline(
            received_ms=received, ready_ms=ready, cadence_delay_ms=cadence)
        old_ready = int(pending.get("ready_ms", received))
        old_send = int(pending.get("send_at", received))
        pending["ready_ms"] = int(timeline["t_ready"])
        pending["send_at"] = int(timeline["t_sent"])
        if old_ready != pending["ready_ms"] or old_send != pending["send_at"]:
            self.store.save_state(self.state)
            self.store.append_event({
                "schema_version": 1,
                "kind": "pending_reply_ready_observed",
                "ts": int(timeline["t_ready"]),
                "turn_id": pending.get("turn_id"),
                "detail": {
                    "previous_ready_ms": old_ready,
                    "previous_send_at": old_send,
                    "ready_ms": int(timeline["t_ready"]),
                    "send_at": int(timeline["t_sent"]),
                },
            })
        return dict(timeline)

    def release_pending(self, now_ms=None):
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        with self._release_lock:
            prev = self.state.get("pending_reply")
            if not prev or now < prev["send_at"]:
                return None
            # 同一用户回合的 direct F16 可能先于某个已冻结、但尚未由 Actor tick
            # 消费的自主 crossing 提交。后者不是已投递事实；它只能被这条正式直接
            # 回复显式替代，绝不能遗留成“已消费”的 edge。``at_ms`` 不是 timer
            # 发送入口，因此即使逻辑目标已过、但没有 F16 dispatch，仍视为未消费。
            prior_clock_plan = self.store.load_clock_plan() or {}
            supersedes_opening = self.wall_clock.has_unconsumed_opening(prior_clock_plan)
            # 锁已经把两个 release 调用串行化；因此不能为了防双写而在真正的
            # 输出收据前先摘掉 pending。任何时间合同异常都必须保留这条尚未发送
            # 的回复，不能让 UI 永远停在“正在输入”。成功提交后才清牌、记未回应。
            rec = self._persist_npc_output(
                state=self.state, ts=prev["send_at"], text=prev["reply"],
                source="reply", S=0.0, tokens=[],
                                 turn_id=prev.get("turn_id"),
                                 recall_mode=prev.get("recall_mode"),
                                 recall_refs=prev.get("recall_refs"),
                                 calibration_assembly_receipt=prev.get("calibration_assembly_receipt"),
                                 received_ms=prev.get("request_ts", prev["send_at"]),
                                 ready_ms=prev.get("ready_ms", prev["send_at"]),
                                 cadence_delay_ms=prev.get("cadence_delay_ms", 0),
                                 raw_deltas=prev.get("self_state_delta"),
                                 evidence=prev.get("self_state_evidence"),
                                 conversation_context=prev.get("conversation_context"),
                                 test_manual_responder=prev.get("test_manual_responder"))
            self.state["pending_reply"] = None
            self._bump_unanswered(self.state, 1, prev["send_at"], "inc_release")
            state_before_output = copy.deepcopy(self.state)
            applied_deltas = self._apply_npc_self_expression(
                self.state, reply=prev["reply"],
                raw_deltas=prev.get("self_state_delta"),
                evidence=prev.get("self_state_evidence"),
                ts=prev["send_at"], source="reply", action_id=rec.get("action_id"),
                turn_id=prev.get("turn_id"),
                output_commit=rec.get("_output_commit_payload"),
                output_evidence=rec.get("_output_commit_evidence"),
                output_memory_recall=rec.get("_output_memory_recall"))
            committed_deltas = self._committed_output_deltas(rec, applied_deltas)
            self._ledger_from_reply(prev["reply"], now, prev.get("facts"),
                                    prev.get("contradictions"),
                                    S=prev.get("S", 0.0), tokens=prev.get("tokens"),
                                    topic=prev.get("topic"), event_id=rec.get("event_id"))
            self.store.save_state(self.state)
            time_binding = self._effective_time_binding()
            p3_output_receipt = self._publish_p3_authority_receipt(
                state_before_output, self.state,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"],
                event_kind="npc_output", event_id=str(rec.get("action_id") or ""),
                observed_at_ms=int(prev["send_at"]), turn_id=prev.get("turn_id"),
                output_conversation_context=rec.get("conversation_context"))
            # 延迟 F16 正式提交后也只重设 observation。它不能补消费用户输入时被
            # 禁止预消费的 F15 crossing；下一次正式 dynamic tick 才可判断开口。
            clock_plan = self.wall_clock.refresh(
                self.state, (self.profile or {}).get("constants") or {},
                prev["send_at"], cause="reply_committed", opening=None,
                cadence=self._cadence_projection(self.state),
                authority_receipt=p3_output_receipt,
                reset_edge=supersedes_opening)
            if supersedes_opening:
                old_opening = (prior_clock_plan.get("opening")
                               if isinstance(prior_clock_plan.get("opening"), dict) else {})
                old_sources = (old_opening.get("sources")
                               if isinstance(old_opening.get("sources"), dict) else {})
                old_edge = (prior_clock_plan.get("opening_edge")
                            if isinstance(prior_clock_plan.get("opening_edge"), dict) else {})
                self.store.append_event({
                    "schema_version": 1,
                    "kind": "opening_superseded_by_direct_reply",
                    "action_id": rec.get("action_id"),
                    "turn_id": prev.get("turn_id"),
                    "ts": int(prev["send_at"]),
                    "detail": {
                        "rule": "direct_f16_before_opening_dispatch",
                        "prior_clock_version": prior_clock_plan.get("version"),
                        "prior_authority_event_id": old_sources.get("authority_event_id"),
                        "prior_anchor": list(old_edge.get("anchor") or ()),
                    },
                })
            trace = self._new_math_trace(
                root_kind="action", root_id=str(rec.get("action_id") or "action_unknown"),
                now_ms=prev["send_at"],
                root_refs={"action_id": str(rec.get("action_id") or "action_unknown"),
                           "turn_id": str(prev.get("turn_id") or "turn_unknown"),
                           "action_ts": int(prev["send_at"])})
            if trace is not None:
                cadence = self._cadence_projection(self.state)
                assembly = (prev.get("calibration_assembly_receipt")
                            if isinstance(prev.get("calibration_assembly_receipt"), dict) else {})
                trace.record(
                    phase="output_committed", formula_id="F16",
                    output_summary={"committed": True,
                                    "delay_ms": int(prev.get("cadence_delay_ms", 0)),
                                    "form_projection": self._projection_trace_summary(
                                        assembly.get("form_projection"))},
                    decision_reason="deferred_output_released",
                    parent_ref=str(rec.get("action_id") or "action_unknown"))
                self._record_output_f06(
                    trace, state_before=state_before_output, state_after=self.state,
                    constants=time_binding["effective_constants"],
                    applied_deltas=committed_deltas, source="reply",
                    parent_ref=str(rec.get("action_id") or "action_unknown"),
                    effective_constants_version=time_binding["effective_constants_version"])
                trace.record(
                    phase="schedule_evaluated", formula_id="F14",
                    input_summary=self._p3_trace_input_summary(p3_output_receipt),
                    output_summary={"candidate_present": False,
                                    "deferred_to_dynamic_tick": True},
                    formula_params={"version": "opening-authority-receipt-v1",
                                    "route": "output_rearm_only"},
                    decision_reason="output_does_not_consume_opening")
                trace.record(
                    phase="schedule_evaluated", formula_id="F15",
                    input_summary=self._p3_trace_input_summary(p3_output_receipt),
                    output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                    "plan_version": int(clock_plan.get("version", 0)),
                                    "reason_count": len(clock_plan.get("reasons") or ()),
                                    "cadence_projection": self._cadence_trace_summary(cadence)},
                    decision_reason=("clock_scheduled" if clock_plan.get("at_ms") is not None
                                     else "clock_idle"))
                self._commit_math_trace(trace)
        return prev["reply"]

    # ------------------------------------------------------------------
    # 挂机衔接 / 沉默期主动消息（二期）
    # ------------------------------------------------------------------
    def _record_catch_up_evaluation(self, *, now, origin, proactive_plan, result,
                                    audit_context=None):
        """为一次离线/定时追平留下无正文决策收据。

        该收据只解释“是否有候选、为何停止、是否真正提交”，不携带 prompt、回复
        文本、记忆正文或模型原始错误。它不参与任何下一步算法，故补齐审计不会把
        登录变成新的行为条件。
        """
        try:
            decision = dict((proactive_plan or {}).get("decision") or {})
            points = list((proactive_plan or {}).get("points") or ())
            detail = {
                "origin": str((audit_context or {}).get("origin") or "direct"),
                "clock_version": (audit_context or {}).get("clock_version"),
                "from_ms": ((origin or {}).get("last_advance_ms")
                            if isinstance(origin, dict) else None),
                "last_message_ms": ((origin or {}).get("last_ts")
                                    if isinstance(origin, dict) else None),
                "to_ms": int(now),
                "candidate_count": len(points),
                "candidate_ts": [int(point.get("ts")) for point in points
                                 if isinstance(point, dict) and point.get("ts") is not None],
                "decision": decision,
                "outcome": str((result or {}).get("reason", {}).get("rule") or "unknown"),
                "committed_count": len((result or {}).get("messages") or ()),
                "params_sha": self.params_sha,
            }
            self.store.append_event({"schema_version": 1,
                                     "kind": "wall_clock_catchup_evaluated",
                                     "ts": int(now), "detail": detail})
        except Exception:
            # 审计追加不能让真实时间追平本身失败。
            pass

    def settle_wall_clock(self, now_ms=None):
        """结算一个**在线**墙钟边界，但绝不补写离线主动消息。

        ``catch_up`` 的职责是登录/恢复后的历史回放：它会扫描整个缺席区间，并把
        已经错过的开口点按历史时间逐条提交。在线 Actor 的单个数学边界上不能调用
        它，否则一次正常 timer 唤醒会误解释成用户离线数
        小时。本方法只推进根状态并刷新下一份版本化计划；已成立的统一
        ``opening_due`` 边界由 Actor 携带冻结 receipt 交给
        ``dispatch_opening_action``，不能把一次在线 tick 伪装成离线 catch-up。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        if not self.profile or self.state.get("last_ts") is None:
            return {"advanced": False, "sent": False, "messages": [],
                    "reason": {"rule": "no_history"}, "clock_plan": None}

        constants = self.profile.get("constants") or {}
        origin = copy.deepcopy(self.state)
        settled = copy.deepcopy(origin)
        events = []
        effective_time_binding = self._advance_effective_time(settled, now, events)
        effective_constants = effective_time_binding["effective_constants"]
        effective_constants_version = effective_time_binding["effective_constants_version"]
        update_hidden(settled, effective_constants, None, events, now)
        advanced = (settled.get("last_advance_ms") == now
                    and settled.get("last_advance_ms") != origin.get("last_advance_ms"))
        self.state = settled
        self.store.save_state(settled)
        # 只有 F08 已实际落入 ``settled`` 后，才可以把这一段墙钟变化冻结成
        # writer 的只读表现锚。preview/UI/计划刷新均不能经过这里；它也没有
        # 资格替代稍后由本 tick 唯一决定的 F15/F16。
        time_expression = self._time_expression_anchor_from_f08_events(
            events=events, time_binding=effective_time_binding, settled_ms=now,
            event_kind="dynamic_tick")
        # P3 的 receipt 在每一个正式 tick 的 F08 后只生成一次：同一份 Z 同时供
        # crossing（I-Theta）和下一次 Actor 观察（Q_tick）读取。它替换旧 F14/F15
        # 矩阵，不能再调用隔离的 legacy root-vector candidate 形成并行资格。
        tick_id = "tick:%d:%d" % (now, int(origin.get("last_advance_ms") or 0))
        output_identity = self._writer_output_identity()
        tick_participant_id = (output_identity.get("participant_id")
                               if isinstance(output_identity, dict) else None)
        p3_authority = self._publish_p3_authority_receipt(
            origin, settled, effective_constants=effective_constants,
            effective_constants_version=effective_constants_version,
            event_kind="tick", event_id=tick_id, observed_at_ms=now)
        opening = self._p3_tick_opening_candidate(
            settled, now, p3_authority,
            effective_constants=effective_constants,
            effective_constants_version=effective_constants_version)
        clock_plan = self.wall_clock.refresh(
            settled, constants, now, opening=opening,
            cadence=self._cadence_projection(settled), authority_receipt=p3_authority)
        # 在线 Actor tick 与登录 catch_up 都是正式 F08→F15 结算；前者过去只有
        # state/plan、没有数学 span，导致演示中真正逐 tick 运行的路径不可逐点
        # 追溯。trace 仍只读已提交状态，不生成消息、不改计划。
        trace = self._new_math_trace(
            root_kind="tick", root_id=tick_id, now_ms=now,
            root_refs={"tick_id": tick_id, "tick_ts": int(now),
                       "origin_ts": int(origin.get("last_advance_ms") or 0)})
        if trace is not None:
            # trace 只摘要刚刚被正式 F15 消费的同一份 P3 receipt；禁止为展示再算一份。
            p3_shadow = self._p3_shadow_schedule_summary(
                origin, settled, effective_constants, effective_constants_version,
                event_kind="tick", event_id=tick_id, observed_at_ms=now,
                receipt=p3_authority, mode="authority")
            trace.record(
                phase="state_settled", formula_id="F08",
                input_summary=self._f08_trace_input_summary(
                    origin, effective_constants, now,
                    effective_constants_version=effective_constants_version),
                output_summary=self._f08_trace_output_summary(
                    settled, effective_constants, advanced,
                    effective_constants_version=effective_constants_version),
                decision_reason="online_tick_time_settled")
            trace.record(
                phase="schedule_evaluated", formula_id="F15",
                input_summary=self._p3_trace_input_summary(p3_authority),
                output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                "plan_version": int(clock_plan.get("version", 0)),
                                "reason_count": len(clock_plan.get("reasons") or ()),
                                "opening": self._opening_trace_summary(opening),
                                "p3_shadow": p3_shadow},
                decision_reason=("opening_scheduled" if clock_plan.get("at_ms") is not None
                                 else "opening_not_eligible"))
            self._commit_math_trace(trace)
        self.store.append_event({
            "kind": "wall_clock_boundary_settled", "ts": now,
            "detail": {"advanced": bool(advanced), "event_count": len(events),
                       "tick_id": tick_id,
                       "participant_id": tick_participant_id,
                       "params_sha": self.params_sha,
                       "plan_version": int(clock_plan.get("version", 0)),
                       "scheduled": clock_plan.get("at_ms") is not None,
                       # F2 只读双算必须能回看这一次 tick 的原始 F15 资格和
                       # 已消费 edge；不能从后来可能覆盖的当前 plan 倒推。
                       "f15_candidate": {
                           "eligible": opening["eligible"],
                           "qualified_at_ms": opening["qualified_at_ms"],
                           "formula_version": opening["sources"]["formula_version"],
                           "authority_event_id": opening["sources"]["authority_event_id"],
                           "authority_event_kind": opening["sources"]["authority_event_kind"],
                           "anchor_ts": opening["sources"]["anchor_ts"],
                           "anchor_turn_id": opening["sources"]["anchor_turn_id"],
                       },
                       "opening_edge": copy.deepcopy(clock_plan.get("opening_edge")),
                       # 可为空：首次 tick 或零 Delta_t 没有可冻结的 F08 转换。
                       "time_expression_anchor": time_expression},
        })
        return {"advanced": advanced, "sent": False, "messages": [],
                "reason": {"rule": "settled_boundary"}, "events": events,
                "clock_plan": clock_plan, "tick_id": tick_id,
                "participant_id": tick_participant_id,
                "params_sha": self.params_sha}

    def preview_wall_clock(self, now_ms=None):
        """给在飞 responder 的 Actor 做一份纯读的墙钟投影。

        F03 已经提交后，展示层仍需要看见随真实时间变化的根状态；但当前主回复
        worker 持有冻结状态快照，若此处直接调用 :meth:`settle_wall_clock`，两个
        writer 会先后写入不同快照，晚到的草稿提交可能覆盖 tick。故本方法严格只
        复制并推进展示用根状态：不改 ``self.state``，不写 Store，不换正式 plan，
        更不会生成或投递 NPC 消息。F15/计划只可由后续正式 tick 结算，不能由这条
        preview 调用预测。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        if not self.profile or self.state.get("last_ts") is None:
            return {"advanced": False, "state": copy.deepcopy(self.state),
                    "events": [], "clock_plan": None, "projection": "readonly"}

        origin = copy.deepcopy(self.state)
        projected = copy.deepcopy(origin)
        events = []
        time_binding = self._advance_effective_time(projected, now, events)
        update_hidden(projected, time_binding["effective_constants"], None, events, now)
        # Worker 尚未收口时不得把状态投影升格为 F15 候选。只交出当前持久计划的
        # 只读副本；其中若有 authority receipt，UI 可展示它，但不能把它换算到 now。
        plan = self.store.load_clock_plan() or {}
        return {
            "advanced": bool(projected.get("last_advance_ms") !=
                             origin.get("last_advance_ms")),
            "state": projected,
            "events": events,
            "clock_plan": dict(plan),
            "projection": "readonly_inflight",
        }

    def dispatch_opening_action(self, opening_receipt, *, now_ms=None, clock_version=None,
                                opening_edge=None, opening_candidate=None,
                                audit_source="actor_tick", _should_continue=None):
        """唯一正式自主 writer 的过渡入口。

        Actor 只能交来已在正式 tick 中冻结的 opening receipt；这里先校验该 receipt
        仍属于当前计划和未消费 edge，再交给唯一的在线 composer/commit。回复后的
        续话和墙钟主动开口只保留表达审计标签，绝不重新扫描历史或选择另一条 writer。
        """
        receipt = dict(opening_receipt) if isinstance(opening_receipt, dict) else {}
        candidate = dict(opening_candidate) if isinstance(opening_candidate, dict) else {}
        edge = dict(opening_edge) if isinstance(opening_edge, dict) else {}
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        def reject(rule):
            # 拒绝也必须可追溯；它是调度审计事实，不写 X、记忆或 plan。
            self.store.append_event({
                "ts": now, "kind": "opening_dispatch_rejected",
                "detail": {"rule": str(rule), "clock_version": clock_version,
                           "audit_source": str(audit_source or "actor_tick")},
            })
            return {"sent": False, "messages": [], "reason": {"rule": str(rule)}}
        if not receipt or not candidate or not bool(candidate.get("eligible")):
            return reject("invalid_opening_receipt")
        sources = candidate.get("sources") if isinstance(candidate.get("sources"), dict) else receipt
        formula = str((sources or {}).get("formula_version") or "")
        # 新档只承认 tick 所冻结的 P3 authority receipt；旧根向量 receipt 仅保留给
        # 存档兼容投递。任何其它来源（UI、草稿、临时公式）均无 writer 权。
        if not formula.startswith(("opening-authority-receipt-v1",
                                   "opening-root-vector-", "f14-root-vector-")):
            return reject("noncanonical_opening")
        plan = self.store.load_clock_plan() or {}
        try:
            version_matches = (clock_version is not None
                               and int(plan.get("version", -1)) == int(clock_version))
        except (TypeError, ValueError):
            version_matches = False
        if not version_matches:
            return reject("stale_opening_plan")
        stored_edge = plan.get("opening_edge") if isinstance(plan.get("opening_edge"), dict) else {}
        # F08 在 F16 可见等待期间可重读同版本状态；那会把存档 edge 标记为
        # ``held_above_after_consumption``。已冻结、带 ``dispatch_eligible`` 的 edge
        # 仍是本次唯一可提交事实，不能被这次自然衰减后的观察反向否决。
        if edge and not bool(edge.get("dispatch_eligible")):
            return reject("opening_edge_consumed")
        if not edge and stored_edge and not bool(stored_edge.get("dispatch_eligible")):
            return reject("opening_edge_consumed")

        # ``observation_source`` 只保留为已发生观察的表达审计；资格、版本、edge 和
        # Actor 投递均已在上面统一。它不得重算 F14/F15，也不得改写 X。
        observation_source = str(candidate.get("observation_source") or "reply_receipt")
        audit = {"origin": str(audit_source or "actor_tick"), "clock_version": clock_version}
        expression_source = ("followup" if observation_source == "reply_receipt"
                             else "online_proactive")
        committed_action_id = None
        reply = self.commit_opening_message(
            now_ms=now, source=expression_source,
            _should_continue=_should_continue)
        # API/真实样本验收需要把最终 F16 action 反向连回本次已冻结的 tick/P3/F15
        # receipt。此前这些事实分别存在于 plan、数学 trace 和 action receipt 中，
        # 只能靠时间邻近猜测，无法证明“这条输出正是那次 crossing 的消费”。本行
        # 仅在 action 已提交后追加无正文关联，不改变状态、计划、记忆或发送资格。
        if reply:
            committed = next((row for row in reversed(self.store.read_action_receipts())
                              if str(row.get("source") or "") == expression_source
                              and int(row.get("ts", -1) or -1) == now), None)
            if committed is not None:
                committed_action_id = str(committed.get("action_id") or "") or None
                authority_event = (receipt.get("event")
                                   if isinstance(receipt.get("event"), dict) else {})
                self.store.append_event({
                    "schema_version": 1,
                    "kind": "opening_dispatch_committed",
                    "action_id": str(committed.get("action_id") or ""),
                    "turn_id": committed.get("turn_id"),
                    "ts": now,
                    "detail": {
                        "clock_version": clock_version,
                        "audit_source": str(audit_source or "actor_tick"),
                        "authority": {
                            "version": str(receipt.get("version") or ""),
                            # P3 的正式 payload 使用 ``event.kind/id``；dispatch
                            # receipt 只把同一不可变主键转写为显式关联字段，不能
                            # 误读成另一个临时 schema 后再靠时间邻近补因果。
                            "event_kind": str(authority_event.get("kind") or ""),
                            "event_id": str(authority_event.get("id") or ""),
                            "observed_at_ms": authority_event.get("observed_at_ms"),
                        },
                        "opening_candidate": {
                            "eligible": bool(candidate.get("eligible")),
                            "margin": candidate.get("margin"),
                            "at_ms": candidate.get("at_ms"),
                            "formula_version": str((candidate.get("sources") or {}).get(
                                "formula_version") or ""),
                        },
                    },
                })
        return {"sent": bool(reply), "reply": reply or "", "messages": (
            [{"reply": reply, "source": expression_source, "ts": now,
              "action_id": committed_action_id}] if reply else []),
                "source": expression_source, "clock_plan": self.store.load_clock_plan(),
                "reason": {"rule": "opening_dispatched", "audit": audit}}

    def run_proactive_boundary(self, now_ms=None, _should_continue=None, _audit_context=None):
        """已退役的在线边界兼容名；不得绕过正式 ``opening_due``。

        旧工具曾把它当作“命中一次 F15”后直接调用 ``catch_up``。二者的事务语义
        相反：前者是一个已冻结的在线 crossing，后者是离线历史区间回放。因此不再
        给这个无 receipt 的 API 任何 writer 权限；调用方必须经 Actor 的正式 dynamic
        tick 投递 ``dispatch_opening_action``。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        self.store.append_event({
            "ts": now, "kind": "opening_dispatch_rejected",
            "detail": {"rule": "direct_online_boundary_retired",
                       "audit_source": str((_audit_context or {}).get("origin") or "legacy_api")},
        })
        return {"sent": False, "messages": [],
                "reason": {"rule": "direct_online_boundary_retired"}}

    def catch_up(self, now_ms=None, on_message=None, parallel=False, _startup_parallel=False,
                 _should_continue=None, _audit_context=None):
        """重启/轮询时先追平时间总线，再**回放**离线期间的主动消息——

        开口点由逐 tick 生命周期回放扫出（零 LLM）：用户离开多久，NPC 就在
        这期间"想"了多久；每条消息带当时的真实时间戳与状态词元（等了多久/放弃
        与否），按时间顺序一条条加载出来——像微信里回来看到对方发过一串消息。
        增量积分（锚 = last_advance_ms）：同一 gap 反复轮询不重复积分。
        on_message(dict)：每条回放消息生成后立即回调（GUI 逐条揭示用）。
        parallel=True：多线程并行生成（开口点相隔以小时计，本就是独立时刻——
        串行的"上下文连贯"收益极低）；落盘与回调仍按 ts 顺序提交。代价：seed
        逐位确定性让位于总耗时（混沌提及时长的随机数跨线程交错）。

        `_startup_parallel=True` 是 GUI 首次加载专用的私有执行开关：只放宽离线
        批次的模型生成并发，时间扫描、落盘、自我表达回流和回调顺序不变。在线
        tick、用户对话、追发、延迟与演示推进从不传入该开关。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        should_continue = _should_continue if callable(_should_continue) else (lambda: True)
        if not self.profile or self.state.get("last_ts") is None:
            result = {"advanced": False, "sent": False, "messages": [],
                      "reason": {"rule": "no_history"}}
            self._record_catch_up_evaluation(
                now=now, origin=self.state, proactive_plan={
                    "points": [], "decision": {"rule": "no_history", "candidate_count": 0}},
                result=result, audit_context=_audit_context)
            return result
        constants = self.profile.get("constants") or {}

        # ① 挂起中的延迟回复到期 → 先释放（时间戳 = 原发送时刻，重启衔接不漏消息）
        self.release_pending(now)

        # 已经由某次正式 dynamic tick 越线、但 F16 仍未到期的 opening 是一份
        # 不可重算的待投递事实。重启后的 catch_up 不能把同一 crossing 重新解释成
        # “离线历史区间里又发生了一次”，否则会绕过 tick 提前落一条 replay。
        # 保持原计划，交还给 Actor 在 ``at_ms`` 到达后的同一 tick 消费。
        persisted_plan = self.store.load_clock_plan()
        if self.wall_clock.has_future_pending_opening(persisted_plan, now):
            result = {"advanced": False, "sent": False, "messages": [],
                      "reason": {"rule": "pending_opening_not_due"},
                      "clock_plan": dict(persisted_plan)}
            self._record_catch_up_evaluation(
                now=now, origin=self.state, proactive_plan={
                    "points": [], "decision": {
                        "rule": "pending_opening_not_due", "candidate_count": 0,
                        "at_ms": int(persisted_plan["at_ms"]),
                    }},
                result=result, audit_context=_audit_context)
            return result

        # ② 开口点扫描（纯数理，零 LLM）。此快照是离线期的起点；不能先把
        # state 直接推进到 now，否则随后在过去时刻说出的自述只能错误地写进现在。
        origin = copy.deepcopy(self.state)
        context = self._scheduler_context(origin, now)
        catchup_id = "catchup:%d:%d" % (now, int(origin.get("last_advance_ms") or 0))
        origin_cadence = self._cadence_projection(origin)
        time_binding = self._effective_time_binding()
        proactive_plan = replay_tick_lifecycle(
            context.state, time_binding["effective_constants"], context.now_ms,
            cadence=origin_cadence,
            effective_constants=time_binding["effective_constants"],
            effective_constants_version=time_binding["effective_constants_version"])
        pts = proactive_plan["points"]

        if not pts:
            events = []
            s = copy.deepcopy(origin)
            time_binding = self._advance_effective_time(s, now, events)
            advanced = (s.get("last_advance_ms") == now
                        and s.get("last_advance_ms") != origin.get("last_advance_ms"))
            drives = calculate_drives(constants, s["vars"])
            update_hidden(s, time_binding["effective_constants"], None, events, now)
            self.state = s
            self.store.save_state(s)
            p3_authority = self._publish_p3_authority_receipt(
                origin, s, effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"],
                event_kind="tick", event_id=catchup_id, observed_at_ms=now)
            opening = self._p3_tick_opening_candidate(
                s, now, p3_authority,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"])
            clock_plan = self.wall_clock.refresh(
                s, constants, now, opening=opening,
                cadence=self._cadence_projection(s), authority_receipt=p3_authority)
            trace = self._new_math_trace(
                root_kind="catchup", root_id=catchup_id, now_ms=now,
                root_refs={"catchup_id": catchup_id,
                           "origin_ts": int(origin.get("last_advance_ms") or 0),
                           "catchup_ts": int(now)})
            if trace is not None:
                trace.record(
                    phase="state_settled", formula_id="F08",
                    input_summary=self._f08_trace_input_summary(
                        origin, time_binding["effective_constants"], now,
                        effective_constants_version=time_binding["effective_constants_version"]),
                    output_summary=self._f08_trace_output_summary(
                        s, time_binding["effective_constants"], advanced,
                        effective_constants_version=time_binding["effective_constants_version"]),
                    decision_reason="time_settled_without_speak_point")
                trace.record(
                    phase="schedule_evaluated", formula_id="F15",
                    input_summary=self._p3_trace_input_summary(p3_authority),
                    output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                    "plan_version": int(clock_plan.get("version", 0)),
                                    "reason_count": len(clock_plan.get("reasons") or ()),
                                    "cadence_projection": self._cadence_trace_summary(
                                        self._cadence_projection(s))},
                    decision_reason=("clock_scheduled" if clock_plan.get("at_ms") is not None
                                     else "clock_idle"))
                self._commit_math_trace(trace)
            result = {"advanced": advanced, "sent": False, "messages": [],
                      "reason": {"rule": "no_speak_point"}, "events": events,
                      "clock_plan": clock_plan,
                      "proactive_plan": proactive_plan["decision"]}
            self._record_catch_up_evaluation(
                now=now, origin=origin, proactive_plan=proactive_plan,
                result=result, audit_context=_audit_context)
            return result

        # ③ 逐条生成 + 历史时序结算。replay_state 从离线起点逐点推进：每一条
        # NPC 自述的微量反馈先落在它真正说出的时刻，后续时间积分再自然带到 now。
        # 生命周期回放本身仍无副作用，不会因生成文本的随机性改变。
        events = []
        replay_state = copy.deepcopy(origin)
        from core.tokens.behavior import analyze_time_bridge
        from core.vectors import get_drive_vector
        messages = []

        # M7 的本地 resolver 会提交实际选中的事件与冷却。若开口点有时间桥或已生效
        # 时间场作为查询证据，就必须依时间顺序串行结算；否则并行 worker 的写入先后
        # 会把“先想起”变成调度偶然性。完全无查询证据时保留原有并发生成路径。
        if (parallel and not _startup_parallel
                and (origin.get("last_end_drives") is not None
                     or self._active_field_recall_tokens(origin))):
            parallel = False

        output_source = "replay"

        def compose_one(pt):
            hint = None
            bridge = []
            if origin.get("last_end_drives") is not None:
                d_pt = calculate_drives(constants, pt["state"]["vars"])
                bridge = analyze_time_bridge(
                    get_drive_vector(d_pt), origin["last_end_drives"])["tokens"]
            # 墙钟重放没有新用户句子；只以这一时刻已结算的时间桥查询本地记忆。
            # 这条查询不按“最近记录”兜底，因此无关旧事保持不触发。
            query = self._proactive_recall_query(
                pt["ts"], state=pt["state"], bridge_tokens=bridge)
            proactive_recall = self._prepare_proactive_recall(
                pt["ts"], query["query_tokens"], output_source, query_receipt=query)
            recall_events = (() if proactive_recall is None
                             else proactive_recall.expression_plan.events)
            # 长文模式：这条消息距上一条的 gap → 小作文概率（登录回放的大 gap 触发）
            lmode = length_mode((pt["ts"] - origin["last_ts"]) / 3_600_000.0,
                                constants, self.rng,
                                variables=pt["state"].get("vars") or {},
                                # 只有这一轮已经由 recall 裁决并交给 writer 的事件才是
                                # F17 的记忆材料；本地 S 最大值不能越权成为长度开关。
                                memory_delivery_count=len(recall_events))
            form_plan = compile_form_projection(
                baseline=self.prior or {k: 0.2 for k in PROTOTYPES},
                dynamic=pt["state"].get("weights") or self.prior,
                constants=constants, variables=pt["state"].get("vars") or {})
            expression_cards, expression_plan, expression_receipt = \
                self._proactive_expression_context(
                    state=pt["state"], now_ms=pt["ts"], source=output_source,
                    previous_drives=origin.get("last_end_drives"),
                    sent_ms=origin.get("last_ts"))
            prompt = assemble_compat_autonomous_prompt(
                persona_blocks=self.persona_blocks, turn=self.turn,
                field_pressure=pt["state"].get("P", 0.0), ranked_tokens=[],
                # replay 的正式 expression 装配也不能借旧 bridge 旁路；显式
                # legacy A/B 才允许保留该历史字段，和 direct/opening 保持一致。
                bridge_tokens=(() if self.expression_attention else [
                    item for item in expression_cards if item.get("prompt_bridge")]),
                attraction=pt["state"]["vars"].get("V_attr", 0.5),
                dialogue_tail="", state_hint=hint,
                budget_chars=self.prompt_budget_chars, length_mode=lmode,
                responder_core_prompt=self.responder_core_prompt, now_ms=pt["ts"],
                recall_expression_events=recall_events, form_projection=form_plan,
                expression_attention_plan=expression_plan,
                temporal_expression_receipt=expression_receipt,
                proactive_mode="replay",
                canonical_memory_sequence=True,
                output_identity=self._writer_output_identity())
            self._trace_proactive_prompt(output_source, pt["ts"], prompt)
            reply, facts, cons, deltas, evidence, _used, _recall_evidence, _protocol = self._generate_reply(
                prompt.system, prompt.user, DEFAULTS["responder_temperature"])
            return (reply, facts, cons, deltas, evidence, lmode,
                    prompt.recall_expression_events, prompt.assembly_receipt,
                    proactive_recall)

        def commit(reply, facts, cons, deltas, evidence, lmode, recall_events,
                   assembly_receipt, proactive_recall, pt):
            if not should_continue():
                return False
            # 先结算到这条历史消息的发送时刻，再把定稿的自述写入该时刻的状态。
            state_before_time = copy.deepcopy(replay_state)
            time_binding = self._advance_effective_time(replay_state, pt["ts"], events)
            drives = calculate_drives(constants, replay_state["vars"])
            update_hidden(replay_state, time_binding["effective_constants"], None, events, pt["ts"])
            state_before_output = copy.deepcopy(replay_state)
            rec = self._persist_npc_output(
                state=replay_state, ts=pt["ts"], text=reply, source=output_source, S=0.0, tokens=[],
                recall_refs=[recall_expression_reference(event)
                             for event in recall_events],
                # replay 是离线历史重建：唯一可信的可见时刻就是已扫描的历史开口点，
                # 绝不把今天的模型生成耗时倒灌进过去。
                received_ms=pt["ts"], ready_ms=pt["ts"], cadence_delay_ms=0,
                raw_deltas=deltas, evidence=evidence,
                calibration_assembly_receipt=assembly_receipt,
                memory_recall=proactive_recall)
            self_deltas = self._apply_npc_self_expression(
                replay_state, reply=reply, raw_deltas=deltas, evidence=evidence,
                ts=pt["ts"], source=output_source, action_id=rec.get("action_id"),
                output_commit=rec.get("_output_commit_payload"),
                output_evidence=rec.get("_output_commit_evidence"),
                output_memory_recall=rec.get("_output_memory_recall"))
            committed_deltas = self._committed_output_deltas(rec, self_deltas)
            self._ledger_from_reply(
                reply, pt["ts"], facts, cons, event_id=rec.get("event_id"))
            trace = self._new_math_trace(
                root_kind="action", root_id=str(rec.get("action_id") or "action_unknown"),
                now_ms=pt["ts"],
                root_refs={"action_id": str(rec.get("action_id") or "action_unknown"),
                           "action_ts": int(pt["ts"]), "catchup_id": catchup_id})
            if trace is not None:
                trace.record(
                    phase="state_settled", formula_id="F08",
                    input_summary=self._f08_trace_input_summary(
                        state_before_time, constants, pt["ts"]),
                    output_summary=self._f08_trace_output_summary(
                        state_before_output, constants,
                        state_before_output.get("last_advance_ms") == pt["ts"]),
                    decision_reason="replay_point_settled")
                trace.record(
                    phase="memory_resolved", formula_id="F10",
                    output_summary={"selected_count": len(recall_events),
                                    "delivered_count": len(recall_events)},
                    decision_reason=("memory_selected" if recall_events
                                     else "memory_not_selected"))
                trace.record(
                    phase="output_committed", formula_id="F16",
                    output_summary={"committed": True, "delay_ms": 0,
                                    "form_projection": self._projection_trace_summary(
                                        (assembly_receipt or {}).get("form_projection"))},
                    decision_reason="replay_output_committed",
                    parent_ref=str(rec.get("action_id") or "action_unknown"))
                self._record_output_f06(
                    trace, state_before=state_before_output, state_after=replay_state,
                    constants=time_binding["effective_constants"],
                    applied_deltas=committed_deltas, source=output_source,
                    parent_ref=str(rec.get("action_id") or "action_unknown"),
                    effective_constants_version=time_binding["effective_constants_version"])
                self._commit_math_trace(trace)
            # 这是已正式落盘的 output receipt 的轻量引用。它只用于 UI 把同一次
            # 启动回放按视觉节奏逐条揭示；不改变回放状态、prompt 或记忆选择。
            msg = {"ts": pt["ts"], "reply": reply,
                   "action_id": rec.get("action_id"),
                   "length_mode": lmode, "npc_self_deltas": self_deltas}
            messages.append(msg)
            if on_message is not None:
                on_message(msg)
            return True

        if parallel and len(pts) > 1:
            from concurrent.futures import ThreadPoolExecutor
            lock = threading.Lock()
            done = {}
            nxt = 0

            def commit_ordered():
                nonlocal nxt
                while nxt < len(pts) and nxt in done:
                    reply, facts, cons, deltas, evidence, lmode, recall_events, assembly_receipt, proactive_recall = done.pop(nxt)
                    if not commit(reply, facts, cons, deltas, evidence, lmode,
                                  recall_events, assembly_receipt, proactive_recall, pts[nxt]):
                        return
                    nxt += 1

            def gen(i):
                pt = pts[i]
                # 并行模式：开口点相隔以小时计，彼此独立——不串上文（用户裁定：
                # 隔那么久，上下不连贯很正常）
                reply, facts, cons, deltas, evidence, lmode, recall_events, assembly_receipt, proactive_recall = compose_one(pt)
                with lock:
                    done[i] = (reply, facts, cons, deltas, evidence, lmode, recall_events, assembly_receipt, proactive_recall)
                    commit_ordered()

            with ThreadPoolExecutor(max_workers=min(4, len(pts))) as ex:
                list(ex.map(gen, range(len(pts))))
        else:
            for pt in pts:
                if not should_continue():
                    break
                reply, facts, cons, deltas, evidence, lmode, recall_events, assembly_receipt, proactive_recall = compose_one(pt)
                if not commit(reply, facts, cons, deltas, evidence, lmode,
                              recall_events, assembly_receipt, proactive_recall, pt):
                    break

        # 用户已在启动追平中开口：保留在中断前真正提交的历史，但不把未发生的候选
        # 积分、落盘或推送进来。随后 Actor 会优先执行那条用户消息。
        if not should_continue():
            if messages:
                self._bump_unanswered(replay_state, len(messages),
                                      int(replay_state.get("last_ts", now)), "inc_replay")
                self.state = replay_state
                self.store.save_state(replay_state)
            result = {"advanced": bool(messages), "sent": bool(messages), "messages": messages,
                      "reason": {"rule": "user_interrupted", "n": len(messages)},
                      "events": events, "interrupted_by_user": True,
                      "proactive_plan": proactive_plan["decision"]}
            self._record_catch_up_evaluation(
                now=now, origin=origin, proactive_plan=proactive_plan,
                result=result, audit_context=_audit_context)
            return result

        # ④ 离线期处理完：从最后一条历史输出自然积分到 now，再封口计时锚。
        final_before_tick = copy.deepcopy(replay_state)
        time_binding = self._advance_effective_time(replay_state, now, events)
        drives = calculate_drives(constants, replay_state["vars"])
        update_hidden(replay_state, time_binding["effective_constants"], None, events, now)
        advanced = (replay_state.get("last_advance_ms") == now
                    and replay_state.get("last_advance_ms") != origin.get("last_advance_ms"))
        replay_state["last_ts"] = now
        replay_state["last_advance_ms"] = now
        # 心冷：回放的每条消息都未获回复（用户还没回来）
        self._bump_unanswered(replay_state, len(messages), now, "inc_replay")
        self.state = replay_state
        self.store.save_state(replay_state)
        p3_authority = self._publish_p3_authority_receipt(
            # ``replay_state`` has already included every committed historical output;
            # this final tick receipt is the only plan handoff to the live Actor.
            final_before_tick, replay_state,
            effective_constants=time_binding["effective_constants"],
            effective_constants_version=time_binding["effective_constants_version"],
            event_kind="tick", event_id=catchup_id, observed_at_ms=now)
        clock_plan = self.wall_clock.refresh(
            replay_state, constants, now, cause="proactive_committed",
            opening=self._p3_tick_opening_candidate(
                replay_state, now, p3_authority,
                effective_constants=time_binding["effective_constants"],
                effective_constants_version=time_binding["effective_constants_version"]),
            cadence=self._cadence_projection(replay_state), authority_receipt=p3_authority)
        trace = self._new_math_trace(
            root_kind="catchup", root_id=catchup_id, now_ms=now,
            root_refs={"catchup_id": catchup_id,
                       "origin_ts": int(origin.get("last_advance_ms") or 0),
                       "catchup_ts": int(now)})
        if trace is not None:
            trace.record(
                phase="state_settled", formula_id="F08",
                input_summary={**self._f08_trace_input_summary(
                                   origin, time_binding["effective_constants"], now,
                                   effective_constants_version=time_binding["effective_constants_version"]),
                               "replay_count": len(messages)},
                output_summary=self._f08_trace_output_summary(
                    replay_state, time_binding["effective_constants"], advanced,
                    effective_constants_version=time_binding["effective_constants_version"]),
                decision_reason="time_settled_after_replay")
            trace.record(
                phase="schedule_evaluated", formula_id="F15",
                input_summary=self._p3_trace_input_summary(p3_authority),
                output_summary={"scheduled": clock_plan.get("at_ms") is not None,
                                "plan_version": int(clock_plan.get("version", 0)),
                                "reason_count": len(clock_plan.get("reasons") or ()),
                                "cadence_projection": self._cadence_trace_summary(
                                    self._cadence_projection(replay_state))},
                decision_reason=("clock_scheduled" if clock_plan.get("at_ms") is not None
                                 else "clock_idle"))
            self._commit_math_trace(trace)
        result = {"advanced": True, "sent": True, "messages": messages,
                  "reply": messages[-1]["reply"],
                  "reason": {"rule": output_source, "n": len(messages)}, "events": events,
                  "output_source": output_source,
                  "clock_plan": clock_plan,
                  "proactive_plan": proactive_plan["decision"]}
        self._record_catch_up_evaluation(
            now=now, origin=origin, proactive_plan=proactive_plan,
            result=result, audit_context=_audit_context)
        return result

    def maybe_proactive(self, now_ms=None, *, _fixture_legacy=False):
        """退役的旧别名；只有显式 fixture 才可转交离线 replay。

        恢复入口的正式名称是 ``catch_up``。保留这个名字而默认转调会让宿主把它
        误当在线“试试看要不要主动说话”的 API；那与 dynamic tick 的唯一投递权冲突。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        if not _fixture_legacy:
            self.store.append_event({
                "ts": now, "kind": "opening_dispatch_rejected",
                "detail": {"rule": "legacy_maybe_proactive_fixture_required"},
            })
            return {"advanced": False, "sent": False, "messages": [],
                    "reason": {"rule": "legacy_maybe_proactive_fixture_required"}}
        return self.catch_up(now_ms=now)

    # ------------------------------------------------------------------
    # 用户线观察（T7-3 · 运行时半）：沉默期流失观察——先观测后干预
    # ------------------------------------------------------------------
    def observe_user_line(self, now_ms=None):
        """tick 时调用：用户沉默时长跨入新分带 → 追加 user_gap 观测事件。

        零 LLM、零干预：只把"离开了多久"变成运行时观测（与 report_churn
        事后回算互补）。返回本轮追加的事件列表（多为空）。
        """
        now = int(now_ms if now_ms is not None else _time.time() * 1000)
        if self._last_user_ts is None:
            for m in reversed(self.store.read_messages()):
                if self._is_preference_evidence(m):
                    self._last_user_ts = int(m.get("ts", now))
                    break
        if self._last_user_ts is None:
            return []
        from ops.user_line import observe
        gap_min = (now - self._last_user_ts) / 60_000.0
        idx, events = observe(self._last_gap_band, gap_min, now)
        self._last_gap_band = idx
        for e in events:
            self.store.append_event(e)
        return events
