"""用户消息的确定性输入信号解析。

这一层只将文本、未曝光弹药和上一轮 extractor 结果编译成时间总线需要的
数值信号。它不保存 profile、不读取 Store，也不推进 temporal 状态；因此可
在不启动 Runner 事务的情况下单独验证。
"""
from dataclasses import dataclass

from core.params import AMMO_HIT_DELTAS, DICT_DELTAS, FITTING, SALIENCE
from ops.ammo import scan_ammo
from interaction_extensions.preference_calibration.cx_six_seven.fitting import (
    dict_tier_hint,
    extract_features,
)


_VARIABLE_KEYS = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")
_EXTRACTOR_CORRECTION_KEYS = ("E_env", "P_need", "V_attr", "V_rep")


@dataclass(frozen=True)
class TurnSignals:
    """时间总线和生成上下文共用的单轮确定性输入。"""

    features: dict
    tier_hint: object
    variable_deltas: dict
    lever_self: float
    lever_opponent: float
    ammo_hits: list
    extractor_pain: float
    # 仅用于 receipt / 审计。它不进入状态、驱动或任何下一轮计算，避免把调试中间
    # 量重新接回数学主链；每个来源均已执行输入侧 L_opponent 不可写约束。
    source_contributions: dict
    # 输入测量的可回放权重说明。它只解释本轮 Delta_X 如何合成，不能作为下一轮
    # 状态、词元或调度输入。
    measurement_receipt: dict


def dict_variable_deltas(text, features=None):
    """词典特征 → 六维变量增量（零 LLM、无副作用）。"""
    feature_map = features if features is not None else extract_features(text)
    deltas = {key: 0.0 for key in _VARIABLE_KEYS}
    for feature, patches in DICT_DELTAS.items():
        if feature_map.get(feature, 0.0) > 0:
            for key, value in patches.items():
                deltas[key] = deltas.get(key, 0.0) + value
    return deltas


def derive_turn_signals(*, text, ammo, now_state_vars, pending_extraction=None,
                        semantic_projection=None, features=None, tier_hint=None,
                        event_kind="human_input"):
    """合并词典、弹药和延迟 extractor 校正，生成本轮总线输入。

    返回的 ``ammo_hits`` 是传入弹药表中原始条目的引用。调用者可在成功决定
    提交该回合后标记 ``exposed`` 并持久化；本函数不读写 profile。
    """
    feature_map = features if features is not None else extract_features(text)
    tier = tier_hint if tier_hint is not None else dict_tier_hint(text)
    raw_dictionary = dict_variable_deltas(text, feature_map)
    source_contributions = {
        "dictionary": dict(raw_dictionary),
        "semantic": {key: 0.0 for key in _VARIABLE_KEYS},
        "ammunition": {key: 0.0 for key in _VARIABLE_KEYS},
        "extractor": {key: 0.0 for key in _VARIABLE_KEYS},
    }
    semantic_confidence = 0.0
    # 语义投影是连续测量，不是一组业务事件分支。词典只适合没有语义测量时的
    # 粗粒度 fallback：若二者在同一轴方向冲突，不能继续等权相加、彼此抹掉。
    # 因此语义置信度 c 出现后，词典的有效权重连续退至 (1-c)^2；c=0 时严格
    # 保留旧词典路径，c=1 时由语义完全解释该轴。这里没有文本/角色分支或阈值。
    if semantic_projection is not None:
        semantic_confidence = min(1.0, max(0.0, float(
            getattr(semantic_projection, "confidence", 0.0) or 0.0)))
        semantic_deltas = getattr(semantic_projection, "variable_deltas", {}) or {}
        for key in _VARIABLE_KEYS:
            source_contributions["semantic"][key] = semantic_confidence * float(
                semantic_deltas.get(key, 0.0) or 0.0)
    dictionary_weight = (1.0 - semantic_confidence) ** 2
    source_contributions["dictionary"] = {
        key: dictionary_weight * float(raw_dictionary.get(key, 0.0) or 0.0)
        for key in _VARIABLE_KEYS
    }
    lever_self = (SALIENCE["tier_lever_delta"].get(tier, 0.0)
                  if tier else 0.0)

    ammo_hits = scan_ammo(text, ammo or [])
    # 变量归属边界：L_opponent = NPC 已经亲口交出的牌，任何用户输入（包括
    # 命中角色的未公开弹药）都不能直接写它。弹药命中仍会提升 E/P/V 并持久化
    # “被触及”事实，只是不能把 NPC 的内部候选秘密伪造成用户已掌握的明牌。
    lever_opponent = 0.0
    for entry in ammo_hits:
        for key, value in AMMO_HIT_DELTAS.items():
            source_contributions["ammunition"][key] += value

    pain = 0.0
    if pending_extraction is not None:
        pain = float(pending_extraction.get("pain", 0.0))
        extracted_vars = pending_extraction.get("vars") or {}
        for key in _EXTRACTOR_CORRECTION_KEYS:
            correction = FITTING["extractor_correction"] * (
                float(extracted_vars.get(key, now_state_vars[key]))
                - now_state_vars[key])
            source_contributions["extractor"][key] += correction

    # 最终结构闸门遵循 Actor-local 方向：收到的任意对端输入只能描述
    # ``L_opponent``，不能把接收 Actor 的自我披露伪造成已发生事实。own F16
    # 输出则相反。这与 semantic_projection 的同一合同重复执行一次，是为了让
    # 无语义 provider 时词典/弹药也不能绕过输入边界；它不引入另一套状态计算。
    non_writable = {
        "human_input": ("L_self",),
        "peer_input": ("L_self",),
        "simulation_input": ("L_self",),
        "user_input": ("L_self",),  # legacy input adapter
        "npc_output": ("L_opponent",),
    }.get(str(event_kind or "human_input"), ("L_self",))
    for contribution in source_contributions.values():
        for key in non_writable:
            contribution[key] = 0.0
    deltas = {
        key: sum(float(contribution.get(key, 0.0) or 0.0)
                 for contribution in source_contributions.values())
        for key in _VARIABLE_KEYS
    }

    return TurnSignals(
        features=feature_map, tier_hint=tier, variable_deltas=deltas,
        lever_self=lever_self, lever_opponent=lever_opponent,
        ammo_hits=ammo_hits, extractor_pain=pain,
        source_contributions=source_contributions,
        measurement_receipt={
            "semantic_confidence": semantic_confidence,
            "dictionary_weight": dictionary_weight,
            # math trace 的字符串字段只接受稳定标识符；具体数值已经在同一
            # receipt 内，不把一段不可验证的自由公式文本混进可回放链路。
            "formula_id": "semantic_dictionary_quadratic_v1",
        },
    )
