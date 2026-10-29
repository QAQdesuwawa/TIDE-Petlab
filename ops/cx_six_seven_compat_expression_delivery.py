"""将六七兼容 ``ExpressionEmissionPlan`` 装配为旧 writer 只读表现材料。

这里不再使用 F07 rank、category fallback、fit multiplier、bridge hint 或固定 Top-N。
唯一可交付的材料是：同一 action 的 plan 已选 key 与携带相同正式 descriptor 的词元卡。

这是迁移期产品兼容实现；正式 v2 走 ExpressionResourceBatch → WriterPacketAssembler。
"""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from model_packages.cx_six_seven_v1.expression_emission import (
    EMISSION_PLAN_VERSION,
    MAPPING_DESCRIPTOR_VERSION,
)


_SOURCE_LANES = {
    "current_drive_state": "npc_drive_state",
    "committed_event_transition": "npc_event_state",
    "committed_memory_transition": "npc_memory_state",
    "committed_time_transition": "npc_time_state",
    "committed_output_transition": "npc_output_transition",
    "committed_interaction_residual": "npc_interaction_residual",
}


@dataclass(frozen=True)
class ExpressionDelivery:
    """已交给 writer 的无行为权限材料及无正文 receipt。"""

    lines: tuple[str, ...]
    current_root_state: tuple[str, ...]
    receipt: tuple[dict[str, object], ...]
    delivered_token_keys: tuple[str, ...]
    # 只保留编排元数据；不含 writer 文本。F16 必须消费这一份同 action 的 plan，
    # 才能把实际 delivery 结算成下一轮的余量，不允许重新编排。
    emission_plan: dict[str, object] | None = None
    feedback_fact: None = None
    fit_projection: None = None


def _descriptor_identity(value: object) -> tuple[str, str, str] | None:
    if not isinstance(value, Mapping):
        return None
    if str(value.get("version") or "") != MAPPING_DESCRIPTOR_VERSION:
        return None
    token_key = str(value.get("token_key") or "").strip()
    source = str(value.get("source") or "").strip()
    axis = str(value.get("axis") or "").strip()
    if not token_key or not source or not axis:
        return None
    return token_key, source, axis


def _plan_candidate_by_key(plan: Mapping[str, object]) -> dict[str, Mapping[str, object]]:
    candidates = plan.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("invalid expression emission plan candidates")
    result: dict[str, Mapping[str, object]] = {}
    for row in candidates:
        identity = _descriptor_identity(row)
        if identity is None:
            raise ValueError("invalid expression emission plan candidate")
        token_key, _source, _axis = identity
        if token_key in result:
            raise ValueError("duplicate expression emission plan candidate key")
        result[token_key] = row
    return result


def _receipt_row(*, token_key: str | None, source: str | None, axis: str | None,
                 delivery: str, reason: str | None = None) -> dict[str, object]:
    lane = _SOURCE_LANES.get(str(source or ""), "unclassified")
    row: dict[str, object] = {
        "token_key": token_key,
        # 保持既有 trace 的 source receipt 形状；原始正式 source 名单单列，
        # 供 E3 断言“不存在 legacy_f07”而不把 schema 再割裂一次。
        "source": {
            "lane": lane,
            "owner": "npc",
            "expression_eligible": source in _SOURCE_LANES,
        },
        "expression_source": source,
        "axis": axis,
        "lane": lane,
        "delivery": delivery,
    }
    if reason is not None:
        row["reason"] = reason
    return row


def compile_expression_delivery(*, cards: Iterable[object],
                                emission_plan: Mapping[str, object]) -> ExpressionDelivery:
    """从已选 plan key 建立分包 writer 材料，缺契约一律不猜测。"""
    if str(emission_plan.get("version") or "") != EMISSION_PLAN_VERSION:
        raise ValueError("invalid expression emission plan")
    candidates = _plan_candidate_by_key(emission_plan)
    selected = emission_plan.get("selected_token_keys")
    if not isinstance(selected, list) or len(selected) != len(set(selected)):
        raise ValueError("invalid expression emission selected keys")

    cards_by_key: dict[str, list[Mapping[str, object]]] = {}
    receipt: list[dict[str, object]] = []
    for raw in cards:
        if not isinstance(raw, Mapping):
            receipt.append(_receipt_row(token_key=None, source=None, axis=None,
                                        delivery="writer_deferred",
                                        reason="invalid_expression_card"))
            continue
        descriptor = raw.get("expression_descriptor")
        identity = _descriptor_identity(descriptor)
        if identity is None:
            receipt.append(_receipt_row(token_key=None, source=None, axis=None,
                                        delivery="writer_deferred",
                                        reason="missing_expression_descriptor"))
            continue
        token_key, source, axis = identity
        cards_by_key.setdefault(token_key, []).append(raw)

    lines: list[str] = []
    current_root_state: list[str] = []
    delivered: list[str] = []
    selected_keys = {str(key) for key in selected}
    for token_key in (str(key) for key in selected):
        planned = candidates.get(token_key)
        if planned is None:
            raise ValueError("selected expression key absent from plan candidates")
        identity = _descriptor_identity(planned)
        assert identity is not None
        _planned_key, source, axis = identity
        matches = cards_by_key.get(token_key, [])
        if len(matches) != 1:
            receipt.append(_receipt_row(
                token_key=token_key, source=source, axis=axis,
                delivery="writer_deferred",
                reason=("missing_card_for_selected_token" if not matches
                        else "duplicate_card_descriptor"),
            ))
            continue
        card = matches[0]
        card_identity = _descriptor_identity(card.get("expression_descriptor"))
        if card_identity != identity:
            receipt.append(_receipt_row(token_key=token_key, source=source, axis=axis,
                                        delivery="writer_deferred",
                                        reason="descriptor_plan_mismatch"))
            continue
        if source not in _SOURCE_LANES:
            receipt.append(_receipt_row(token_key=token_key, source=source, axis=axis,
                                        delivery="writer_deferred",
                                        reason="unsupported_expression_source"))
            continue
        text = str(card.get("text") or "").strip()
        if not text:
            receipt.append(_receipt_row(token_key=token_key, source=source, axis=axis,
                                        delivery="writer_deferred",
                                        reason="empty_expression_material"))
            continue
        if source == "current_drive_state":
            current_root_state.append(text)
            delivery = "writer_current_root_state"
        else:
            lines.append("- " + text)
            delivery = "writer_material"
        delivered.append(token_key)
        receipt.append(_receipt_row(token_key=token_key, source=source, axis=axis,
                                    delivery=delivery))

    # 正式 plan 内未获 slot 的候选同样留下 receipt；不由技术装配器再次排序或补位。
    for token_key, candidate in candidates.items():
        if token_key in selected_keys:
            continue
        identity = _descriptor_identity(candidate)
        assert identity is not None
        _key, source, axis = identity
        receipt.append(_receipt_row(token_key=token_key, source=source, axis=axis,
                                    delivery="writer_deferred", reason="plan_not_selected"))

    return ExpressionDelivery(
        lines=tuple(lines), current_root_state=tuple(current_root_state),
        receipt=tuple(receipt), delivered_token_keys=tuple(delivered),
        emission_plan=dict(emission_plan),
    )
