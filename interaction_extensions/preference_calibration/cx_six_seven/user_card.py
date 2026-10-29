# ops/user_card.py
"""用户卡 v1（T6 · 关系层，与人格卡对称）：用户侧散装状态的统一容器。

per (user, NPC) 一份，物理隔离（每角色一个 UserStore 目录——与人格卡对称）。
铁律（承回路审计 §3.1 裁定）：
- 内容必须可溯源到观测事件（source_events 逐字段登记来源），
  禁止推断人格特质——只收"你在这段关系里说过/做过/表现过的"；
- 写入门由版本锁管理（schema_version；版本 bump = 旧卡失效）；
- **禁手：跨对聚合**——单一关系数据无法分解"人"与"关系"；每对卡物理隔离，
  永不合并；反向侧写若重启须为版本级决策 + 同意框架（本模块不提供任何
  跨 store 聚合函数）。

v1 字段：prior（当前 B₀ 的兼容镜像，profile 版本档案为权威）/
preference_declared / baseline（表达量程+回复节奏：延迟/字数个人基线——
修复"基线只在内存、重启即丢"的真缺口）/ user_forbids（未来话题边界，预留；
不是历史记忆删除）/
source_events（溯源表）。
"""
from interaction_extensions.preference_calibration.cx_six_seven.fitting import PROTOTYPES

USER_CARD_VERSION = 1

# 溯源口径：每个数据字段 → 观测事件类型（铁律：可溯源，不推断）
SOURCE_EVENTS = {
    "prior": "preference_declaration",            # 开局/补问声明
    "preference_declared": "preference_declaration",
    "b0_version": "preference_declaration",
    "b0_enabled": "preference_declaration",
    "baseline": "user_message_delay_length",      # 逐轮延迟/字数的个人基线
    "user_forbids": "user_declaration",           # 未来话题边界（预留，不删除历史）
}


def new_card(prior=None, declared=False):
    return {
        "schema_version": USER_CARD_VERSION,
        "prior": {k: float((prior or {}).get(k, 0.2)) for k in PROTOTYPES},
        "preference_declared": bool(declared),
        "b0_version": None,
        "b0_enabled": False,
        "baseline": {},
        "user_forbids": [],
        "source_events": dict(SOURCE_EVENTS),
    }


def validate(card):
    """结构校验（宽松）：版本 + 溯源覆盖 + 字段类型。返回问题列表。"""
    if not isinstance(card, dict):
        return ["card 必须是 dict"]
    problems = []
    if card.get("schema_version") != USER_CARD_VERSION:
        problems.append(f"schema_version 非法：{card.get('schema_version')}")
    src = card.get("source_events") or {}
    for key in ("prior", "preference_declared", "baseline", "user_forbids"):
        if key not in card:
            problems.append(f"缺数据字段 {key}")
        elif key not in src:
            problems.append(f"缺溯源 {key}")
    for k, v in (card.get("prior") or {}).items():
        if k not in PROTOTYPES or not isinstance(v, (int, float)):
            problems.append(f"prior 非法键/值：{k}={v}")
    return problems


def load(store):
    """store.user_card_path → card；缺失 → None。"""
    return store.read_json(store.user_card_path)


def save(store, card):
    """校验后写卡（版本锁：非法卡拒绝写入，不静默）。"""
    problems = validate(card)
    if problems:
        raise ValueError("user_card 不合法: " + "；".join(problems))
    return store.write_json(store.user_card_path, card)


def sync_from_profile(card, profile):
    """prior/declared ← profile（v1：profile 为权威，卡双写跟随）。"""
    c = dict(card)
    c["prior"] = {k: float((profile or {}).get("prior", {}).get(k, 0.2))
                  for k in PROTOTYPES}
    c["preference_declared"] = bool((profile or {}).get("preference_declared"))
    active = (profile or {}).get("session_b0_version")
    c["b0_version"] = active
    c["b0_enabled"] = any(
        isinstance(record, dict) and record.get("version") == active
        and bool(record.get("enabled"))
        for record in ((profile or {}).get("b0_versions") or [])
    )
    return c
