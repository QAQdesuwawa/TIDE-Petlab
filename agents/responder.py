# agents/responder.py
"""回复生成（同步）：人格卡 + 写作直觉 + 回归提示 → 一次调用（架构文档 §15）。

只做 prompt 组装与回复清洗（纯函数，零 LLM），调用接线在 P6 runner。
"""
from __future__ import annotations

import os
import hashlib
import json
import re
from dataclasses import dataclass

from framework.model_api import thaw_model_data
from product_adapters.cx_six_seven_v1.realizer_packet import (
    DEFAULT_WRITER_PROMPT_BUDGET_CHARS,
    WRITER_LANE_ORDER,
    WriterPacket as FrameworkWriterPacket,
    verify_writer_packet,
)
from product_adapters.cx_six_seven_v1.writer_attempts import (
    WriterAttemptJournal,
    WriterAttemptJournalError,
)


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "responder.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _norm_entries(items):
    """块列表归一：(priority, text) 或 str → (priority, text)。"""
    out = []
    for it in items or []:
        if isinstance(it, tuple):
            out.append((float(it[0]), str(it[1])))
        else:
            out.append((5.0, str(it)))
    return out


@dataclass(frozen=True)
class WriterCard:
    """已由上游 receipt 选出的 writer 客观材料。

    这是最后的文字边界：调用方只能交 fields，不能预写 card prose；所有中文卡面
    均由本模块渲染。``group/index`` 只复用既有预算收据的索引，不参与重新选择。
    """

    kind: str
    source: str
    source_id: str
    fields: tuple
    priority: float = 5.0
    group: str = "generic"
    index: int = -1
    # normal 仍受通用字符安全预算约束；preserve_attention_selection 表示上游
    # 已完成 MemoryCard 注意力占位，通用预算只能报告超限，不能再改选该集合。
    budget_policy: str = "normal"


@dataclass(frozen=True)
class PromptAssembly:
    """一次 responder 装配的无正文预算收据。

    卡包的触发/筛选仍在调用方；这里仅记录它们在统一预算竞争后是否实际进入
    system。索引对应调用方传入的同类条目顺序，避免在 trace 复制人格卡、用户原文
    或记忆正文。
    """

    system: str
    user: str
    budget_chars: int
    core_chars: int
    core_over_budget: bool
    kept_persona_indices: tuple
    kept_response_indices: tuple
    kept_event_indices: tuple
    kept_ledger_indices: tuple
    delivered_materials: tuple = ()
    rejected_materials: tuple = ()
    budget_overflow_materials: tuple = ()
    source_packet_fingerprint: str | None = None
    prompt_fingerprint: str | None = None


@dataclass(frozen=True)
class ResponderAttemptResult:
    """Uncommitted provider result plus its exact archived prompt assembly."""

    assembly: PromptAssembly
    raw_candidate: str
    parsed: dict


_CARD_FIELDS = {
    # AUD-125：本次请求实际代表哪个 Actor 输出是结构事实；它不是角色任务、
    # 人格 prose 或会话 frame 的可选附属字段。
    "output_identity": {"本次输出归属", "参与者类型"},
    "persona_fact": {"角色静态资料", "角色表达特征", "角色公开边界"},
    "committed_event": {"说话者", "时间", "原话", "事件", "事件标识"},
    "state_descriptor": {"已结算状态", "已结算表达形式状态"},
    "feedback_fact": {"已确认反馈"},
    "conversation_fact": {"当前会话事实"},
    "current_input": {"说话者", "提交时间", "原话", "事件标识", "投递标识"},
    "proactive_context": {"当前输入状态"},
}

_CARD_TITLE = {
    "committed_event": "已提交事件",
    "current_input": "本轮已提交事件",
}


def _material_record(card):
    return {"kind": card.kind, "source": card.source, "source_id": card.source_id}


def _normalize_writer_card(card):
    """验证结构字段，而非以中文词表猜测一句话是否“像建议”。"""
    if not isinstance(card, WriterCard):
        return None, {"source": "untyped", "reason": "untyped_material"}
    allowed = _CARD_FIELDS.get(str(card.kind))
    if allowed is None:
        return None, {"source": str(card.source), "source_id": str(card.source_id),
                      "reason": "unsupported_kind"}
    source = str(card.source or "").strip()
    source_id = str(card.source_id or "").strip()
    if not source or not source_id:
        return None, {"source": source or "unknown", "source_id": source_id,
                      "reason": "missing_provenance"}
    fields = []
    for item in tuple(card.fields or ()):
        if not isinstance(item, tuple) or len(item) != 2:
            return None, {"source": source, "source_id": source_id,
                          "reason": "invalid_fields"}
        label, value = str(item[0]).strip(), str(item[1]).strip()
        if label not in allowed or not value:
            return None, {"source": source, "source_id": source_id,
                          "reason": "field_not_allowed"}
        fields.append((label, value))
    if not fields:
        return None, {"source": source, "source_id": source_id,
                      "reason": "empty_fields"}
    budget_policy = str(card.budget_policy or "normal")
    if budget_policy not in {"normal", "preserve_attention_selection"}:
        return None, {"source": source, "source_id": source_id,
                      "reason": "invalid_budget_policy"}
    return WriterCard(str(card.kind), source, source_id, tuple(fields),
                      float(card.priority), str(card.group), int(card.index),
                      budget_policy), None


def _render_writer_card(card):
    """唯一 writer 卡面 renderer：只有客观栏目和字段，不接受上游 prose。"""
    lines = []
    title = _CARD_TITLE.get(card.kind)
    if title:
        lines.append(title + "：")
    for label, value in card.fields:
        lines.append(label + "：" + value)
    return "\n".join(lines)


def _render_system(core_text, cards):
    parts = [core_text]
    parts.extend(_render_writer_card(card) for card in cards)
    return "\n\n".join(parts)


_PACKET_LANE_TITLES = {
    "current_input": "当前已提交输入",
    "persona": "角色事实",
    "conversation": "当前会话事实",
    "expression": "当前表达资源",
    "memory": "已唤起的已提交记忆",
    "form": "表达形式事实",
}


def _packet_field_text(value):
    plain = thaw_model_data(value)
    if isinstance(plain, str):
        return plain
    if plain is None or isinstance(plain, (bool, int, float)):
        return json.dumps(plain, ensure_ascii=False, sort_keys=True)
    return json.dumps(
        plain, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _render_packet_card(card):
    lines = []
    for raw_label, value in thaw_model_data(card.fields).items():
        label = str(raw_label or "").strip()
        if not label:
            raise ValueError("writer packet card fields require non-empty labels")
        lines.append(label + "：" + _packet_field_text(value))
    if not lines:
        raise ValueError("writer packet card fields cannot be empty")
    return "\n".join(lines)


def _packet_material_record(card):
    return {
        "kind": card.kind,
        "source_id": card.source_id,
        "card_id": card.card_id,
        "lane": card.lane,
    }


def compose_responder_packet(packet, *, budget_chars=None, return_assembly=False, core_text=None):
    """Render one verified framework packet into the existing responder host.

    The adapter preserves packet lane/order and only serializes objective fields.
    It performs no model/resource/memory selection and never calls a provider.
    """
    packet_receipt = verify_writer_packet(packet)
    if not isinstance(packet, FrameworkWriterPacket):
        raise ValueError("responder packet input must be a WriterPacket")
    core_text = _load_prompt() if core_text is None else core_text
    if not isinstance(core_text, str) or not core_text.strip():
        raise ValueError("writer core protocol must be non-empty text")
    cap = int(
        budget_chars
        if budget_chars is not None
        else DEFAULT_WRITER_PROMPT_BUDGET_CHARS
    )
    identity = packet.output_identity
    addressed = "、".join(identity.addressed_participant_ids) or "未明确指向（广播）"
    identity_block = "\n".join((
        "输出身份：",
        "本次输出归属：" + identity.speaker_participant_id,
        "参与者类型：" + identity.speaker_kind,
        "当前可见参与者：" + "、".join(identity.audience_participant_ids),
        "已明确指向：" + addressed,
    ))
    fixed_parts = [core_text, identity_block]
    kept_parts = []
    delivered = []
    rejected = []
    overflow = []
    kept_cards = []
    for lane in WRITER_LANE_ORDER:
        title = _PACKET_LANE_TITLES[lane]
        for card in packet.sections[lane]:
            rendered = title + "：\n" + _render_packet_card(card)
            trial = "\n\n".join((*fixed_parts, *kept_parts, rendered))
            record = _packet_material_record(card)
            if len(trial) <= cap:
                kept_parts.append(rendered)
                kept_cards.append(card)
                delivered.append(record)
            elif card.budget_policy == "preserve_selection":
                kept_parts.append(rendered)
                kept_cards.append(card)
                delivered.append(record)
                overflow.append({
                    **record,
                    "reason": "attention_selection_over_budget",
                })
            else:
                rejected.append({**record, "reason": "budget_excluded"})

    # Only the base selection can use its existing preserve_selection exception.
    # Admit children after all base materials, against the same actual cap; no
    # parent is dropped/reselected to make space and no child inherits overflow.
    if any(card.subcards for cards in packet.sections.values() for card in cards):
        nested_parts = [[part] for part in kept_parts]
        child_deliveries = {}
        for index, card in enumerate(kept_cards):
            accepted = []
            for child in card.subcards:
                record = {
                    "kind": child.kind, "source_id": child.source_id,
                    "card_id": child.subcard_id, "parent_card_id": card.card_id,
                    "lane": card.lane,
                }
                rendered = _render_packet_card(child)
                trial_parts = [part for group in nested_parts for part in group]
                # Count exact separators and all other material, not a private
                # child allowance or an estimate of its payload alone.
                insertion = sum(len(group) for group in nested_parts[:index + 1])
                trial_parts.insert(insertion, rendered)
                trial = "\n\n".join((*fixed_parts, *trial_parts))
                if len(trial) <= cap:
                    nested_parts[index].append(rendered)
                    accepted.append(record)
                else:
                    rejected.append({**record, "reason": "budget_excluded"})
            child_deliveries[card.card_id] = accepted
        delivered = [record for card in kept_cards
                     for record in (_packet_material_record(card),
                                    *child_deliveries[card.card_id])]
        kept_parts = [part for group in nested_parts for part in group]
        kept_ids = {card.card_id for card in kept_cards}
        for cards in packet.sections.values():
            for card in cards:
                if card.card_id not in kept_ids:
                    rejected.extend({
                        "kind": child.kind, "source_id": child.source_id,
                        "card_id": child.subcard_id, "parent_card_id": card.card_id,
                        "lane": card.lane, "reason": "parent_not_delivered",
                    } for child in card.subcards)

    system = "\n\n".join((*fixed_parts, *kept_parts))
    user = (
        "当前输入状态：存在已提交输入。"
        if packet.sections["current_input"]
        else "当前输入状态：无新的已提交输入。"
    )
    prompt_fingerprint = hashlib.sha256(
        (system + "\0" + user).encode("utf-8")
    ).hexdigest()
    assembly = PromptAssembly(
        system=system,
        user=user,
        budget_chars=cap,
        core_chars=len("\n\n".join(fixed_parts)),
        core_over_budget=len("\n\n".join(fixed_parts)) > cap,
        kept_persona_indices=(),
        kept_response_indices=(),
        kept_event_indices=(),
        kept_ledger_indices=(),
        delivered_materials=tuple(delivered),
        rejected_materials=tuple(rejected),
        budget_overflow_materials=tuple(overflow),
        source_packet_fingerprint=str(packet_receipt["packet_fingerprint"]),
        prompt_fingerprint=prompt_fingerprint,
    )
    return assembly if return_assembly else (system, user)


def prepare_responder_attempt(packet, *, journal, budget_chars=None, source_evidence=None,
                              provider_dispatch_tracked=False):
    """Archive the exact verified Packet and rendered prompt before provider I/O.

    This is a preparation boundary only.  It does not call a provider, parse a
    candidate, authorize output, or create an interaction event.  The returned
    assembly is the exact body stored in the isolated attempt artifact.
    """
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("responder attempt requires WriterAttemptJournal")
    try:
        assembly = None
        if provider_dispatch_tracked:
            assembly = compose_responder_packet(packet,
                budget_chars=budget_chars, return_assembly=True)
            journal.record_packet(packet.writer_request_id, packet=packet,
                source_evidence=source_evidence, provider_dispatch_tracked=True,
                prepared_prompt={"system": assembly.system, "user": assembly.user,
                                 "prompt_fingerprint": assembly.prompt_fingerprint},
                prepared_assembly={"version": "writer-prompt-assembly-metadata-v1",
                    "metadata": {name: getattr(assembly, name)
                        for name in PromptAssembly.__dataclass_fields__
                        if name not in {"system", "user"}}})
        elif source_evidence is None:
            journal.record_packet(packet.writer_request_id, packet=packet)
        else:
            journal.record_packet(packet.writer_request_id, packet=packet,
                source_evidence=source_evidence, provider_dispatch_tracked=provider_dispatch_tracked)
        if assembly is None:
            assembly = compose_responder_packet(
                packet, budget_chars=budget_chars, return_assembly=True)
        journal.record_prompt(
            packet.writer_request_id,
            system=assembly.system,
            user=assembly.user,
            prompt_fingerprint=assembly.prompt_fingerprint,
        )
        return assembly
    except Exception as exc:
        # Even validation/assembly failures remain auditable.  This terminal
        # record is operational evidence only and grants no Memory/history role.
        try:
            packet_snapshot = (
                packet.to_record()
                if isinstance(packet, FrameworkWriterPacket) else None
            )
            journal.record_terminal(
                getattr(packet, "writer_request_id", ""),
                status="failed",
                error={
                    "stage": "writer_packet_preparation",
                    "type": type(exc).__name__,
                    "message": str(exc),
                    "attempted_packet": packet_snapshot,
                },
            )
        except Exception as audit_exc:
            # Audit loss must fail closed and remain visible.  The original
            # preparation error is retained in the message and exception chain.
            raise WriterAttemptJournalError(
                "writer attempt failure could not be archived; "
                f"original={type(exc).__name__}: {exc}"
            ) from audit_exc
        raise


def _raw_rejection(source, value):
    if value is None or value == "" or value == () or value == []:
        return None
    return {"source": source, "reason": "raw_prose_not_writer_card"}


def compose_responder_messages(persona_core, ranked_tokens, bridge_tokens=None,
                               dialogue_tail=None, state_hint=None,
                               response_blocks=None, persona_details=None,
                               self_facts=None, budget_chars=None,
                               proactive=False, gap_text=None,
                               length_mode="short", memory_supplement=None,
                               recall_event_blocks=None,
                               conversation_hint=None,
                               expression_attention_lines=None,
                               expression_attention_feedback_fact=None,
                               expression_attention_enabled=False,
                               expression_current_root_state=(),
                               core_prompt=None,
                               proactive_mode="replay",
                               writer_cards=(),
                               return_assembly=False):
    """只将 core、机器协议和带来源的客观 ``WriterCard`` 交给 writer。

    旧参数保留在签名中，目的是让历史 caller 可被明确拒绝并留下 receipt；它们不再
    具有自然语言 writer delivery 权限。卡的候选/排序由调用方既有 receipt 决定，
    此处只做最终卡面渲染和同一预算下的裁剪。
    """
    del proactive_mode, expression_attention_enabled, gap_text, memory_supplement
    core_text = _load_prompt()
    cap = int(
        budget_chars
        if budget_chars is not None
        else DEFAULT_WRITER_PROMPT_BUDGET_CHARS
    )
    rejected = []
    for source, value in (
        ("persona_core", persona_core), ("ranked_tokens", ranked_tokens),
        ("bridge", bridge_tokens), ("dialogue_tail", dialogue_tail),
        ("state_hint", state_hint), ("response_block", response_blocks),
        ("persona_detail", persona_details), ("self_facts", self_facts),
        ("recall_event_block", recall_event_blocks),
        ("conversation_hint", conversation_hint),
        ("expression_attention_lines", expression_attention_lines),
        ("expression_feedback", expression_attention_feedback_fact),
        ("expression_root_state", expression_current_root_state),
        ("core_override", core_prompt),
    ):
        row = _raw_rejection(source, value)
        if row is not None:
            rejected.append(row)

    admitted = []
    for ordinal, raw_card in enumerate(tuple(writer_cards or ())):
        card, rejection = _normalize_writer_card(raw_card)
        if rejection is not None:
            rejected.append(rejection)
            continue
        admitted.append((ordinal, card))

    # 既有候选排序/priority 已在上游结算；同 priority 时保留 caller 的稳定顺序。
    admitted.sort(key=lambda row: (-row[1].priority, row[0]))
    kept = []
    budget_overflow = []
    for _ordinal, card in admitted:
        trial = kept + [card]
        if len(_render_system(core_text, trial)) <= cap:
            kept.append(card)
        elif card.budget_policy == "preserve_attention_selection":
            # 该集合已经由 MemoryCard 注意力占位层决定。这里保留材料并显式报告
            # 技术预算不匹配；不能再按 priority 静默产生第二套记忆选择。
            kept.append(card)
            budget_overflow.append({
                **_material_record(card),
                "reason": "attention_selection_over_budget",
            })
        else:
            rejected.append({"source": card.source, "source_id": card.source_id,
                             "kind": card.kind, "reason": "budget_excluded"})

    system = _render_system(core_text, kept)
    if proactive:
        user = "当前输入状态：无新的已提交消息。"
    else:
        user = "当前输入状态：存在本轮已提交输入。"
    fixed_core_chars = len(core_text)
    assembly = PromptAssembly(
        system=system, user=user, budget_chars=cap,
        core_chars=fixed_core_chars,
        core_over_budget=bool(fixed_core_chars > cap),
        kept_persona_indices=tuple(card.index for card in kept
                                   if card.group == "persona" and card.index >= 0),
        kept_response_indices=tuple(card.index for card in kept
                                    if card.group == "response" and card.index >= 0),
        kept_event_indices=tuple(card.index for card in kept
                                 if card.group == "recall" and card.index >= 0),
        kept_ledger_indices=tuple(card.index for card in kept
                                  if card.group == "ledger" and card.index >= 0),
        delivered_materials=tuple(_material_record(card) for card in kept),
        rejected_materials=tuple(rejected),
        budget_overflow_materials=tuple(budget_overflow),
    )
    return assembly if return_assembly else (system, user)


def clean_reply(raw):
    """回复清洗：去首尾空白；空响应返回空串（调用方兜底）。"""
    return (raw or "").strip()


def redact_recall_audit_identifiers(reply, allowed_event_ids):
    """阻止本轮内部事件标识从 JSON 审计通道泄漏到可见回复。

    这是输出封装，而不是对 NPC 内容或语气的规则：只删除本轮 prompt 已发放的
    精确内部 ID 及其完整审计标签。调用方应把发生过的清理单独留审计；不能据此
    推断模型是否自然地使用了记忆事件。
    """
    text = clean_reply(reply)
    redacted = []
    # 长 ID 先处理，避免 ledger:10 被视为 ledger:100 的子串。
    for event_id in sorted({str(item).strip() for item in allowed_event_ids or []
                            if str(item).strip()}, key=len, reverse=True):
        before = text
        # 完整标签是 prompt 的内部格式；正文中即使意外复制也不应可见。
        text = text.replace("【事件审计标识：%s】" % event_id, "")
        # ID 也可能被模型以纯文本复制。边界避免把正常词的一部分错误删掉。
        text = re.sub(r"(?<![A-Za-z0-9_-])%s(?![A-Za-z0-9_-])" % re.escape(event_id),
                      "", text)
        if text != before:
            redacted.append(event_id)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r" *\n *", "\n", text).strip()
    # 模型在中文句内复制 ID 时通常主动插入空格；删除 ID 后只收紧两侧都是汉字
    # 的残留空白，避免破坏英文/数字回复的正常分词。
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    return text, redacted


def parse_responder(raw):
    """responder JSON 输出 → {reply, facts, contradictions, self_state_delta, evidence}。

    承 MANE Agent3→Agent0 洞察：长期台账该由"写的人"（responder 自己）在生成时刻
    就地提交——facts 只容纳稳定自我事实/明确长期承诺，精确归因到这条回复，
    而不是把每句即时反应升格后由旁观者事后猜。
    任何失败 → 纯文本兜底（整段当 reply、facts 空，台账靠批量提取兜底，不崩）。
    """
    import json
    import re
    data = None
    parse_status = "fallback_invalid_json"
    fenced = False
    raw_text = (raw or "").strip()
    try:
        m = re.search(r"```(?:json)?\s*(.*?)\s*```", raw_text, re.S)
        fenced = bool(m)
        data = json.loads(m.group(1) if m else raw_text)
        parse_status = "fenced_json" if fenced else "json"
    except Exception:
        data = None
    protocol_base = {
        "parse_status": parse_status,
        "raw_sha": hashlib.sha256(raw_text.encode("utf-8")).hexdigest()[:16],
        "raw_chars": len(raw_text),
    }
    empty = {"facts": [], "contradictions": [], "used_recall_event_ids": [],
             "recall_expression_evidence": [], "self_state_delta": {},
             "self_state_evidence": []}
    if not isinstance(data, dict):
        return {"reply": clean_reply(raw), **empty, "response_protocol": {
            **protocol_base, "recall_audit_fields": {
                "used_recall_event_ids": "missing",
                "recall_expression_evidence": "missing",
            }}}
    reply = str(data.get("reply") or "").strip()
    if not reply:
        return {"reply": clean_reply(raw), **empty, "response_protocol": {
            **protocol_base, "parse_status": "fallback_missing_reply",
            "recall_audit_fields": {
                "used_recall_event_ids": "missing",
                "recall_expression_evidence": "missing",
            }}}
    facts = []
    if isinstance(data.get("facts"), list):
        facts = [str(f).strip() for f in data["facts"] if str(f).strip()]
    cons = []
    for c in (data.get("contradictions") or []):
        if isinstance(c, dict) and c.get("old") and c.get("new"):
            cons.append({"old": str(c["old"])[:80], "new": str(c["new"])[:80]})
    raw_deltas = data.get("self_state_delta")
    deltas = dict(raw_deltas) if isinstance(raw_deltas, dict) else {}
    evidence = data.get("self_state_evidence")
    evidence = [str(item).strip()[:120] for item in evidence or []
                if str(item).strip()] if isinstance(evidence, list) else []
    used_ids = data.get("used_recall_event_ids")
    used_ids = list(dict.fromkeys(str(item).strip()[:120] for item in used_ids or []
                                  if str(item).strip())) if isinstance(used_ids, list) else []
    recall_evidence = []
    for item in data.get("recall_expression_evidence") or []:
        if not isinstance(item, dict):
            continue
        event_id, quote = str(item.get("event_id") or "").strip(), str(item.get("quote") or "").strip()
        if event_id and quote:
            recall_evidence.append({"event_id": event_id[:120], "quote": quote[:120]})
    audit_fields = {}
    for field in ("used_recall_event_ids", "recall_expression_evidence"):
        value = data.get(field)
        audit_fields[field] = ("declared" if isinstance(value, list)
                               else "missing" if field not in data else "invalid_type")
    return {"reply": reply, "facts": facts, "contradictions": cons,
            "used_recall_event_ids": used_ids,
            "recall_expression_evidence": recall_evidence,
            "self_state_delta": deltas, "self_state_evidence": evidence,
            "response_protocol": {**protocol_base, "recall_audit_fields": audit_fields}}


def _verify_prepared_responder_attempt(packet, *, assembly, journal):
    if not isinstance(journal, WriterAttemptJournal):
        raise ValueError("prepared responder attempt requires WriterAttemptJournal")
    verify_writer_packet(packet)
    if not isinstance(assembly, PromptAssembly):
        raise ValueError("prepared responder attempt requires PromptAssembly")
    if assembly.source_packet_fingerprint != packet.packet_fingerprint:
        raise ValueError("prepared prompt references another WriterPacket")
    artifact = journal.read_attempt(packet.writer_request_id)
    if artifact.get("parent_event_id") != packet.event_id:
        raise ValueError("archived attempt parent differs from WriterPacket")
    if artifact.get("packet") != packet.to_record():
        raise ValueError("archived WriterPacket differs from prepared input")
    expected_prompt = {
        "system": assembly.system,
        "user": assembly.user,
        "prompt_fingerprint": assembly.prompt_fingerprint,
    }
    if artifact.get("prompt") != expected_prompt:
        raise ValueError("archived prompt differs from prepared input")
    if any(stage in artifact for stage in ("provider_dispatch", "provider", "terminal", "commit")):
        raise ValueError("prepared responder attempt has already advanced")


def execute_prepared_responder_attempt(
    packet,
    *,
    assembly,
    journal,
    invoke,
    provider_metadata=None,
):
    """Execute only an exact Packet/prompt pair already archived as prepared."""
    if not callable(invoke):
        raise ValueError("responder attempt invoke must be callable")
    _verify_prepared_responder_attempt(
        packet, assembly=assembly, journal=journal)
    artifact = journal.read_attempt(packet.writer_request_id)
    if artifact.get("provider_dispatch_tracked") is True:
        journal.record_provider_dispatch(packet.writer_request_id)
    try:
        raw = invoke(assembly.system, assembly.user)
    except Exception as exc:
        try:
            journal.record_terminal(
                packet.writer_request_id,
                status="failed",
                error={
                    "stage": "provider_call",
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            )
        except Exception as audit_exc:
            raise WriterAttemptJournalError(
                "provider failure could not be archived; "
                f"original={type(exc).__name__}: {exc}"
            ) from audit_exc
        raise

    raw_candidate = raw if isinstance(raw, str) else str(raw)
    journal.record_provider_result(
        packet.writer_request_id,
        provider_result=provider_metadata,
        candidate=raw_candidate,
    )
    if not isinstance(raw, str):
        error = TypeError("responder provider must return text")
        journal.record_terminal(
            packet.writer_request_id,
            status="failed",
            error={
                "stage": "provider_result",
                "type": type(error).__name__,
                "message": str(error),
            },
        )
        raise error

    try:
        parsed = parse_responder(raw_candidate)
    except Exception as exc:
        journal.record_terminal(
            packet.writer_request_id,
            status="failed",
            error={
                "stage": "response_parse",
                "type": type(exc).__name__,
                "message": str(exc),
            },
        )
        raise
    protocol = parsed.get("response_protocol") or {}
    journal.record_terminal(
        packet.writer_request_id,
        status="candidate_ready",
        provider_result={
            "parse_status": protocol.get("parse_status"),
            "parsed": parsed,
        },
    )
    return ResponderAttemptResult(
        assembly=assembly,
        raw_candidate=raw_candidate,
        parsed=parsed,
    )


def execute_responder_attempt(
    packet,
    *,
    journal,
    invoke,
    provider_metadata=None,
    budget_chars=None,
):
    """Prepare then run one uncommitted Realizer attempt.

    This compatibility entry keeps the existing one-call behavior but delegates
    provider execution to the same prepared-only boundary used by the formal
    product lifecycle.
    """
    assembly = prepare_responder_attempt(
        packet, journal=journal, budget_chars=budget_chars)
    return execute_prepared_responder_attempt(
        packet,
        assembly=assembly,
        journal=journal,
        invoke=invoke,
        provider_metadata=provider_metadata,
    )


def validate_recall_expression(reply, used_event_ids, evidence, allowed_event_ids):
    """限制 responder 的审计声明只能引用本轮已选事件，并校验逐字证据。"""
    allowed = {str(item) for item in allowed_event_ids or []}
    used = [item for item in used_event_ids or [] if item in allowed]
    used = list(dict.fromkeys(used))
    valid_evidence = []
    for item in evidence or []:
        if not isinstance(item, dict):
            continue
        event_id, quote = item.get("event_id"), str(item.get("quote") or "").strip()
        if event_id in used and quote and quote in str(reply or ""):
            valid_evidence.append({"event_id": event_id, "quote": quote})
    return {"used_recall_event_ids": used,
            "recall_expression_evidence": valid_evidence}
