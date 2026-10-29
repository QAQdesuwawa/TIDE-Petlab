"""单轮回忆上下文解析。

该模块只负责把词元、语义和陈述台账三条召回通道收敛为给 responder
使用的上下文。它不关心回复生成、状态提交或消息落盘；这些仍由 Runner
作为一次用户回合的事务编排者负责。
"""
from dataclasses import dataclass
import random
from typing import Optional

from core.params import MEMORY, LEDGER
from ops.ledger import episode_slice
from ops.embeddings import has_exact_ngram_overlap
from ops.fixture_provenance import is_non_dialogue_seed
from ops.recall_gate import (gate_probability, memory_query_tokens, recall_mode,
                             token_evidence)
from ops.recall_evidence import (evidence_snapshot, observe_evidence,
                                 qualifies_for_recall, recall_propensity)
from ops.recall_candidate_pool import (build_ledger_candidate_pool,
                                       ledger_candidate_id)


@dataclass(frozen=True)
class RecallExpressionEvent:
    """Alpha 0.3/legacy family 的提前正文事件卡。

    Alpha 0.4 C5 不消费该 DTO：新链以 CanonicalRecallItem 保持 ID/receipt，
    attention 结束后才从 canonical event 水合 MemoryCard。保留本类型只为旧会话
    与 family-off 基线，不能把它重新接入 Alpha 0.4。
    """

    event_id: str
    source: str
    record_ts: int
    text: str
    prompt_priority: float
    identity_kind: str = "legacy_no_event_id"
    # 这不是一次新测量，也不是关系状态：只把 committed record 已经保存的发言
    # provenance 带到表现卡，避免第一人称原文在 writer 处失去说话者。
    speaker_scope: str = "legacy_source"
    speaker_participant_id: str = ""


@dataclass(frozen=True)
class RecallExpressionPlan:
    """只转换既有选择结果；不检索、不重掷随机数、不生成文本。"""

    events: tuple = ()
    excluded_ledger_ts: tuple = ()


@dataclass(frozen=True)
class TurnRecall:
    """一次用户消息的召回结果及其可观测归因。"""

    mode: str
    evidence: float
    memory_record: object
    memory_hint: str
    embedding: object
    semantic_hit: str
    semantic_record: object
    ledger_record: object
    ledger_hint: str
    episode: str
    shadow: object = None
    selected_records: tuple = ()
    selected_hints: tuple = ()
    # 仅由 Runner 的可抢占主回复使用：prompt 已看到这些选择，但 archive/ledger
    # 冷却账本尚未提交。内容不出现在公开 trace，也不供主动线复用。
    commit_plan: tuple = ()

    @property
    def sources(self):
        """实际命中的召回通道，按提示词注入顺序返回。"""
        if self.selected_records:
            return [str(item[0]) for item in self.selected_records]
        return [name for name, used in (
            ("memory", bool(self.memory_record)),
            ("semantic", bool(self.semantic_hit)),
            ("ledger", bool(self.ledger_record)),
        ) if used]

    @property
    def state_hint(self):
        """给 responder 的紧凑自然语言提示；无命中时返回 ``None``。"""
        if self.selected_hints:
            return " ".join(self.selected_hints + ((self.episode,) if self.episode else ()))
        return " ".join(value for value in (
            self.memory_hint, self.semantic_hit, self.ledger_hint, self.episode,
        ) if value) or None

    @property
    def expression_plan(self):
        """将已提交记录投影为有容量的生成事件卡。"""
        events, excluded_ledger_ts = [], []
        total = len(self.selected_records)
        for index, (source, record) in enumerate(self.selected_records):
            if not isinstance(record, dict):
                continue
            # 情绪 archive 只含内部测量，没有可归因的对话原文；它可以继续影响墙钟状态，
            # 但绝不能被润色成可见“事件”，否则会把系统自己的解释伪装成共同经历。
            text = str(record.get("text") or "").strip()
            if not text:
                continue
            record_ts = int(record.get("ts", 0))
            # 新 committed event 使用存储中的精确 ID，使 writer delivery 能与
            # ordered sequence 精确互斥；旧档保持原 source:ts 形状，绝不在读取时
            # 用时间或正文补造身份。
            committed_event_id = str(record.get("event_id") or "").strip()
            event_id = committed_event_id or "%s:%d" % (str(source), record_ts)
            speaker_scope, speaker_participant_id = _recall_speaker_provenance(record, source)
            events.append(RecallExpressionEvent(
                event_id=event_id,
                source=str(source), record_ts=record_ts, text=text,
                # 选择器的排序已经冻结；这里只把其相对次序交给技术预算装配，
                # 不再以 fixed Top-N 重写“本轮实际想起了哪些事件”。
                prompt_priority=float(total - index),
                identity_kind=("exact_committed_event" if committed_event_id
                               else "legacy_no_event_id"),
                speaker_scope=speaker_scope,
                speaker_participant_id=speaker_participant_id))
            if source == "ledger":
                excluded_ledger_ts.append(record_ts)
        return RecallExpressionPlan(tuple(events), tuple(excluded_ledger_ts))


def _recall_speaker_provenance(record, source):
    """读取已提交 event 的发言归属；缺失时如实保留 unknown，绝不按正文猜测。"""
    if not isinstance(record, dict):
        return "unknown", ""
    output_identity = record.get("output_identity")
    if isinstance(output_identity, dict):
        sender = str(output_identity.get("participant_id") or "").strip()
        if sender:
            return "self", sender
    incoming = record.get("conversation_provenance")
    if isinstance(incoming, dict):
        sender = str(incoming.get("sender_participant_id") or "").strip()
        if sender:
            return "peer", sender
    own_output = record.get("conversation_context")
    if isinstance(own_output, dict):
        sender = str(own_output.get("sender_participant_id") or "").strip()
        if sender:
            return "self", sender
    visible = record.get("group_visibility_provenance")
    if isinstance(visible, dict):
        sender = str(visible.get("sender_participant_id") or "").strip()
        owner = str(visible.get("owner_participant_id") or "").strip()
        if sender and owner:
            return ("self" if sender == owner else "peer"), sender
    role = str(record.get("role") or "").strip().lower()
    if role == "user":
        return "peer", ""
    if role == "npc":
        return "self", ""
    # 这是旧 source 的既有概念边界，不从第一人称正文反推身份。
    if str(source) == "ledger":
        return "self", ""
    if str(source) == "semantic":
        return "peer", ""
    if bool(record.get("non_dialogue_seed")):
        return "shared_fact", ""
    return "unknown", ""


@dataclass(frozen=True)
class RecallIntent:
    """P2 影子门：只说明本轮是否值得读取候选，不改变现行实际召回。"""

    allow: bool
    # None 表示候选资格本身没有固定事件数上限；最终 writer delivery 由独立的
    # prompt 预算处理并留下逐卡 receipt。0 只表示本轮无资格读取。
    budget: Optional[int]
    reason_codes: tuple
    evidence: float


RAG_SELECTION_RECEIPT_VERSION = "rag-selection-receipt-v1"
ACTUAL_RECALL_RECEIPT_VERSION = "actual-recall-selection-v1"
_SAME_EVENT_SOURCE_PRIORITY = {"memory": 0, "ledger": 1, "semantic": 2}


def recall_receipt_reference(source, record, *, now_ms=None):
    """发布一条已选记录的无正文 receipt 引用。

    ``source + record_ts`` 只保留为旧档定位信息，不能再被 action receipt 当作
    新档的事件身份。带 ``event_id`` 的 committed record 必须把这份 exact identity
    一起带到 selection/action receipt；缺 identity 的历史行明确标为 legacy，而不是
    在读取时按时间或正文补造一条身份。
    """
    if not isinstance(record, dict):
        raise ValueError("recall record 必须是字典")
    try:
        record_ts = int(record.get("ts", now_ms))
    except (TypeError, ValueError) as exc:
        raise ValueError("recall record 缺少有效 ts") from exc
    ref = {"source": str(source), "record_ts": record_ts}
    event_id = str(record.get("event_id") or "").strip()
    if event_id:
        ref["event_id"] = event_id
        ref["identity_kind"] = "exact_committed_event"
    else:
        ref["identity_kind"] = "legacy_no_event_id"
    return ref


def build_actual_recall_receipt(*, trigger_event_id, evaluation_time_ms,
                                strategy, actual_refs):
    """发布 actual recall 已完成事实的无正文、exact-ID 回执。

    该函数不检索、不排序、不读取候选分数，也不决定表达资格。它只把 resolver
    已经完成的选择冻结为 C3-R1 可以校验的结构事实。Alpha 0.4 正式路径要求
    exact committed identity；旧档兼容行不得在这里按 source/time 猜补身份。
    """
    trigger = str(trigger_event_id or "").strip()
    if not trigger:
        raise ValueError("actual recall receipt 缺少 trigger_event_id")
    if isinstance(evaluation_time_ms, bool):
        raise ValueError("actual recall receipt evaluation_time_ms 必须是非负整数")
    try:
        at_ms = int(evaluation_time_ms)
    except (TypeError, ValueError) as exc:
        raise ValueError("actual recall receipt evaluation_time_ms 必须是非负整数") from exc
    if at_ms < 0 or at_ms != evaluation_time_ms:
        raise ValueError("actual recall receipt evaluation_time_ms 必须是非负整数")
    strategy_name = str(strategy or "").strip()
    if not strategy_name:
        raise ValueError("actual recall receipt 缺少 strategy")

    references, event_ids, seen = [], [], set()
    for raw in tuple(actual_refs or ()):
        if not isinstance(raw, dict):
            raise ValueError("actual recall reference 必须是字典")
        identity_kind = str(raw.get("identity_kind") or "").strip()
        event_id = str(raw.get("event_id") or "").strip()
        if identity_kind != "exact_committed_event" or not event_id:
            raise ValueError("Alpha 0.4 actual recall 需要 exact committed identity")
        if event_id in seen:
            raise ValueError("actual recall receipt 不能包含重复 event_id")
        seen.add(event_id)
        try:
            record_ts = int(raw["record_ts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("actual recall reference 缺少有效 record_ts") from exc
        source = str(raw.get("source") or "").strip()
        if not source:
            raise ValueError("actual recall reference 缺少 source")
        references.append({
            "event_id": event_id,
            "identity_kind": "exact_committed_event",
            "source": source,
            "record_ts": record_ts,
        })
        event_ids.append(event_id)
    return {
        "version": ACTUAL_RECALL_RECEIPT_VERSION,
        "trigger_event_id": trigger,
        "evaluation_time_ms": at_ms,
        "strategy": strategy_name,
        "recalled_event_ids": tuple(event_ids),
        "references": tuple(references),
        "selection_completed": True,
        "content_included": False,
        "derived_only": True,
    }


def recall_expression_reference(event):
    """将已经通过 writer delivery 的离散事件卡投影为 action receipt 引用。"""
    identity_kind = str(getattr(event, "identity_kind", "legacy_no_event_id"))
    if identity_kind not in {"exact_committed_event", "legacy_no_event_id"}:
        raise ValueError("recall event identity_kind 不受支持")
    ref = {
        "source": str(getattr(event, "source", "")),
        "record_ts": int(getattr(event, "record_ts")),
        "identity_kind": identity_kind,
    }
    speaker_scope = str(getattr(event, "speaker_scope", "legacy_source") or "legacy_source")
    # 无归属的旧档不能伪装成可用 provenance。只有已知的 committed 归属才进入
    # action receipt；writer 端仍会把 unknown 当作不可归属事实处理。
    if speaker_scope in {"peer", "self", "shared_fact"}:
        ref["speaker_scope"] = speaker_scope
        speaker_participant_id = str(getattr(event, "speaker_participant_id", "") or "")
        if speaker_participant_id:
            ref["speaker_participant_id"] = speaker_participant_id
    if identity_kind == "exact_committed_event":
        event_id = str(getattr(event, "event_id", "") or "").strip()
        if not event_id:
            raise ValueError("已交付 exact recall event 缺少 event_id")
        ref["event_id"] = event_id
    return ref


def _selected_relation_source(record):
    """只投影已选历史的 relation 来源；不授予 P4 跨关系状态权限。"""
    if not isinstance(record, dict):
        return None
    scope = record.get("relationship_runtime")
    relation = scope.get("relation_key") if isinstance(scope, dict) else None
    expected = {"schema_version", "owner_participant_id", "counterparty_participant_id"}
    if not isinstance(relation, dict) or set(relation) != expected:
        return None
    try:
        version = int(relation["schema_version"])
    except (TypeError, ValueError):
        return None
    owner = str(relation["owner_participant_id"] or "").strip()
    counterparty = str(relation["counterparty_participant_id"] or "").strip()
    if version != 1 or not owner or not counterparty or owner == counterparty:
        return None
    return {
        "schema_version": version,
        "owner_participant_id": owner,
        "counterparty_participant_id": counterparty,
    }


def selected_rag_receipts(*, selected_records, shadow):
    """将**已实际选中且实际取得**的 RAG bundle 投影为无正文 selection receipt。

    这是 recall selection → P4 状态桥的单向适配器。它不查询、不排序、不重算 relevance，
    更不调用 Agent 1；只在 resolver 已经留下 ``selected_bundles`` 后，将其中与
    ``selected_records`` 同 source/ts 的既成选择回执取出。候选、被拒绝 bundle、writer
    delivery 和表达卡都无权进入返回值。
    """
    actual = {
        (str(source), int(record.get("ts", 0))): record
        for source, record in (selected_records or ())
        if isinstance(record, dict)
    }
    bundles = (shadow or {}).get("selected_bundles") if isinstance(shadow, dict) else ()
    receipts = {}
    for bundle in bundles or ():
        if not isinstance(bundle, dict):
            continue
        try:
            source, ts = str(bundle["source"]), int(bundle["record_ts"])
            relevance = float(bundle["relevance"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("selected RAG bundle 缺少有效 source/record_ts/relevance") from exc
        if not 0.0 <= relevance <= 1.0:
            raise ValueError("selected RAG bundle relevance 必须在 0 到 1 之间")
        key = (source, ts)
        record = actual.get(key)
        if record is None:
            continue
        reference = recall_receipt_reference(source, record, now_ms=ts)
        canonical_event_id = str(bundle.get("canonical_event_id") or "").strip()
        actual_event_id = str(reference.get("event_id") or "").strip()
        # 新档候选与实际 selected record 必须是同一 exact event；不能因同 source/ts
        # 恰巧相同便替换或吞掉另一条 committed 事实。旧档保留显式兼容，不猜补。
        if canonical_event_id and canonical_event_id != actual_event_id:
            continue
        ref = actual_event_id or ("%s:%d" % key)
        if ref in receipts:
            raise ValueError("selected RAG bundle 不能为同一记录重复生成 receipt")
        receipt = {
            "version": RAG_SELECTION_RECEIPT_VERSION,
            **reference,
            "relevance": relevance,
            "derived_only": True,
        }
        relation_source = _selected_relation_source(record)
        if relation_source is not None:
            receipt["recalled_from_relationship"] = relation_source
        receipts[ref] = receipt
    return receipts


def derive_recall_intent(text, evidence, *, allow_token_only=False,
                         has_query_tokens=False):
    """确定性、零 LLM 的最小门。

    每条非空消息都允许**只读**候选收集；显式提问和词元证据只作为可解释的附加理由。
    真正是否选中仍由候选相关性决定，因此高显著但无关的旧事不能单独通过。这能覆盖
    日常的非问句续话，同时不改变 P2 的真实召回路径。
    """
    reasons = []
    content = str(text or "").strip()
    if content:
        reasons.append("message")
    if "?" in content or "？" in content:
        reasons.append("question")
    if abs(float(evidence)) > 0.0:
        reasons.append("token_evidence")
    if not content and allow_token_only and has_query_tokens:
        # 主动线没有一条新的用户原文，但墙钟已经结算出可追溯的桥接词元；它可作为
        # 查询证据，而不是伪造一句用户消息或按“最新记录”直接塞卡。
        reasons.append("proactive_token_evidence")
    allow = bool(content) or (bool(allow_token_only) and bool(has_query_tokens))
    return RecallIntent(allow, None if allow else 0,
                        tuple(reasons), round(float(evidence), 4))


def _candidate(source, record, relevance, salience, now_ms, source_score=None,
               direct_text_anchor=False, temporal_readout=None,
               canonical_event_id=None, mirror_sources=()):
    """统一候选元数据；事件日志只保存这些数值和时间，不保存历史文本。"""
    rec = {
        "source": source,
        "record_ts": int(record.get("ts", now_ms)),
        "relevance": round(float(relevance), 4),
        "salience": round(float(salience), 4),
    }
    if canonical_event_id:
        rec["canonical_event_id"] = str(canonical_event_id)
    if mirror_sources:
        rec["mirror_sources"] = tuple(str(item) for item in mirror_sources)
    if temporal_readout is not None:
        try:
            age_retention = float(temporal_readout["age_retention"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("memory candidate 缺少有效 age_retention") from exc
        if not 0.0 <= age_retention <= 1.0:
            raise ValueError("memory candidate age_retention 必须在 0 到 1 之间")
        # 选择 utility 只用于 F10 的读序；原始 relevance 保留给 P4 的 current
        # relation receipt。二者不能混成同一分数，否则 P4 会重复扣时间权重。
        rec["age_retention"] = age_retention
        rec["selection_utility"] = round(float(relevance) * age_retention, 6)
    # 仅供本次 resolver 提交选中候选；_shadow_event 会剥离，绝不复制正文到 events。
    rec["_record"] = record
    rec["_source_score"] = float(relevance if source_score is None else source_score)
    # 当前原文与历史原文的已有连续片段是比“状态词元恰好重合”更强的选择证据。
    # 这是本轮临时排序标签：不持久化、不增容量，也不替代各来源已有资格门。
    rec["_direct_text_anchor"] = bool(direct_text_anchor)
    return rec


def collect_shadow_candidates(*, intent, memory, ledger, query_tokens, query_text,
                              now_ms, semantic_record=None, semantic_score=None,
                              embed_fn=None, memory_tokens=None,
                              temporal_readout=None, exact_memory_event_ids=(),
                              exact_memory_query_tokens=(),
                              memory_anchor_event_id=None,
                              include_memory_anchor=True,
                              memory_local_only=False):
    """只读收集三通道候选，绝不写唤起账本或消耗选择随机数。"""
    if not intent.allow:
        return []
    candidates = []
    canonical_candidates = {}
    # AUD-131：产品 F10 的 memory-local 路径不能读取 token/text/embedding。
    # 参数仍保留给历史诊断调用；只有显式 anchor 的现代路径才进入这个分支。
    anchor_id = str(memory_anchor_event_id or "").strip()
    if anchor_id and hasattr(memory, "interaction_candidates"):
        memory_items = memory.interaction_candidates(
            now_ms=now_ms, anchor_event_id=anchor_id,
            include_anchor=bool(include_memory_anchor),
            temporal_readout=temporal_readout)
    else:
        memory_tokens = query_tokens if memory_tokens is None else memory_tokens
        continuity_anchor_id = next((str(event_id).strip()
                                     for event_id in (exact_memory_event_ids or ())
                                     if str(event_id).strip()), "")
        memory_items = memory.emotional_candidates(
            memory_tokens, now_ms, top_k=3, temporal_readout=temporal_readout,
            exact_event_ids=exact_memory_event_ids,
            exact_query_tokens=exact_memory_query_tokens,
            temporal_continuity_anchor_event_id=continuity_anchor_id)
    for item in memory_items:
        rec = item["record"]
        candidate = _candidate("memory", rec, item["score"],
                               rec.get("S", 0.0), now_ms,
                               temporal_readout=item.get("temporal_readout"),
                               canonical_event_id=item.get("canonical_event_id"),
                               mirror_sources=item.get("mirror_sources") or ())
        if item.get("exact_event_reference"):
            # 只作无正文 receipt 标记：该 candidate 仍是同一个 memory record，
            # 仍经相同 selector/P4/writer delivery，不产生“当前输入卡”。
            candidate["exact_event_reference"] = True
        if item.get("temporal_continuity"):
            candidate["temporal_continuity"] = True
        if item.get("memory_relation_source"):
            candidate["memory_relation_source"] = str(item["memory_relation_source"])
        if isinstance(item.get("trajectory_receipt"), dict):
            # No text or root snapshot is copied: exact event IDs resolve the
            # immutable six-dimensional anchors, while the receipt preserves
            # intervals and derived comparison components for replay.
            candidate["trajectory_receipt"] = dict(item["trajectory_receipt"])
            candidate["trajectory_similarity"] = float(item["trajectory_similarity"])
            candidate["center_direction_similarity"] = float(
                item["center_direction_similarity"])
        candidates.append(candidate)
        canonical_event_id = str(item.get("canonical_event_id") or rec.get("event_id") or "").strip()
        if canonical_event_id:
            canonical_candidates[canonical_event_id] = candidate
    if memory_local_only:
        return candidates
    # 当前运行路径只在问句时计算 embedding；影子线复用已有结果，不能为审计额外
    # 调远程 embedding 服务。
    if semantic_record is not None and semantic_score is not None:
        direct_text_anchor = has_exact_ngram_overlap(
            query_text, semantic_record.get("text", ""),
            LEDGER["fallback_min_shared_ngram_chars"])
        semantic_candidate = _candidate(
            "semantic", semantic_record, semantic_score,
            semantic_record.get("S", 0.0), now_ms,
            direct_text_anchor=direct_text_anchor)
        # A3-5 的 canonical event identity 不能因词面候选未命中而丢失：若本地
        # semantic index 已明确返回一条 committed event，先精确回到同一 Actor
        # Store 的 message view。语义分支仍作为同源相关性 witness 留给 resolver
        # receipt；它不能单独变成 ``semantic:<ts>`` 的 P4 状态记忆。
        semantic_event_id = str(semantic_record.get("event_id") or "").strip()
        if semantic_event_id and semantic_event_id not in canonical_candidates:
            parent = (memory.committed_event(semantic_event_id)
                      if hasattr(memory, "committed_event") else None)
            if isinstance(parent, dict):
                canonical = _candidate(
                    "memory", parent, semantic_score, parent.get("S", 0.0), now_ms,
                    source_score=semantic_score, direct_text_anchor=direct_text_anchor,
                    temporal_readout=(temporal_readout(parent)
                                      if temporal_readout is not None else None),
                    canonical_event_id=semantic_event_id,
                    mirror_sources=("message",))
                candidates.append(canonical)
                canonical_candidates[semantic_event_id] = canonical
        candidates.append(semantic_candidate)
    for score, s_eff, relevance, rec in ledger.candidates(
            query_tokens, query_text, now_ms, embed_fn=embed_fn):
        # 台账旧算法允许高 S + 零相关的候选；影子线显式剔除，验证这一收紧是否合理。
        if relevance <= 0.0:
            continue
        # 已有 ledger 的事实正文／主题与当前原文存在连续片段时，属于比状态词元
        # 偶合更直接的本轮关联证据。它只影响已有候选的本轮读序，不造新事实、
        # 不改 relevance，也不让 writer 读取 raw ordered history。
        direct_text_anchor = any(
            has_exact_ngram_overlap(query_text, str(rec.get(field) or ""),
                                    LEDGER["fallback_min_shared_ngram_chars"])
            for field in ("topic", "text")
        )
        event_id = str(rec.get("event_id") or "").strip()
        if not event_id:
            # 旧台账仍可按旧规则读取；它没有可验证的 parent event，绝不靠 ts/text
            # 伪装成 annotation。A3-5c 的 migration 会单独处理这类行。
            candidates.append(_candidate("ledger", rec, relevance, s_eff, now_ms,
                                         source_score=score,
                                         direct_text_anchor=direct_text_anchor))
            continue

        candidate = canonical_candidates.get(event_id)
        if candidate is None and hasattr(memory, "committed_event"):
            parent = memory.committed_event(event_id)
            if parent is not None:
                candidate = _candidate(
                    "memory", parent, relevance, s_eff, now_ms,
                    source_score=score, temporal_readout=(
                        temporal_readout(parent) if temporal_readout is not None else None),
                    canonical_event_id=event_id,
                    mirror_sources=("message",),
                    direct_text_anchor=direct_text_anchor)
                candidates.append(candidate)
                canonical_candidates[event_id] = candidate
        if candidate is None:
            # 有 identity 但 parent 不在本 Actor Store 的 annotation 不能凭自身成为
            # 第二条 memory 入口；保留在 Ledger，供显式迁移/诊断而非 writer 选择。
            continue

        # annotation 只增强所属 canonical event 的现有关系证据；它不复制正文，
        # 不改变 P4 的 event identity，也不会增加 candidate 数量。
        candidate["relevance"] = round(max(float(candidate["relevance"]), float(relevance)), 4)
        candidate["salience"] = round(max(float(candidate["salience"]), float(s_eff)), 4)
        if "age_retention" in candidate:
            candidate["selection_utility"] = round(
                float(candidate["relevance"]) * float(candidate["age_retention"]), 6)
        else:
            candidate["selection_utility"] = float(candidate["relevance"])
        candidate["_source_score"] = max(float(candidate["_source_score"]), float(score))
        candidate["_direct_text_anchor"] = bool(candidate.get("_direct_text_anchor")
                                                  or direct_text_anchor)
        annotations = list(candidate.get("annotation_sources") or [])
        annotation = {"source": "ledger", "record_ts": int(rec.get("ts", now_ms))}
        if annotation not in annotations:
            annotations.append(annotation)
        candidate["annotation_sources"] = annotations
    return candidates


def _underlying_event_key(candidate):
    """为跨来源镜像建立只读的底层事件身份。

    同一用户/NPC 原文可能同时被 archive、local semantic index 或 ledger 投影命中；
    来源名不同不代表发生了多次事件。缺少可比归属的旧记录继续按来源独立处理，避免
    凭时间戳猜测两个无关旧档是同一件事。
    """
    record = candidate.get("_record") if isinstance(candidate, dict) else None
    if not isinstance(record, dict):
        return None
    canonical_event_id = str(record.get("event_id") or "").strip()
    if canonical_event_id:
        return "committed_event", canonical_event_id
    # Legacy rows lack an auditable event identity. They remain explicit isolated
    # compatibility candidates; source+timestamp is not enough to assert two
    # files describe one committed event.
    return None


def _collapse_same_event_mirrors(candidates):
    """合并 archive/semantic/ledger 对同一原始事件的镜像候选。

    这只收敛选择输入，不改各通道的候选资格或历史数据。`memory` 优先保留是来源
    保真：它携带已提交事件的 state imprint；其 semantic 镜像仍留 rejected receipt。
    """
    groups, passthrough = {}, []
    for candidate in candidates or ():
        key = _underlying_event_key(candidate)
        if key is None:
            passthrough.append(candidate)
        else:
            groups.setdefault(key, []).append(candidate)
    retained, rejected = list(passthrough), []
    for event_key in sorted(groups):
        mirrors = groups[event_key]
        preferred_raw = min(mirrors, key=lambda item: (
            _SAME_EVENT_SOURCE_PRIORITY.get(str(item.get("source")), 99),
            -float(item.get("relevance", 0.0)), -float(item.get("salience", 0.0)),
            str(item.get("source")),
        ))
        preferred = preferred_raw
        # `memory` 是同一历史事件进入 P4 的唯一 canonical source；但一个被压掉的
        # semantic/ledger 镜像若已经提供正相关凭证，不能连同这份 *表达资格事实*
        # 一起丢掉。它不是第二个候选、不是第二个 actual ref，也不携带正文；只让
        # F05 知道 canonical event 已经由同一轮选择证明与当前输入相关。
        witnesses, expression_record = [], None
        for candidate in mirrors:
            if candidate is preferred_raw or str(candidate.get("source")) not in {"semantic", "ledger"}:
                continue
            try:
                relevant = float(candidate.get("relevance", 0.0) or 0.0) > 0.0
                record_ts = int(candidate.get("record_ts"))
            except (TypeError, ValueError):
                continue
            if relevant:
                witnesses.append({"source": str(candidate.get("source")), "record_ts": record_ts})
                raw_record = candidate.get("_record")
                if (expression_record is None and isinstance(raw_record, dict)
                        and str(raw_record.get("text") or "").strip()):
                    # 只在本次 resolver 内保留已有镜像正文；它不会回写 memory archive，
                    # 也不会进入公开 selection receipt。
                    expression_record = raw_record
        if witnesses:
            preferred = dict(preferred, expression_relevance_witnesses=witnesses)
            if expression_record is not None:
                preferred["_expression_record"] = expression_record
        retained.append(preferred)
        for candidate in mirrors:
            if candidate is preferred_raw:
                continue
            rejected.append({**candidate, "drop_reason": "same_origin_duplicate_event",
                             "duplicate_of": {"source": str(preferred.get("source")),
                                              "record_ts": int(preferred.get("record_ts", 0))}})
    return retained, rejected


def prepare_semantic_recall_pool(*, store, memory, ledger, text, now_ms,
                                 ranked_tokens, bridge_tokens, capacity,
                                 exploration_budget, embed_fn=None):
    """只读提出可供语义重排评估的台账候选池。

    这不是一次实际召回，也不运行概率门或写审计。调用方只有在显式启用的实验配置
    下才应将返回条目发送给网络适配器。
    """
    evidence = token_evidence(ranked_tokens, bridge_tokens)
    intent = derive_recall_intent(text, evidence)
    recall_tokens = memory_query_tokens(
        ranked_tokens,
        allow_resident=_has_prior_exact_user_text(store, text, before_ms=now_ms))
    local = collect_shadow_candidates(
        intent=intent, memory=memory, ledger=ledger,
        query_tokens=ranked_tokens, query_text=text, now_ms=now_ms,
        embed_fn=embed_fn, memory_tokens=recall_tokens)
    return build_ledger_candidate_pool(
        ledger=ledger, local_candidates=local, now_ms=now_ms,
        capacity=capacity, exploration_budget=exploration_budget)


def merge_semantic_rerank_candidates(*, candidates, candidate_pool, matches,
                                     ledger, now_ms, min_relevance, min_confidence):
    """把合格重排证据并入既有候选，不直接选择或提交台账。

    两个最小分数是调用方随预检声明的实验配置；本函数不提供运行时隐式默认值。
    """
    min_relevance, min_confidence = float(min_relevance), float(min_confidence)
    if not (0.0 <= min_relevance <= 1.0 and 0.0 <= min_confidence <= 1.0):
        raise ValueError("语义重排资格线无效")
    pool = {item.candidate_id: item for item in candidate_pool or ()}
    merged = [dict(item) for item in candidates or ()]
    by_id = {ledger_candidate_id(item["_record"]): item
             for item in merged
             if item.get("source") == "ledger" and isinstance(item.get("_record"), dict)}
    for match in matches or ():
        candidate_id = str(getattr(match, "candidate_id", ""))
        relevance = float(getattr(match, "relevance", 0.0))
        confidence = float(getattr(match, "confidence", 0.0))
        item = pool.get(candidate_id)
        if item is None or relevance < min_relevance or confidence < min_confidence:
            continue
        semantic_score = round(relevance * confidence, 4)
        detail = {"candidate_id": candidate_id, "relevance": round(relevance, 4),
                  "confidence": round(confidence, 4), "evidence": str(getattr(match, "evidence", ""))}
        existing = by_id.get(candidate_id)
        if existing is not None:
            existing["relevance"] = max(float(existing["relevance"]), semantic_score)
            existing["_source_score"] = max(float(existing.get("_source_score", 0.0)),
                                             float(ledger.params["recall_w_s"]) * item.salience
                                             + float(ledger.params["recall_w_rel"]) * semantic_score)
            existing["semantic_rerank"] = detail
            continue
        candidate = _candidate(
            "ledger", item.record, semantic_score, item.salience, now_ms,
            source_score=(float(ledger.params["recall_w_s"]) * item.salience
                          + float(ledger.params["recall_w_rel"]) * semantic_score))
        candidate["semantic_rerank"] = detail
        merged.append(candidate)
        by_id[candidate_id] = candidate
    return merged


def decide_shadow_recall(intent, candidates):
    """选择所有已资格化的 distinct committed event，相关性只决定读序。

    F10 不以固定 Top-N 定义“本轮只能想起几件事”。同一事件镜像仍去重；writer
    delivery 另由自己的技术预算逐卡裁剪，不能反过来否认这些已选择记忆。
    """
    ranked = sorted(candidates, key=lambda c: (
        -int(bool(c.get("_direct_text_anchor", False))),
        -float(c.get("selection_utility", c["relevance"])),
        -float(c["relevance"]), -float(c["salience"]), -int(c["record_ts"]),
        str(c["source"])))
    selected, rejected, seen = [], [], set()
    if intent.allow and intent.budget != 0:
        for candidate in ranked:
            key = (str(candidate["source"]), int(candidate["record_ts"]))
            if key in seen:
                rejected.append({**candidate, "drop_reason": "duplicate_event"})
            else:
                selected.append(candidate)
                seen.add(key)
    return {"intent": {"allow": intent.allow, "budget": intent.budget,
                       "reason_codes": list(intent.reason_codes),
                       "evidence": intent.evidence},
            "candidates": ranked, "selected": selected[0] if selected else None,
            "selected_bundles": selected, "rejected_bundles": rejected}


def decide_evidence_recall(intent, candidates, rng=None):
    """v2：仅对已合格候选做可回放抽样，随机性绝不绕过相关性资格门。"""
    rng = rng if rng is not None else random.Random(0)
    ranked = sorted(candidates, key=lambda candidate: (
        -float(candidate.get("selection_utility", candidate.get("utility", 0.0))),
        -float(candidate.get("utility", 0.0)), -float(candidate["relevance"]),
        -int(candidate["record_ts"]), str(candidate["source"])))
    selected, rejected, seen = [], [], set()
    if intent.allow and intent.budget != 0:
        for candidate in ranked:
            key = (str(candidate["source"]), int(candidate["record_ts"]))
            if key in seen:
                rejected.append({**candidate, "drop_reason": "duplicate_event"})
                continue
            seen.add(key)
            draw = round(float(rng.random()), 6)
            attempted = {**candidate, "draw": draw}
            if draw <= float(candidate["propensity"]):
                selected.append(attempted)
            else:
                rejected.append({**attempted, "drop_reason": "propensity_miss"})
    return {"intent": {"allow": intent.allow, "budget": intent.budget,
                       "reason_codes": list(intent.reason_codes),
                       "evidence": intent.evidence},
            "candidates": ranked, "selected": selected[0] if selected else None,
            "selected_bundles": selected, "rejected_bundles": rejected}


def decide_additive_evidence_recall(intent, primary_candidates, weak_candidates):
    """保留 unified 的事实选择，再以剩余容量追加已资格化的弱证据。

    高频资格不是一条新的“更高分”排序轴：普通 unified 的候选必须先按原规则
    完整裁决，弱证据只可使用未占用的事件包容量。这样重复提及可以形成可见候选，
    却不能挤掉当前输入已经直接相关的共同经历。
    """
    primary = decide_shadow_recall(intent, primary_candidates)
    selected = [dict(item, selection_lane="primary")
                for item in primary.get("selected_bundles") or ()]
    rejected = [dict(item, selection_lane="primary")
                for item in primary.get("rejected_bundles") or ()]
    primary_keys = {(str(item["source"]), int(item["record_ts"]))
                    for item in primary_candidates}
    selected_keys = {(str(item["source"]), int(item["record_ts"]))
                     for item in selected}
    seen_weak, additive_candidates = set(), []
    for candidate in sorted(weak_candidates, key=lambda c: (
            -float(c["relevance"]), -float(c["salience"]), -int(c["record_ts"]),
            str(c["source"]))):
        key = (str(candidate["source"]), int(candidate["record_ts"]))
        if key in seen_weak:
            continue
        seen_weak.add(key)
        # 已经进入普通候选池的事实由 primary lane 全权处理；不复制成第二张卡。
        if key in primary_keys:
            continue
        tagged = dict(candidate, selection_lane="additive_frequency")
        additive_candidates.append(tagged)
        if not intent.allow or intent.budget == 0:
            rejected.append({**tagged, "drop_reason": "intent_not_allowed"})
        elif key in selected_keys:
            rejected.append({**tagged, "drop_reason": "duplicate_event"})
        else:
            selected.append(tagged)
            selected_keys.add(key)
    candidates = [dict(item, selection_lane="primary") for item in primary_candidates]
    candidates.extend(additive_candidates)
    return {"intent": dict(primary["intent"]), "candidates": candidates,
            "selected": selected[0] if selected else None,
            "selected_bundles": selected, "rejected_bundles": rejected}


def _shadow_event(shadow, ts, turn_id=None):
    """审计行不含历史文本，避免把记忆内容复制进另一条持久化流。"""
    def public(candidate):
        if candidate is None:
            return None
        return {k: v for k, v in candidate.items() if not k.startswith("_")}

    detail = dict(shadow)
    detail["candidates"] = [public(c) for c in shadow.get("candidates") or []]
    detail["selected"] = public(shadow.get("selected"))
    detail["selected_bundles"] = [public(c) for c in shadow.get("selected_bundles") or []]
    detail["rejected_bundles"] = [public(c) for c in shadow.get("rejected_bundles") or []]
    rec = {"ts": int(ts), "kind": "recall_shadow", "detail": detail}
    if turn_id is not None:
        rec["turn_id"] = str(turn_id)
    return rec


def _record_recall_geometry(store, shadow, ranked_tokens, now_ms, turn_id, strategy,
                            event_sink=None):
    """M2 词元几何观察卡：只读候选的家族方向与归档 drift，绝不改变选择。

    在 recall_shadow 之前落盘，保持"最后一件事是召回影子审计"的既有事件顺序
    约定；观察行不含历史文本与内部记录（承 _shadow_event 纪律）。
    """
    from ops.recall_geometry import (geometry_event, geometry_observations,
                                     token_direction)
    dir_now = token_direction(ranked_tokens)
    observations = geometry_observations(dir_now, shadow.get("candidates") or [])
    if not observations:
        return None
    event = geometry_event(ts=now_ms, turn_id=turn_id, strategy=strategy,
                           dir_now=dir_now, observations=observations)
    if callable(event_sink):
        event_sink(event)
    else:
        store.append_event(event)
    return event


def _record_deny_declarations(store, text, shadow, now_ms, turn_id, params_sha,
                              boundary_sink=None):
    """M3 否认挂账卡：文本 × 本轮候选 → 边界词元行（零压制，只挂账观察）。

    边界行是独立账本流（boundary.jsonl），不进入事件序、不影响任何选择。
    回合序号从 turn_id 解析（"turn:<ts>:<seq>"），供 M4 按轮归龄。
    """
    if not hasattr(store, "append_boundary"):
        return []
    from ops.deny import declare_denials, turn_index_of
    rows = declare_denials(text=text, candidates=shadow.get("candidates") or [],
                           now_ms=now_ms, turn_id=turn_id,
                           turn=turn_index_of(turn_id), params_sha=params_sha)
    for row in rows:
        if callable(boundary_sink):
            boundary_sink(row)
        else:
            store.append_boundary(row)
    return rows


def _semantic_lookup(memory, text, embed_fn, semantic_min_score):
    """本地消息索引的保守回退；不以向量相似单独创造相关性。

    非问句仍默认不走广义语义召回，但只要当前原文与历史原文有足够长的真实
    连续片段，就允许已有本地索引给出一条候选。这覆盖“那家咖啡店／那杯”
    这类明确重提，同时避免时间场 Top-3 裁掉事件锚后只剩泛状态记忆。
    ``BigramEmbedder`` 的哈希余弦只用于候选排序；最终资格仍由未哈希词面证据
    决定，因此它不是外部 embedding/RAG 的隐式替代，也不新增运行时变量。
    """
    if not str(text or "").strip():
        # 主动线只复用本地、由时间桥给出的词元查询；没有新句子就不把空串送到
        # embedding 适配器，更不因此触发网络调用。
        return None, None, "", None
    embedding = embed_fn(text) if embed_fn else None
    semantic_record = None
    semantic_hit = ""
    semantic_score = None
    threshold = (MEMORY["semantic_min_score"] if semantic_min_score is None
                 else semantic_min_score)
    if embedding is not None:
        # 当前 E_user 已经是正式 message，故它可能拥有与 query 完全相同的向量。
        # 它不能作为自己的历史证据；但也不能因占据 Top-1 而把更早的相关事实一并
        # 遮蔽。候选容量仍受既有 local Top-K 约束，下面只在同一池内跳过 self-hit。
        hits = memory.semantic_search(
            embedding, top_k=max(2, int(MEMORY["semantic_top_k"])))
        for hit in hits:
            if float(hit.get("score", 0.0)) < threshold:
                continue
            candidate = hit.get("record") or {}
            candidate_text = str(candidate.get("text", ""))
            if candidate_text == text:
                continue
            lexical_anchor = has_exact_ngram_overlap(
                text, candidate_text, LEDGER["fallback_min_shared_ngram_chars"])
            if "？" not in text and "?" not in text and not lexical_anchor:
                continue
            semantic_record = candidate
            semantic_score = hit["score"]
            semantic_hit = "你想起对方之前说过：" + candidate_text[:60]
            break
    return embedding, semantic_record, semantic_hit, semantic_score


def _refs(*items, now_ms):
    return [recall_receipt_reference(name, record, now_ms=now_ms)
            for name, record in items if record is not None]


def _hydrate_memory_expression_record(store, record):
    """情绪 archive 被选中后，仅按 exact event_id 读取所属 committed message。

    archive 仍只存状态词元和测量；缺 identity 的旧行保持内部状态，不以 source、
    timestamp 或正文猜测一个可见共同经历。
    """
    if not isinstance(record, dict) or str(record.get("text") or "").strip():
        return record
    event_id = str(record.get("event_id") or "").strip()
    if not event_id:
        return record
    for message in reversed(store.read_messages()):
        if str(message.get("event_id") or "").strip() != event_id:
            continue
        text = str(message.get("text") or "").strip()
        if text:
            return {**record, "text": text}
    return record


def _has_prior_exact_user_text(store, text, *, before_ms=None):
    """同一句用户原文再次出现时，允许常驻词元作为辅助的记忆锚。

    这是输入事实的精确重复，而非“某类用户”的偏好特例；它解决重复表达本身应有
    连续性的需求，同时不让跨话题的常驻人格方向泛化成旧事相关性。调用方在当前
    用户事件已落盘后，必须传入该事件的 ``before_ms``：当前事件不能为自己放行。
    """
    needle = " ".join(str(text or "").casefold().split())
    if not needle:
        return False
    for message in store.read_messages():
        if message.get("role") != "user":
            continue
        if before_ms is not None and int(message.get("ts", -1)) >= int(before_ms):
            continue
        candidate = " ".join(str(message.get("text") or "").casefold().split())
        if candidate == needle:
            return True
    return False


def _resolve_unified(*, store, memory, ledger, text, now_ms, ranked_tokens,
                     bridge_tokens, embed_fn, semantic_min_score, turn_id,
                     evidence, mode, strategy="unified", rng=None,
                     semantic_rerank_pool=(), semantic_rerank_matches=(),
                     semantic_rerank_min_relevance=None,
                     semantic_rerank_min_confidence=None, params_sha=None,
                     allow_token_only=False, event_sink=None, boundary_sink=None,
                     commit_selected=True, temporal_readout=None,
                     exact_memory_event_ids=(), exact_memory_query_tokens=(),
                     memory_anchor_event_id=None, include_memory_anchor=True):
    """统一 F10：只读 committed identity、墙钟和六维事件印痕。"""
    anchor_id = str(memory_anchor_event_id or "").strip()
    if not anchor_id:
        raise ValueError("统一 F10 缺少已提交 memory anchor identity")
    # AUD-131：ranked/bridge token、正文、semantic 与 Ledger 均不构成 F10 资格。
    intent = RecallIntent(True, None, ("memory_local_committed_anchor",), 0.0)
    candidates = collect_shadow_candidates(
        intent=intent, memory=memory, ledger=ledger, query_tokens=(), query_text="",
        now_ms=now_ms, memory_tokens=(),
        temporal_readout=temporal_readout,
        memory_anchor_event_id=anchor_id,
        include_memory_anchor=include_memory_anchor,
        memory_local_only=True)
    candidates, same_event_rejects = _collapse_same_event_mirrors(candidates)
    shadow = decide_shadow_recall(intent, candidates)
    if same_event_rejects:
        shadow["rejected_bundles"] = list(same_event_rejects) + list(
            shadow.get("rejected_bundles") or [])
    selected = shadow["selected_bundles"]
    memory_record = ledger_record = None
    memory_hint = ledger_hint = ""
    selected_records, selected_hints, commit_plan = [], [], []
    embedding = semantic_record = None
    semantic_hit = ""
    for selected_bundle in selected:
        source = selected_bundle["source"]
        record = selected_bundle["_record"]
        if source == "memory":
            if commit_selected:
                record, hint = memory.commit_recall(record, now_ms, mode="mixed")
            else:
                hint = memory.recall_hint(record, mode="mixed")
                commit_plan.append(("memory", record, {"mode": "mixed"}))
            record = _hydrate_memory_expression_record(store, record)
            # canonical memory 仍只承担一次 P4/recall 事实。若 archive 自身没有正文，
            # 仅可借同源、已选、正相关镜像在本回合提供的临时表达原文；绝不把该正文
            # 写回 archive，更不能用不相关或未选候选填充它。
            if not str(record.get("text") or "").strip():
                backing = selected_bundle.get("_expression_record")
                backing_text = (str(backing.get("text") or "").strip()
                                if isinstance(backing, dict) else "")
                if backing_text:
                    record = {**record, "text": backing_text}
            memory_record = memory_record or record
        else:
            continue
        selected_records.append((source, record))
        selected_hints.append(hint)
    memory_hint = next((hint for (source, _), hint in zip(selected_records, selected_hints)
                        if source == "memory"), "")
    actual_refs = _refs(*selected_records, now_ms=now_ms)
    fire_times = [ref["record_ts"] for ref in actual_refs]
    episode = episode_slice(store, min(fire_times)) if fire_times else ""
    shadow["actual_refs"] = actual_refs
    shadow["strategy"] = strategy
    # Alpha 0.4 只增加选择完成后的结构回执，不改变 selector、P4 或 writer。
    # 旧 fixture 若仍缺 exact identity，明确记录不可适配而不按时间猜补身份。
    exact_identity_available = all(
        str(ref.get("identity_kind") or "") == "exact_committed_event"
        and bool(str(ref.get("event_id") or "").strip())
        for ref in actual_refs
    )
    if exact_identity_available:
        shadow["actual_recall_receipt"] = build_actual_recall_receipt(
            trigger_event_id=anchor_id,
            evaluation_time_ms=now_ms,
            strategy=strategy,
            actual_refs=actual_refs,
        )
    else:
        shadow["actual_recall_receipt"] = None
        shadow["actual_recall_receipt_unavailable"] = {
            "version": ACTUAL_RECALL_RECEIPT_VERSION,
            "reason": "non_exact_legacy_identity",
            "selection_completed": True,
            "content_included": False,
        }
    shadow_event = _shadow_event(shadow, now_ms, turn_id=turn_id)
    if callable(event_sink):
        event_sink(shadow_event)
    else:
        store.append_event(shadow_event)
    return TurnRecall(
        mode=mode, evidence=evidence, memory_record=memory_record,
        memory_hint=memory_hint, embedding=embedding,
        semantic_hit=semantic_hit, semantic_record=semantic_record,
        ledger_record=ledger_record, ledger_hint=ledger_hint,
        episode=episode, shadow=shadow,
        selected_records=tuple(selected_records), selected_hints=tuple(selected_hints),
        commit_plan=tuple(commit_plan))


def select_compat_received_recall(*, store, memory, ledger, text, now_ms, ranked_tokens,
                                  bridge_tokens, embed_fn=None, semantic_min_score=None,
                                  turn_id=None, strategy="legacy", rng=None,
                                  semantic_rerank_pool=(), semantic_rerank_matches=(),
                                  semantic_rerank_min_relevance=None,
                                  semantic_rerank_min_confidence=None, params_sha=None,
                                  allow_token_only=False, event_sink=None,
                                  boundary_sink=None, commit_selected=True,
                                  temporal_readout=None, exact_memory_event_ids=(),
                                  exact_memory_query_tokens=(), include_memory_anchor=True):
    """解析单轮回忆上下文。

    ``legacy`` 保持原有多通道概率路径；``unified`` 保持确定性选择；
    ``unified_evidence_v2`` 是历史的替换式频率实验；
    ``unified_evidence_additive_v1`` 仅以剩余容量追加已资格化弱证据。均保留审计行。
    ``params_sha`` 仅供观察卡（M2 几何/M3 否认挂账）打参数版本戳，不影响选择。
    ``commit_selected=False`` 仅供可抢占的主回复：选择和 prompt 投递照常完成，
    但 archive/ledger 冷却计数延迟至 ``commit_turn_recall``。
    """
    if strategy not in {"legacy", "unified", "unified_evidence_v2",
                        "unified_evidence_additive_v1",
                        "unified_suppressed"}:
        raise ValueError("recall strategy 无效")
    if semantic_rerank_matches and strategy == "legacy":
        raise ValueError("legacy 策略不接受语义重排结果")
    explicit_anchor = next((str(event_id).strip() for event_id in (exact_memory_event_ids or ())
                            if str(event_id).strip()), "")
    turn_anchor = (memory.committed_event_id_for_turn(turn_id)
                   if not explicit_anchor and hasattr(memory, "committed_event_id_for_turn")
                   else "")
    memory_anchor_event_id = explicit_anchor or turn_anchor
    # AUD-114：online opening anchor 是已经提交到 owner-local Store 的 exact
    # event identity。即使角色档案仍带 legacy recall strategy，它也不能退回 legacy
    # 的概率式 `maybe_recall`，否则 identity 虽到达 selector 却在实际 selected
    # /writer delivery 前丢失。这里不建立第二张“当前输入卡”，只让同一 unified
    # selector 按既有 event record 完成 P4/交付；无 exact identity 的 legacy 行为
    # 完全保持原样。
    exact_event_required = bool(memory_anchor_event_id)
    # 现代产品路径在 F03 后必须能精确回读 anchor；无 anchor 的旧 fixture/诊断
    # 保持历史 resolver，不能伪装成 memory-local F10。
    if exact_event_required:
        return _resolve_unified(
            store=store, memory=memory, ledger=ledger, text=text, now_ms=now_ms,
            ranked_tokens=ranked_tokens, bridge_tokens=bridge_tokens,
            embed_fn=embed_fn, semantic_min_score=semantic_min_score,
            turn_id=turn_id, evidence=0.0, mode="mixed",
            strategy=("unified" if exact_event_required and strategy == "legacy" else strategy),
            rng=rng,
            semantic_rerank_pool=semantic_rerank_pool,
            semantic_rerank_matches=semantic_rerank_matches,
            semantic_rerank_min_relevance=semantic_rerank_min_relevance,
            semantic_rerank_min_confidence=semantic_rerank_min_confidence,
            params_sha=params_sha, allow_token_only=allow_token_only,
            event_sink=event_sink, boundary_sink=boundary_sink,
            commit_selected=commit_selected,
            temporal_readout=temporal_readout,
            exact_memory_event_ids=exact_memory_event_ids,
            exact_memory_query_tokens=exact_memory_query_tokens,
            memory_anchor_event_id=memory_anchor_event_id,
            include_memory_anchor=include_memory_anchor)

    evidence = token_evidence(ranked_tokens, bridge_tokens)
    mode = recall_mode(ranked_tokens, bridge_tokens)

    probability = gate_probability(
        mode, memory.params["recall_p_trigger"], evidence)
    recall_tokens = memory_query_tokens(
        ranked_tokens,
        allow_resident=_has_prior_exact_user_text(store, text, before_ms=now_ms))
    commit_plan = []
    if commit_selected:
        memory_record, memory_hint = memory.maybe_recall(
            recall_tokens, now_ms, mode=mode, p=probability)
    else:
        memory_record, memory_hint = memory.select_recall(
            recall_tokens, now_ms, mode=mode, p=probability)
        if memory_record is not None:
            commit_plan.append(("memory", memory_record, {"mode": mode}))
    memory_record = _hydrate_memory_expression_record(store, memory_record)

    embedding, semantic_record, semantic_hit, semantic_score = _semantic_lookup(
        memory, text, embed_fn, semantic_min_score)

    if commit_selected:
        ledger_record, ledger_hint = ledger.maybe_recall(
            ranked_tokens, text, now_ms, embed_fn=embed_fn)
    else:
        ledger_record, ledger_hint, ledger_score, ledger_probability = ledger.select_recall(
            ranked_tokens, text, now_ms, embed_fn=embed_fn)
        if ledger_record is not None:
            commit_plan.append(("ledger", ledger_record, {
                "score": ledger_score, "probability": ledger_probability,
            }))

    fire_times = []
    for record in (memory_record, semantic_record, ledger_record):
        if record is not None:
            fire_times.append(record.get("ts", now_ms))
    episode = episode_slice(store, min(fire_times)) if fire_times else ""
    actual_refs = _refs(("memory", memory_record), ("semantic", semantic_record),
                        ("ledger", ledger_record), now_ms=now_ms)

    # P2：真实旧路径全部完成后才做只读影子比较，避免额外读取改变随机数、
    # 冷却或真实召回时序。semantic_record/score 仅复用本轮已经算出的结果。
    intent = derive_recall_intent(
        text, evidence, allow_token_only=allow_token_only,
        has_query_tokens=bool(ranked_tokens or bridge_tokens))
    shadow = decide_shadow_recall(
        intent,
        collect_shadow_candidates(
            intent=intent, memory=memory, ledger=ledger,
            query_tokens=ranked_tokens, query_text=text, now_ms=now_ms,
            semantic_record=semantic_record, semantic_score=semantic_score,
            embed_fn=embed_fn, memory_tokens=recall_tokens))
    shadow["actual_refs"] = actual_refs
    shadow["strategy"] = "legacy"
    _record_deny_declarations(store, text, shadow, now_ms, turn_id, params_sha,
                              boundary_sink=boundary_sink)
    _record_recall_geometry(store, shadow, ranked_tokens, now_ms, turn_id, "legacy",
                            event_sink=event_sink)
    shadow_event = _shadow_event(shadow, now_ms, turn_id=turn_id)
    if callable(event_sink):
        event_sink(shadow_event)
    else:
        store.append_event(shadow_event)

    return TurnRecall(
        mode=mode, evidence=evidence, memory_record=memory_record,
        memory_hint=memory_hint or "", embedding=embedding,
        semantic_hit=semantic_hit, semantic_record=semantic_record,
        ledger_record=ledger_record, ledger_hint=ledger_hint or "",
        episode=episode, shadow=shadow,
        selected_records=tuple((name, record) for name, record in (
            ("memory", memory_record), ("semantic", semantic_record), ("ledger", ledger_record))
            if record is not None),
        selected_hints=tuple(value for value in (memory_hint, semantic_hit, ledger_hint) if value),
        commit_plan=tuple(commit_plan),
    )


def commit_turn_recall(*, memory, ledger, recall, now_ms):
    """提交一份已经选定的召回计划，且绝不重新检索或重掷随机数。

    它是主回复事务的唯一 recall 落盘边界。只有提交闸已通过才调用；被抢占
    的生成因此既不会写 recall 账本，也不会推进候选的冷却时间。
    """
    committed = []
    for source, record, kwargs in tuple(getattr(recall, "commit_plan", ()) or ()):
        if source == "memory":
            memory.commit_recall(record, now_ms, mode=kwargs.get("mode"))
        elif source == "ledger":
            ledger.commit_recall(record, now_ms, score=kwargs["score"],
                                 probability=kwargs.get("probability"),
                                 policy=kwargs.get("policy"))
        else:
            continue
        committed.append({"source": str(source), "record_ts": int(record.get("ts", now_ms))})
    return tuple(committed)


def select_compat_autonomous_recall(*, store, memory, ledger, now_ms, bridge_tokens,
                                    strategy="legacy", rng=None, params_sha=None,
                                    turn_id=None, commit_selected=True,
                                    temporal_readout=None, opening_anchor_event_id=None):
    """主动输出的本地回忆查询。

    ``bridge_tokens`` 仍由调用方用于表达层卡包；统一 F10 不消费它。主动记忆只从
    已提交 opening anchor、墙钟与六维事件印痕读取。
    """
    return select_compat_received_recall(
        store=store, memory=memory, ledger=ledger, text="", now_ms=now_ms,
        ranked_tokens=bridge_tokens or (), bridge_tokens=(), embed_fn=None,
        turn_id=turn_id, strategy=strategy, rng=rng, params_sha=params_sha,
        allow_token_only=True, commit_selected=commit_selected,
        temporal_readout=temporal_readout,
        exact_memory_event_ids=((str(opening_anchor_event_id),)
                                if str(opening_anchor_event_id or "").strip() else ()),
        exact_memory_query_tokens=(), include_memory_anchor=True)


# 历史工具 API：只为 Alpha 0.3／family-off 调用方保留同一函数对象；默认产品
# 必须从 ``ops.legacy_recall_compat`` 显式选择兼容能力，不能把这里当正式 Memory。
resolve_turn_recall = select_compat_received_recall
resolve_proactive_recall = select_compat_autonomous_recall


def _with_evidence_candidates(candidates, weak_candidates):
    """仅保留 v2 已资格化候选，并并入弱证据，不改动原始记录。

    ``collect_shadow_candidates`` 的“相关”门比 v2 的强相关资格更宽：它适合标准
    unified 的确定性排序，却不能让低相关事件在首次探测时直接进入概率抽样。v2 中，
    直接候选必须达到 strong 阈值；其余候选只能由跨回合弱证据资格化后加入。
    """
    from core.params import RECALL_EVIDENCE
    weak_by_key = {
        (str(weak["source"]), int(weak["record"].get("ts", 0))): weak
        for weak in weak_candidates
    }
    enriched = []
    for candidate in candidates:
        key = (str(candidate["source"]), int(candidate["record_ts"]))
        if float(candidate["relevance"]) >= float(RECALL_EVIDENCE["strong_relevance_min"]):
            enriched.append(dict(candidate))
        elif key in weak_by_key:
            # 使用弱探测的相关度与累计曝光，不能把较宽的普通 candidate relevance
            # 伪装成一次“强相关”。仍复用原 canonical candidate，不能让 evidence
            # 支路丢掉其 exact event ID、镜像来源、直接文本锚或时间 readout。
            weak = weak_by_key[key]
            merged = dict(candidate)
            merged["relevance"] = round(float(weak["relevance"]), 4)
            merged["salience"] = round(float(weak["salience"]), 4)
            merged["_source_score"] = float(weak["source_score"])
            if "age_retention" in merged:
                merged["selection_utility"] = round(
                    float(merged["relevance"]) * float(merged["age_retention"]), 6)
            else:
                merged["selection_utility"] = float(merged["relevance"])
            enriched.append(merged)
    by_key = {(str(candidate["source"]), int(candidate["record_ts"])): candidate
              for candidate in enriched}
    for weak in weak_candidates:
        key = (str(weak["source"]), int(weak["record"].get("ts", 0)))
        candidate = by_key.get(key)
        if candidate is None:
            canonical_event_id = (str(weak["record"].get("event_id") or "").strip()
                                  if str(weak["source"]) == "memory" else "")
            candidate = _candidate(
                weak["source"], weak["record"], weak["relevance"], weak["salience"],
                int(weak["record"].get("ts", 0)), source_score=weak["source_score"],
                canonical_event_id=canonical_event_id,
                mirror_sources=(("message",) if canonical_event_id else ()))
            enriched.append(candidate)
            by_key[key] = candidate
        candidate["exposure"] = round(float(weak["exposure"]), 4)
    for candidate in enriched:
        exposure = float(candidate.get("exposure", 0.0))
        candidate["exposure"] = round(exposure, 4)
        candidate["utility"] = round(
            float(RECALL_EVIDENCE["utility_relevance_weight"]) * float(candidate["relevance"])
            + float(RECALL_EVIDENCE["utility_salience_weight"]) * float(candidate["salience"])
            + float(RECALL_EVIDENCE["utility_exposure_weight"]) * exposure, 4)
        candidate["propensity"] = recall_propensity(
            salience=candidate["salience"], relevance=candidate["relevance"],
            exposure=exposure, params=RECALL_EVIDENCE)
    return enriched


def _record_weak_ledger_evidence(*, store, memory, ledger, text, now_ms, turn_id,
                                  query_tokens=()):
    """记录统一事件的弱探测；返回仅供显式策略使用的资格化候选。

    函数名为兼容保留。ledger 仍使用文本/词元弱探测；情绪 archive 则直接复用既有
    词元重合候选。两者共用无正文 evidence 流，不创建新的“创伤计数”。
    """
    if not (hasattr(store, "read_recall_evidence")
            and hasattr(store, "append_recall_evidence")
            and hasattr(ledger, "weak_probe_candidates")):
        return [], []
    from core.params import RECALL_EVIDENCE
    history = store.read_recall_evidence()
    observations, qualified, observed_keys = [], [], set()
    for item in ledger.weak_probe_candidates(
            text, now_ms, RECALL_EVIDENCE["weak_probe_min"],
            query_tokens=query_tokens):
        record = item["record"]
        # 新 Ledger 行只是 parent event 的 annotation。文本弱探测仍是这条共同
        # 经历的合法前向证据，但必须登记到同一 exact memory event，而非另造
        # source=ledger 平行记忆；否则 canonicalization 会意外吞掉重复提及链。
        event_id = str(record.get("event_id") or "").strip()
        parent = (memory.committed_event(event_id)
                  if event_id and hasattr(memory, "committed_event") else None)
        if isinstance(parent, dict):
            evidence_source, evidence_record = "memory", parent
            salience = float(parent.get("S", 0.0))
        else:
            evidence_source, evidence_record = "ledger", record
            salience = ledger.decayed_salience(record, now_ms)
        evidence_key = (evidence_source, int(evidence_record.get("ts", now_ms)))
        evidence = observe_evidence(
            history, ts=now_ms, turn_id=turn_id, source=evidence_source,
            record_ts=evidence_key[1], probe_score=item["score"],
            stage="weak_probe", params=RECALL_EVIDENCE)
        store.append_recall_evidence(evidence)
        history = list(history) + [evidence]
        snapshot = evidence_snapshot(history, source=evidence_source,
                                     record_ts=evidence_key[1],
                                     now_ms=now_ms, params=RECALL_EVIDENCE)
        projected = qualifies_for_recall(
            # 这里记录的是弱探测投影；真正的强相关仍由现有 candidates 路径决定。
            strong_relevance=0.0, weak_relevance=item["score"], snapshot=snapshot,
            params=RECALL_EVIDENCE)
        observations.append({
            "source": evidence_source, "record_ts": evidence_key[1],
            "probe_score": evidence["probe_score"],
            "probe_basis": item.get("probe_basis", "unknown"),
            "evidence_before": evidence.get("evidence_before"),
            "evidence_after": evidence.get("evidence_after"),
            "distinct_turns": snapshot["distinct_turns"],
            "projected_eligible": projected["eligible"],
            "projected_reason": projected["reason"],
            "projected_propensity": recall_propensity(
                salience=salience,
                relevance=item["score"], exposure=snapshot["exposure"],
                params=RECALL_EVIDENCE),
        })
        observed_keys.add(evidence_key)
        # 弱探测只提供连续的相关性证据；是否真正唤起仍由统一 resolver 的资格和
        # 概率门决定。上次 recall 不能作为固定小时排除条件。
        if projected["eligible"]:
            qualified.append({
                "source": evidence_source, "record": evidence_record,
                "relevance": item["score"], "salience": salience,
                "exposure": snapshot["exposure"],
                "source_score": (float(ledger.params["recall_w_s"]) * salience
                                 + float(ledger.params["recall_w_rel"]) * item["score"]),
            })
    # archive 没有正文弱探测：只使用已由 Memory 计算的词元重合。
    if memory is not None and query_tokens:
        for item in memory.emotional_candidates(query_tokens, now_ms, top_k=3):
            record, score = item["record"], float(item["score"])
            evidence_key = ("memory", int(record.get("ts", now_ms)))
            if evidence_key in observed_keys:
                continue
            evidence = observe_evidence(
                history, ts=now_ms, turn_id=turn_id, source="memory",
                record_ts=int(record.get("ts", now_ms)), probe_score=score,
                stage="weak_probe", params=RECALL_EVIDENCE)
            store.append_recall_evidence(evidence)
            history = list(history) + [evidence]
            snapshot = evidence_snapshot(history, source="memory",
                                         record_ts=int(record.get("ts", now_ms)),
                                         now_ms=now_ms, params=RECALL_EVIDENCE)
            projected = qualifies_for_recall(
                strong_relevance=0.0, weak_relevance=score, snapshot=snapshot,
                params=RECALL_EVIDENCE)
            observations.append({
                "source": "memory", "record_ts": int(record.get("ts", now_ms)),
                "probe_score": evidence["probe_score"], "probe_basis": "token_overlap",
                "evidence_before": evidence.get("evidence_before"),
                "evidence_after": evidence.get("evidence_after"),
                "distinct_turns": snapshot["distinct_turns"],
                "projected_eligible": projected["eligible"],
                "projected_reason": projected["reason"],
                "projected_propensity": recall_propensity(
                    salience=float(record.get("S", 0.0)), relevance=score,
                    exposure=snapshot["exposure"], params=RECALL_EVIDENCE),
            })
            if projected["eligible"]:
                qualified.append({
                    "source": "memory", "record": record, "relevance": score,
                    "salience": float(record.get("S", 0.0)),
                    "exposure": snapshot["exposure"], "source_score": score,
                })
    return observations, qualified
