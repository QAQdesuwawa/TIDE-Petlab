"""真实 responder/API 样本的 receipt-first 验收视图。

本模块只读取已提交的本地档案，把一条已发送 NPC 输出还原为可审计的因果链。它
不读取 prompt/message 正文，不调用模型，不写 Store，也不参与 P3、tick、P4、F16
或 writer 决策。它的唯一职责是阻止“文本看起来不错”被误报为总线正确。
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Mapping


SCHEMA_VERSION = "api-receipt-manifest-v1"
_AUTONOMOUS_SOURCES = frozenset({"online_proactive", "followup"})


def _text(value: object) -> str:
    return str(value or "")


def _p3_event_kind(event: Mapping[str, object] | None) -> str:
    """读取 P3 的正式 ``kind``；兼容早期只读导出中的别名。"""
    row = event if isinstance(event, Mapping) else {}
    return _text(row.get("kind") or row.get("event_kind"))


def _p3_event_id(event: Mapping[str, object] | None) -> str:
    """读取 P3 的正式 ``id``；不以时间邻近替代存在的事件主键。"""
    row = event if isinstance(event, Mapping) else {}
    return _text(row.get("id") or row.get("event_id"))


def _p3_event_from_row(row: Mapping[str, object] | None) -> Mapping[str, object]:
    """损坏的旧档只令 manifest 失败，不能让只读审计器异常退出。"""
    detail = (row or {}).get("detail") if isinstance(row, Mapping) else None
    detail = detail if isinstance(detail, Mapping) else {}
    event = detail.get("event")
    return event if isinstance(event, Mapping) else {}


def _checkpoint(name: str, passed: bool, *, required: bool = True,
                detail: str = "") -> dict[str, object]:
    return {
        "name": name,
        "required": bool(required),
        "passed": bool(passed),
        "detail": str(detail),
    }


def _matches(rows: list[dict], *, action_id: str | None = None,
             turn_id: str | None = None, kind: str | None = None) -> list[dict]:
    found = []
    for row in rows:
        if kind is not None and _text(row.get("kind")) != kind:
            continue
        if action_id is not None and _text(row.get("action_id")) != action_id:
            continue
        if turn_id is not None and _text(row.get("turn_id")) != turn_id:
            continue
        found.append(row)
    return found


def _assembly_projection(trace: Mapping[str, object] | None) -> dict[str, object]:
    """只导出装配 receipt 的结构性事实，绝不导出 prompt 或正文。"""
    assembly = ((trace or {}).get("assembly_receipt")
                if isinstance(trace, Mapping) else None)
    if not isinstance(assembly, Mapping):
        return {"present": False}
    delivery = assembly.get("recall_delivery")
    delivery = delivery if isinstance(delivery, Mapping) else {}
    delivery_decisions = tuple(
        row for row in (delivery.get("event_decisions") or ()) if isinstance(row, Mapping))
    frame = assembly.get("reference_frame")
    frame = frame if isinstance(frame, Mapping) else {}
    attention = assembly.get("expression_attention")
    attention = attention if isinstance(attention, Mapping) else {}
    temporal = assembly.get("temporal_expression")
    temporal = temporal if isinstance(temporal, Mapping) else {}
    interaction_residual = temporal.get("interaction_residual")
    interaction_residual = (interaction_residual
                            if isinstance(interaction_residual, Mapping) else {})
    attention_tokens = tuple(attention.get("tokens") or ())
    residual_delivered = any(
        isinstance(row, Mapping)
        and _text((row.get("source") or {}).get("lane")) == "npc_interaction_residual"
        and _text(row.get("delivery")) == "writer_material"
        for row in attention_tokens)
    emission = attention.get("emission")
    emission = emission if isinstance(emission, Mapping) else {}
    emission_plan = emission.get("plan")
    emission_plan = emission_plan if isinstance(emission_plan, Mapping) else None
    selected = (emission_plan.get("selected_token_keys")
                if isinstance(emission_plan, Mapping) else ())
    selected = selected if isinstance(selected, list) else ()
    delivered = emission.get("delivered_token_keys")
    delivered = delivered if isinstance(delivered, list) else ()
    deferred_count = sum(
        1 for row in attention_tokens
        if isinstance(row, Mapping) and _text(row.get("delivery")) == "writer_deferred")
    residual_deferred = any(
        isinstance(row, Mapping)
        and _text((row.get("source") or {}).get("lane")) == "npc_interaction_residual"
        and _text(row.get("delivery")) == "writer_deferred"
        for row in attention_tokens)
    residual_view = {
        "available": bool(interaction_residual.get("available")),
        "source_action_present": bool(interaction_residual.get("action_id")),
        "delivered": residual_delivered,
    }
    # “未交付”不是一律错误：正式量化器可在没有 slot 时明确延后；只有 plan
    # 与 deferred receipt 同时存在才算合法，不得把旧路径的漏投递粉饰成 defer。
    if residual_deferred:
        residual_view["formally_deferred"] = emission_plan is not None
    return {
        "present": True,
        "recall_delivery": {
            "recalled_count": len(tuple(delivery.get("recalled_event_ids") or ())),
            "delivered_count": len(tuple(delivery.get("delivered_event_ids") or ())),
            "suppressed_count": len(tuple(delivery.get("suppressed") or ())),
            "event_decision_count": len(delivery_decisions),
            "exact_delivery_count": sum(
                1 for row in delivery_decisions
                if _text(row.get("representation")) == "discrete_recall_card"
                and _text(row.get("identity_kind")) == "exact_committed_event"),
            "legacy_delivery_count": sum(
                1 for row in delivery_decisions
                if _text(row.get("representation")) == "discrete_recall_card"
                and _text(row.get("identity_kind")) == "legacy_no_event_id"),
        },
        "reference_frame": {
            "resolved": bool(frame.get("resolved")),
            "delivery_scope": frame.get("delivery_scope"),
        },
        "expression": {
            "attention_enabled": bool(attention.get("enabled")),
            "current_root_state_count": int(attention.get("current_root_state_count") or 0),
            "interaction_residual": residual_view,
            # Manifest 只公开 formal plan 是否存在、版本及计数，不导出 token 文本、
            # descriptor 输入 receipt 或 token key；这些可从同 action 本地收据复核。
            "emission": {
                "plan_present": emission_plan is not None,
                "plan_version": (_text(emission_plan.get("version"))
                                 if emission_plan is not None else None),
                "selected_count": len(selected),
                "delivered_count": len(delivered),
                "deferred_count": deferred_count,
            },
        },
    }


def _p3_event_for_dispatch(events: list[dict], dispatch: Mapping[str, object]) -> dict | None:
    detail = dispatch.get("detail") if isinstance(dispatch.get("detail"), Mapping) else {}
    authority = detail.get("authority") if isinstance(detail.get("authority"), Mapping) else {}
    event_id = _text(authority.get("event_id"))
    observed_at = authority.get("observed_at_ms")
    for row in events:
        if _text(row.get("kind")) != "opening_authority_receipt":
            continue
        event = _p3_event_from_row(row)
        if event_id and _p3_event_id(event) == event_id:
            return row
        if observed_at is not None and row.get("ts") == observed_at:
            return row
    return None


def build_action_manifest(store: object, action_id: str) -> dict[str, object]:
    """只读构造一条 NPC 输出的因果 manifest。

    `action_id` 是唯一主键。若历史档缺少新 receipt，manifest 会明确失败；它不会
    以消息、UI、模型返回或“最接近的旧事件”猜测补齐。
    """
    action_id = _text(action_id)
    messages = list(getattr(store, "read_messages")())
    actions = list(getattr(store, "read_action_receipts")())
    events = list(getattr(store, "read_events")())
    traces = list(getattr(store, "read_prompt_traces")())

    output_rows = [row for row in messages
                   if _text(row.get("role")) == "npc"
                   and _text(row.get("action_id")) == action_id]
    output = output_rows[0] if len(output_rows) == 1 else {}
    source = _text(output.get("source"))
    turn_id = _text(output.get("turn_id"))
    checkpoints: list[dict[str, object]] = [
        _checkpoint("output_message", len(output_rows) == 1,
                    detail="exactly_one" if len(output_rows) == 1
                    else f"count={len(output_rows)}"),
    ]

    action_rows = _matches(actions, action_id=action_id)
    action = action_rows[0] if len(action_rows) == 1 else {}
    checkpoints.append(_checkpoint(
        "f16_action_receipt", len(action_rows) == 1
        and _text(action.get("source")) == source
        and (not turn_id or _text(action.get("turn_id")) == turn_id),
        detail="exactly_one_matching_output" if len(action_rows) == 1
        else f"count={len(action_rows)}"))

    prepared_rows = _matches(events, action_id=action_id, kind="output_commit_prepared")
    prepared = prepared_rows[0] if len(prepared_rows) == 1 else {}
    checkpoints.append(_checkpoint(
        "f16_prepared_commit", len(prepared_rows) == 1
        and (not turn_id or _text(prepared.get("turn_id")) == turn_id),
        detail="exactly_one" if len(prepared_rows) == 1 else f"count={len(prepared_rows)}"))

    state_rows = _matches(events, action_id=action_id, kind="output_state_committed")
    checkpoints.append(_checkpoint(
        "f16_state_committed_once", len(state_rows) == 1,
        detail="exactly_once" if len(state_rows) == 1 else f"count={len(state_rows)}"))

    # AUD-021：新 output transaction 的根变化只能来自 F16 后的 Agent 1 measurement。
    # 旧档没有这个 declared contract 时不倒填、不误判；新档缺失则明确令 action
    # manifest 失败，而不是以 writer self delta 或通用 release 猜补。
    output_semantic_required = bool(prepared.get("output_semantic_required"))
    output_semantic_rows = _matches(
        events, action_id=action_id, kind="output_semantic_measurement_committed")
    output_semantic = output_semantic_rows[0] if len(output_semantic_rows) == 1 else {}
    semantic_matches = (len(output_semantic_rows) == 1
                        and (not turn_id or _text(output_semantic.get("turn_id")) == turn_id)
                        and isinstance(output_semantic.get("delta_x"), Mapping))
    checkpoints.append(_checkpoint(
        "f16_output_semantic_measurement", (not output_semantic_required) or semantic_matches,
        required=output_semantic_required,
        detail=("legacy_not_required" if not output_semantic_required
                else "exactly_one" if semantic_matches
                else f"count={len(output_semantic_rows)}")))

    trace_rows = _matches(traces, turn_id=turn_id) if turn_id else []
    trace = trace_rows[-1] if trace_rows else None
    assembly = _assembly_projection(trace)
    raw_assembly = ((trace or {}).get("assembly_receipt")
                    if isinstance(trace, Mapping) else None)
    raw_assembly = raw_assembly if isinstance(raw_assembly, Mapping) else {}
    checkpoints.append(_checkpoint(
        "expression_assembly_receipt", bool(assembly.get("present")),
        detail="prompt_body_omitted" if assembly.get("present") else "missing"))

    binding = prepared.get("expression_binding") if isinstance(prepared, Mapping) else None
    checkpoints.append(_checkpoint(
        "expression_constant_binding", isinstance(binding, Mapping)
        and bool(_text(binding.get("effective_constants_version"))),
        detail="prepared_same_version_binding" if isinstance(binding, Mapping) else "missing"))

    # E5：若 actual output 已冻结 formal emission plan，manifest 必须能证明 trace
    # 与 F16 后提交属于同一 action；旧档没有该字段时不倒填，也不误报失败。
    staged_emission = (prepared.get("expression_emission")
                       if isinstance(prepared, Mapping) else None)
    staged_emission = staged_emission if isinstance(staged_emission, Mapping) else None
    staged_plan = (staged_emission.get("plan")
                   if isinstance(staged_emission, Mapping) else None)
    staged_plan = staged_plan if isinstance(staged_plan, Mapping) else None
    staged_delivered = (staged_emission.get("delivered_token_keys")
                        if isinstance(staged_emission, Mapping) else None)
    staged_delivered = staged_delivered if isinstance(staged_delivered, list) else None
    emission_required = staged_plan is not None and staged_delivered is not None
    emission_rows = _matches(events, action_id=action_id,
                             kind="expression_emission_committed")
    emission_commit = emission_rows[0] if len(emission_rows) == 1 else {}
    emission_detail = (emission_commit.get("detail")
                       if isinstance(emission_commit, Mapping) else None)
    emission_detail = emission_detail if isinstance(emission_detail, Mapping) else {}
    assembly_emission = ((assembly.get("expression") or {}).get("emission")
                         if isinstance(assembly, Mapping) else {})
    assembly_emission = assembly_emission if isinstance(assembly_emission, Mapping) else {}
    staged_version = _text(staged_plan.get("version")) if staged_plan else ""
    same_trace_plan = (bool(assembly_emission.get("plan_present"))
                       and _text(assembly_emission.get("plan_version")) == staged_version)
    same_delivery = (int(assembly_emission.get("delivered_count") or 0)
                     == len(staged_delivered or ()))
    same_commit = (bool(emission_detail.get("committed"))
                   and set(_text(key) for key in emission_detail.get("delivered_token_keys") or ())
                   == set(_text(key) for key in staged_delivered or ())
                   and isinstance(emission_detail.get("remainder_updates"), Mapping))
    checkpoints.append(_checkpoint(
        "expression_emission_f16_commit", (not emission_required)
        or (len(emission_rows) == 1 and same_trace_plan and same_delivery and same_commit),
        required=emission_required,
        detail=("not_formal_emission" if not emission_required
                else "same_action_plan_delivery_and_remainder"
                if len(emission_rows) == 1 and same_trace_plan and same_delivery and same_commit
                else "missing_or_mismatched_formal_emission_commit")))

    residual = ((assembly.get("expression") or {}).get("interaction_residual")
                if isinstance(assembly, Mapping) else {})
    residual = residual if isinstance(residual, Mapping) else {}
    residual_available = bool(residual.get("available"))
    checkpoints.append(_checkpoint(
        "interaction_residual_delivery", (not residual_available)
        or bool(residual.get("delivered")) or bool(residual.get("formally_deferred")),
        required=residual_available,
        detail=("not_applicable" if not residual_available
                else "writer_material" if residual.get("delivered")
                else "formal_deferred" if residual.get("formally_deferred")
                else "missing_writer_delivery")))

    input_rows = _matches(events, turn_id=turn_id, kind="input_measurement_committed")
    p3_rows = _matches(events, turn_id=turn_id, kind="opening_authority_receipt")
    if source in _AUTONOMOUS_SOURCES:
        dispatch_rows = _matches(events, action_id=action_id, kind="opening_dispatch_committed")
        dispatch = dispatch_rows[0] if len(dispatch_rows) == 1 else {}
        p3 = _p3_event_for_dispatch(events, dispatch) if dispatch else None
        p3_detail = p3.get("detail") if isinstance(p3, Mapping) else {}
        p3_event = (p3_detail.get("event") if isinstance(p3_detail, Mapping) else {})
        dispatch_detail = dispatch.get("detail") if isinstance(dispatch, Mapping) else {}
        candidate = (dispatch_detail.get("opening_candidate")
                     if isinstance(dispatch_detail, Mapping) else None)
        checkpoints.extend((
            _checkpoint("f08_tick_authority", isinstance(p3_event, Mapping)
                        and _p3_event_kind(p3_event) == "tick",
                        detail=("linked_p3_tick" if isinstance(p3_event, Mapping)
                                and _p3_event_kind(p3_event) == "tick"
                                else "missing_dispatch_to_tick_p3_link")),
            _checkpoint("f15_frozen_crossing", isinstance(candidate, Mapping)
                        and bool(candidate.get("eligible")),
                        detail="frozen_eligible_candidate" if isinstance(candidate, Mapping)
                        else "missing"),
            _checkpoint("tick_only_opening_dispatch", len(dispatch_rows) == 1,
                        detail="exactly_one" if len(dispatch_rows) == 1
                        else f"count={len(dispatch_rows)}"),
        ))
    else:
        input_p3_rows = [
            row for row in p3_rows
            if _p3_event_kind(_p3_event_from_row(row)) in {
                "user_input", "human_input", "peer_input", "simulation_input"}
            and _p3_event_id(_p3_event_from_row(row)) == turn_id
        ]
        checkpoints.extend((
            _checkpoint("f03_input_committed", len(input_rows) == 1,
                        detail="exactly_one" if len(input_rows) == 1 else f"count={len(input_rows)}"),
            _checkpoint("p3_authority_after_f03", len(input_p3_rows) == 1,
                        detail="exactly_one_received_input_p3" if len(input_p3_rows) == 1
                        else f"received_input_count={len(input_p3_rows)}"),
        ))

    # 检查 exact delivery 时读取本机同 action 的原始无正文 assembly receipt；公开
    # manifest 仍只导出计数，不能因此成为 prompt／正文旁路。
    recall_delivery = raw_assembly.get("recall_delivery")
    recall_delivery = recall_delivery if isinstance(recall_delivery, Mapping) else {}
    delivered_count = int((recall_delivery or {}).get("delivered_count") or 0)
    recall_refs = tuple(action.get("recall_refs") or ()) if isinstance(action, Mapping) else ()
    delivery_decisions = tuple(
        row for row in ((recall_delivery or {}).get("event_decisions") or ())
        if isinstance(row, Mapping))
    if delivery_decisions:
        expected_exact = {
            _text(row.get("event_id")) for row in delivery_decisions
            if (_text(row.get("representation")) == "discrete_recall_card"
                and _text(row.get("identity_kind")) == "exact_committed_event")
        }
        expected_legacy = {
            (_text(row.get("source")), int(row.get("record_ts")))
            for row in delivery_decisions
            if (_text(row.get("representation")) == "discrete_recall_card"
                and _text(row.get("identity_kind")) == "legacy_no_event_id")
        }
        actual_exact = {
            _text(row.get("event_id")) for row in recall_refs if isinstance(row, Mapping)
            and _text(row.get("identity_kind")) == "exact_committed_event"
        }
        actual_legacy = {
            (_text(row.get("source")), int(row.get("record_ts")))
            for row in recall_refs if isinstance(row, Mapping)
            and _text(row.get("identity_kind")) == "legacy_no_event_id"
        }
        delivery_matches_output = (expected_exact == actual_exact
                                   and expected_legacy == actual_legacy)
        delivery_detail = ("exact_event_refs_match" if delivery_matches_output
                           else "event_identity_mismatch")
    else:
        # 旧 trace 尚未发布逐 event decision，只能保留历史长度检查，不得假装
        # 它已经具备 A3-5d 的 exact identity 证据。
        delivery_matches_output = delivered_count == 0 or len(recall_refs) > 0
        delivery_detail = ("no_delivery" if delivered_count == 0
                           else "legacy_receipt_has_recall_refs")
    checkpoints.append(_checkpoint(
        "recall_delivery_matches_output", delivery_matches_output,
        detail=delivery_detail))

    state_batches = [row for row in events if _text(row.get("kind")) == "memory_state_batch_committed"
                     and (_text(row.get("action_id")) == action_id
                          or (turn_id and _text(row.get("turn_id")) == turn_id))]
    suppressed = [row for row in events if _text(row.get("kind")) == "memory_state_recall_suppressed"
                  and (_text(row.get("action_id")) == action_id
                       or (turn_id and _text(row.get("turn_id")) == turn_id))]
    p4_decisions = [row for row in events
                    if _text(row.get("kind")) == "memory_state_selection_evaluated"
                    and (_text(row.get("action_id")) == action_id
                         or (turn_id and _text(row.get("turn_id")) == turn_id))]
    decision = (p4_decisions[0].get("detail")
                if len(p4_decisions) == 1 and isinstance(p4_decisions[0].get("detail"), Mapping)
                else {})
    outcome = _text(decision.get("outcome"))
    known_outcomes = {
        "state_impact_required", "state_impact_blocked", "state_suppressed",
        "state_ineligible", "no_state_selected_record",
    }
    checkpoints.append(_checkpoint(
        "p4_selection_decision_exactly_once",
        (not p4_decisions) or (len(p4_decisions) == 1 and outcome in known_outcomes),
        required=bool(p4_decisions),
        detail=("not_recorded_legacy_trace" if not p4_decisions
                else outcome if len(p4_decisions) == 1 and outcome in known_outcomes
                else f"decision_count={len(p4_decisions)} outcome={outcome or 'missing'}")))

    # writer delivery and P4 must never be inferred from one another.  A decision
    # declares whether selected RAG records have a qualified state imprint; the
    # actual batch proves the one permitted state write.  Older traces predate the
    # decision receipt, so an already committed batch remains auditable but a
    # missing historical decision is explicitly labelled rather than guessed.
    if outcome == "state_impact_required":
        p4_required, p4_passed, p4_detail = True, len(state_batches) == 1, (
            "exactly_once" if len(state_batches) == 1
            else f"batch_count={len(state_batches)}")
    elif outcome == "state_suppressed":
        p4_required, p4_passed, p4_detail = True, (bool(suppressed) and not state_batches), (
            "state_suppressed" if bool(suppressed) and not state_batches
            else f"suppression_count={len(suppressed)} batch_count={len(state_batches)}")
    elif outcome in {"state_ineligible", "no_state_selected_record"}:
        p4_required, p4_passed, p4_detail = True, not state_batches, (
            outcome if not state_batches else f"unexpected_batch_count={len(state_batches)}")
    elif outcome == "state_impact_blocked":
        p4_required, p4_passed, p4_detail = True, False, "uncommittable_selection"
    elif state_batches:
        p4_required, p4_passed, p4_detail = True, len(state_batches) == 1, (
            "exactly_once_legacy_trace" if len(state_batches) == 1
            else f"batch_count={len(state_batches)}")
    elif suppressed:
        p4_required, p4_passed, p4_detail = True, True, "state_suppressed_legacy_trace"
    else:
        p4_required, p4_passed, p4_detail = False, True, "no_p4_state_impact_observed"
    checkpoints.append(_checkpoint(
        "p4_state_impact_contract", p4_passed,
        required=p4_required, detail=p4_detail))

    failed = [row["name"] for row in checkpoints if row["required"] and not row["passed"]]
    return {
        "schema_version": SCHEMA_VERSION,
        "privacy": "no_message_body_no_prompt_body_no_model_response",
        "action": {
            "action_id": action_id,
            "turn_id": turn_id or None,
            "source": source or None,
            "sent_ms": output.get("ts") if output else None,
        },
        "assembly": assembly,
        "checkpoints": checkpoints,
        "passed": not failed,
        "failed_checkpoints": failed,
    }


def build_session_manifest(store: object, *, action_ids: tuple[str, ...] | None = None) -> dict[str, object]:
    """导出一个会话中指定（或全部）已提交 NPC action 的无正文验收包。"""
    known = []
    for row in getattr(store, "read_messages")():
        if _text(row.get("role")) != "npc":
            continue
        action_id = _text(row.get("action_id"))
        if action_id and action_id not in known:
            known.append(action_id)
    wanted = tuple(_text(value) for value in action_ids or known if _text(value))
    actions = [build_action_manifest(store, action_id) for action_id in wanted]
    return {
        "schema_version": SCHEMA_VERSION,
        "privacy": "no_message_body_no_prompt_body_no_model_response",
        "action_count": len(actions),
        "passed": bool(actions) and all(bool(item["passed"]) for item in actions),
        "actions": actions,
    }
