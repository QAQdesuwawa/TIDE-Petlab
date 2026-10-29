"""面向界面的只读会话摘要。

这里不导入 Runner、Store 或 Tk：它只把已经持久化/计算完成的状态快照投影为可读数值，
因此不会把展示层重新耦合回时间总线、记忆选择或 prompt 装配。
"""
from collections import Counter


METERS = (
    ("P", "互动势能"),
    ("E", "情绪张力"),
    ("E_env", "环境压力"),
    ("P_need", "靠近需求"),
    ("V_attr", "吸引强度"),
    ("V_rep", "关系张力"),
)


def build_session_summary(state, messages=()):
    """将已有状态转成 UI 摘要；输入不变，缺失旧字段自然降级。"""
    snapshot = state if isinstance(state, dict) else {}
    variables = snapshot.get("vars") if isinstance(snapshot.get("vars"), dict) else {}
    counts = Counter(
        str(row.get("role")) for row in (messages or ()) if isinstance(row, dict))
    meters = []
    for key, label in METERS:
        raw = snapshot.get(key, variables.get(key, 0.0))
        try:
            value = float(raw)
        except (TypeError, ValueError):
            value = 0.0
        # 状态方程自身负责范围；UI 只为进度条安全钳制显示长度，保留原数值给读者。
        meters.append({"key": key, "label": label, "value": round(value, 3),
                       "bar": max(0.0, min(1.0, value))})
    return {
        "last_ts": snapshot.get("last_ts"),
        "field_active": bool(snapshot.get("field")),
        "messages": {"user": counts["user"], "npc": counts["npc"]},
        "meters": meters,
    }
