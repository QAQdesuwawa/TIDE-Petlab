# agents/ledger.py
"""台账提取 Agent（异步批量）：NPC 自己的回复 → 事实/承诺/立场 + 矛盾标记。

只做 prompt 组装与输出解析（纯函数，零 LLM），调用接线在 runner。
"""
import json
import os
import re

from core.params import LEDGER


def _load_prompt():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "prompts", "ledger.md")
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_ledger_messages(npc_messages, existing_facts):
    system = _load_prompt()
    facts = "\n".join(f"- {f}" for f in (existing_facts or [])[-LEDGER["existing_window"]:])
    msgs = "\n".join(f"角色：{m}" for m in (npc_messages or []))
    user = ("【角色最近的回复】\n" + msgs
            + "\n\n【角色已有陈述台账】\n" + (facts or "（暂无）"))
    return system, user


def _strip_fences(raw):
    t = (raw or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)\s*```", t, re.S)
    return m.group(1) if m else t


def parse_ledger(raw):
    """解析台账提取输出；任何失败 → 空台账（不崩，连续性损失可被下次提取弥补）。"""
    data = None
    try:
        data = json.loads(_strip_fences(raw))
    except Exception:
        data = None
    if not isinstance(data, dict):
        data = {}
    facts = data.get("facts") or []
    if not isinstance(facts, list):
        facts = []
    facts = [str(f).strip() for f in facts if str(f).strip()]
    contradictions = data.get("contradictions") or []
    if not isinstance(contradictions, list):
        contradictions = []
    clean = []
    for c in contradictions:
        if isinstance(c, dict) and c.get("old") and c.get("new"):
            clean.append({"old": str(c["old"])[:80], "new": str(c["new"])[:80]})
    return {"facts": facts, "contradictions": clean}
