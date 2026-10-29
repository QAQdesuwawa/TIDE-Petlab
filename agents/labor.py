# agents/labor.py
"""开局劳动 Agent：用户背景文本 → 三件套（人格卡 / 5 模板权重 / 坦白弹药账本）。

只做 prompt 组装与输出解析（纯函数，零 LLM），调用接线在 P6 runner。
输出 JSON 契约见 prompts/labor.md；解析铁律：任何失败都不崩，按最大不确定度兜底。
"""
import json
import os
import re

from core.schema_matrix import SchemaMatrix

TIERS = ("日常", "私密", "创伤")


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "labor.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _strip_fences(raw):
    t = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    return m.group(1) if m else t


def build_labor_messages(background_text):
    system = _load_prompt()
    user = "【用户写的角色背景】\n" + (background_text or "")
    return system, user


def parse_labor(raw):
    """解析劳动 Agent 输出。

    兜底策略（防御感优先，永远可开局）：
    - persona_card 解析失败 → 退回原文（用户至少能看到自己写的内容）；
    - weights 缺失/非法 → 五模板均匀先验（最大不确定度，集中度 0.2）；
    - weights 总和 ≠ 1 → 归一化；
    - ammo 档位非法/缺失 → 私密；空文本条目丢弃。
    """
    data = None
    try:
        data = json.loads(_strip_fences(raw))
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}

    # 1. 人格卡（用户可见）
    persona = data.get("persona_card") or raw or ""
    persona = str(persona).strip()

    # 2. 权重（5 模板，归一化）
    sm = SchemaMatrix()
    valid = set(sm.get_available_schemas())
    weights = data.get("weights") or {}
    if not isinstance(weights, dict):
        weights = {}
    w = {}
    for k, v in weights.items():
        if k not in valid:
            continue
        try:
            w[k] = float(v)
        except (TypeError, ValueError):
            continue
    total = sum(w.values())
    if total > 0:
        w = {k: v / total for k, v in w.items()}
    else:
        w = {k: 1.0 / len(valid) for k in valid}

    # 3. 弹药账本（三档 + disclosed 位 + 关键词表透传）
    ammo = data.get("ammo") or []
    if not isinstance(ammo, list):
        ammo = []
    ammo_out = []
    for item in ammo:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        tier = item.get("tier") if item.get("tier") in TIERS else "私密"
        entry = {"text": text, "tier": tier, "disclosed": False}
        kws = item.get("keywords") or []
        if isinstance(kws, str):
            kws = [kws]
        if isinstance(kws, list) and kws:
            entry["keywords"] = [str(k).strip() for k in kws if str(k).strip()]
        ammo_out.append(entry)
    return {"persona_card": persona, "weights": w, "ammo": ammo_out}
