"""表现词元的纯根量编译器。

这里把同一版 ``C_effective`` 与已结算 ``X`` 投影为三张离散底色卡；F16 输出、
F03 外部输入与 P4 已提交记忆影响分别可提供同 C 的前后态锚，编译各自独立的
时间变化／当下变化／记忆余波卡。它不写状态、不选择记忆、不参与 P3/F15/F16
或 dynamic tick。

数值、版本、时间戳和 action identity 只在返回 receipt 中保留；writer 只接收
``cards[*].text``。时间不是第四个心理根量：第四方向仅表示这两个三维切片之间
在真实墙钟前进后的变化。
"""
from __future__ import annotations

import math
from typing import Mapping

from core.drives import calculate_drives
from core.effective_constants import CONSTANT_ORDER
from core.params import FALLBACK_VARS
from core.vectors import component_difference, get_drive_vector, vector_magnitude
from core.tokens.expression_emission import MAPPING_DESCRIPTOR_VERSION


TOKEN_VERSION = "expression-root-vector-v6"
ANCHOR_VERSION = "output-expression-anchor-v1"
MEMORY_ANCHOR_VERSION = "memory-expression-anchor-v1"
EVENT_ANCHOR_VERSION = "event-expression-anchor-v1"
TIME_ANCHOR_VERSION = "time-expression-anchor-v1"
INTERACTION_RESIDUAL_ANCHOR_VERSION = "interaction-residual-expression-anchor-v1"
_DRIVE_AXES = (
    ("x", "索取趋向"),
    ("y", "防御趋向"),
    ("z", "情绪张力"),
)


def _finite_vector(value: Mapping[str, object], axes: tuple[str, ...]) -> dict[str, float] | None:
    if not isinstance(value, Mapping) or set(value) != set(axes):
        return None
    out: dict[str, float] = {}
    for axis in axes:
        try:
            number = float(value[axis])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        out[axis] = number
    return out


def _drive_cards(drives: Mapping[str, float]) -> list[dict[str, object]]:
    """将三驱动力的方向编码成可读、非命令式的离散底色。

    分档只服务表现词表：以当前三维向量的单位方向识别何种取向更显著，不能回流为
    门限、场景或角色标签。它不使用消息数量、固定冷却或用户行为猜测。
    """
    vector = get_drive_vector(dict(drives))
    magnitude = vector_magnitude(vector)
    if magnitude <= 1e-12:
        return []
    cards: list[dict[str, object]] = []
    for key, label in _DRIVE_AXES:
        share = abs(float(vector[key])) / magnitude
        if share >= 0.65:
            level = "显著"
        elif share >= 0.30:
            level = "在场"
        else:
            level = "微弱"
        cards.append({
            "text": f"[表达底色·{label}·{level}]",
            "category": "expression_drive_state",
            "layer": "里",
            "expression_source": "current_drive_state",
            "prompt_bridge": False,
            "drive_axis": key,
            # 只供新描述符读取；保留原有 writer 卡字段及语义不变。
            "mapping_score": share,
        })
    return cards


def _time_transition_card(*, current: Mapping[str, float], anchor: Mapping[str, float]) -> tuple[dict[str, object], dict[str, object]]:
    """以同一 C 下的 ``D_now - D_anchor`` 编译单张时间变化卡。"""
    now_vector = get_drive_vector(dict(current))
    anchor_vector = get_drive_vector(dict(anchor))
    delta = component_difference(now_vector, anchor_vector)
    delta_keys = {"x": "delta_aggro", "y": "delta_defend", "z": "delta_chaos"}
    axis = max(("x", "y", "z"), key=lambda item: abs(float(delta[delta_keys[item]])))
    delta_key = delta_keys[axis]
    signed = float(delta[delta_key])
    change_norm = math.sqrt(sum(float(delta[key]) ** 2 for key in delta))
    baseline = vector_magnitude(now_vector) + vector_magnitude(anchor_vector)
    relative_change = (change_norm / baseline) if baseline > 1e-12 else 0.0

    if change_norm <= 1e-9:
        text = "[时间变化·余波持续]"
        phase = "inertia"
    elif axis == "x" and signed > 0:
        text, phase = "[时间变化·索取趋向增强]", "claiming_rise"
    elif axis == "x":
        text, phase = "[时间变化·索取趋向收回]", "claiming_fall"
    elif axis == "y" and signed > 0:
        text, phase = "[时间变化·防御趋向加重]", "defence_rise"
    elif axis == "y":
        text, phase = "[时间变化·防御趋向松动]", "defence_fall"
    elif signed > 0:
        text, phase = "[时间变化·情绪张力升高]", "tension_rise"
    else:
        text, phase = "[时间变化·情绪张力缓解]", "tension_fall"

    # 这张卡的高权重是表现层排序语义：只要真实墙钟已越过一个完整输出锚，它就
    # 先于普通素材进入 writer。不是 P3 门限，不与等待时长或消息条数绑定。
    card = {
        "text": text,
        "category": "expression_time_transition",
        "layer": "势",
        "strength": 1.0,
        "score": 1.0,
        "mapping_score": relative_change,
        "expression_source": "committed_output_transition",
        "prompt_bridge": False,
    }
    receipt = {
        "phase": phase,
        "dominant_axis": axis,
        "relative_change": round(relative_change, 9),
        "drive_delta": {name: round(float(value), 9) for name, value in delta.items()},
    }
    return card, receipt


def _memory_transition_card(*, before: Mapping[str, float], after: Mapping[str, float],
                            constants: Mapping[str, float]) -> tuple[dict[str, object] | None,
                                                                  dict[str, object]]:
    """把一次已提交 P4 影响投影为一张非叙事的记忆余波卡。

    这里比较的是同一冻结 C 下，P4 写入前后的完整六轴状态；不是把候选 recall、
    记忆正文或当前 writer 的解释送回公式。零实际变化不伪造卡，即使 batch 曾被
    选中也只说明它没有改变状态。
    """
    before_drive = calculate_drives(dict(constants), dict(before))
    after_drive = calculate_drives(dict(constants), dict(after))
    before_vector = get_drive_vector(dict(before_drive))
    after_vector = get_drive_vector(dict(after_drive))
    delta = component_difference(after_vector, before_vector)
    delta_keys = {"x": "delta_aggro", "y": "delta_defend", "z": "delta_chaos"}
    change_norm = math.sqrt(sum(float(delta[key]) ** 2 for key in delta))
    baseline = vector_magnitude(before_vector) + vector_magnitude(after_vector)
    relative_change = (change_norm / baseline) if baseline > 1e-12 else 0.0
    if change_norm <= 1e-9:
        return None, {
            "available": False,
            "reason": "no_actual_memory_drive_change",
            "relative_change": 0.0,
        }
    axis = max(("x", "y", "z"), key=lambda item: abs(float(delta[delta_keys[item]])))
    signed = float(delta[delta_keys[axis]])
    if axis == "x" and signed > 0:
        text, phase = "[记忆余波·索取趋向被牵动]", "claiming_rise"
    elif axis == "x":
        text, phase = "[记忆余波·索取趋向收束]", "claiming_fall"
    elif axis == "y" and signed > 0:
        text, phase = "[记忆余波·防御趋向被牵动]", "defence_rise"
    elif axis == "y":
        text, phase = "[记忆余波·防御趋向松动]", "defence_fall"
    elif signed > 0:
        text, phase = "[记忆余波·情绪张力被牵动]", "tension_rise"
    else:
        text, phase = "[记忆余波·情绪张力缓和]", "tension_fall"
    return {
        "text": text,
        "category": "expression_memory_transition",
        "layer": "势",
        # 强度只表达这次同 C 投影的相对改变，用于既有 writer 带宽排序；它不是
        # 记忆选择分、开口门线、冷却、消息数或场景优先级。
        "strength": relative_change,
        "score": relative_change,
        "mapping_score": relative_change,
        "expression_source": "committed_memory_transition",
        "prompt_bridge": False,
    }, {
        "available": True,
        "phase": phase,
        "dominant_axis": axis,
        "relative_change": round(relative_change, 9),
        "drive_delta": {name: round(float(value), 9) for name, value in delta.items()},
    }


def _event_transition_card(*, before: Mapping[str, float], after: Mapping[str, float],
                           constants: Mapping[str, float]) -> tuple[dict[str, object] | None,
                                                                     dict[str, object]]:
    """把一次已发生外部输入的 F03 根变化投影为一张当前变化卡。

    这与 P4 记忆余波刻意分开：前者只描述这一条已经进入共同历史的外部事件，后者
    只描述实际 selected recall 的独立状态影响。两者都不含正文、语义理由、ID 或
    数值，也不改变任何调度资格。
    """
    before_drive = calculate_drives(dict(constants), dict(before))
    after_drive = calculate_drives(dict(constants), dict(after))
    before_vector = get_drive_vector(dict(before_drive))
    after_vector = get_drive_vector(dict(after_drive))
    delta = component_difference(after_vector, before_vector)
    delta_keys = {"x": "delta_aggro", "y": "delta_defend", "z": "delta_chaos"}
    change_norm = math.sqrt(sum(float(delta[key]) ** 2 for key in delta))
    baseline = vector_magnitude(before_vector) + vector_magnitude(after_vector)
    relative_change = (change_norm / baseline) if baseline > 1e-12 else 0.0
    if change_norm <= 1e-9:
        return None, {
            "available": False,
            "reason": "no_actual_event_drive_change",
            "relative_change": 0.0,
        }
    axis = max(("x", "y", "z"), key=lambda item: abs(float(delta[delta_keys[item]])))
    signed = float(delta[delta_keys[axis]])
    if axis == "x" and signed > 0:
        text, phase = "[当下变化·索取趋向被触动]", "claiming_rise"
    elif axis == "x":
        text, phase = "[当下变化·索取趋向收束]", "claiming_fall"
    elif axis == "y" and signed > 0:
        text, phase = "[当下变化·防御趋向被触动]", "defence_rise"
    elif axis == "y":
        text, phase = "[当下变化·防御趋向松动]", "defence_fall"
    elif signed > 0:
        text, phase = "[当下变化·情绪张力被触动]", "tension_rise"
    else:
        text, phase = "[当下变化·情绪张力缓和]", "tension_fall"
    return {
        "text": text,
        "category": "expression_event_transition",
        "layer": "势",
        # 只表示同 C 的这次变化相对大小，供既有 writer 带宽排序；不是对该输入
        # 的评价、回复策略、开口门限或用户画像。
        "strength": relative_change,
        "score": relative_change,
        "mapping_score": relative_change,
        "expression_source": "committed_event_transition",
        "prompt_bridge": False,
    }, {
        "available": True,
        "phase": phase,
        "dominant_axis": axis,
        "relative_change": round(relative_change, 9),
        "drive_delta": {name: round(float(value), 9) for name, value in delta.items()},
    }


def _interaction_residual_card(*, before_event: Mapping[str, float],
                               after_output: Mapping[str, float],
                               current: Mapping[str, float],
                               constants: Mapping[str, float]) -> tuple[dict[str, object] | None,
                                                                        dict[str, object]]:
    """把 F03→F16 后仍存在的同向关系残量编译为一张离散表达卡。

    ``before_event`` 到 ``after_output`` 只确定这次完整互动曾经推向哪里；是否仍未
    收住只由当前已结算 ``X_now`` 相对同一起点的投影决定。它不重新投递 F03 event
    card、没有用户正文，且完全不参与状态或发送。
    """
    baseline = get_drive_vector(calculate_drives(dict(constants), dict(before_event)))
    settled = get_drive_vector(calculate_drives(dict(constants), dict(after_output)))
    now = get_drive_vector(calculate_drives(dict(constants), dict(current)))
    initial = component_difference(settled, baseline)
    residual = component_difference(now, baseline)
    initial_norm = math.sqrt(sum(float(value) ** 2 for value in initial.values()))
    residual_norm = math.sqrt(sum(float(value) ** 2 for value in residual.values()))
    if initial_norm <= 1e-9:
        return None, {"available": False, "reason": "no_interaction_drive_change"}
    if residual_norm <= 1e-9:
        return None, {"available": False, "reason": "interaction_residual_settled"}
    dot = sum(float(initial[key]) * float(residual[key]) for key in initial)
    # 余弦相似度的数学定义域就是 [-1, 1]。完全同向时二进制浮点除法可能只因
    # 舍入得到 1.0000000000000002；将它钳回定义域，避免合法、已提交的残量卡在
    # descriptor 的 [0, 1] 合同前被误判为无效。这不改变根量、相对方向或发放规则。
    alignment = max(-1.0, min(1.0, dot / (initial_norm * residual_norm)))
    if alignment <= 0.0:
        return None, {
            "available": False,
            "reason": "interaction_direction_no_longer_aligned",
            "alignment": round(alignment, 9),
        }
    delta_keys = {"x": "delta_aggro", "y": "delta_defend", "z": "delta_chaos"}
    axis = max(("x", "y", "z"),
               key=lambda item: abs(float(residual[delta_keys[item]])))
    signed = float(residual[delta_keys[axis]])
    if axis == "x" and signed > 0:
        text, phase = "[未解互动·索取趋向仍在牵引]", "claiming_residual"
    elif axis == "x":
        text, phase = "[未解互动·索取趋向仍未收住]", "claiming_withdrawal_residual"
    elif axis == "y" and signed > 0:
        text, phase = "[未解互动·防御趋向仍在牵引]", "defence_residual"
    elif axis == "y":
        text, phase = "[未解互动·防御趋向仍未收住]", "defence_release_residual"
    elif signed > 0:
        text, phase = "[未解互动·情绪张力仍在牵引]", "tension_residual"
    else:
        text, phase = "[未解互动·情绪张力仍未收住]", "tension_release_residual"
    # 强度仅为“当前残量仍占完整互动变化的多少”的 writer 排序读数。它既不将
    # 新的墙钟变化归因给旧输入，也不成为门限、冷却或消息数量规则。
    retained = min(1.0, residual_norm / initial_norm)
    strength = retained * alignment
    return {
        "text": text,
        "category": "expression_interaction_residual",
        "layer": "势",
        "strength": strength,
        "score": strength,
        "mapping_score": strength,
        "expression_source": "committed_interaction_residual",
        "prompt_bridge": False,
    }, {
        "available": True,
        "phase": phase,
        "dominant_axis": axis,
        "alignment": round(alignment, 9),
        "retained_fraction": round(retained, 9),
        "initial_drive_delta": {name: round(float(value), 9)
                                for name, value in initial.items()},
        "current_residual_delta": {name: round(float(value), 9)
                                   for name, value in residual.items()},
    }


def _wall_clock_transition_card(*, before: Mapping[str, float], after: Mapping[str, float],
                                constants: Mapping[str, float]) -> tuple[dict[str, object] | None,
                                                                          dict[str, object]]:
    """把一次已提交 F08 的 Delta_X_time 编译为离散墙钟流变卡。"""
    before_drive = calculate_drives(dict(constants), dict(before))
    after_drive = calculate_drives(dict(constants), dict(after))
    before_vector = get_drive_vector(dict(before_drive))
    after_vector = get_drive_vector(dict(after_drive))
    delta = component_difference(after_vector, before_vector)
    delta_keys = {"x": "delta_aggro", "y": "delta_defend", "z": "delta_chaos"}
    change_norm = math.sqrt(sum(float(delta[key]) ** 2 for key in delta))
    baseline = vector_magnitude(before_vector) + vector_magnitude(after_vector)
    relative_change = (change_norm / baseline) if baseline > 1e-12 else 0.0
    if change_norm <= 1e-9:
        return None, {
            "available": False,
            "reason": "no_actual_time_drive_change",
            "relative_change": 0.0,
        }
    axis = max(("x", "y", "z"), key=lambda item: abs(float(delta[delta_keys[item]])))
    signed = float(delta[delta_keys[axis]])
    if axis == "x" and signed > 0:
        text, phase = "[墙钟流变·索取趋向回升]", "claiming_rise"
    elif axis == "x":
        text, phase = "[墙钟流变·索取趋向回落]", "claiming_fall"
    elif axis == "y" and signed > 0:
        text, phase = "[墙钟流变·防御趋向回升]", "defence_rise"
    elif axis == "y":
        text, phase = "[墙钟流变·防御趋向回落]", "defence_fall"
    elif signed > 0:
        text, phase = "[墙钟流变·情绪张力回升]", "tension_rise"
    else:
        text, phase = "[墙钟流变·情绪张力回落]", "tension_fall"
    return {
        "text": text,
        "category": "expression_time_state_transition",
        "layer": "势",
        # 强度只是这次 F08 的同 C 表现排序读数，不是观察间隔、开口余量或一条
        # “时间一到必须发话”的规则。
        "strength": relative_change,
        "score": relative_change,
        "mapping_score": relative_change,
        "expression_source": "committed_time_transition",
        "prompt_bridge": False,
    }, {
        "available": True,
        "phase": phase,
        "dominant_axis": axis,
        "relative_change": round(relative_change, 9),
        "drive_delta": {name: round(float(value), 9) for name, value in delta.items()},
    }


_TRANSITION_RECEIPT_KEY = {
    "committed_output_transition": "time_transition",
    "committed_memory_transition": "memory_transition",
    "committed_event_transition": "event_transition",
    "committed_interaction_residual": "interaction_residual",
    "committed_time_transition": "wall_clock_transition",
}


def _descriptor_input_receipt(*, source: str, receipt: Mapping[str, object],
                              now_ms: int) -> dict[str, object] | None:
    """提取词元映射已读取过的 receipt 身份，不重读根量或正文。"""
    version = str(receipt.get("current_constants_version") or "").strip()
    if not version:
        return None
    if source == "current_drive_state":
        return {"effective_constants_version": version, "observed_ms": now_ms}
    receipt_key = _TRANSITION_RECEIPT_KEY.get(source)
    transition = receipt.get(receipt_key) if receipt_key else None
    if not isinstance(transition, Mapping) or transition.get("available") is not True:
        return None
    out: dict[str, object] = {"effective_constants_version": version}
    for key in ("anchor_action_id", "action_id", "turn_id", "event_ms", "sent_ms",
                "anchor_sent_ms", "committed_ms", "settled_ms", "elapsed_ms"):
        value = transition.get(key)
        if isinstance(value, (str, int, float)) and not isinstance(value, bool):
            out[key] = value
    return out


def _expression_descriptor(*, card: Mapping[str, object], receipt: Mapping[str, object],
                           now_ms: int) -> dict[str, object] | None:
    """发布映射器已经计算出的 descriptor，不在这里二次解释根量。"""
    source = str(card.get("expression_source") or "").strip()
    if not source:
        return None
    input_receipt = _descriptor_input_receipt(source=source, receipt=receipt, now_ms=now_ms)
    if input_receipt is None:
        return None
    if source == "current_drive_state":
        axis = str(card.get("drive_axis") or "").strip()
        neutral = 1.0 / 3.0
        phase = "current"
    else:
        transition = receipt.get(_TRANSITION_RECEIPT_KEY.get(source, ""))
        if not isinstance(transition, Mapping):
            return None
        axis = str(transition.get("dominant_axis") or "").strip()
        neutral = 0.0
        phase = str(transition.get("phase") or "").strip()
    try:
        score = float(card["mapping_score"])
    except (KeyError, TypeError, ValueError):
        return None
    if not axis or not phase or not math.isfinite(score) or not 0.0 <= score <= 1.0:
        return None
    return {
        "version": MAPPING_DESCRIPTOR_VERSION,
        "token_key": f"{source}:{axis}:{phase}",
        "source": source,
        "axis": axis,
        "score": score,
        "domain": {"low": 0.0, "high": 1.0},
        "neutral": neutral,
        "input_receipt": input_receipt,
    }


def _finalize_expression_result(*, cards: list[dict[str, object]],
                                receipt: dict[str, object], now_ms: int) -> dict[str, object]:
    """为旧 writer 卡附加只读 descriptor；旧卡排序字段保持原样。"""
    candidates: list[dict[str, object]] = []
    rejected: list[dict[str, str]] = []
    for card in cards:
        descriptor = _expression_descriptor(card=card, receipt=receipt, now_ms=now_ms)
        if descriptor is None:
            rejected.append({
                "expression_source": str(card.get("expression_source") or "<unknown>"),
                "reason": "descriptor_metadata_unavailable",
            })
            continue
        card["expression_descriptor"] = descriptor
        candidates.append(descriptor)
    receipt["mapping_descriptor_version"] = MAPPING_DESCRIPTOR_VERSION
    receipt["mapping_descriptor_count"] = len(candidates)
    receipt["mapping_descriptor_rejections"] = rejected
    return {"cards": cards, "candidates": candidates, "receipt": receipt}


def compile_expression_tokens(*, current_constants: Mapping[str, object],
                              current_constants_version: str,
                              current_variables: Mapping[str, object], now_ms: int,
                              output_anchor: Mapping[str, object] | None = None,
                              memory_anchor: Mapping[str, object] | None = None,
                              event_anchor: Mapping[str, object] | None = None,
                              time_anchor: Mapping[str, object] | None = None,
                              interaction_residual_anchor: Mapping[str, object] | None = None,
                              source: str = "proactive") -> dict[str, object]:
    """把根量切片编译为仅供 writer 的离散词元及可审计 receipt。

    ``output_anchor`` 只来自 ``output_state_committed.expression_anchor``；
    ``time_anchor`` 只来自已提交 F08 receipt。没有锚时仍可产生当前三维底色；
    各变化卡严格空退。当前版本与锚版本不一致时，绝不做跨 C 比较，也不以可变
    ``prev_vars``、旧驱动力、草稿或 UI 补齐。
    """
    constants = _finite_vector(current_constants, CONSTANT_ORDER)
    variables = _finite_vector(current_variables, FALLBACK_VARS)
    version = str(current_constants_version or "").strip()
    if constants is None or variables is None or not version:
        return {"cards": [], "candidates": [], "receipt": {
            "version": TOKEN_VERSION, "available": False,
            "reason": "invalid_current_root_slice",
            "mapping_descriptor_version": MAPPING_DESCRIPTOR_VERSION,
            "mapping_descriptor_count": 0,
            "mapping_descriptor_rejections": [],
        }}
    now = int(now_ms)
    current_drives = calculate_drives(constants, variables)
    cards = _drive_cards(current_drives)
    receipt: dict[str, object] = {
        "version": TOKEN_VERSION,
        "available": True,
        "source": str(source),
        "current_constants_version": version,
        "current_drive": dict(current_drives),
        "current_drive_card_count": len(cards),
        "time_transition": {"available": False, "reason": "missing_output_anchor"},
        "memory_transition": {"available": False, "reason": "missing_memory_anchor"},
        "event_transition": {"available": False, "reason": "missing_event_anchor"},
        "interaction_residual": {"available": False,
                                   "reason": "missing_interaction_residual_anchor"},
        "wall_clock_transition": {"available": False, "reason": "missing_time_anchor"},
    }

    # F08 Delta_X_time 与 output-anchor 的“输出后至今比较”不是同一来源：前者是
    # 一次已经提交的墙钟状态转换，后者是表现层对最后输出锚和当前切片的比较。
    # 两张卡分别保留自己的 receipt，禁止互相代算或把 UI preview 当 F08。
    if isinstance(time_anchor, Mapping):
        if str(time_anchor.get("version") or "") != TIME_ANCHOR_VERSION:
            receipt["wall_clock_transition"] = {"available": False, "reason": "invalid_time_anchor"}
        else:
            try:
                settled_ms = int(time_anchor["settled_ms"])
                elapsed_ms = int(time_anchor["elapsed_ms"])
            except (KeyError, TypeError, ValueError):
                receipt["wall_clock_transition"] = {"available": False, "reason": "invalid_time_anchor"}
            else:
                anchor_version = str(time_anchor.get("effective_constants_version") or "").strip()
                anchor_constants = _finite_vector(time_anchor.get("effective_constants"), CONSTANT_ORDER)
                before = _finite_vector(time_anchor.get("X_before"), FALLBACK_VARS)
                delta = _finite_vector(time_anchor.get("Delta_X_time"), FALLBACK_VARS)
                after = _finite_vector(time_anchor.get("X_after"), FALLBACK_VARS)
                if (settled_ms > now or elapsed_ms <= 0 or not anchor_version
                        or anchor_constants is None or before is None or delta is None or after is None):
                    receipt["wall_clock_transition"] = {"available": False, "reason": "invalid_time_anchor"}
                elif any(not math.isclose(before[axis] + delta[axis], after[axis],
                                          rel_tol=0.0, abs_tol=1e-9)
                         for axis in FALLBACK_VARS):
                    receipt["wall_clock_transition"] = {"available": False, "reason": "invalid_time_anchor"}
                elif anchor_version != version or anchor_constants != constants:
                    receipt["wall_clock_transition"] = {
                        "available": False,
                        "reason": "effective_constants_version_boundary",
                        "anchor_constants_version": anchor_version,
                    }
                else:
                    time_card, time_receipt = _wall_clock_transition_card(
                        before=before, after=after, constants=anchor_constants)
                    receipt["wall_clock_transition"] = {
                        "settled_ms": settled_ms,
                        "elapsed_ms": elapsed_ms,
                        "effective_constants_version": version,
                        **time_receipt,
                    }
                    if time_card is not None:
                        cards.append(time_card)

    # 输入事件与 P4 一样，必须先有完整、已结算的根状态转换；不同的是它只描述
    # F03 的 Delta_X_event。它不读取输入正文/semantic receipt，且不把当前最终 X
    # （可能已叠加 P4）错误当成“用户事件本身”的后态。
    if isinstance(event_anchor, Mapping):
        if str(event_anchor.get("version") or "") != EVENT_ANCHOR_VERSION:
            receipt["event_transition"] = {"available": False, "reason": "invalid_event_anchor"}
        else:
            try:
                event_ms = int(event_anchor["event_ms"])
            except (KeyError, TypeError, ValueError):
                receipt["event_transition"] = {"available": False, "reason": "invalid_event_anchor"}
            else:
                anchor_version = str(event_anchor.get("effective_constants_version") or "").strip()
                anchor_constants = _finite_vector(event_anchor.get("effective_constants"), CONSTANT_ORDER)
                before = _finite_vector(event_anchor.get("X_before"), FALLBACK_VARS)
                delta = _finite_vector(event_anchor.get("Delta_X_event"), FALLBACK_VARS)
                after = _finite_vector(event_anchor.get("X_after"), FALLBACK_VARS)
                if event_ms > now or not anchor_version or anchor_constants is None or before is None or delta is None or after is None:
                    receipt["event_transition"] = {"available": False, "reason": "invalid_event_anchor"}
                elif any(not math.isclose(before[axis] + delta[axis], after[axis],
                                          rel_tol=0.0, abs_tol=1e-9)
                         for axis in FALLBACK_VARS):
                    receipt["event_transition"] = {"available": False, "reason": "invalid_event_anchor"}
                elif anchor_version != version or anchor_constants != constants:
                    receipt["event_transition"] = {
                        "available": False,
                        "reason": "effective_constants_version_boundary",
                        "anchor_constants_version": anchor_version,
                    }
                else:
                    event_card, event_receipt = _event_transition_card(
                        before=before, after=after, constants=anchor_constants)
                    receipt["event_transition"] = {
                        "event_ms": event_ms,
                        "effective_constants_version": version,
                        **event_receipt,
                    }
                    if event_card is not None:
                        cards.append(event_card)

    # F03 已在直接 F16 中被吸收，主动 writer 不能重投它的 event card。这里读取的
    # 是与该 F16 action 精确绑定的无正文残量锚：仍以同 C 的当前 X 判断这段互动是否
    # 留有同向余波，而不是把旧输入或旧回复再塞回表现层。
    if isinstance(interaction_residual_anchor, Mapping):
        if (str(interaction_residual_anchor.get("version") or "")
                != INTERACTION_RESIDUAL_ANCHOR_VERSION):
            receipt["interaction_residual"] = {
                "available": False, "reason": "invalid_interaction_residual_anchor"}
        else:
            try:
                event_ms = int(interaction_residual_anchor["event_ms"])
                sent_ms = int(interaction_residual_anchor["sent_ms"])
            except (KeyError, TypeError, ValueError):
                receipt["interaction_residual"] = {
                    "available": False, "reason": "invalid_interaction_residual_anchor"}
            else:
                anchor_version = str(interaction_residual_anchor.get(
                    "effective_constants_version") or "").strip()
                anchor_constants = _finite_vector(
                    interaction_residual_anchor.get("effective_constants"), CONSTANT_ORDER)
                before_event = _finite_vector(
                    interaction_residual_anchor.get("X_before_event"), FALLBACK_VARS)
                after_event = _finite_vector(
                    interaction_residual_anchor.get("X_after_event"), FALLBACK_VARS)
                after_output = _finite_vector(
                    interaction_residual_anchor.get("X_after_output"), FALLBACK_VARS)
                if (event_ms > sent_ms or sent_ms > now or not anchor_version
                        or anchor_constants is None or before_event is None
                        or after_event is None or after_output is None
                        or not str(interaction_residual_anchor.get("action_id") or "")
                        or not str(interaction_residual_anchor.get("turn_id") or "")):
                    receipt["interaction_residual"] = {
                        "available": False, "reason": "invalid_interaction_residual_anchor"}
                elif anchor_version != version or anchor_constants != constants:
                    receipt["interaction_residual"] = {
                        "available": False,
                        "reason": "effective_constants_version_boundary",
                        "anchor_constants_version": anchor_version,
                    }
                else:
                    residual_card, residual_receipt = _interaction_residual_card(
                        before_event=before_event, after_output=after_output,
                        current=variables, constants=anchor_constants)
                    receipt["interaction_residual"] = {
                        "action_id": str(interaction_residual_anchor["action_id"]),
                        "turn_id": str(interaction_residual_anchor["turn_id"]),
                        "event_ms": event_ms,
                        "sent_ms": sent_ms,
                        "effective_constants_version": version,
                        **residual_receipt,
                    }
                    if residual_card is not None:
                        cards.append(residual_card)

    # 记忆余波的资格与输出时间锚不同：它只需是一条已经落盘的 P4 状态转换，
    # 但同样必须用它自己的冻结 C 版本投影。当前 C 已换版时宁可空退，不能将
    # 校定变化伪装成“这段记忆的情绪影响”。
    if isinstance(memory_anchor, Mapping):
        if str(memory_anchor.get("version") or "") != MEMORY_ANCHOR_VERSION:
            receipt["memory_transition"] = {"available": False, "reason": "invalid_memory_anchor"}
        else:
            try:
                committed_ms = int(memory_anchor["committed_ms"])
            except (KeyError, TypeError, ValueError):
                receipt["memory_transition"] = {"available": False, "reason": "invalid_memory_anchor"}
            else:
                anchor_version = str(memory_anchor.get("effective_constants_version") or "").strip()
                anchor_constants = _finite_vector(memory_anchor.get("effective_constants"), CONSTANT_ORDER)
                before = _finite_vector(memory_anchor.get("X_before"), FALLBACK_VARS)
                delta = _finite_vector(memory_anchor.get("Delta_X_memory"), FALLBACK_VARS)
                after = _finite_vector(memory_anchor.get("X_after"), FALLBACK_VARS)
                if committed_ms > now or not anchor_version or anchor_constants is None or before is None or delta is None or after is None:
                    receipt["memory_transition"] = {"available": False, "reason": "invalid_memory_anchor"}
                elif any(not math.isclose(before[axis] + delta[axis], after[axis],
                                          rel_tol=0.0, abs_tol=1e-9)
                         for axis in FALLBACK_VARS):
                    receipt["memory_transition"] = {"available": False, "reason": "invalid_memory_anchor"}
                elif anchor_version != version or anchor_constants != constants:
                    receipt["memory_transition"] = {
                        "available": False,
                        "reason": "effective_constants_version_boundary",
                        "anchor_constants_version": anchor_version,
                    }
                else:
                    memory_card, memory_receipt = _memory_transition_card(
                        before=before, after=after, constants=anchor_constants)
                    receipt["memory_transition"] = {
                        "committed_ms": committed_ms,
                        "effective_constants_version": version,
                        **memory_receipt,
                    }
                    if memory_card is not None:
                        cards.append(memory_card)

    if not isinstance(output_anchor, Mapping):
        return _finalize_expression_result(cards=cards, receipt=receipt, now_ms=now)
    if str(output_anchor.get("version") or "") != ANCHOR_VERSION:
        receipt["time_transition"] = {"available": False, "reason": "invalid_output_anchor"}
        return _finalize_expression_result(cards=cards, receipt=receipt, now_ms=now)
    try:
        sent_ms = int(output_anchor["sent_ms"])
    except (KeyError, TypeError, ValueError):
        receipt["time_transition"] = {"available": False, "reason": "invalid_output_anchor"}
        return _finalize_expression_result(cards=cards, receipt=receipt, now_ms=now)
    elapsed_ms = now - sent_ms
    if elapsed_ms <= 0:
        receipt["time_transition"] = {"available": False, "reason": "no_elapsed_wall_clock"}
        return _finalize_expression_result(cards=cards, receipt=receipt, now_ms=now)

    anchor_version = str(output_anchor.get("effective_constants_version") or "").strip()
    anchor_constants = _finite_vector(output_anchor.get("effective_constants"), CONSTANT_ORDER)
    anchor_variables = _finite_vector(output_anchor.get("X_after"), FALLBACK_VARS)
    if anchor_constants is None or anchor_variables is None or not anchor_version:
        receipt["time_transition"] = {"available": False, "reason": "invalid_output_anchor"}
        return _finalize_expression_result(cards=cards, receipt=receipt, now_ms=now)
    # 版本与实际七维快照必须同时相同；版本串碰撞或未来错误复用时，快照比较仍会
    # 阻止两个 C 被悄悄混入同一次差分。
    if anchor_version != version or anchor_constants != constants:
        receipt["time_transition"] = {
            "available": False, "reason": "effective_constants_version_boundary",
            "anchor_constants_version": anchor_version,
        }
        return _finalize_expression_result(cards=cards, receipt=receipt, now_ms=now)

    anchor_drives = calculate_drives(anchor_constants, anchor_variables)
    time_card, transition_receipt = _time_transition_card(
        current=current_drives, anchor=anchor_drives)
    cards.append(time_card)
    receipt["time_transition"] = {
        "available": True,
        "anchor_action_id": str(output_anchor.get("action_id") or ""),
        "anchor_sent_ms": sent_ms,
        "elapsed_ms": elapsed_ms,
        "effective_constants_version": version,
        "anchor_drive": dict(anchor_drives),
        **transition_receipt,
    }
    return _finalize_expression_result(cards=cards, receipt=receipt, now_ms=now)
