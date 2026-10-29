"""TIDE 的词元语义适配与表达投递（E0.1，纯函数）。

MANE 的 token 文本可描述两个虚拟角色之间的策略；TIDE 中用户不是可控行动主体。
本模块不改原始 token、分数、时间场或记忆选择，只恢复 token 在生成层所需的
来源和消费者边界：外部偏好/相性/战术是调度读数，NPC 状态才是对白素材。
"""
from dataclasses import dataclass
from typing import Optional


_FALLBACK_SOURCE = {
    # 没有来源旁路的旧调用保持安全：带“对方/策略”语义的类别不直送语言模型。
    "compat": ("external_fit", "relation", False),
    "nature": ("npc_self_comparison", "npc", False),
    "gameplay": ("npc_user_measurement", "relation", False),
    "tactics": ("mane_interaction_strategy", "relation", False),
    "relation": ("relationship_context", "relation", False),
    # 原子标签来自人格常量，不是本轮状态；无来源的旧调用也必须安全地阻止其直送。
    "atomic": ("npc_persona_measurement", "npc", False),
    "trend": ("npc_time_change", "npc", True),
    "phase": ("npc_variable_state", "npc", True),
    "pain": ("npc_variable_state", "npc", True),
    "expression_drive_state": ("npc_drive_state", "npc", True),
    "expression_time_transition": ("npc_output_transition", "npc", True),
    "expression_memory_transition": ("npc_memory_state", "npc", True),
    "expression_event_transition": ("npc_event_state", "npc", True),
    "expression_interaction_residual": ("npc_interaction_residual", "npc", True),
    "expression_time_state_transition": ("npc_time_state", "npc", True),
    "other": ("unclassified", "unknown", False),
}

_WRITER_SURFACES = {
    "npc_internal_state": "角色此刻的内在状态",
    "npc_variable_state": "角色此刻的状态变化",
    "npc_drive_state": "角色此刻的三向底色",
    "npc_event_state": "当前互动带来的状态变化",
    "npc_interaction_residual": "上一段互动尚未收住的状态方向",
    "npc_memory_state": "已被唤起的记忆余波",
    "npc_time_state": "真实墙钟推进后的状态变化",
    "npc_time_change": "时间流过后角色自身的变化",
    # 已发出的上一条话与其后的墙钟推进是可追溯事件事实。它只帮助作者区分
    # “刚说完后的续话”与“隔一段时间后的重新开口”，绝不取得调度权限。
    "npc_output_transition": "已发生输出后的状态变化",
    # 浓度卡是既有 fit 总线生成的当轮观测，不是 MANE 的双角色行动词元；它可作为
    # 对白作家理解当前语境的材料，但前缀仍只描述观察，不规定安慰/顺应等动作。
    "user_emotional_context": "对方此刻的情绪线索",
}


def _source_for(token, source_row):
    """返回既有计算的来源旁路；缺失时按安全的类别契约回退。"""
    source = source_row.get("source") if isinstance(source_row, dict) else None
    if isinstance(source, dict):
        return dict(source)
    category = str(token.get("category", "other"))
    lane, owner, eligible = _FALLBACK_SOURCE.get(category, _FALLBACK_SOURCE["other"])
    return {"lane": lane, "owner": owner, "expression_eligible": eligible}


def _level(value, maximum):
    """只按同一轮已获表达资格的 rank 分数分档，无额外 P 乘子。"""
    if maximum <= 0:
        return "弱"
    ratio = value / maximum
    if ratio >= 0.7:
        return "强"
    if ratio >= 0.4:
        return "中"
    return "弱"


def _writer_prefix(lane, level):
    """按来源类型写只读素材标识，而非为具体 token 预写台词或动作。"""
    surface = _WRITER_SURFACES[lane]
    return "【状态素材·%s·%s】" % (surface, level)


@dataclass(frozen=True)
class ExpressionAttentionPlan:
    """本轮临时投递计划；receipt 不含用户原文或完整 prompt。"""

    lines: tuple
    receipt: tuple
    feedback_fact: Optional[str]
    fit_projection: Optional[dict]
    # 当前根量经三驱动力投影的底色卡不是资源排序结果。它在主动续话中以已结算
    # ``C_effective + X`` 的只读观察进入单独栏目；不能因缺少 rank score 被误标
    # 为“弱”、也不占普通 Top-N 带宽。
    current_root_state: tuple = ()


def compile_expression_attention(*, ranked, field_pressure=None, bridge_tokens=(),
                                 feedback_context=None, source_receipt=(),
                                 max_writer_tokens=3, fit_projection=None):
    """把已排序词元适配为 TIDE 单 NPC 的表达素材。

    ``field_pressure`` 保留在签名中以兼容 E0 调用和 trace；E0.1 不再用它把 compat
    放大成语言层指令。外部偏好仍在原有总线、排序和评估中生效，只失去直送 LLM 的资格。
    """
    del field_pressure  # 本版本刻意不再把压力投影成 compat 的语言放大器。
    bridge_texts = {str(token.get("text", "")) for token in (bridge_tokens or [])}
    source_rows = list(source_receipt or ())
    rows = []
    for index, token in enumerate((ranked or {}).get("all", ())):
        item = dict(token)
        source = _source_for(item, source_rows[index] if index < len(source_rows) else None)
        lane = str(source.get("lane", "unclassified"))
        eligible = bool(source.get("expression_eligible")) and lane in _WRITER_SURFACES
        score = float(item.get("score", item.get("strength", 0.0)) or 0.0)
        # B0 不直接作为词元/行为指令交给小说家。它只能通过受限投影重排已有 NPC
        # 状态或时间素材；其他来源（含 user_preference/compat）永远不因此取得表达资格。
        fit_weight = (float(fit_projection.weight_for(lane))
                      if eligible and fit_projection is not None else 1.0)
        rows.append({"token": item, "source": source, "eligible": eligible,
                     "attention": score * fit_weight if eligible else 0.0,
                     "fit_weight": fit_weight,
                     "time_bridge": str(item.get("text", "")) in bridge_texts})

    root_state_rows = [
        row for row in rows
        if row["eligible"]
        and str(row["token"].get("expression_source", "")) in {
            "current_root_state", "current_drive_state"}
    ]
    root_state_ids = {id(row) for row in root_state_rows}
    # 当前相位是既有 X 的事实投影，而不是 resource-ranked token。若强行让它和
    # trend 的 score 比较，phase token 的天然零 score 会把“当前状态”错误写成
    # “弱、不抢话题”。独立交付只改变 writer 的可见材料，不会回流到选择或调度。
    current_root_state = tuple(
        str(row["token"].get("text", "")).strip()
        for row in root_state_rows
        if str(row["token"].get("text", "")).strip()
    )
    eligible_rows = [row for row in rows
                     if row["eligible"] and id(row) not in root_state_ids]

    def _rank_score(row):
        return float(row["token"].get("score", row["token"].get("strength", 0.0)) or 0.0)

    def _base_key(row):
        return (-_rank_score(row),
                str(row["token"].get("text", "")))

    def _fit_key(row):
        return (-row["attention"],
                -_rank_score(row),
                str(row["token"].get("text", "")))

    # 表达侧的容量仅是 prompt 带宽，不参与时间场物理。候选次序完全复用本轮资源账本
    # 已结算的 score；不引入新的心理变量、类别配额或 token→台词映射。
    capacity = max(0, int(max_writer_tokens))
    # 受限拟合投影只允许改 NPC 状态/时间两条来源的相对次序。已有非 NPC 表达素材
    # （当前用户情绪语境、明确反馈）若按原账本本可入选，必须保留席位；否则 B0 会
    # 反向挤掉用户刚刚交出的事实，变成不安全的跨来源抢占。
    base_selected = sorted(eligible_rows, key=_base_key)[:capacity]
    anchored = [row for row in base_selected
                if row["source"].get("lane") not in {
                    "npc_variable_state", "npc_drive_state", "npc_time_change",
                    "npc_output_transition", "npc_memory_state", "npc_event_state",
                    "npc_interaction_residual", "npc_time_state"}]
    selected_rows = list(anchored)
    remaining = capacity - len(selected_rows)
    npc_rows = [row for row in eligible_rows if row not in anchored]
    selected_rows.extend(sorted(npc_rows, key=_fit_key)[:max(0, remaining)])
    selected_rows.sort(key=_fit_key)
    selected_ids = {id(row) for row in selected_rows}
    maximum = max((row["attention"] for row in selected_rows), default=0.0)

    lines = []
    for row in selected_rows:
        level = _level(row["attention"], maximum)
        row["attention_level"] = level
        lines.append("- %s %s" % (
            _writer_prefix(str(row["source"]["lane"]), level),
            str(row["token"].get("text", ""))))

    receipt = []
    for row in rows:
        token, source = row["token"], row["source"]
        receipt.append({
            "text": str(token.get("text", "")),
            "category": str(token.get("category", "other")),
            "layer": str(token.get("layer", "表")),
            "rank_score": float(token.get("score", 0.0) or 0.0),
            "token_strength": float(token.get("strength", 0.0) or 0.0),
            "source": source,
            "delivery": ("writer_current_root_state" if id(row) in root_state_ids
                         else "writer_material" if id(row) in selected_ids
                         else "writer_deferred" if row["eligible"]
                         else "scheduler_only"),
            "attention": row["attention"],
            "fit_weight": row["fit_weight"],
            "attention_level": row.get("attention_level"),
            "time_bridge": row["time_bridge"],
        })

    feedback_fact = None
    if isinstance(feedback_context, dict) and feedback_context.get("meaning"):
        feedback_fact = "【对话事实】%s。" % str(feedback_context["meaning"])
    projection_record = (fit_projection.as_record()
                         if fit_projection is not None and hasattr(fit_projection, "as_record")
                         else None)
    return ExpressionAttentionPlan(
        tuple(lines), tuple(receipt), feedback_fact, projection_record,
        current_root_state=current_root_state)
