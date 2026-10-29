"""responder 提示词上下文装配。

普通用户回合与 NPC 主动输出的触发语义不同，但二者都必须通过同一套骨架、人格、
预算和表达素材边界。这里不决定“是否主动开口”；那仍是墙钟/Actor 的职责。
"""
from dataclasses import dataclass
from collections.abc import Mapping

from agents.responder import WriterCard, compose_responder_messages
from core.params import LEDGER
from ops.deny import detect_denial
from ops.embeddings import has_exact_ngram_overlap
from ops.length_mode import length_mode, memory_supplement
from ops.persona_pack import assemble_persona_blocks
from ops.response_pack import assemble_response_blocks
from interaction_extensions.preference_calibration.feedback import (
    expression_feedback_context,
)
from ops.reference_frame import referent_delivery_gate
from ops.conversations import ConversationFrame
from ops.memory_card_attention import MemoryCardAttentionOccupation
from ops.memory_contracts import MemoryCard


@dataclass(frozen=True)
class TurnPrompt:
    """一次普通用户回合调用 responder 所需的完整输入与观测标签。"""

    system: str
    user: str
    response_block_ids: list
    persona_block_ids: list
    length_mode: str
    recall_event_ids: tuple = ()
    excluded_ledger_ts: tuple = ()
    recall_expression_events: tuple = ()
    history_sources: tuple = ()
    history_exclusions: tuple = ()
    kept_persona_block_ids: tuple = ()
    kept_response_block_ids: tuple = ()
    dropped_persona_block_ids: tuple = ()
    dropped_response_block_ids: tuple = ()
    dropped_recall_event_ids: tuple = ()
    expression_feedback: dict = None
    assembly_receipt: dict = None


def _unselected_ledger_exclusions(ledger, now_ms, selected_events):
    """未选台账只留来源收据，绝不作为 self_facts 绕过召回选择。"""
    if not hasattr(ledger, "top_recent_records"):
        return ()
    selected_ts = {int(event.record_ts) for event in (selected_events or ())
                   if getattr(event, "source", None) == "ledger"}
    return tuple({"source": "ledger_unselected", "record_ts": int(record.get("ts", -1)),
                  "reason": "requires_recall_selection"}
                 for record in ledger.top_recent_records(now=now_ms)
                 if int(record.get("ts", -1)) not in selected_ts)


def relative_time_label(record_ts, now_ms):
    """将既有事件时间戳转成表达可用的自然时间感，不参与任何选择或状态更新。"""
    elapsed = max(0, int(now_ms) - int(record_ts))
    hour_ms = 3_600_000
    day_ms = 24 * hour_ms
    if elapsed < hour_ms:
        return "刚才"
    if elapsed < day_ms:
        return "今天早些时候"
    days = elapsed // day_ms
    if days == 1:
        return "昨天"
    if days == 2:
        return "前两天"
    if days < 7:
        return "前几天"
    return "更早之前"


def recall_event_attribution(source):
    """来源归属是事件事实的解释，不是生成层的行为规则。"""
    return {
        "ledger": "这是你此前说过或承诺过的事",
        "semantic": "这是对方此前提过的事",
        "memory": "这是你们此前互动中留下的事实",
    }.get(str(source), "这是此前互动中的相关事实")


def format_recall_event_block(event, now_ms):
    """把已选事件格式化为带时间、speaker 归属的内容卡；不会增删事件。"""
    scope = str(getattr(event, "speaker_scope", "legacy_source") or "legacy_source")
    participant_id = str(getattr(event, "speaker_participant_id", "") or "").strip()
    if scope == "peer":
        speaker = "对方" + (("（participant:%s）" % participant_id) if participant_id else "")
        return ("【对方此前说过的话（仅作事实证据）】\n"
                "时间感：%s\n发言归属：%s\n对方原话：%s\n【事件审计标识：%s】" % (
                relative_time_label(event.record_ts, now_ms), speaker, event.text, event.event_id))
    if scope == "self":
        speaker = "当前角色" + (("（participant:%s）" % participant_id) if participant_id else "")
        return ("【当前角色此前说过的话（仅作事实证据）】\n"
                "时间感：%s\n发言归属：%s\n此前原话：%s\n【事件审计标识：%s】" % (
                relative_time_label(event.record_ts, now_ms), speaker, event.text, event.event_id))
    if scope == "shared_fact":
        return ("【已提交互动事实（仅作内在语境）】\n"
                "时间感：%s\n归属：共同经历（非任何人原话）\n事件：%s\n【事件审计标识：%s】" % (
                relative_time_label(event.record_ts, now_ms), event.text, event.event_id))
    if scope == "unknown":
        return ("【已提交互动记录（归属记录不完整）】\n"
                "时间感：%s\n发言归属：存档未提供；不得将其中第一人称视为当前角色台词\n"
                "记录正文：%s\n【事件审计标识：%s】" % (
                relative_time_label(event.record_ts, now_ms), event.text, event.event_id))
    # 旧夹具或无 provenance 的历史 event 保持原卡面，不能在读取时根据正文补造身份。
    return ("【共同经历卡（仅作内在语境）】\n"
            "时间感：%s\n归属：%s\n事件：%s\n【事件审计标识：%s】" % (
            relative_time_label(event.record_ts, now_ms),
                recall_event_attribution(event.source), event.text, event.event_id))


def recall_event_writer_card(event, now_ms, index, *, attention_selected=False):
    """只交事件字段；writer 卡面的中文由 responder 统一渲染。"""
    scope = str(getattr(event, "speaker_scope", "legacy_source") or "legacy_source")
    participant_id = str(getattr(event, "speaker_participant_id", "") or "").strip()
    if scope == "peer":
        speaker = "对方" + (("（participant:%s）" % participant_id) if participant_id else "")
        body_label = "原话"
    elif scope == "self":
        speaker = "当前角色" + (("（participant:%s）" % participant_id) if participant_id else "")
        body_label = "原话"
    elif scope == "shared_fact":
        speaker, body_label = "共同经历", "事件"
    elif scope == "unknown":
        speaker, body_label = "存档未提供", "事件"
    else:
        speaker, body_label = recall_event_attribution(event.source), "事件"
    return WriterCard(
        "committed_event", "recall", str(event.event_id),
        (("说话者", speaker), ("时间", relative_time_label(event.record_ts, now_ms)),
         (body_label, str(event.text)), ("事件标识", str(event.event_id))),
        priority=float(event.prompt_priority), group="recall", index=int(index),
        budget_policy=("preserve_attention_selection" if attention_selected else "normal"))


def memory_card_writer_card(card, now_ms, index):
    """把 C5 已后置水合的 MemoryCard 转成客观 writer 卡。

    该函数不接受 ID 引用或 canonical loader；正文读取必须已经在 C5 完成。
    所有卡共享同一技术 priority 并保留 caller 顺序，通用字符预算不得再次
    改选 attention 已占位的集合。
    """
    if not isinstance(card, MemoryCard):
        raise ValueError("card must be a hydrated MemoryCard")
    scope = card.speaker_scope
    participant_id = card.speaker_participant_id
    if scope == "peer":
        speaker = "对方" + (("（participant:%s）" % participant_id)
                          if participant_id else "")
        body_label = "原话"
    elif scope == "self":
        speaker = "当前角色" + (("（participant:%s）" % participant_id)
                              if participant_id else "")
        body_label = "原话"
    elif scope == "shared_fact":
        speaker, body_label = "共同经历", "事件"
    else:
        speaker, body_label = "存档未提供", "事件"
    return WriterCard(
        "committed_event", "memory_card", card.event_id,
        (("说话者", speaker),
         ("时间", relative_time_label(card.committed_at_ms, now_ms)),
         (body_label, card.text),
         ("事件标识", card.event_id)),
        priority=5.0,
        group="recall",
        index=int(index),
        budget_policy="preserve_attention_selection",
    )


def _memory_card_attention_events(events, plan):
    """在 recall 正文卡创建前按 attention receipt 过滤 exact IDs。"""
    original = tuple(events or ())
    if plan is None:
        return original, {
            "enabled": False,
            "reason": "legacy_family_or_unwired_shadow",
            "recalled_event_ids": [str(event.event_id) for event in original],
            "selected_for_hydration_ids": [str(event.event_id) for event in original],
        }
    if not isinstance(plan, MemoryCardAttentionOccupation):
        raise ValueError("memory_card_attention_plan must be MemoryCardAttentionOccupation")
    recalled = set(plan.recalled_event_ids)
    missing = [str(event.event_id) for event in original
               if str(event.event_id) not in recalled]
    if missing:
        raise ValueError("memory-card attention plan is missing recalled event IDs: "
                         + ", ".join(sorted(missing)))
    selected = set(plan.selected_for_hydration_ids)
    occupied = tuple(event for event in original if str(event.event_id) in selected)
    record = plan.to_record()
    record["enabled"] = True
    record["delivery_eligible_event_ids"] = [str(event.event_id) for event in original]
    record["occupied_event_ids_present_for_hydration"] = [
        str(event.event_id) for event in occupied]
    return occupied, record


def _persona_writer_cards(persona_blocks, details, block_ids):
    """把已选人格资料交为字段；不再把 section prose 直接拼进 writer。"""
    cards = []
    identity = str((persona_blocks or {}).get("身份", "") or "").strip()
    if identity:
        cards.append(WriterCard(
            "persona_fact", "persona", "identity",
            (("角色静态资料", identity),), priority=11.0))
    labels = {"语气": "角色表达特征", "口癖": "角色表达特征",
              "文风": "角色表达特征", "禁区": "角色公开边界"}
    for index, (block_id, detail) in enumerate(zip(block_ids or (), details or ())):
        key = str(block_id).split("·", 1)[0]
        label = labels.get(key)
        if label is None:
            # 打样对话不是已发生事件，也不是角色事实；保留 source 让 responder receipt
            # 明确拒绝它，不能把样句换壳送进 writer。
            cards.append(WriterCard("persona_sample", "persona", str(block_id),
                                    (("角色表达特征", str(detail)),),
                                    priority=6.0, group="persona", index=index))
            continue
        cards.append(WriterCard(
            "persona_fact", "persona", str(block_id), ((label, str(detail)),),
            priority=8.0, group="persona", index=index))
    return tuple(cards)


def _expression_writer_cards(plan, form_projection=None):
    """只消费已发布 descriptor receipt；不接收 expression lines 的预写 prose。"""
    cards = []
    for index, row in enumerate(tuple(getattr(plan, "receipt", ()) or ())):
        if str(row.get("delivery", "")) not in {
                "writer_material", "writer_current_root_state"}:
            continue
        text = str(row.get("text", "") or "").strip()
        if text:
            cards.append(WriterCard(
                "state_descriptor", "expression", "expression:%d" % index,
                (("已结算状态", text),), priority=float(row.get("attention", 0.0) or 0.0)))
    # 当前 root state 已经是同轮已发布 descriptor；若 receipt 没有对应 row，仍以独立
    # source identity 交付，不重算任何根量。
    for index, text in enumerate(tuple(getattr(plan, "current_root_state", ()) or ())):
        text = str(text or "").strip()
        if text:
            cards.append(WriterCard(
                "state_descriptor", "expression_root", "root:%d" % index,
                (("已结算状态", text),), priority=0.0))
    for index, row in enumerate(tuple(getattr(form_projection, "receipt", ()) or ())):
        axis = str(row.get("axis", "") or "").strip()
        direction = str(row.get("direction", "") or "").strip()
        level = str(row.get("level", "") or "").strip()
        if axis and direction and level:
            cards.append(WriterCard(
                "state_descriptor", "form_projection", "form:%d" % index,
                (("已结算表达形式状态", "%s·%s·%s" % (axis, direction, level)),),
                priority=0.0))
    return tuple(cards)


def _output_identity_writer_card(output_identity):
    """将本次 writer 的 canonical Actor 归属交为只读结构字段。

    它不从 persona、conversation title、输入正文或 recall 反推身份；调用者必须给出
    已绑定 Participant 的稳定 ID 与 kind。None 仅供尚未迁移的旧 fixture/read path
    保持只读兼容，正式 Actor ingress 必须由 Runner 传入明确 identity。
    """
    if output_identity is None:
        return None
    if not isinstance(output_identity, Mapping):
        raise ValueError("output_identity 必须来自 canonical Participant record")
    participant_id = str(output_identity.get("participant_id") or "").strip()
    actor_kind = str(output_identity.get("actor_kind") or "").strip().lower()
    if not participant_id or actor_kind not in {"human", "npc", "simulation"}:
        raise ValueError("output_identity 缺少有效 participant_id 或 actor_kind")
    return WriterCard(
        "output_identity", "actor", participant_id,
        (("本次输出归属", "participant:%s" % participant_id),
         ("参与者类型", actor_kind)),
        priority=10_000.0, group="identity")


def _conversation_writer_card(frame):
    if frame is None:
        return None
    kind = "一对一会话" if len(tuple(frame.active_member_ids)) == 2 else "群聊"
    value = "%s；当前成员：%s" % (kind, "、".join(frame.active_member_ids))
    return WriterCard(
        "conversation_fact", "conversation", str(frame.conversation_id),
        (("当前会话事实", value),),
        priority=1.0)


def _current_input_writer_card(*, text, now_ms, current_input_event):
    """把 direct F16 当前来信保留为带归属的 committed event 卡。

    当前输入不是 raw-tail 的替代品，也不创建第二份 memory。它就是本次 canonical
    ingress 已经带来的 source 事实；speaker/time/event/delivery 都由 Runner 的
    delivery lineage 透传，只有 legacy adapter 缺少 participant ID 时才如实保留来源种类。
    """
    context = dict(current_input_event or {}) if isinstance(current_input_event, Mapping) else {}
    speaker = str(context.get("speaker") or "external").strip()
    event_id = str(context.get("event_id") or ("turn:%d" % int(now_ms))).strip()
    try:
        committed_at_ms = int(context.get("committed_at_ms", now_ms))
    except (TypeError, ValueError):
        committed_at_ms = int(now_ms)
    fields = [
        ("说话者", speaker),
        ("提交时间", "%dms" % committed_at_ms),
        ("原话", str(text)),
        ("事件标识", event_id),
    ]
    delivery_id = str(context.get("delivery_id") or "").strip()
    if delivery_id:
        fields.append(("投递标识", delivery_id))
    return WriterCard("current_input", "committed_input", event_id,
                      tuple(fields), priority=1000.0)


def format_expression_feedback_block(context):
    """反馈只作为本回合表达语境，不能指令删除/否定已经选中的事实。"""
    return ("【上一轮表达反馈】\n"
            "对方已明确感到：%s。这是此刻已知的互动事实。" %
            str(context["meaning"]))


def _history_sources(persona_core, persona_block_ids, dialogue_tail, kept_events, now_ms):
    """实际装配的历史性材料来源（不含原文）。"""
    sources = []
    persona_ids = (["身份"] if str(persona_core or "").strip() else []) + list(persona_block_ids)
    if persona_ids:
        sources.append({"source": "persona_static", "block_ids": persona_ids})
    tail = str(dialogue_tail or "")
    if tail.strip():
        sources.append({"source": "dialogue_tail",
                        "line_count": len([line for line in tail.splitlines() if line.strip()])})
    for event in kept_events or ():
        sources.append({"source": "recall_event", "event_id": event.event_id,
                        "record_ts": int(event.record_ts), "origin": event.source,
                        "relative_time": relative_time_label(event.record_ts, now_ms),
                        "attribution": recall_event_attribution(event.source),
                        "reason": "turn_recall_selected"})
    return tuple(sources)


def _kept_expression_events(system, events):
    """预算裁剪后只保留实际进入“相关事件”块的事件元数据。"""
    heading = "【相关事件（仅在与当前输入直接相关时使用）】"
    if heading not in system:
        return ()
    section = system.split(heading, 1)[1].split("\n\n【", 1)[0]
    return tuple(event for event in events
                 if event.text in section and ("事件审计标识：" + event.event_id) in section)


def _ids_at_indices(ids, indices):
    """保留调用方已选 ID 的顺序；预算装配只返回索引，避免复制正文。"""
    keep = set(indices or ())
    return tuple(item for index, item in enumerate(ids or ()) if index in keep)


def _without_ids(ids, kept_ids):
    kept = set(kept_ids or ())
    return tuple(item for item in (ids or ()) if item not in kept)


def _recall_delivery_decisions(events, delivered_event_ids, suppressed):
    """写出已选离散事件卡在本次 writer 装配中的表示／结果。

    该 receipt 只说明现成 recall event 是否通过预算与边界进入 writer；不重新选择、
    不读取根状态，也不将 writer delivery 反写为 P4 资格。
    """
    delivered = {str(item) for item in (delivered_event_ids or ())}
    suppression = {
        str(item.get("event_id")): str(item.get("reason"))
        for item in (suppressed or ()) if isinstance(item, Mapping)
    }
    rows = []
    for event in events or ():
        event_id = str(getattr(event, "event_id", "") or "")
        is_delivered = event_id in delivered
        rows.append({
            "event_id": event_id,
            "source": str(getattr(event, "source", "")),
            "record_ts": int(getattr(event, "record_ts")),
            "identity_kind": str(getattr(event, "identity_kind", "legacy_no_event_id")),
            "representation": ("discrete_recall_card" if is_delivered else "not_delivered"),
            "reason": ("writer_budget_kept" if is_delivered else
                       suppression.get(event_id, "writer_budget_excluded")),
        })
    return rows


def conversation_writer_condition(conversation_frame):
    """把已投递的场景 frame 变成无身份的 writer 边界，不参与选择或调度。"""
    if conversation_frame is None:
        return None
    if not isinstance(conversation_frame, ConversationFrame):
        raise TypeError("conversation_frame 必须为 ConversationFrame")
    if (len(conversation_frame.active_member_ids) == 2
            and len(conversation_frame.audience_participant_ids) == 1):
        return "本轮会话：已核验的一对一会话。"
    return "本轮会话：已核验的当前会话。"


def _selected_relevance_anchor(recall, event):
    """返回召回选择层已为事件给出的当前相关性凭证。

    这是 selection → writer 的单向交接：只认本轮 ``shadow.selected_bundles``
    中同一来源、同一记录的正相关候选。它不重新检索、不计算新分数，也不把
    ``memory`` 的内部状态记忆普遍放宽成可说事实。唯一例外是同一底层事件在
    canonical 收敛前已有 semantic/ledger 正相关 witness；该 witness 只可随相同
    timestamp 的 canonical memory 一起使用。前置的暂停和指代边界仍在调用方更早
    执行，因而该凭证不能跨过它们。
    """
    shadow = getattr(recall, "shadow", None) or {}
    if not isinstance(shadow, dict):
        return False
    for candidate in shadow.get("selected_bundles") or ():
        if not isinstance(candidate, dict):
            continue
        try:
            same_record = int(candidate.get("record_ts")) == int(event.record_ts)
        except (TypeError, ValueError):
            same_record = str(candidate.get("event_id") or "") == str(event.event_id)
        if not same_record:
            continue
        try:
            if (str(event.source) in {"ledger", "semantic"}
                    and str(candidate.get("source")) == str(event.source)
                    and float(candidate.get("relevance", 0.0) or 0.0) > 0.0):
                return True
        except (TypeError, ValueError):
            pass
        if str(event.source) != "memory" or str(candidate.get("source")) != "memory":
            continue
        for witness in candidate.get("expression_relevance_witnesses") or ():
            if not isinstance(witness, dict) or str(witness.get("source")) not in {"semantic", "ledger"}:
                continue
            try:
                if int(witness.get("record_ts")) == int(event.record_ts):
                    return True
            except (TypeError, ValueError):
                continue
    return False


def _current_turn_recall_delivery(recall, text, reference_frame=None):
    """将已唤起事实投影为本轮可说的事件卡，不重新选择或修改记忆。

    记忆选择保留“想起了什么”的内部事实；即时回复另需判断“这句话是否该把它说
    出来”。默认仍要求既有三字词面锚；但若 ``ledger``/``semantic`` 事件已在本轮
    选择层以正相关凭证选中，也可带着该凭证交给作者，避免“第二天了”这类自然续话
    被纯字符串复核误伤。若已有边界检测器明确记录本轮“先不聊／别提”，该事实只阻
    止把旧卡交给作者，不会删除内部唤起或 archive。问句可维持 ``semantic`` 候选的
    查询资格；任意 ``memory`` 事件仍不能越过当前词面锚。这样既保留“想起了什么”
    的完整 trace，也避免无关承诺在本轮对白中挤占当前话题。
    """
    expression_plan = getattr(recall, "expression_plan", None)
    if expression_plan is None:
        return (), (), ()
    reason_codes = set(((getattr(recall, "shadow", None) or {}).get("intent") or {}).get("reason_codes") or ())
    explicit_question = "question" in reason_codes
    explicit_pause = detect_denial(text) is not None
    # 明确换题／暂停是用户本轮的直接边界，优先于关系归属的推断性边界；否则 trace
    # 会把“先别聊这个”误记成主客体冲突。其余候选再经引用门，仍在通用词面锚之前
    # 给出 third_party/unresolved 的可解释原因。三者都只过滤交付副本，不改 recall。
    pause_delivered, suppressed = [], []
    for event in tuple(expression_plan.events):
        needs_current_anchor = event.source in {"memory", "semantic", "ledger"}
        if needs_current_anchor and explicit_pause:
            suppressed.append({"event_id": event.event_id,
                               "reason": "explicit_topic_pause"})
            continue
        pause_delivered.append(event)
    referent_events, referent_suppressed = referent_delivery_gate(
        pause_delivered, text, reference_frame)
    delivered, delivery_evidence = [], []
    for event in referent_events:
        needs_current_anchor = event.source in {"memory", "semantic", "ledger"}
        lexical_anchor = has_exact_ngram_overlap(
            text, event.text, LEDGER["fallback_min_shared_ngram_chars"])
        allow_question_semantic = explicit_question and event.source == "semantic"
        selection_anchor = _selected_relevance_anchor(recall, event)
        if needs_current_anchor and not (lexical_anchor or allow_question_semantic or selection_anchor):
            suppressed.append({"event_id": event.event_id,
                               "reason": "current_anchor_miss"})
            continue
        delivered.append(event)
        if selection_anchor and not lexical_anchor:
            # 公开 receipt 仅表明使用了已选相关性，不复制候选正文或分数。
            delivery_evidence.append({"event_id": event.event_id,
                                      "mode": "selected_relevance"})
    return (tuple(delivered), tuple(suppressed) + tuple(referent_suppressed),
            tuple(delivery_evidence))


def recall_selection_summary(recall):
    """把召回选择层压缩成无正文 receipt，供校准而非 prompt 决策使用。"""
    shadow = getattr(recall, "shadow", None) or {}
    if not isinstance(shadow, dict):
        return {"available": False, "reason": "no_shadow"}
    candidates = [item for item in (shadow.get("candidates") or ()) if isinstance(item, dict)]
    selected = [item for item in (shadow.get("selected_bundles") or ()) if isinstance(item, dict)]
    rejected = [item for item in (shadow.get("rejected_bundles") or ()) if isinstance(item, dict)]

    def counts(rows, key):
        result = {}
        for item in rows:
            value = str(item.get(key) or "unknown")
            result[value] = result.get(value, 0) + 1
        return dict(sorted(result.items()))

    def maximum(name):
        values = []
        for item in candidates:
            try:
                values.append(float(item.get(name)))
            except (TypeError, ValueError):
                continue
        return round(max(values), 6) if values else None

    intent = shadow.get("intent") if isinstance(shadow.get("intent"), dict) else {}
    return {
        "available": True,
        "intent": {
            "allow": bool(intent.get("allow", False)),
            "budget": max(0, int(intent.get("budget", 0) or 0)),
            "reason_codes": [str(item) for item in intent.get("reason_codes") or ()],
            "evidence": round(float(intent.get("evidence", 0.0) or 0.0), 6),
        },
        "candidate_count": len(candidates),
        "selected_count": len(selected),
        "rejected_count": len(rejected),
        "candidate_sources": counts(candidates, "source"),
        "selected_sources": counts(selected, "source"),
        "rejected_by_reason": counts(rejected, "drop_reason"),
        # 只保留现有排序读数的聚合上界；不复制 candidate/event ID、时间戳或正文。
        "score_max": {"relevance": maximum("relevance"),
                      "salience": maximum("salience"),
                      "propensity": maximum("propensity"),
                      "utility": maximum("utility")},
    }


# 兼容已有内部调用/fixture；正式消费者使用不带下划线的公共只读摘要。
_recall_selection_summary = recall_selection_summary


def assemble_compat_received_prompt(*, text, features, tier_hint, ammo_hits, persona_blocks,
                      turn, field_pressure, bridge_tokens, intuition_tokens,
                      attraction, recall, ledger, store, now_ms, gap_hours,
                      constants, rng, dialogue_tail,
                      budget_chars=None, semantic_tokens=None, feedback_record=None,
                      expression_attention_plan=None, responder_core_prompt=None,
                      response_pack_enabled=False, reference_frame=None,
                      form_projection=None, recall_projection=None,
                      conversation_frame=None, canonical_memory_sequence=False,
                      current_input_event=None, output_identity=None,
                      memory_card_attention_plan=None):
    """装配本轮 responder 提示词，返回生成输入及落盘归因所需标签。"""
    # 固定响应卡保留为 legacy candidate／选择回执，不能进入正式 writer。
    # 这既不删除历史候选，也不让“收束／安抚／回避”等旧建议绕开 descriptor
    # card 的客观材料边界。
    formal_expression_writer = (
        expression_attention_plan is not None or
        bool(getattr(form_projection, "lines", ()) or ())
    )
    if response_pack_enabled and not formal_expression_writer:
        response_blocks, response_block_ids = assemble_response_blocks(
            text, features=features, tier_hint=tier_hint, ammo_hits=ammo_hits)
        response_pack_receipt = {
            "requested": True,
            "writer_delivery": "excluded",
            "reason": "central_writer_objective_cards",
        }
    elif response_pack_enabled:
        response_blocks, response_block_ids = [], []
        response_pack_receipt = {
            "requested": True,
            "writer_delivery": "excluded",
            "reason": "formal_expression_writer",
        }
    else:
        response_blocks, response_block_ids = [], []
        response_pack_receipt = {
            "requested": False,
            "writer_delivery": "excluded",
            "reason": "disabled_by_caller",
        }
    feedback_context = expression_feedback_context(feedback_record)
    if feedback_context is not None:
        # 只是既有 response lane 的一张高优先级、单回合校准卡；事件事实仍按 P1
        # 先于所有写作卡装配，反馈也不能改变 response pack 的原有筛选规则。
        response_blocks = [(100.0, format_expression_feedback_block(feedback_context))] + list(response_blocks)
        response_block_ids = ["feedback_expression"] + list(response_block_ids)
    persona_details, persona_block_ids = assemble_persona_blocks(
        persona_blocks, {
            "turn": turn, "ammo_hits": ammo_hits, "tier_hint": tier_hint,
            "features": features, "P": field_pressure,
            "bridge": bridge_tokens, "text": text, "V_attr": attraction,
        })
    delivery_eligible_events, delivery_suppressed, delivery_evidence = _current_turn_recall_delivery(
        recall, text, reference_frame=reference_frame)
    expression_events, memory_card_attention_receipt = _memory_card_attention_events(
        delivery_eligible_events, memory_card_attention_plan)
    occupied_ids = {str(event.event_id) for event in expression_events}
    attention_suppressed = tuple({
        "event_id": str(event.event_id), "reason": "attention_not_occupied",
    } for event in delivery_eligible_events if str(event.event_id) not in occupied_ids)
    delivery_suppressed = tuple(delivery_suppressed) + attention_suppressed
    # 事件的选择/priority 已在 recall receipt 中冻结；这里只交结构字段，禁止先拼
    # 成带“仅作/不得/应该”等自然语言解释的 writer prose。
    recall_cards = tuple(
        recall_event_writer_card(
            event, now_ms, index,
            attention_selected=memory_card_attention_plan is not None)
        for index, event in enumerate(expression_events)
    )
    # AUD-015/016：高层 formal writer 已不接受 raw history。保留两个旧参数仅
    # 为旧调用点的短期形状兼容；无论 mode/预算为何，它们都不能重开历史旁路。
    del dialogue_tail, canonical_memory_sequence
    writer_dialogue_tail = ""
    supplement = memory_supplement(store, now_ms)
    # 保留抽样，确保既有随机序列的消费位置不漂移；但即时用户回合不再把“隔得久”
    # 直接翻译成写作字数命令。墙钟已经通过 field、词元和已选择事件抵达作者，
    # 对话长度由当前材料决定。长文抽样仍供主动输出路径使用。
    drawn_length_mode = length_mode(gap_hours or 0.0, constants, rng)
    selected_length_mode = "short"
    length_mode_reason = "interactive_reply_length_neutral"
    # Agent 1 的可读语义测量只进入 F03 状态/召回查询，不得作为 writer 卡绕过
    # descriptor→emission 发放。仅保留数量与去向收据，避免把测量文本再次落盘。
    semantic_measurement_count = len([
        1 for token in (semantic_tokens or [])
        if isinstance(token, Mapping) and str(token.get("text", "")).strip()
    ])
    output_identity_card = _output_identity_writer_card(output_identity)
    writer_cards = [
        *( (output_identity_card,) if output_identity_card is not None else () ),
        _current_input_writer_card(
            text=text, now_ms=now_ms, current_input_event=current_input_event),
        *_persona_writer_cards(persona_blocks, persona_details, persona_block_ids),
        *recall_cards,
        *_expression_writer_cards(expression_attention_plan, form_projection),
    ]
    if feedback_context is not None and str(feedback_context.get("meaning", "")).strip():
        writer_cards.append(WriterCard(
            "feedback_fact", "feedback", "feedback:%d" % int(now_ms),
            (("已确认反馈", str(feedback_context["meaning"])),), priority=100.0))
    conversation_card = _conversation_writer_card(conversation_frame)
    if conversation_card is not None:
        writer_cards.append(conversation_card)

    def assemble():
        return compose_responder_messages(
            None, [],
            budget_chars=budget_chars,
            length_mode=selected_length_mode,
            # 正式核心只能来自 responder.md；显式 override 被 responder 拒绝并留 receipt。
            core_prompt=responder_core_prompt,
            writer_cards=tuple(writer_cards),
            return_assembly=True,
        )

    assembly = assemble()
    system, user = assembly.system, assembly.user
    kept_events = tuple(event for index, event in enumerate(expression_events)
                        if index in set(assembly.kept_event_indices))
    excluded_ledger_ts = tuple(event.record_ts for event in kept_events if event.source == "ledger")
    recall_event_ids = tuple(event.event_id for event in kept_events)
    selected_event_ids = tuple(event.event_id for event in expression_events)
    recalled_event_ids = tuple(event.event_id for event in getattr(
        getattr(recall, "expression_plan", None), "events", ()) or ())
    kept_persona_ids = _ids_at_indices(persona_block_ids, assembly.kept_persona_indices)
    kept_response_ids = _ids_at_indices(response_block_ids, assembly.kept_response_indices)
    assembly_receipt = {
        "budget_chars": assembly.budget_chars,
        "core_chars": assembly.core_chars,
        "core_over_budget": assembly.core_over_budget,
        "system_chars": len(system),
        "writer_materials": {
            "delivered": list(assembly.delivered_materials),
            "rejected": list(assembly.rejected_materials),
            "budget_overflow": list(assembly.budget_overflow_materials),
        },
        "memory_card_attention": memory_card_attention_receipt,
        "output_identity": ({
            "participant_id": str(output_identity["participant_id"]),
            "actor_kind": str(output_identity["actor_kind"]),
            "delivered": any(item.get("kind") == "output_identity"
                             for item in assembly.delivered_materials),
        } if output_identity is not None else {
            "delivered": False,
            "reason": "runner_identity_unbound_legacy_compat",
        }),
        "selected": {
            "persona_block_ids": list(persona_block_ids),
            "response_block_ids": list(response_block_ids),
            "recall_event_ids": list(selected_event_ids),
        },
        "recall_delivery": {
            "recalled_event_ids": list(recalled_event_ids),
            "delivery_eligible_event_ids": [
                str(event.event_id) for event in delivery_eligible_events],
            "attention_occupied_event_ids": list(selected_event_ids),
            "delivered_event_ids": list(selected_event_ids),
            "suppressed": list(delivery_suppressed),
            "event_decisions": _recall_delivery_decisions(
                getattr(getattr(recall, "expression_plan", None), "events", ()),
                selected_event_ids, delivery_suppressed),
        },
        "recall_selection": recall_selection_summary(recall),
        "recall_projection": (recall_projection.as_record()
                              if recall_projection is not None and hasattr(recall_projection, "as_record")
                              else {"enabled": False, "reason": "not_compiled"}),
        "reference_frame": {
            "resolved": reference_frame is not None,
            "delivery_scope": (getattr(reference_frame, "delivery_scope", None)),
            "resolver_source": (getattr(reference_frame, "resolver_source", None)),
            # ReferenceFrame 是符号侧的 recall/P4 资格事实。禁止把它或由它
            # 派生的关系说明重新编码为自然语言 writer 指令。
            "writer_condition_included": False,
            "writer_direct_delivery": "excluded",
        },
        "semantic_measurement": {
            "candidate_count": semantic_measurement_count,
            "writer_delivery": "excluded",
            "reason": "state_and_recall_only",
        },
        "legacy_response_pack": response_pack_receipt,
        "conversation_frame": ({
            "delivered": True,
            **conversation_frame.as_record(),
            "writer_condition_included": conversation_card is not None,
        } if conversation_frame is not None else {
            "delivered": False,
            "writer_condition_included": False,
        }),
        "kept": {
            "persona_block_ids": list(kept_persona_ids),
            "response_block_ids": list(kept_response_ids),
            "recall_event_ids": list(recall_event_ids),
        },
        "dropped": {
            "persona_block_ids": list(_without_ids(persona_block_ids, kept_persona_ids)),
            "response_block_ids": list(_without_ids(response_block_ids, kept_response_ids)),
            "recall_event_ids": list(_without_ids(selected_event_ids, recall_event_ids)),
        },
        "non_event_history": {
            "dialogue_tail_line_count": len([line for line in str(writer_dialogue_tail or "").splitlines()
                                               if line.strip()]),
            "essay_memory_supplement": {
                "included": False,
                "available_recent_count": len(supplement.get("recent") or ()),
                "available_past_count": len(supplement.get("past") or ()),
                "reason": "selection_boundary",
            },
        },
        "memory_representation": {
            "representation": "discrete_recall_only",
            "event_ids": [],
            "event_decisions": [],
            "reason": "raw_ordered_sequence_excluded",
        },
        "length_mode": {"selected": selected_length_mode, "reason": length_mode_reason,
                        "drawn": drawn_length_mode},
        "expression_feedback": (dict(feedback_context) if feedback_context is not None
                                else {"included": False, "reason": "no_unique_confirmed_feedback"}),
        "expression_attention": {
            "enabled": expression_attention_plan is not None,
            "tokens": list(getattr(expression_attention_plan, "receipt", ()) or ()),
            "fit_projection": getattr(expression_attention_plan, "fit_projection", None),
            "feedback_fact_included": bool(getattr(expression_attention_plan, "feedback_fact", None)),
            "current_root_state_count": len(tuple(getattr(
                expression_attention_plan, "current_root_state", ()) or ())),
            # E3/E4 正式词元发放只把无正文的 plan 和实际 delivery key 留入
            # assembly receipt。草稿、typing 与 UI 预览没有这张 F16 receipt，因而
            # 不可能写回余量。
            "emission": {
                "plan": (dict(getattr(expression_attention_plan, "emission_plan", {}) or {})
                         if isinstance(getattr(expression_attention_plan, "emission_plan", None), dict)
                         else None),
                "delivered_token_keys": list(getattr(
                    expression_attention_plan, "delivered_token_keys", ()) or ()),
            },
        },
        "form_projection": (form_projection.as_record()
                            if form_projection is not None and hasattr(form_projection, "as_record")
                            else {"enabled": False, "reason": "not_compiled"}),
    }
    if delivery_evidence:
        assembly_receipt["recall_delivery"]["anchor_evidence"] = list(delivery_evidence)
    history_sources = _history_sources(
        persona_blocks.get("身份", ""), persona_block_ids, writer_dialogue_tail, kept_events, now_ms)
    history_exclusions = _unselected_ledger_exclusions(ledger, now_ms, kept_events)
    return TurnPrompt(
        system=system, user=user, response_block_ids=response_block_ids,
        persona_block_ids=persona_block_ids, length_mode=selected_length_mode,
        recall_event_ids=recall_event_ids, excluded_ledger_ts=excluded_ledger_ts,
        recall_expression_events=kept_events, history_sources=history_sources,
        history_exclusions=history_exclusions,
        kept_persona_block_ids=kept_persona_ids,
        kept_response_block_ids=kept_response_ids,
        dropped_persona_block_ids=tuple(assembly_receipt["dropped"]["persona_block_ids"]),
        dropped_response_block_ids=tuple(assembly_receipt["dropped"]["response_block_ids"]),
        dropped_recall_event_ids=tuple(assembly_receipt["dropped"]["recall_event_ids"]),
        expression_feedback=feedback_context,
        assembly_receipt=assembly_receipt,
    )


def assemble_compat_autonomous_prompt(*, persona_blocks, turn, field_pressure,
                                ranked_tokens, bridge_tokens, attraction,
                                dialogue_tail, state_hint, budget_chars=None,
                                length_mode="short", responder_core_prompt=None,
                                expression_attention_plan=None, now_ms=0,
                                recall_expression_events=(), form_projection=None,
                                temporal_expression_receipt=None,
                                proactive_mode="replay", memory_messages=None,
                                canonical_memory_sequence=False,
                                output_identity=None,
                                memory_card_attention_plan=None):
    """装配 NPC 主动输出的 responder 输入。

    追发、主动分享与墙钟回放共用此入口。它们没有一条新的用户输入，故不伪造
    response pack、反馈或回忆选择；是否把已有表达素材交给写作层由调用方传入的
    ``expression_attention_plan`` 明确决定。主动线的共同经历也只能由调用方交付
    已选事件卡：追发继承刚刚实际装配的事件，墙钟开口则先经 turn_recall 重选。
    这样不会把旧的 MANE 原词元或未选台账直接塞回模型。
    """
    persona_details, persona_block_ids = assemble_persona_blocks(
        persona_blocks, {
            "turn": turn, "ammo_hits": [], "tier_hint": None,
            "features": {}, "P": field_pressure,
            "bridge": bridge_tokens, "text": "", "V_attr": attraction,
        })
    recalled_expression_events = tuple(recall_expression_events or ())
    expression_events, memory_card_attention_receipt = _memory_card_attention_events(
        recalled_expression_events, memory_card_attention_plan)
    occupied_ids = {str(event.event_id) for event in expression_events}
    attention_suppressed = tuple({
        "event_id": str(event.event_id), "reason": "attention_not_occupied",
    } for event in recalled_expression_events if str(event.event_id) not in occupied_ids)
    recall_cards = tuple(
        recall_event_writer_card(
            event, now_ms, index,
            attention_selected=memory_card_attention_plan is not None)
        for index, event in enumerate(expression_events)
    )
    # AUD-015/016：主动与 direct writer 同一边界。保留旧参数只为调用形状兼容，
    # 但不论 mode 或 budget，已提交原文都不能经 raw tail 回到模型。
    del dialogue_tail, memory_messages, canonical_memory_sequence
    writer_dialogue_tail = ""
    output_identity_card = _output_identity_writer_card(output_identity)
    writer_cards = (
        *((output_identity_card,) if output_identity_card is not None else ()),
        WriterCard(
            kind="proactive_context",
            source="opening",
            source_id=f"opening:{int(now_ms)}",
            fields=(("当前输入状态", "无新的已提交消息。"),),
            priority=1000.0,
            group="current_input",
        ),
        *_persona_writer_cards(persona_blocks, persona_details, persona_block_ids),
        *recall_cards,
        *_expression_writer_cards(expression_attention_plan, form_projection),
    )

    def assemble():
        return compose_responder_messages(
            None, ranked_tokens or [],
            bridge_tokens=bridge_tokens,
            dialogue_tail=writer_dialogue_tail,
            state_hint=state_hint,
            response_blocks=[],
            persona_details=persona_details,
            self_facts=None,
            budget_chars=budget_chars,
            proactive=True,
            length_mode=length_mode,
            memory_supplement=None,
            expression_attention_lines=(
                tuple(getattr(expression_attention_plan, "lines", ()) or ()) +
                tuple(getattr(form_projection, "lines", ()) or ())),
            expression_attention_feedback_fact=getattr(
                expression_attention_plan, "feedback_fact", None),
            expression_current_root_state=getattr(
                expression_attention_plan, "current_root_state", ()),
            expression_attention_enabled=(expression_attention_plan is not None or
                                          bool(getattr(form_projection, "lines", ()) or ())),
            core_prompt=responder_core_prompt,
            proactive_mode=proactive_mode,
            writer_cards=writer_cards,
            return_assembly=True,
        )

    assembly = assemble()
    kept_persona_ids = _ids_at_indices(persona_block_ids, assembly.kept_persona_indices)
    kept_events = tuple(event for index, event in enumerate(expression_events)
                        if index in set(assembly.kept_event_indices))
    recall_event_ids = tuple(event.event_id for event in kept_events)
    selected_event_ids = tuple(event.event_id for event in expression_events)
    receipt = {
        "budget_chars": assembly.budget_chars,
        "core_chars": assembly.core_chars,
        "core_over_budget": assembly.core_over_budget,
        "system_chars": len(assembly.system),
        "selected": {"persona_block_ids": list(persona_block_ids),
                     "recall_event_ids": list(selected_event_ids)},
        "kept": {"persona_block_ids": list(kept_persona_ids),
                 "recall_event_ids": list(recall_event_ids)},
        "dropped": {"persona_block_ids": list(_without_ids(persona_block_ids, kept_persona_ids)),
                    "recall_event_ids": list(_without_ids(selected_event_ids, recall_event_ids))},
        "recall_delivery": {
            "recalled_event_ids": [
                str(event.event_id) for event in recalled_expression_events],
            "delivery_eligible_event_ids": [
                str(event.event_id) for event in recalled_expression_events],
            "attention_occupied_event_ids": list(selected_event_ids),
            "delivered_event_ids": list(recall_event_ids),
            "suppressed": list(attention_suppressed),
            "event_decisions": _recall_delivery_decisions(
                recalled_expression_events, recall_event_ids, attention_suppressed),
        },
        "memory_card_attention": memory_card_attention_receipt,
        "mode": "proactive",
        "proactive_mode": str(proactive_mode),
        "writer_materials": {
            "delivered": list(assembly.delivered_materials),
            "rejected": list(assembly.rejected_materials),
            "budget_overflow": list(assembly.budget_overflow_materials),
        },
        "output_identity": ({
            "participant_id": str(output_identity["participant_id"]),
            "actor_kind": str(output_identity["actor_kind"]),
            "delivered": any(item.get("kind") == "output_identity"
                             for item in assembly.delivered_materials),
        } if output_identity is not None else {
            "delivered": False,
            "reason": "runner_identity_unbound_legacy_compat",
        }),
        "non_event_history": {
            "dialogue_tail_line_count": len([
                line for line in str(writer_dialogue_tail or "").splitlines()
                if line.strip()
            ]),
        },
        "memory_representation": {
            "representation": "discrete_recall_only",
            "event_ids": [],
            "event_decisions": [],
            "reason": "raw_ordered_sequence_excluded",
        },
        "expression_attention": {
            "enabled": expression_attention_plan is not None,
            "tokens": list(getattr(expression_attention_plan, "receipt", ()) or ()),
            "fit_projection": getattr(expression_attention_plan, "fit_projection", None),
            "current_root_state_count": len(tuple(getattr(
                expression_attention_plan, "current_root_state", ()) or ())),
            "emission": {
                "plan": (dict(getattr(expression_attention_plan, "emission_plan", {}) or {})
                         if isinstance(getattr(expression_attention_plan, "emission_plan", None), dict)
                         else None),
                "delivered_token_keys": list(getattr(
                    expression_attention_plan, "delivered_token_keys", ()) or ()),
            },
        },
        # 不保存 prompt 正文；这份 receipt 只证明本轮表达时间卡是否来自一条已
        # 发送输出、经过了多少已结算墙钟，以及根量投影的差异。
        "temporal_expression": dict(temporal_expression_receipt or {}),
        "form_projection": (form_projection.as_record()
                            if form_projection is not None and hasattr(form_projection, "as_record")
                            else {"enabled": False, "reason": "not_compiled"}),
    }
    return TurnPrompt(
        system=assembly.system, user=assembly.user,
        response_block_ids=[], persona_block_ids=persona_block_ids,
        length_mode=length_mode,
        recall_event_ids=recall_event_ids,
        excluded_ledger_ts=tuple(event.record_ts for event in kept_events
                                 if event.source == "ledger"),
        recall_expression_events=kept_events,
        history_sources=tuple(
            dict(item, reason="proactive_recall_selected")
            if item.get("source") == "recall_event" else item
            for item in _history_sources(persona_blocks.get("身份", ""), persona_block_ids,
                                         writer_dialogue_tail, kept_events, now_ms)),
        kept_persona_block_ids=kept_persona_ids,
        dropped_persona_block_ids=tuple(receipt["dropped"]["persona_block_ids"]),
        dropped_recall_event_ids=tuple(receipt["dropped"]["recall_event_ids"]),
        assembly_receipt=receipt,
    )


# 历史工具 API：Alpha 0.3／family-off 继续获得同一 builder 对象；默认产品
# 必须从 ``ops.legacy_prompt_compat`` 显式选择，不能把它当正式 Writer fallback。
build_turn_prompt = assemble_compat_received_prompt
build_proactive_turn_prompt = assemble_compat_autonomous_prompt
