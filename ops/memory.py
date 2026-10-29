# ops/memory.py
"""记忆层（实现设计 §12）：全量存储 + 双索引 + 概率唤起。

- 语义向量库：messages.jsonl 每条消息带 emb（embedding 可插拔，P6 接 API）
- 情绪词元库：archive.jsonl 归档结束词元（强度+时间戳+漂移参数+S）
- 无遗忘：存储无条件；候选由相关性及已有资格排序，不以固定小时冷却剔除
- 注入 = 情绪色彩污染：只给"似曾相识"的感觉，不复述原文（架构文档 §12.4）
- archive 为 append-only：唤起事件以新行记账，不回写旧行
"""
import math
import random

from core.params import MEMORY
from core.interaction_geometry import ROOT_ORDER, root_snapshot

RECALL_PHRASES = {
    "aggro": "你心里涌上一阵久违的火气——像极了某一次不欢而散的针锋相对。",
    "love": "你忽然想起，上一次你们之间也是这样的柔软——甜得让人心里发慌。",
    "fatigue": "一种似曾相识的疲惫漫上来，像某一次无疾而终的沉默。",
    "chaos": "你感到一阵熟悉的失控感正在靠近——上一次这样的时候，你们差点把一切都砸了。",
    "info_upper": "你想起来，你曾经也这样占据过上风——然后呢？",
    "info_lower": "这种被拿捏的感觉不是第一次了——你记得。",
    "fam_close": "你想起你们最熟稔的那些日子——和现在像，又不像。",
    "info_calm": "你隐约记得，也有过这样平静到发慌的时刻。",
}


# ---------------------------------------------------------------------
# 确定性算子
# ---------------------------------------------------------------------
def cosine(a, b):
    """余弦相似度（零分母防护）。"""
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na < 1e-9 or nb < 1e-9:
        return 0.0
    return round(dot / (na * nb), 4)


def token_overlap(query_tokens, archive_tokens):
    """情绪词元相似度：共同词元的加权重合 / 查询总权重 ∈ [0,1]。"""
    q = {t["text"]: float(t.get("strength", 1.0) or 1.0) for t in (query_tokens or [])}
    a = {t["text"]: float(t.get("strength", 1.0) or 1.0) for t in (archive_tokens or [])}
    qw = sum(q.values())
    if qw <= 0:
        return 0.0
    common = sum(min(q[k], a[k]) for k in q if k in a)
    return round(common / qw, 4)


def _event_delta(record):
    """只读取一条 committed event 已经固化的六维变化，不补造旧档。"""
    if not isinstance(record, dict):
        return None
    imprint = record.get("state_imprint")
    if not isinstance(imprint, dict) or "Delta_X_event" not in imprint:
        return None
    try:
        return root_snapshot(imprint["Delta_X_event"])
    except (TypeError, ValueError):
        return None


def _positive_directional_similarity(left, right):
    """同向历史变化的向量相关；反向或无印痕不借时间伪造关联。"""
    if left is None or right is None:
        return 0.0
    value = cosine(tuple(left[axis] for axis in ROOT_ORDER),
                   tuple(right[axis] for axis in ROOT_ORDER))
    return max(0.0, float(value))


def _vector_magnitude(vector):
    values = root_snapshot(vector)
    return math.sqrt(sum(float(values[axis]) ** 2 for axis in ROOT_ORDER))


def _ratio_similarity(left, right):
    """Symmetric 0..1 magnitude/interval similarity without a hidden direction."""
    a, b = abs(float(left)), abs(float(right))
    if a <= 1e-12 and b <= 1e-12:
        return 1.0
    if a <= 1e-12 or b <= 1e-12:
        return 0.0
    return min(a, b) / max(a, b)


def _subtract_vectors(left, right):
    a, b = root_snapshot(left), root_snapshot(right)
    return {axis: a[axis] - b[axis] for axis in ROOT_ORDER}


def _trajectory_scope(record):
    """Return the immutable owner/relation scope already attached to an event."""
    runtime = record.get("relationship_runtime") if isinstance(record, dict) else None
    relation = runtime.get("relation_key") if isinstance(runtime, dict) else None
    if isinstance(relation, dict):
        owner = str(relation.get("owner_participant_id") or "").strip()
        peer = str(relation.get("counterparty_participant_id") or "").strip()
        if owner and peer and owner != peer:
            return ("relationship", owner, peer)
    return ("actor_store",)


def _committed_trajectory_chain(records, *, now_ms, anchor_record):
    """Build an in-memory O(N) event spine; never persists trajectory snapshots."""
    scope = _trajectory_scope(anchor_record)
    rows, seen = [], set()
    for ordinal, record in enumerate(records or ()):
        if not isinstance(record, dict) or _trajectory_scope(record) != scope:
            continue
        event_id = str(record.get("event_id") or "").strip()
        delta = _event_delta(record)
        try:
            ts = int(record.get("ts", 0))
        except (TypeError, ValueError):
            continue
        if not event_id or event_id in seen or delta is None or ts > int(now_ms):
            continue
        seen.add(event_id)
        rows.append({"event_id": event_id, "ts": ts, "delta": delta,
                     "ordinal": ordinal})
    rows.sort(key=lambda row: (row["ts"], row["ordinal"], row["event_id"]))
    return tuple(rows)


def _causal_trajectory_readout(chain, *, history_event_id, current_event_id, chain_index=None):
    """Compare causal trigger paths and retain historical aftermath as evidence.

    The current side ends at ``current_event_id`` and therefore never reads a
    future event.  A historical next event may be used only because it is already
    committed by the evaluation time.  Every scalar below is derived from exact
    six-dimensional event deltas and wall-clock intervals and is receipt-visible.
    """
    index = (chain_index if isinstance(chain_index, dict) else
             {row["event_id"]: offset for offset, row in enumerate(chain)})
    history_index = index.get(str(history_event_id or ""))
    current_index = index.get(str(current_event_id or ""))
    if history_index is None or current_index is None:
        return None
    history = chain[history_index]
    current = chain[current_index]
    history_prev = chain[history_index - 1] if history_index > 0 else None
    history_next = chain[history_index + 1] if history_index + 1 < len(chain) else None
    current_prev = chain[current_index - 1] if current_index > 0 else None

    center_direction = _positive_directional_similarity(
        history["delta"], current["delta"])
    center_magnitude = _ratio_similarity(
        _vector_magnitude(history["delta"]), _vector_magnitude(current["delta"]))
    trigger_components = {
        "center_direction": center_direction,
        "center_magnitude": center_magnitude,
    }
    if history_prev is not None and current_prev is not None:
        trigger_components.update({
            "previous_direction": _positive_directional_similarity(
                history_prev["delta"], current_prev["delta"]),
            "previous_magnitude": _ratio_similarity(
                _vector_magnitude(history_prev["delta"]),
                _vector_magnitude(current_prev["delta"])),
            "turn_direction": _positive_directional_similarity(
                _subtract_vectors(history["delta"], history_prev["delta"]),
                _subtract_vectors(current["delta"], current_prev["delta"])),
            "interval_similarity": _ratio_similarity(
                history["ts"] - history_prev["ts"],
                current["ts"] - current_prev["ts"]),
        })
    trigger_similarity = sum(trigger_components.values()) / len(trigger_components)

    aftermath = None
    if history_next is not None and history_next["event_id"] != current_event_id:
        signed_alignment = cosine(
            tuple(history["delta"][axis] for axis in ROOT_ORDER),
            tuple(history_next["delta"][axis] for axis in ROOT_ORDER))
        continuation_ratio = min(1.0, _vector_magnitude(history_next["delta"]) /
                                 max(_vector_magnitude(history["delta"]), 1e-12))
        # 0.5 is neutral: an aligned committed aftermath raises persistence and
        # an opposing aftermath lowers it by the same bounded amount.
        aftermath = max(0.0, min(1.0,
            0.5 + 0.5 * float(signed_alignment) * continuation_ratio))
    trajectory_similarity = (trigger_similarity if aftermath is None else
                             (trigger_similarity + aftermath) / 2.0)

    history_ids = [row["event_id"] for row in
                   (history_prev, history, history_next) if row is not None]
    history_times = [row["ts"] for row in
                     (history_prev, history, history_next) if row is not None]
    current_ids = [row["event_id"] for row in
                   (current_prev, current) if row is not None]
    current_times = [row["ts"] for row in
                     (current_prev, current) if row is not None]
    return {
        "version": "causal-memory-trajectory-v1",
        "center_direction_similarity": center_direction,
        "trigger_similarity": trigger_similarity,
        "aftermath_persistence": aftermath,
        "trajectory_similarity": trajectory_similarity,
        "trigger_components": trigger_components,
        "history_anchor_event_ids": history_ids,
        "current_anchor_event_ids": current_ids,
        "history_intervals_ms": [right - left for left, right in
                                 zip(history_times, history_times[1:])],
        "current_intervals_ms": [right - left for left, right in
                                 zip(current_times, current_times[1:])],
        "current_future_read": False,
        "persisted_snapshot": False,
        "derived_only": True,
    }

def _state_imprint(state_before=None, event_delta=None, state_after=None,
                   state_version=None):
    """验证一条已提交事件可供未来 recall 使用的六轴状态印痕。

    P4 不能为了让旧档案“看起来支持状态 recall”而补造变化值。因此三个快照
    必须同时由已提交事件的结算边界传入，或全部缺省并明确保留为旧档兼容记录。
    这里仅保存事实；不选择记忆、不计算唤起强度、也不写当前状态。
    """
    supplied = (state_before is not None, event_delta is not None, state_after is not None)
    if any(supplied) and not all(supplied):
        raise ValueError("状态印痕必须同时提供 X_before、Delta_X_event、X_after")
    if not any(supplied):
        return None
    before = root_snapshot(state_before)
    after = root_snapshot(state_after)
    delta = root_snapshot(event_delta)
    for axis in ROOT_ORDER:
        expected = before[axis] + delta[axis]
        if not math.isclose(after[axis], expected, rel_tol=0.0, abs_tol=1e-8):
            raise ValueError(f"状态印痕 {axis} 不满足 X_after = X_before + Delta_X_event")
    return {
        "version": str(state_version or "interaction-state-imprint-v1"),
        "X_before": before,
        "Delta_X_event": delta,
        "X_after": after,
    }


def recall_family(tokens):
    """回忆短语族：取最强词元经 lexicon 匹配的族（无命中 → info_calm）。"""
    from temporal.lexicon import match_rule
    best = None
    for t in tokens or []:
        family = match_rule(t).get("family", "none")
        if family != "none":
            strength = float(t.get("strength", 1.0) or 1.0)
            if best is None or strength > best[1]:
                best = (family, strength)
    return best[0] if best else "info_calm"


# ---------------------------------------------------------------------
# 双索引记忆
# ---------------------------------------------------------------------
class Memory:
    """绑定一个 UserStore 的全量记忆（零 LLM，embedding 由外部注入）。"""

    def __init__(self, store, params=None, rng=None):
        self.store = store
        self.params = params or MEMORY
        self.rng = rng if rng is not None else random.Random()

    # ── 语义向量库 ──
    def remember_message(self, ts, role, text, S=0.0, tokens=None, emb=None,
                         delay_ms=None, blocks=None, s_parts=None, reward=None,
                         source=None, params_sha=None, turn_id=None,
                         action_id=None, cause_id=None, seed_id=None,
                         event_id=None,
                         non_dialogue_seed=False, state_before=None,
                         event_delta=None, state_after=None, state_version=None,
                         conversation_provenance=None, conversation_context=None,
                         group_visibility_provenance=None,
                         participant_source_kind=None, preference_evidence=None,
                         test_manual_responder=None, output_identity=None):
        """全量落盘一条消息（emb 为外部 embedding 函数的结果，可缺省）。

        s_parts / reward：显著性分量与响应强度 R 的观察样本，供外环对账；R-only
        不得直接用于参数更新。
        source（A1）：NPC 消息来源（reply/proactive/followup/share/replay），
        调动结果标签器靠它区分"自主消息"与"回复"。
        params_sha（B3）：标定区参数版本——旧数据在新参数下自动失效（承 MANE 纪律）。
        """
        rec = {"ts": ts, "role": role, "text": text, "S": S, "tokens": tokens or []}
        imprint = _state_imprint(state_before, event_delta, state_after, state_version)
        if imprint is not None:
            # 只在正式 message append 时固化；recall 以后只能读取这份历史印痕，
            # 不能根据今天的状态反写或补全它。
            rec["state_imprint"] = imprint
        if source is not None:
            rec["source"] = source
        if params_sha is not None:
            rec["params_sha"] = params_sha
        if turn_id is not None:
            rec["turn_id"] = str(turn_id)
        if action_id is not None:
            rec["action_id"] = str(action_id)
        if cause_id is not None:
            rec["cause_id"] = str(cause_id)
        if seed_id is not None:
            rec["seed_id"] = str(seed_id)
        if event_id is None:
            # 产品 F03/F16 已各自拥有不可变 turn/action 身份。这里仅把那份既有
            # committed receipt 公开给记忆索引，不新造另一条调度 ID。
            if action_id is not None:
                event_id = "action:" + str(action_id)
            elif turn_id is not None:
                event_id = "turn:" + str(turn_id)
        if event_id is not None:
            normalized_event_id = str(event_id).strip()
            if not normalized_event_id:
                raise ValueError("committed event_id 不能为空")
            # archive/semantic 视图只能以这条已提交事件身份聚合；不得以时间邻近
            # 或正文相似度猜测两个记录是否同源。
            rec["event_id"] = normalized_event_id
        if non_dialogue_seed:
            # 历史导入属于记忆层事实，不是聊天短尾。显式事实位优先于来源字符串，
            # 以免新来源遗漏黑名单后把背景原文绕过 recall 直接喂给 writer。
            rec["non_dialogue_seed"] = True
        if conversation_provenance is not None:
            if not isinstance(conversation_provenance, dict):
                raise ValueError("conversation provenance 必须为字典")
            required = {
                "event_id", "conversation_id", "delivery_id", "sender_participant_id",
                "membership_version", "recipient_participant_id",
                "visible_recipient_ids", "member_snapshot_ids",
            }
            if set(conversation_provenance) != required:
                raise ValueError("conversation provenance 字段不完整")
            normalized = {
                "event_id": str(conversation_provenance["event_id"]),
                "conversation_id": str(conversation_provenance["conversation_id"]),
                "delivery_id": str(conversation_provenance["delivery_id"]),
                "sender_participant_id": str(conversation_provenance["sender_participant_id"]),
                "membership_version": int(conversation_provenance["membership_version"]),
                "recipient_participant_id": str(conversation_provenance["recipient_participant_id"]),
                "visible_recipient_ids": [str(item) for item in conversation_provenance["visible_recipient_ids"]],
                "member_snapshot_ids": [str(item) for item in conversation_provenance["member_snapshot_ids"]],
            }
            if (not all(normalized[key] for key in (
                    "event_id", "conversation_id", "delivery_id", "sender_participant_id",
                    "recipient_participant_id"))
                    or normalized["membership_version"] < 1
                    or normalized["recipient_participant_id"] not in normalized["visible_recipient_ids"]):
                raise ValueError("conversation provenance 内容无效")
            rec["conversation_provenance"] = normalized
        if conversation_context is not None:
            if not isinstance(conversation_context, dict):
                raise ValueError("conversation context 必须为字典")
            required = {
                "conversation_id", "membership_version", "owner_participant_id",
                "sender_participant_id", "reply_to_participant_id",
                "caused_by_event_id", "caused_by_delivery_id",
                "visible_recipient_ids", "member_snapshot_ids",
            }
            if set(conversation_context) != required:
                raise ValueError("conversation context 字段不完整")
            normalized_context = {
                "conversation_id": str(conversation_context["conversation_id"]),
                "membership_version": int(conversation_context["membership_version"]),
                "owner_participant_id": str(conversation_context["owner_participant_id"]),
                "sender_participant_id": str(conversation_context["sender_participant_id"]),
                "reply_to_participant_id": str(conversation_context["reply_to_participant_id"]),
                "caused_by_event_id": str(conversation_context["caused_by_event_id"]),
                "caused_by_delivery_id": str(conversation_context["caused_by_delivery_id"]),
                "visible_recipient_ids": [str(item) for item in conversation_context["visible_recipient_ids"]],
                "member_snapshot_ids": [str(item) for item in conversation_context["member_snapshot_ids"]],
            }
            if (not all(normalized_context[key] for key in (
                    "conversation_id", "owner_participant_id", "sender_participant_id",
                    "reply_to_participant_id", "caused_by_event_id", "caused_by_delivery_id"))
                    or normalized_context["membership_version"] < 1
                    or normalized_context["owner_participant_id"] not in normalized_context["member_snapshot_ids"]
                    or normalized_context["reply_to_participant_id"] not in normalized_context["member_snapshot_ids"]
                    or normalized_context["reply_to_participant_id"] == normalized_context["owner_participant_id"]
                    or normalized_context["sender_participant_id"] != normalized_context["owner_participant_id"]):
                raise ValueError("conversation context 内容无效")
            # context 是 own F16 的会话归属，不是一条伪造的 incoming delivery。
            rec["conversation_context"] = normalized_context
        if output_identity is not None:
            if not isinstance(output_identity, dict):
                raise ValueError("output identity 必须为字典")
            participant_id = str(output_identity.get("participant_id") or "").strip()
            actor_kind = str(output_identity.get("actor_kind") or "").strip().lower()
            if (set(output_identity) != {"participant_id", "actor_kind"}
                    or not participant_id
                    or actor_kind not in {"human", "npc", "simulation"}):
                raise ValueError("output identity 内容无效")
            # 这是已提交 own F16 的归属，不是 input delivery，也不参与记忆选择。
            rec["output_identity"] = {
                "participant_id": participant_id,
                "actor_kind": actor_kind,
            }
        if group_visibility_provenance is not None:
            if not isinstance(group_visibility_provenance, dict):
                raise ValueError("group_visibility_provenance 必须为字典")
            required = {
                "event_id", "conversation_id", "membership_version",
                "owner_participant_id", "sender_participant_id",
                "member_snapshot_ids", "visible_recipient_ids",
                "addressed_participant_ids",
            }
            if set(group_visibility_provenance) != required:
                raise ValueError("group visibility provenance 字段不完整")
            normalized_visibility = {
                "event_id": str(group_visibility_provenance["event_id"]),
                "conversation_id": str(group_visibility_provenance["conversation_id"]),
                "membership_version": int(group_visibility_provenance["membership_version"]),
                "owner_participant_id": str(group_visibility_provenance["owner_participant_id"]),
                "sender_participant_id": str(group_visibility_provenance["sender_participant_id"]),
                "member_snapshot_ids": [str(item) for item in group_visibility_provenance["member_snapshot_ids"]],
                "visible_recipient_ids": [str(item) for item in group_visibility_provenance["visible_recipient_ids"]],
                "addressed_participant_ids": [str(item) for item in group_visibility_provenance["addressed_participant_ids"]],
            }
            if (not all(normalized_visibility[key] for key in (
                    "event_id", "conversation_id", "owner_participant_id",
                    "sender_participant_id"))
                    or normalized_visibility["membership_version"] < 1
                    or normalized_visibility["owner_participant_id"] not in normalized_visibility["member_snapshot_ids"]
                    or normalized_visibility["sender_participant_id"] not in normalized_visibility["member_snapshot_ids"]
                    or any(item not in normalized_visibility["member_snapshot_ids"]
                           for item in normalized_visibility["addressed_participant_ids"])):
                raise ValueError("group visibility provenance 内容无效")
            rec["group_visibility_provenance"] = normalized_visibility
        if participant_source_kind is not None:
            kind = str(participant_source_kind).strip().lower()
            if kind not in {"human", "npc", "simulation"}:
                raise ValueError("participant_source_kind 无效")
            rec["participant_source_kind"] = kind
        if preference_evidence is not None:
            if not isinstance(preference_evidence, bool):
                raise ValueError("preference_evidence 必须为布尔值")
            rec["preference_evidence"] = preference_evidence
        if test_manual_responder is not None:
            if not isinstance(test_manual_responder, dict):
                raise ValueError("test_manual_responder 必须为字典")
            required = {"schema_version", "kind", "origin", "session_id", "request_id", "text_sha256"}
            if set(test_manual_responder) != required:
                raise ValueError("test_manual_responder 字段不完整")
            normalized_manual = {
                "schema_version": int(test_manual_responder["schema_version"]),
                "kind": str(test_manual_responder["kind"]),
                "origin": str(test_manual_responder["origin"]),
                "session_id": str(test_manual_responder["session_id"]),
                "request_id": str(test_manual_responder["request_id"]),
                "text_sha256": str(test_manual_responder["text_sha256"]),
            }
            if (normalized_manual["schema_version"] != 1
                    or normalized_manual["kind"] != "test_manual_responder_consumed"
                    or normalized_manual["origin"] != "test_manual_responder"
                    or not normalized_manual["session_id"].startswith("test:")
                    or not normalized_manual["request_id"]
                    or len(normalized_manual["text_sha256"]) != 64):
                raise ValueError("test_manual_responder 内容无效")
            rec["test_manual_responder"] = normalized_manual
        if emb is not None:
            rec["emb"] = [float(x) for x in emb]
        if delay_ms is not None:
            rec["delay_ms"] = float(delay_ms)
        if blocks:
            rec["blocks"] = list(blocks)
        if s_parts:
            rec["S_parts"] = dict(s_parts)
        if reward is not None:
            rec["R"] = float(reward)
        self.store.append_message(rec)
        return rec

    def semantic_search(self, query_vec, top_k=None):
        """语义检索（传统 RAG 路径）：暴力余弦，Top-K。"""
        k = top_k or self.params.get("semantic_top_k", 3)
        scored = []
        for rec in self.store.read_messages():
            if "emb" not in rec:
                continue
            scored.append((cosine(query_vec, rec["emb"]), rec))
        scored.sort(key=lambda x: -x[0])
        return [{"score": s, "record": r} for s, r in scored[:k]]

    def committed_event(self, event_id):
        """按精确 identity 读取 canonical committed message（纯读）。

        这是 Ledger/semantic annotation 回到其所属事件的唯一入口；不得以时间、
        正文或 source 做最近邻补全。返回副本避免调用方修改 Store 读取结果。
        """
        target = str(event_id or "").strip()
        if not target:
            return None
        for record in reversed(self.store.read_messages()):
            if str(record.get("event_id") or "").strip() == target:
                return dict(record)
        return None

    def committed_event_id_for_turn(self, turn_id):
        """从已提交 message 的精确 turn identity 读取其 event identity（纯读）。

        F10 不能用正文、时间邻近或表达词元猜测“当前事件”；direct 输入已经提交后，
        Runner 只能由同一 ``turn_id`` 找回这条 canonical event。缺失或歧义都返回空，
        不补造 identity。
        """
        target = str(turn_id or "").strip()
        if not target:
            return ""
        ids = {str(record.get("event_id") or "").strip()
               for record in self.store.read_messages()
               if str(record.get("turn_id") or "").strip() == target
               and str(record.get("event_id") or "").strip()}
        return next(iter(ids)) if len(ids) == 1 else ""

    def interaction_candidates(self, *, now_ms, anchor_event_id,
                               include_anchor, temporal_readout):
        """F10 的 memory-local committed-event 候选。

        此入口有意不接受 token、文本、embedding、词元类别或 writer 材料。它只读取
        exact committed identity、已经固化的六维事件印痕与同一份墙钟 readout。archive
        是同一 event 的测量镜像，绝不形成第二条候选。
        """
        anchor_id = str(anchor_event_id or "").strip()
        anchor = self.committed_event(anchor_id)
        anchor_delta = _event_delta(anchor)
        if not anchor_id or anchor is None or anchor_delta is None:
            return []

        messages = tuple(self.store.read_messages())
        trajectory_chain = _committed_trajectory_chain(
            messages, now_ms=now_ms, anchor_record=anchor)
        trajectory_index = {row["event_id"]: offset
                            for offset, row in enumerate(trajectory_chain)}
        groups = {}

        def offer(view, record):
            if not isinstance(record, dict):
                return
            event_id = str(record.get("event_id") or "").strip()
            if not event_id:
                return
            ts = int(record.get("ts", 0) or 0)
            # direct current input 可作为 relation anchor，但不能又以 memory card
            # 重复送入 writer；proactive anchor 则已经早于当前 tick，可显式保留。
            if ts >= int(now_ms) and event_id != anchor_id:
                return
            trajectory_readout = None
            relation_source = "event_delta_cosine"
            if event_id == anchor_id:
                if not include_anchor or ts >= int(now_ms):
                    return
                relation = 1.0
                continuity = False
            else:
                delta = _event_delta(record)
                if delta is None:
                    return
                center_relation = _positive_directional_similarity(anchor_delta, delta)

                trajectory_readout = _causal_trajectory_readout(
                    trajectory_chain, history_event_id=event_id,
                    current_event_id=anchor_id, chain_index=trajectory_index)
                relation = (float(trajectory_readout["trajectory_similarity"])
                            if trajectory_readout is not None else center_relation)
                relation_source = ("causal_memory_trajectory_v1"
                                   if trajectory_readout is not None
                                   else "event_delta_cosine")
                continuity = True
            current = groups.setdefault(event_id, {
                "canonical_event_id": event_id,
                "record": record,
                "relation": float(relation),
                "temporal_continuity": bool(continuity),
                "memory_relation_source": relation_source,
                "trajectory_readout": trajectory_readout,
                "views": {},
            })
            current["views"][view] = record
            if float(relation) > float(current["relation"]):
                current["relation"] = float(relation)
                current["memory_relation_source"] = relation_source
                current["trajectory_readout"] = trajectory_readout
            current["temporal_continuity"] = bool(
                current["temporal_continuity"] or continuity)
            # message 是 canonical 对话事实；archive 只能补同 event 的测量视图。
            if view == "message" or "text" not in current["record"]:
                current["record"] = record

        for record in messages:
            offer("message", record)
        for record in self.store.read_archive():
            if "recall_of_ts" not in record:
                offer("archive", record)

        candidates = []
        for group in groups.values():
            record = group["record"]
            readout = temporal_readout(record)
            if not isinstance(readout, dict):
                raise ValueError("memory temporal readout 必须返回字典")
            try:
                retention = float(readout["age_retention"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("memory candidate 缺少有效 age_retention") from exc
            if not 0.0 <= retention <= 1.0:
                raise ValueError("memory temporal age_retention 必须在 0 到 1 之间")
            relation = float(group["relation"])
            candidates.append({
                "score": relation,
                "record": record,
                "canonical_event_id": group["canonical_event_id"],
                "exact_event_reference": group["canonical_event_id"] == anchor_id,
                "temporal_continuity": bool(group["temporal_continuity"]),
                "memory_relation_source": str(group["memory_relation_source"]),
                "temporal_readout": readout,
                "selection_utility": relation * retention,
                "mirror_sources": tuple(view for view in ("message", "archive")
                                        if view in group["views"]),
                **({
                    "center_direction_similarity": float(group["trajectory_readout"]["center_direction_similarity"]),
                    "trajectory_similarity": float(group["trajectory_readout"]["trajectory_similarity"]),
                    "trajectory_receipt": dict(group["trajectory_readout"]),
                } if isinstance(group.get("trajectory_readout"), dict) else {}),
            })
        candidates.sort(key=lambda item: (-float(item["selection_utility"]),
                                          -float(item["score"]),
                                          -int(item["record"].get("ts", 0) or 0),
                                          str(item["canonical_event_id"])))
        return candidates

    # ── 情绪词元库 ──
    def archive_tokens(self, ts, tokens, S=0.0, drift=None, source=None, seed_id=None,
                       state_before=None, event_delta=None, state_after=None,
                       state_version=None, event_id=None, turn_id=None,
                       action_id=None):
        """归档结束词元（强度+时间戳+漂移参数+S）。

        archive 是 F10 的真实候选来源。因此当、且仅当调用方持有同一已提交事件的
        完整状态 receipt 时，archive 也要保存同源状态印痕。不能从当前状态、词元或
        writer 反推；历史 seed / 兼容行仍保持无状态资格的旧形状。

        ``source/seed_id`` 仅供隔离测试夹具标示合成来源；生产调用不传状态 receipt 时
        维持旧行形状。
        """
        rec = {"ts": ts, "tokens": tokens or [], "S": S, "drift": drift or {}}
        imprint = _state_imprint(state_before, event_delta, state_after, state_version)
        if imprint is not None:
            rec["state_imprint"] = imprint
        if source is not None:
            rec["source"] = str(source)
        if seed_id is not None:
            rec["seed_id"] = str(seed_id)
        if event_id is None:
            if action_id is not None:
                event_id = "action:" + str(action_id)
            elif turn_id is not None:
                event_id = "turn:" + str(turn_id)
        if event_id is not None:
            normalized_event_id = str(event_id).strip()
            if not normalized_event_id:
                raise ValueError("committed event_id 不能为空")
            rec["event_id"] = normalized_event_id
        self.store.append_archive(rec)
        return rec

    def emotional_candidates(self, query_tokens, now_ms, top_k=None,
                             temporal_readout=None, exact_event_ids=(),
                             exact_query_tokens=(),
                             temporal_continuity_anchor_event_id=None):
        """从已提交 event 的 message/archive 视图读取情绪候选。

        ``messages.jsonl`` 是已发生互动的 canonical event 入口：F03/F16 message
        即使暂时没有 archive 镜像，也已经是未来可检索的记忆。archive 只保存同一
        event 的词元／显著性观测。两者只有带完全相同的 ``event_id`` 时才合并；旧行
        各自保留，绝不以 ts、文本或来源猜测同一性。

        真实年龄的统一 readout 会在后续 F10/P4 共用的切片发布。本函数只恢复
        canonical event 候选身份，不提前改变选择、P4 或 writer 资格。
        """
        # ``top_k=None`` 保持旧的普通词元检索容量；只有明确给出一个已提交的
        # current anchor 时，才开放同一条 Store 内的近期连续互动候选。后者不是
        # raw tail，也不是按条数取最近消息：每条记录仍保留自己的 identity、年龄与
        # 六维 state imprint，并在 F10 之后与其它候选同样排序。
        continuity_anchor_id = str(temporal_continuity_anchor_event_id or "").strip()
        continuity_anchor = (self.committed_event(continuity_anchor_id)
                             if continuity_anchor_id else None)
        continuity_delta = _event_delta(continuity_anchor)
        k = (None if continuity_delta is not None else
             (top_k or self.params.get("semantic_top_k", 3)))
        # ``exact_event_ids`` 不是第二个记忆池，也不是最近消息尾窗。它只允许
        # 调用方拿已经提交、已持久化的 event identity 回到本 Store 读取同一条
        # 记录。普通候选继续排除常驻人格词元；仅这个精确事件可用它自己的原始
        # 查询词元证明“当前因果锚”与已归档事实相同，避免人格常驻词元泛化命中
        # 任意旧事。
        exact_ids = {str(event_id).strip() for event_id in (exact_event_ids or ())
                     if str(event_id).strip()}
        exact_tokens = tuple(exact_query_tokens or ())
        groups, legacy = {}, []

        def offer(view, rec):
            if int(rec.get("ts", 0) or 0) >= int(now_ms):
                # 当前输入可在 responder 前先落为 committed message，但它不是“过去
                # 的记忆”。同毫秒并发时也保守排除，不能靠来源或正文猜先后。
                return
            event_id = str(rec.get("event_id") or "").strip()
            score = token_overlap(query_tokens, rec.get("tokens") or [])
            exact_event_reference = False
            if score <= 0 and event_id in exact_ids:
                score = token_overlap(exact_tokens, rec.get("tokens") or [])
                exact_event_reference = score > 0
            temporal_continuity = False
            if score <= 0 and continuity_delta is not None:
                # 近期互动的连续性来自同一套已提交六维变化：历史 event 的
                # Delta_X_event 与当前因果锚的 Delta_X_event 同向，才有资格越过
                # “词元恰好重合”的旧过滤。时间权重仍只由统一 temporal readout
                # 产生；这里既不看消息条数，也不制造另一份上下文。
                relation = _positive_directional_similarity(
                    continuity_delta, _event_delta(rec))
                if relation > 0.0:
                    score = relation
                    temporal_continuity = True
            if score <= 0:
                return
            if not event_id:
                legacy.append({"score": score, "record": rec,
                               "mirror_sources": (view,)})
                return
            group = groups.setdefault(event_id, {
                "canonical_event_id": event_id,
                "score": 0.0,
                "record": rec,
                "views": {},
                "temporal_continuity": False,
            })
            group["views"][view] = rec
            group["score"] = max(float(group["score"]), float(score))
            group["exact_event_reference"] = bool(
                group.get("exact_event_reference") or exact_event_reference)
            group["temporal_continuity"] = bool(
                group.get("temporal_continuity") or temporal_continuity)
            # message 是事件正文与 F03/F16 state imprint 的 canonical view；archive
            # 只补同一 event 的词元测量，不能取代已提交对话事实。
            if view == "message" or "text" not in group["record"]:
                group["record"] = rec

        for rec in self.store.read_messages():
            offer("message", rec)
        for rec in self.store.read_archive():
            if "recall_of_ts" not in rec:
                offer("archive", rec)

        def with_temporal(item):
            if temporal_readout is None:
                return item
            readout = temporal_readout(item["record"])
            if not isinstance(readout, dict):
                raise ValueError("memory temporal readout 必须返回字典")
            try:
                retention = float(readout["age_retention"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("memory temporal readout 缺少 age_retention") from exc
            if not 0.0 <= retention <= 1.0:
                raise ValueError("memory temporal age_retention 必须在 0 到 1 之间")
            # `relevance` 仍是当下关联，供 P4 读取；年龄只生成选择排序的独立
            # utility，避免 P4 将同一衰减再乘一次。
            item["temporal_readout"] = readout
            item["selection_utility"] = float(item["score"]) * retention
            return item

        out = [with_temporal(item) for item in legacy]
        for group in groups.values():
            views = group["views"]
            out.append(with_temporal({
                "score": float(group["score"]),
                "record": group["record"],
                "canonical_event_id": group["canonical_event_id"],
                "exact_event_reference": bool(group.get("exact_event_reference")),
                "temporal_continuity": bool(group.get("temporal_continuity")),
                "mirror_sources": tuple(
                    view for view in ("message", "archive") if view in views),
            }))
        out.sort(key=lambda item: (-float(item.get("selection_utility", item["score"])),
                                   -float(item["score"]),
                                   -int(item["record"].get("ts", 0) or 0),
                                   str(item.get("canonical_event_id") or "")))
        return out if k is None else out[:k]

    def select_recall(self, query_tokens, now_ms, mode=None, p=None):
        """只选择一次用户侧唤起，不写 archive。

        主回复是可被下一次 Enter 废弃的事务。选择仍在生成 prompt 前完成（因此
        保持原有随机抽样和本轮上下文），但把冷却账本的提交留给 Runner 的提交闸。
        ``maybe_recall`` 继续是兼容旧调用方的“选择后立即提交”封装。
        """
        pref = None
        if mode:
            from ops.recall_gate import preferred_families
            pref = preferred_families(mode)
        cands = self.emotional_candidates(query_tokens, now_ms, top_k=6 if pref else 1)
        if not cands:
            return None, ""
        if pref:
            def key(c):
                fam = recall_family(c["record"].get("tokens") or [])
                return (0 if fam in pref else 1, -c["score"])
            cands = sorted(cands, key=key)
        best = cands[0]
        p = p if p is not None else self.params["recall_p_trigger"]
        if self.rng.random() > p:
            return None, ""
        rec = best["record"]
        return rec, self.recall_hint(rec, mode=mode)

    def recall_hint(self, rec, mode=None):
        """把已选记录转换为既有提示措辞；不产生任何持久化副作用。"""
        family = recall_family(rec.get("tokens") or [])
        if mode and mode != "mixed":
            from ops.recall_gate import hint_for
            return hint_for(mode, family)
        return RECALL_PHRASES.get(family, RECALL_PHRASES["info_calm"])

    def maybe_recall(self, query_tokens, now_ms, mode=None, p=None):
        """兼容入口：选择后立刻提交一次用户侧记忆唤起。"""
        rec, hint = self.select_recall(query_tokens, now_ms, mode=mode, p=p)
        if rec is None:
            return None, ""
        self.commit_recall(rec, now_ms, mode=mode)
        return rec, hint

    def commit_recall(self, rec, now_ms, mode=None):
        """提交一次已由外部确定选择的用户侧记忆唤起。

        P3 统一裁决使用此接口；它保留 append-only 账本与既有提示措辞，选择算法仍由
        调用方负责。该方法不抽样、不读取候选，因此不会引入额外随机性。
        """
        family = recall_family(rec.get("tokens") or [])
        receipt = {"ts": now_ms, "recall_of_ts": rec["ts"], "family": family}
        # 新 committed event 的 recall 轨迹必须沿用同一 exact identity。保留
        # ``recall_of_ts`` 仅为旧档可读性和时间审计；后续读取不得以它为现代事件
        # 的身份猜测入口。
        event_id = str(rec.get("event_id") or "").strip()
        if event_id:
            receipt["recall_of_event_id"] = event_id
        self.store.append_archive(receipt)
        return rec, self.recall_hint(rec, mode=mode)

    def recall_trajectory_history(self, selected_records, now_ms):
        """只读整理 selected archive 记忆此前的真实唤起轨迹。

        P4 的轨迹增益不能把候选出现、prompt 投递或本轮尚未提交的选择当成“想起过”。
        archive 中 `recall_of_ts` 行才是既成事实；本方法以本轮 `now_ms` 为严格上界，
        因而当前将要提交的 recall 不会提前影响自身。返回值只包含离散引用和计数，
        没有正文、词元或状态写权限。
        """
        try:
            now = int(now_ms)
        except (TypeError, ValueError) as exc:
            raise ValueError("now_ms 必须是整数时间戳") from exc
        selected, legacy_by_ts = {}, {}
        for source, record in tuple(selected_records or ()):
            if str(source) != "memory" or not isinstance(record, dict):
                continue
            try:
                record_ts = int(record["ts"])
            except (KeyError, TypeError, ValueError) as exc:
                raise ValueError("selected memory record 缺少有效 ts") from exc
            event_id = str(record.get("event_id") or "").strip()
            ref = event_id or "memory:%d" % record_ts
            selected[ref] = record_ts
            if not event_id:
                legacy_by_ts[record_ts] = ref
        counts = {ref: 0 for ref in selected}
        for row in self.store.read_archive():
            if not isinstance(row, dict) or "recall_of_ts" not in row:
                continue
            try:
                ts = int(row["ts"])
                recalled_ts = int(row["recall_of_ts"])
            except (KeyError, TypeError, ValueError):
                continue
            recalled_event_id = str(row.get("recall_of_event_id") or "").strip()
            if recalled_event_id:
                ref = recalled_event_id
            else:
                # 历史 recall 账本没有 event_id 时只能服务当前也明确为 legacy 的
                # selection；绝不能用 ts 给现代 committed event 猜补身份。
                ref = legacy_by_ts.get(recalled_ts)
                if ref is None:
                    continue
            if ref in counts and ts < now:
                counts[ref] += 1
        return {
            ref: {
                "version": "archive-recall-trajectory-v1",
                "prior_actual_recalls": count,
                "as_of_ms": now,
                "derived_only": True,
            }
            for ref, count in counts.items()
        }
