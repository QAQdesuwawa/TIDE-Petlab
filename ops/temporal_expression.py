"""已发送输出之后的时间—状态表达卡。

这不是 F15/F16 的第二套开口判断，也不写入状态。它只把已经发生的 F16
输出、其后的墙钟推进，以及根量投影的差异，整理为下一次主动写作可见的
卡包。调度仍只读取符号层；这里的卡只供对白作者换态度和切口。
"""
from __future__ import annotations

from core.vectors import component_difference, cosine_similarity


def compile_output_transition_cards(*, source, sent_ms, now_ms,
                                    current_drives, previous_drives,
                                    previous_output_text=None,
                                    current_variables=None,
                                    previous_variables=None):
    """编译一次主动输出前的表达上下文。

    ``sent_ms`` 必须来自已经提交的上一条输出时间锚；没有该锚、没有发生墙钟
    推进或没有可比较的驱动力快照时，保持空白。卡的类别来自输出事件与根量差，
    不以固定秒数把用户误写成“沉默很久”。
    """
    if sent_ms is None or previous_drives is None or current_drives is None:
        return {"cards": [], "receipt": {"available": False,
                                            "reason": "missing_output_anchor"}}
    try:
        elapsed_ms = max(0, int(now_ms) - int(sent_ms))
    except (TypeError, ValueError):
        return {"cards": [], "receipt": {"available": False,
                                            "reason": "invalid_output_anchor"}}
    if elapsed_ms <= 0:
        return {"cards": [], "receipt": {"available": False,
                                            "reason": "no_elapsed_wall_clock"}}

    similarity = float(cosine_similarity(current_drives, previous_drives))
    delta = component_difference(current_drives, previous_drives)
    if similarity > 0.85:
        text = "[上一句之后·余波未散]"
        strength = similarity
        phase = "inertia"
    elif similarity > 0.5:
        text = "[上一句之后·状态微变]"
        strength = similarity
        phase = "drift"
    else:
        text = "[上一句之后·状态转向]"
        strength = min(1.0, 1.0 - similarity)
        phase = "turn"

    # source 是已发生事件的出处，不进入台词，也不参与开口资格；保留在 receipt
    # 中让测试能够区分“首答后的追发”和“墙钟主动开口”。
    card = {
        "text": text,
        "category": "trend",
        "layer": "势",
        "strength": round(max(0.0, min(1.0, strength)), 3),
        "score": round(max(0.0, min(1.0, strength)), 3),
        # 只有这张时间桥卡可以作为既有 bridge 包的一部分。当前相位卡和表达边界
        # 另走 writer 素材栏，避免把“根状态观察”伪装成一段既定回归文案。
        "prompt_bridge": True,
        "expression_source": "output_transition",
    }

    # 主动 writer 不能复用上一条 F16 的 attention（那会把旧应对直接带回去），
    # 但也不能只剩一张笼统的“余波未散”卡。此处复用正式事件线早已使用的变量
    # 相位词元：它们是当前六维根量的只读表述，不是新的打分函数、门限或开口
    # 条件。保持 token 原本的 value/delta/cell，故可直接回溯到 X 与前一状态。
    # 没有可用 X 时宁可不补卡，绝不从旧 prompt/旧台词猜一份状态。
    state_cards = []
    if isinstance(current_variables, dict) and current_variables:
        from core.tokens.variable import compute_variable_tokens
        prior_variables = (previous_variables
                           if isinstance(previous_variables, dict) else None)
        for token in compute_variable_tokens(current_variables, prior_variables).get("npc", ()):
            # 痛苦词元也由同一组六维根量/前态得出；它和六张相位卡一样只是当前
            # 状态的写作观察，不能取得任何调度权限。
            current_card = dict(token)
            current_card["expression_source"] = "current_root_state"
            current_card["prompt_bridge"] = False
            state_cards.append(current_card)
    # ``previous_output_text`` 仅保留调用兼容性。已提交输出的“存在”不能再被
    # 编成一条固定续话禁令：它既不是根量事实，也会在缺少新事件材料时把 writer
    # 推向泛化场景描写。F03→F16 的未解互动另由无正文残量锚在词元编译器中处理。
    del previous_output_text
    return {
        "cards": [card] + state_cards,
        "receipt": {
            "available": True,
            "source": str(source),
            "from_output_sent_ms": int(sent_ms),
            "now_ms": int(now_ms),
            "elapsed_ms": elapsed_ms,
            "phase": phase,
            "similarity": round(similarity, 6),
            "drive_delta": {key: round(float(value), 6)
                            for key, value in delta.items()},
            "card_texts": [text] + [str(item.get("text") or "") for item in state_cards],
            "current_root_state": {
                "available": bool(state_cards),
                "source": "compute_variable_tokens",
                "axes": [str(item.get("var")) for item in state_cards
                         if item.get("var")],
                "card_count": len(state_cards),
            },
        },
    }
