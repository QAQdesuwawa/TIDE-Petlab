"""台账语义重排前的本地候选池。

候选池是纯只读步骤：保留已有本地相关候选，并可补入少量高显著条目，
让弱词面指代仍有机会进入后续受限重排。它不调用模型、不选择、不写入 Ledger。
"""
from dataclasses import dataclass


def ledger_candidate_id(record):
    """台账记录在单角色目录内的稳定候选标识。"""
    if not isinstance(record, dict) or "ts" not in record:
        raise ValueError("ledger 候选缺少 ts")
    return "ledger:%d" % int(record["ts"])


@dataclass(frozen=True)
class LedgerCandidate:
    candidate_id: str
    record: dict
    local_relevance: float
    salience: float
    origin: str

    def public(self):
        """供审计/网络边界使用的无正文元数据。"""
        return {
            "candidate_id": self.candidate_id,
            "source": "ledger",
            "record_ts": int(self.record["ts"]),
            "local_relevance": round(self.local_relevance, 4),
            "salience": round(self.salience, 4),
            "origin": self.origin,
        }


def _validate_bounds(capacity, exploration_budget):
    capacity, exploration_budget = int(capacity), int(exploration_budget)
    if capacity < 1 or exploration_budget < 0 or exploration_budget > capacity:
        raise ValueError("候选池容量或探索配额无效")
    return capacity, exploration_budget


def build_ledger_candidate_pool(*, ledger, local_candidates, now_ms,
                                capacity, exploration_budget):
    """构造有限的台账候选池，不改变既有本地相关性语义。

    ``local_candidates`` 只读取其中的 ``source/_record/relevance`` 字段，允许直接消费
    turn_recall 的内部候选形状。探索项不等于已相关：它们仅作为后续重排器可拒绝的
    提名，最终是否进入选择仍由 turn_recall 的资格门和概率门决定。
    """
    capacity, exploration_budget = _validate_bounds(capacity, exploration_budget)
    selected, seen = [], set()

    local = []
    for item in local_candidates or ():
        if not isinstance(item, dict) or item.get("source") != "ledger":
            continue
        record = item.get("_record") or item.get("record")
        if not isinstance(record, dict):
            continue
        candidate_id = ledger_candidate_id(record)
        if candidate_id in seen:
            continue
        local.append(LedgerCandidate(
            candidate_id=candidate_id, record=record,
            local_relevance=float(item.get("relevance", 0.0)),
            salience=float(ledger.decayed_salience(record, now_ms)), origin="local",
        ))
        seen.add(candidate_id)
    local.sort(key=lambda item: (-item.local_relevance, -item.salience,
                                 -int(item.record["ts"])))
    selected.extend(local[:capacity])
    seen = {item.candidate_id for item in selected}

    remaining = max(0, min(exploration_budget, capacity - len(selected)))
    if remaining == 0:
        return tuple(selected)

    exploration = []
    for record in ledger.read():
        if not isinstance(record, dict) or "contradicts" in record:
            continue
        candidate_id = ledger_candidate_id(record)
        if candidate_id in seen:
            continue
        salience = float(ledger.decayed_salience(record, now_ms))
        if salience <= 0.0:
            continue
        exploration.append(LedgerCandidate(
            candidate_id=candidate_id, record=record, local_relevance=0.0,
            salience=salience, origin="exploration"))
    exploration.sort(key=lambda item: (-item.salience, -int(item.record["ts"])))
    selected.extend(exploration[:remaining])
    return tuple(selected)
