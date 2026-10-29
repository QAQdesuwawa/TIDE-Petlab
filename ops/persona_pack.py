# ops/persona_pack.py
"""人格卡包：背景设定结构化拆分 + 按需装配（承 MANE G5d 人设节流 + G6 on-demand）。

MANE 实证：人格卡整段每轮注入 → softmax 全局归一化稀释所有 token，
口癖/文风这类细节最先被冲淡（"傲娇底色在、口癖少了"正是症状）。
G5d 解法 = 人设词元幕级衰减 + 人格节流（身份声明 once:true）。

TIDE 化：
- 人格卡拆成 6 块（身份/口癖/语气/文风/禁区/打样对话）；
- 身份、语气 = 骨架，每轮常驻（固定小核心，核心区不过筛）；
- 口癖 = 关系换挡时的词汇素材；中间态不常驻，避免把示例台词误当角色的唯一句式；
- 其余细节块 = 卡包，按既有触发信号按需挂载，每轮 ≤ PERSONA_MAX_BLOCKS 块，
  优先级：禁区 > 口癖 > 语气 > 文风 > 打样；
- 打样对话只在冷启动期挂载（建立声音后退出，不再稀释后续轮次）。
触发信号零新增测量：复用 turn / ammo_hits / tier_hint / features / P / bridge。
"""
import re

from core.params import FITTING

PERSONA_SECTIONS = ("身份", "口癖", "语气", "文风", "禁区", "打样对话")
PERSONA_MAX_BLOCKS = 2          # 动态块上限（锚块常驻，不占此名额）

# 声音锚块：语气每轮常驻。口癖是角色提供的词汇示例，若每轮全文挂载，模型会把
# “我可不是在等你”一类例句误当成所有场景的句首模板；它只在已有关系档位需要换挡时
# 进入。本模块不生成台词，也不追踪新的“口癖次数”。
PERSONA_ANCHORS = ("语气",)

# 细节块优先级（动态块在同轮名额内竞争；锚块不参与竞争，只参与排序）
_DETAIL_PRIORITY = {"禁区": 10, "口癖": 9, "语气": 8, "文风": 7, "打样对话": 6}


def parse_persona_blocks(card_text):
    """人格卡 markdown → {"身份": ..., "口癖": ..., ..., "variants": {(节, 名): 文本}}。

    支持变体小节：`## 口癖·亲近` / `## 口癖·疏远` → variants[("口癖", "亲近")]；
    标题/说明行并入身份不丢弃；未知小节并入头部；无任何分节 → 整卡归身份。
    """
    text = card_text or ""
    blocks = {s: "" for s in PERSONA_SECTIONS}
    variants = {}
    parts = re.split(r"^##\s+", text, flags=re.M)
    head = parts[0].strip()
    for part in parts[1:]:
        lines = part.split("\n", 1)
        raw_name = lines[0].strip()
        body = lines[1].strip() if len(lines) > 1 else ""
        matched = False
        for s in PERSONA_SECTIONS:
            if raw_name == s:
                blocks[s] = body
                matched = True
                break
            if raw_name.startswith(s + "·"):
                variants[(s, raw_name[len(s) + 1:])] = body
                matched = True
                break
        if not matched:
            head = (head + "\n" + part).strip()
    if not any(blocks.values()) and not variants:
        blocks["身份"] = text.strip() or head
    else:
        blocks["身份"] = (head + "\n" + blocks["身份"]).strip() if head else blocks["身份"]
    blocks["variants"] = variants
    return blocks


def evaluate_persona_triggers(blocks, signals):
    """→ [(section, 原因)]（确定性，零新增测量）。只评估动态块：
    禁区（戳秘密/沉重话题）/ 文风（复杂输入）/ 打样（冷启动）。
    口癖与语气是声音锚块，由 assemble 无条件挂载，不进触发闸门。

    signals = {"turn": int, "ammo_hits": list, "tier_hint": str|None,
               "features": dict, "P": float, "bridge": list}
    """
    turn = int(signals.get("turn", 0))
    warmup = turn < FITTING["fit_warmup_n"]
    out = []
    if signals.get("ammo_hits") or signals.get("tier_hint") in ("创伤", "私密"):
        out.append(("禁区", "戳中秘密/沉重话题：禁区纪律"))
    text = signals.get("text") or ""
    if len(text) >= 120 or len(re.split(r"[。！？!?\n]", text)) >= 4:
        out.append(("文风", "复杂输入：句式纪律"))
    if warmup:
        out.append(("打样对话", "冷启动：建立声音"))
    return out


def assemble_persona_blocks(blocks, signals, max_blocks=None):
    """锚块常驻 + 动态触发按优先级截断 + **口癖关系变体换挡** → (文本列表, id 列表)。

    口癖变体（关系状态维度，底色不变）：signals["V_attr"] ≥ variant_close_v → 亲近；
    ≤ variant_far_v → 疏远；中间 → 默认口癖。变体 id 带档名（"口癖·亲近"）——
    进行为签名 → 高响应签名聚合与标定观察；R 只能提示复核，不可单独判定换挡有效。
    """
    cap = max_blocks if max_blocks is not None else PERSONA_MAX_BLOCKS
    from core.params import PERSONA
    v_attr = float(signals.get("V_attr", 0.5))
    variants = blocks.get("variants") or {}
    sections = list(PERSONA_ANCHORS)                # 语气锚块：无条件挂载
    # 词汇口癖仅在冷启动建立声音，或已有关系值落入亲近/疏远变体时送给作者。
    # 中间态只提供人格语气，具体句式交由当前场景决定。
    warmup = int(signals.get("turn", 0)) < FITTING["fit_warmup_n"]
    if warmup or v_attr >= PERSONA["variant_close_v"] or v_attr <= PERSONA["variant_far_v"]:
        sections.append("口癖")
    for section, _reason in evaluate_persona_triggers(blocks, signals):
        if section not in sections:
            sections.append(section)
    # 动态块按优先级截断（锚块豁免）；整体按优先级排序输出
    dynamic = [s for s in sections if s not in PERSONA_ANCHORS]
    keep_dynamic = sorted(dynamic, key=lambda s: -_DETAIL_PRIORITY[s])[:cap]
    keep = [s for s in PERSONA_ANCHORS] + keep_dynamic
    keep.sort(key=lambda s: -_DETAIL_PRIORITY[s])
    chosen = []
    for section in keep:
        text = blocks.get(section)
        if section == "口癖":
            vname = None
            if v_attr >= PERSONA["variant_close_v"]:
                vname = "亲近"
            elif v_attr <= PERSONA["variant_far_v"]:
                vname = "疏远"
            if vname and variants.get(("口癖", vname)):
                chosen.append((section + "·" + vname, variants[("口癖", vname)]))
                continue
        if text:
            chosen.append((section, text))
    texts = [t for _, t in chosen]
    ids = [i for i, _ in chosen]
    return texts, ids


def registry_report():
    return {"sections": list(PERSONA_SECTIONS),
            "anchors": list(PERSONA_ANCHORS),
            "max_dynamic_blocks": PERSONA_MAX_BLOCKS,
            "priority": dict(_DETAIL_PRIORITY)}
