# agents/extractor.py
"""异步提取 Agent：6 变量精提取 + 三档私密分级 + 情绪打标 + 痛苦度（一次调用，实现设计 §5 异步 A）。

词典快速提取先行（同步路径），本模块结果**延迟一轮**校正（架构文档 §5 的成本纪律）。
只做 prompt 组装与输出解析（纯函数，零 LLM），调用接线在 P6 runner。
"""
import json
import math
import os
import re

from core.params import FALLBACK_VARS

VAR_KEYS = ("E_env", "L_self", "L_opponent", "P_need", "V_attr", "V_rep")
TIERS = ("日常", "私密", "创伤")


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "extractor.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def _strip_fences(raw):
    t = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    return m.group(1) if m else t


def build_extractor_messages(text, prev_vars=None):
    system = _load_prompt()
    prev = prev_vars or FALLBACK_VARS
    user = ("【上一轮 NPC 六变量（连续性参考）】\n"
            + json.dumps(prev, ensure_ascii=False)
            + "\n\n【本轮用户消息】\n" + (text or ""))
    return system, user


def _safe_float(value, default):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if math.isnan(v) or math.isinf(v):
        return default
    return min(1.0, max(0.0, v))


def parse_extractor(raw, fallback=None):
    """解析提取 Agent 输出。

    兜底（MANE 容错纪律的移植）：扁平/包装多格式容错、[0,1] 钳制、NaN 兜底；
    完全不可解析时全量回落兜底变量，tier=日常、pain=0、emotion 空。
    """
    fb = fallback or dict(FALLBACK_VARS)
    data = None
    try:
        data = json.loads(_strip_fences(raw))
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}

    raw_vars = data.get("vars") or {}
    if isinstance(raw_vars, dict):
        vars_out = {k: round(_safe_float(raw_vars.get(k), fb.get(k, 0.0)), 3)
                    for k in VAR_KEYS}
    else:
        vars_out = dict(fb)

    tier = data.get("tier") if data.get("tier") in TIERS else "日常"
    pain = round(_safe_float(data.get("pain"), 0.0), 3)
    emotion = str(data.get("emotion") or "")
    return {"vars": vars_out, "tier": tier, "pain": pain, "emotion": emotion}
