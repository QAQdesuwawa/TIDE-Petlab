"""版本化的墙钟计划持久化（不持有计时器，不调用 Runner/LLM）。

``temporal.schedule`` 回答“连续公式的下一边界是什么”；本模块只让该答案在进程重启后
仍可审计，并让新用户输入能使旧到期任务失效。实际排队和串行执行仍属于 Actor。
"""
from temporal.schedule import plan_next_wake


class WallClockPlanner:
    """每角色一个计划仓库适配器。所有方法都是小型、可重建的存储事务。"""

    def __init__(self, store):
        self.store = store

    @staticmethod
    def normalize_legacy_plan(record):
        """把旧存档的续话字段一次性收束为当前 ``opening`` 计划。

        这是存储兼容迁移，不是第二套调度语义：迁移后的 Actor、WallClock 和
        ``temporal.schedule`` 都只会看到 ``opening`` / ``opening_due``。原 JSONL
        历史不被改写；调用方可在写入新 plan 时额外留下 migration receipt。
        """
        if not isinstance(record, dict):
            return {}, False
        plan = dict(record)
        migrated = False
        had_continuation = "continuation" in plan
        continuation = plan.pop("continuation", None)
        # 即使新旧字段并存，也必须留下迁移 receipt：当前 opening 保留优先，
        # 旧副本只被移除，不能静默消失而让审计误以为从未发生过迁移。
        if had_continuation:
            migrated = True
        if isinstance(continuation, dict) and not isinstance(plan.get("opening"), dict):
            plan["opening"] = dict(continuation)
        reasons = []
        for reason in plan.get("reasons") or ():
            canonical = "opening_due" if reason == "continuation_due" else reason
            if canonical not in reasons:
                reasons.append(canonical)
            if canonical != reason:
                migrated = True
        if list(plan.get("reasons") or ()) != reasons:
            plan["reasons"] = reasons
        return plan, migrated

    def _load_plan_for_write(self, now_ms, *, operation):
        """读取并规范化旧 plan；由紧随其后的当前写入完成迁移。"""
        raw = self.store.load_clock_plan() or {}
        plan, migrated = self.normalize_legacy_plan(raw)
        if migrated:
            self.store.append_event({
                "ts": int(now_ms), "kind": "wall_clock_plan_migrated",
                "detail": {"from": "continuation", "to": "opening",
                           "operation": str(operation)},
            })
        return plan

    @staticmethod
    def _opening_identity(candidate):
        """返回一次 F15 观察所属的不可变用户锚。

        这不是新的心理量，也不参与任何公式；它只是离散事件调度所需的
        ``e_user`` 身份。没有它，连续的 ``margin > 0`` 会在每个 tick 被误
        解为一次新的 crossing。
        """
        row = candidate if isinstance(candidate, dict) else {}
        sources = row.get("sources") if isinstance(row.get("sources"), dict) else {}
        anchor_ts = sources.get("anchor_ts", row.get("anchor_ts"))
        anchor_turn = sources.get("anchor_turn_id", row.get("anchor_turn_id", ""))
        version = sources.get("formula_version", "")
        try:
            anchor_ts = int(anchor_ts)
        except (TypeError, ValueError):
            anchor_ts = None
        # 旧存档没有 anchor 时不把不相干的候选错误并为同一条事件。
        return (str(version or "legacy"), anchor_ts, str(anchor_turn or ""))

    @staticmethod
    def has_future_pending_opening(record, now_ms):
        """判断一份已越线、但尚未到 F16 的正式 opening 是否仍待投递。

        这只是持久化计划的调度身份判断，不重算 margin、更不读取状态。恢复期若
        已经有这份冻结 crossing，``catch_up`` 不能再把它扫成一条离线历史消息；
        唯一合法消费者仍是计划到期后的 Actor dynamic tick。
        """
        plan, _migrated = WallClockPlanner.normalize_legacy_plan(record)
        opening = plan.get("opening")
        sources = opening.get("sources") if isinstance(opening, dict) else {}
        try:
            due_at = int(plan.get("at_ms"))
            current = int(now_ms)
        except (TypeError, ValueError):
            return False
        canonical = (isinstance(sources, dict)
                     and str(sources.get("formula_version") or "").startswith((
                         "opening-authority-receipt-v1", "opening-root-vector-")))
        return bool(
            isinstance(opening, dict)
            and bool(opening.get("eligible"))
            and opening.get("qualified_at_ms") is not None
            and canonical
            and due_at > current
            and "opening_due" in (plan.get("reasons") or ())
        )

    @classmethod
    def has_unconsumed_opening(cls, record):
        """判断一份 persisted opening 是否尚未形成自主 F16。

        ``at_ms`` 是 crossing 的逻辑投递目标，不是独立 timer 发送入口；它即使已经
        过去，只要 Actor 尚未经正式 dynamic tick 消费该计划，就仍可能被随后 direct
        F16 替代。这里仅识别 Store 中仍具 ``dispatch_eligible`` 身份的冻结候选，
        不读取正文、不重算 margin，也不改变状态。
        """
        plan, _migrated = cls.normalize_legacy_plan(record)
        opening = plan.get("opening") if isinstance(plan, dict) else None
        edge = plan.get("opening_edge") if isinstance(plan, dict) else None
        sources = opening.get("sources") if isinstance(opening, dict) else None
        canonical = (
            isinstance(sources, dict)
            and str(sources.get("formula_version") or "").startswith((
                "opening-authority-receipt-v1", "opening-root-vector-"))
        )
        return bool(
            isinstance(opening, dict)
            and isinstance(edge, dict)
            and canonical
            and bool(opening.get("eligible"))
            and opening.get("qualified_at_ms") is not None
            and bool(edge.get("dispatch_eligible"))
            and "opening_due" in (plan.get("reasons") or ())
        )

    @classmethod
    def _opening_edge(cls, old, candidate, *, reset=False, now_ms=0):
        """把连续 margin 投影为一次可消费的离散零交叉。

        ``eligible`` 仍是 F15 的原始数学读数；``dispatch_eligible`` 才是本次
        能否产生输出的调度资格。这样 trace 能同时说明“在线但已消费”和“真
        正再次越线”，不会靠条数上限或耐火期压住连发。
        """
        if not isinstance(candidate, dict):
            return None, None
        raw_eligible = bool(candidate.get("eligible"))
        try:
            margin = float(candidate.get("score", -1.0))
        except (TypeError, ValueError):
            margin = -1.0
        identity = cls._opening_identity(candidate)
        old_edge = old.get("opening_edge") if isinstance(old, dict) else None
        same_anchor = (
            not reset
            and isinstance(old_edge, dict)
            and tuple(old_edge.get("anchor") or ()) == identity
        )
        was_above = bool(old_edge.get("above")) if same_anchor else False
        dispatch_eligible = bool(raw_eligible and not was_above)
        if raw_eligible:
            disposition = "crossed" if dispatch_eligible else "held_above_after_consumption"
        else:
            disposition = "below_zero_rearmed" if was_above else "below_zero"
        edge = {
            "version": "opening-edge-receipt-v1",
            "anchor": list(identity),
            "above": raw_eligible,
            "last_margin": round(margin, 6),
            "dispatch_eligible": dispatch_eligible,
            "disposition": disposition,
            "observed_at_ms": int(now_ms),
        }
        # 计划器只消费这个副本；原始 F15 receipt 必须原样留在计划/trace 中。
        dispatch = dict(candidate)
        dispatch["eligible"] = dispatch_eligible
        dispatch["qualified_at_ms"] = (
            candidate.get("qualified_at_ms") if dispatch_eligible else None)
        dispatch["edge_receipt"] = dict(edge)
        return edge, dispatch

    def _plan_record(self, old, state, constants, now_ms, *, params=None,
                     cause, candidate=None, cadence=None, reset_edge=False,
                     version=None, authority_receipt=None):
        """由一份 F15 receipt 生成持久化计划；不改变 C/X 或公式读数。"""
        if version is None:
            version = max(1, int((old or {}).get("version", 1)))
        edge, dispatch = self._opening_edge(
            old or {}, candidate, reset=reset_edge, now_ms=now_ms)
        plan = plan_next_wake(state, constants, now_ms, params=params,
                              opening=dispatch, cadence=cadence)
        record = dict(plan, version=int(version), cause=str(cause),
                      status="scheduled" if plan["at_ms"] is not None else "idle")
        if isinstance(candidate, dict):
            # ``opening`` 保持原始 F15 读数（可解释、可回放）；真正可投递性写在
            # 单独的 edge receipt，避免把调度结果伪装成数学读数。
            record["opening"] = dict(candidate)
        if edge is not None:
            record["opening_edge"] = edge
        elif not reset_edge and isinstance((old or {}).get("opening_edge"), dict):
            # 输出 receipt / 重启可以只刷新状态与版本，却不应抹掉同一用户锚已经
            # 消费过的 crossing。否则下一次 tick 会把 held-high 当作第一次越线，
            # 重新形成连发。新用户输入的 replace(reset_edge=True) 才会明确 rearm。
            record["opening_edge"] = dict((old or {}).get("opening_edge") or {})
        if isinstance(cadence, dict):
            record["cadence"] = dict(cadence)
        if isinstance(authority_receipt, dict):
            # 这是已提交事件的纯数学 receipt，不是一个未来计划或 UI 预测。保留在
            # 同版本 plan 只为 Actor/UI 订阅；它不改 edge、不会令 at_ms 提前成立。
            record["opening_authority_receipt"] = dict(authority_receipt)
        return record

    def replace(self, state, constants, now_ms, params=None, cause="user_event",
                opening=None, cadence=None, authority_receipt=None):
        """新扰动后替换未来计划，版本单调增加，旧任务因此失效。"""
        old = self._load_plan_for_write(now_ms, operation="replace")
        version = int(old.get("version", 0)) + 1
        record = self._plan_record(
            old, state, constants, now_ms, params=params, cause=cause,
            candidate=opening, cadence=cadence, reset_edge=True, version=version,
            authority_receipt=authority_receipt)
        self.store.save_clock_plan(record)
        self.store.append_event({"ts": int(now_ms), "kind": "wall_clock_plan_replaced",
                                 "detail": {"version": version, "at_ms": record["at_ms"],
                                            "reasons": list(record["reasons"]),
                                            "cause": record["cause"]}})
        return record

    def rebuild(self, state, constants, now_ms, params=None, opening=None, cadence=None):
        """重启/恢复时由快照与绝对时间重算，不凭进程内 timer 猜剩余时间。"""
        old = self._load_plan_for_write(now_ms, operation="rebuild")
        version = max(1, int(old.get("version", 1)))
        # 已经越线并进入 F16 的开口是一个离散、可取消的待投递事件，不是每次
        # 重启都要重新问一次连续函数的预测。它只会由新的用户事件（replace 递增
        # version）或正式输出收据替换；否则“刚刚越线、重启后已回落”会吞掉消息。
        old_is_pending_opening = self.has_future_pending_opening(old, now_ms)
        if old_is_pending_opening:
            record = dict(old, computed_at_ms=int(now_ms), cause="rebuild_pending",
                          status="scheduled")
            if isinstance(cadence, dict):
                record["cadence"] = dict(cadence)
            self.store.save_clock_plan(record)
            return record
        candidate = opening if isinstance(opening, dict) else old.get("opening")
        sources = candidate.get("sources") if isinstance(candidate, dict) else {}
        canonical = (isinstance(sources, dict)
                     and str(sources.get("formula_version") or "").startswith((
                         "opening-authority-receipt-v1", "opening-root-vector-")))
        if canonical:
            record = self._plan_record(
                old, state, constants, now_ms, params=params, cause="rebuild",
                candidate=candidate, cadence=cadence, reset_edge=False, version=version,
                authority_receipt=old.get("opening_authority_receipt"))
        else:
            plan = plan_next_wake(state, constants, now_ms, params=params,
                                  opening=candidate, cadence=cadence)
            record = dict(plan, version=version, cause="rebuild", status="scheduled"
                          if plan["at_ms"] is not None else "idle")
            if isinstance(candidate, dict):
                record["opening"] = dict(candidate)
            if isinstance(cadence, dict):
                record["cadence"] = dict(cadence)
            authority = old.get("opening_authority_receipt")
            if isinstance(authority, dict):
                record["opening_authority_receipt"] = dict(authority)
        self.store.save_clock_plan(record)
        return record

    def refresh(self, state, constants, now_ms, params=None, cause="time_advanced",
                opening=None, cadence=None, authority_receipt=None, reset_edge=False):
        """时间已经结算、但没有新的用户扰动时刷新同一版本的未来边界。"""
        old = self._load_plan_for_write(now_ms, operation="refresh")
        version = max(1, int(old.get("version", 1)))
        record = self._plan_record(
            old, state, constants, now_ms, params=params, cause=cause,
            candidate=opening, cadence=cadence, reset_edge=bool(reset_edge), version=version,
            authority_receipt=authority_receipt)
        self.store.save_clock_plan(record)
        self.store.append_event({"ts": int(now_ms), "kind": "wall_clock_plan_refreshed",
                                 "detail": {"version": version, "at_ms": record["at_ms"],
                                            "reasons": list(record["reasons"]),
                                            "cause": record["cause"]}})
        return record

    @staticmethod
    def is_due(record, now_ms, expected_version=None):
        """仅当前版本且到达绝对时刻的计划可以投递。"""
        if not record or record.get("at_ms") is None:
            return False
        if expected_version is not None and int(record.get("version", -1)) != int(expected_version):
            return False
        return int(now_ms) >= int(record["at_ms"])
