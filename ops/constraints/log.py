# ops/constraints/log.py
"""constraint_log 落盘口径（承 MANE ops/constraints/log.py schema）：
每张卡每次触发（fail）追加一行；审计口径 = 谁（card_id）、为何（verdict）、
哪一轮（turn）。v1 只记 fail 行（触发率 = fail 行数 / 总轮数，报告层折算）。"""
from ops.storage import UserStore  # noqa: F401  —— 类型参考（store 由调用方传入）

# 字段口径（照抄 MANE，聊天版）
CONSTRAINT_LOG_SCHEMA = {
    "ts": int,        # 时间戳毫秒
    "turn": int,      # 回合号
    "kind": str,      # v1 只有 "check"
    "card_id": str,   # 卡名（等价锁的锚）
    "event": str,     # "pass" | "fail"
    "verdict": dict,  # 卡自己的判定 payload
    "action": str,    # 采取的动作（v1 恒 "log"/"none"）
    "text_before_sha": str,   # 动过的注入文本哈希（v1 无注入 → null）
    "text_after_sha": str,
    "golden_key": str,        # 与 golden 对账的键（v1 无 → null）
}


def validate_row(row):
    """结构校验（宽松）：必填键 + kind/action 枚举。"""
    if not isinstance(row, dict):
        return ["row 必须是 dict"]
    problems = []
    for key in ("ts", "kind", "card_id", "event", "action"):
        if key not in row:
            problems.append(f"缺必填键 {key}")
    if row.get("kind") not in ("check",):
        problems.append(f"kind 非法：{row.get('kind')}")
    if row.get("action") not in ("none", "log"):
        problems.append(f"action 非法：{row.get('action')}")
    return problems


def log_row(store, row):
    """追加一行 constraint_log（校验不过抛 ValueError，不静默）。"""
    problems = validate_row(row)
    if problems:
        raise ValueError("constraint_log 行不合法: " + "；".join(problems))
    store.append_constraint(row)
    return row
