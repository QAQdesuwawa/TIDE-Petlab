"""连续墙钟的输出边界规划（纯函数，无计时器、无 I/O）。

F15 不再从冻结的当前 X 求未来越线，也不暗自另起固定频率的观察 timer。每次真实
Actor tick 都会先 F08 推进根状态、再计算本轮 ``I15 - Theta15``；本模块只在**当前
已经越线**时给出立即到期的输出候选。因此 ``at_ms`` 不是预写的消息时间；非越线
状态则由 Actor 的根量观察节律持续重新结算。
"""
from core.params import TIME


MIN_MS = 60_000.0
HOUR_MS = 3_600_000.0


def plan_next_wake(state, constants, now_ms, params=None, opening=None,
                   cadence=None):
    """规划当前状态的下一次数学边界。

    返回 JSON 可持久化字典：``at_ms`` 为下一唤醒绝对时刻或 ``None``，``reasons``
    是恰好在该时刻到达的公式边界。函数不修改 ``state``；计划版本、取消和实际投递
    属于 ``ops/`` 的职责。
    """
    p = params or TIME
    now = int(now_ms)
    candidates = []

    # 没有已提交用户输入冻结出的锚，就没有可归属的一次自主开口。不能让旧 F15
    # 从纯状态快照、最后活动时间或历史计数伪造一条新的用户事件；旧档必须在调用方
    # 显式迁移后才会拥有 anchor。
    if not isinstance((state or {}).get("opening_anchor"), dict):
        return {"at_ms": None, "reasons": [], "computed_at_ms": now}

    # 唯一开口边界：回复后与 tick 后只是不同观察来源，不能各自再算一条 F14/F15。
    # 旧 ``continuation`` 只允许在 WallClock 的存档迁移器中被读取；调度纯函数
    # 只接收已经规范化的 ``opening``，避免运行时存在第二个同义输入面。
    if isinstance(opening, dict) and opening.get("eligible"):
        due = opening.get("at_ms", now)
        try:
            candidates.append((max(now, int(due)), "opening_due"))
        except (TypeError, ValueError):
            pass

    if not candidates:
        return {"at_ms": None, "reasons": [], "computed_at_ms": now}
    at_ms = min(item[0] for item in candidates)
    reasons = [reason for when, reason in candidates if when == at_ms]
    return {"at_ms": int(at_ms), "reasons": reasons, "computed_at_ms": now}
