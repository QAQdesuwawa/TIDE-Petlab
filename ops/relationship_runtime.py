"""定向关系状态与 Actor 单线运行时的持久化边界。

关系 key 只隔离 ``owner → counterparty`` 的六维状态与快照；它不是第二个
Actor。每个 owner 的墙钟计划、dynamic tick、committed event/action 顺序和运行时回执
都留在同一个 owner Store。关系 Runner 写这些 Actor 总账时带上 provenance，并只读取
属于当前 relation 的计算视图，绝不因此创建第二条时间线。
"""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import time
from typing import Mapping

from ops.storage import UserStore


class RelationshipRuntimeSchemaError(ValueError):
    """定向关系运行时的身份或持久化合同无效。"""


def _participant_id(value: object, label: str) -> str:
    participant_id = str(value or "").strip()
    if not participant_id:
        raise RelationshipRuntimeSchemaError(f"{label} 不能为空")
    return participant_id


@dataclass(frozen=True)
class DirectedRelationshipKey:
    """一条本地关系视图：``owner`` 对 ``counterparty``，而非无向 pair。"""

    owner_participant_id: str
    counterparty_participant_id: str
    schema_version: int = 1

    def __post_init__(self) -> None:
        owner = _participant_id(self.owner_participant_id, "owner_participant_id")
        counterparty = _participant_id(self.counterparty_participant_id,
                                      "counterparty_participant_id")
        if owner == counterparty:
            raise RelationshipRuntimeSchemaError("owner 与 counterparty 不能是同一 participant")
        if self.schema_version != 1:
            raise RelationshipRuntimeSchemaError("不支持的 relationship runtime schema_version")
        object.__setattr__(self, "owner_participant_id", owner)
        object.__setattr__(self, "counterparty_participant_id", counterparty)

    @property
    def relation_id(self) -> str:
        """可读、方向敏感的稳定关系身份；它不是文件路径。"""
        return "%s→%s" % (self.owner_participant_id, self.counterparty_participant_id)

    @property
    def runtime_store_suffix(self) -> str:
        """安全、稳定的目录后缀；不以裁剪后的 participant 文本制造碰撞。"""
        return sha256(self.relation_id.encode("utf-8")).hexdigest()[:24]

    def as_record(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "owner_participant_id": self.owner_participant_id,
            "counterparty_participant_id": self.counterparty_participant_id,
        }

    @classmethod
    def from_record(cls, raw: Mapping[str, object]) -> "DirectedRelationshipKey":
        if not isinstance(raw, Mapping):
            raise RelationshipRuntimeSchemaError("relationship runtime key 必须为对象")
        fields = {"schema_version", "owner_participant_id", "counterparty_participant_id"}
        unknown = set(raw) - fields
        missing = fields - set(raw)
        if unknown or missing:
            details = []
            if unknown:
                details.append("未知字段：%s" % ", ".join(sorted(unknown)))
            if missing:
                details.append("缺少字段：%s" % ", ".join(sorted(missing)))
            raise RelationshipRuntimeSchemaError("relationship runtime key 合同错误（%s）" % "；".join(details))
        return cls(
            owner_participant_id=raw["owner_participant_id"],
            counterparty_participant_id=raw["counterparty_participant_id"],
            schema_version=raw["schema_version"],
        )


class RelationshipRuntimeStore:
    """一个 Actor 的共享记忆与一条定向关系运行态之间的显式分流。

    这不是 ``UserStore`` 的透明替身，故意不实现 ``__getattr__``：尚未完成审计的
    Runner 不会意外把 relation 和 owner 两条 lane 混用。调用方只能明确选择：

    - relation state / snapshots → ``runtime_store``；
    - clock plan / events / actions / runtime receipts / messages / archive / identity
      → ``owner_store``。
    """

    def __init__(self, owner_store: UserStore, key: DirectedRelationshipKey):
        if not isinstance(owner_store, UserStore):
            raise RelationshipRuntimeSchemaError("owner_store 必须为 UserStore")
        if not isinstance(key, DirectedRelationshipKey):
            raise RelationshipRuntimeSchemaError("key 必须为 DirectedRelationshipKey")
        self.owner_store = owner_store
        self.key = key
        owner_digest = sha256(owner_store.user_id.encode("utf-8")).hexdigest()[:10]
        runtime_id = "relationship-%s-%s" % (owner_digest, key.runtime_store_suffix)
        self.runtime_store = UserStore(
            runtime_id,
            base_dir=owner_store.base_dir,
            durable_writes=owner_store._durable_writes,
        )

    @property
    def relation_id(self) -> str:
        return self.key.relation_id

    def scope_record(self) -> dict[str, object]:
        return {
            "schema_version": 1,
            "relation_key": self.key.as_record(),
            "owner_store_id": self.owner_store.user_id,
            "runtime_store_id": self.runtime_store.user_id,
        }

    # -- relation state lane: no clock, no Actor event/action ledger --
    def save_state(self, state):
        return self.runtime_store.save_state(state)

    def load_state(self):
        return self.runtime_store.load_state()

    def save_clock_plan(self, plan):
        return self.owner_store.save_clock_plan(self._tag_actor_clock_plan(plan))

    def load_clock_plan(self):
        plan = self.owner_store.load_clock_plan()
        return self._relation_clock_plan(plan)

    def save_snapshot(self, state, turn_id):
        return self.runtime_store.save_snapshot(state, turn_id)

    def list_snapshots(self):
        return self.runtime_store.list_snapshots()

    def append_event(self, record):
        return self.owner_store.append_event(self._tag_actor_runtime_record(record))

    def read_events(self):
        return self._read_relation_runtime_rows("read_events")

    def append_action_receipt(self, record):
        return self.owner_store.append_action_receipt(self._tag_actor_runtime_record(record))

    def read_action_receipts(self):
        return self._read_relation_runtime_rows("read_action_receipts")

    def append_outcome(self, record):
        return self.owner_store.append_outcome(self._tag_actor_runtime_record(record))

    def read_outcomes(self):
        return self._read_relation_runtime_rows("read_outcomes")

    def read_actor_clock_plan(self):
        """读取 owner 的唯一计划；供 Actor 调度器按 scope 选择 relation。

        这不是关系 Runner 的本地 P3 读取入口，不能由 UI/Router 用来推进或发送。
        """
        return self.owner_store.load_clock_plan()

    def read_actor_events(self):
        """只读 owner 的单一 committed event 顺序，供回放/审计使用。"""
        return self.owner_store.read_events()

    def read_actor_action_receipts(self):
        """只读 owner 的单一 action 顺序，供回放/审计使用。"""
        return self.owner_store.read_action_receipts()

    def reserve_npc_action_id(self, ts):
        """关系输出仍向 owner 的唯一 action ledger 领取身份。"""
        return self.owner_store.reserve_npc_action_id(ts)

    def _tag_actor_runtime_record(self, record: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(record, Mapping):
            raise RelationshipRuntimeSchemaError("Actor runtime record 必须为对象")
        existing = record.get("relationship_runtime")
        if existing is not None and existing != self.scope_record():
            raise RelationshipRuntimeSchemaError("relation runtime 不得伪造另一条 provenance")
        tagged = dict(record)
        tagged["relationship_runtime"] = self.scope_record()
        return tagged

    def _tag_actor_clock_plan(self, plan: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(plan, Mapping):
            raise RelationshipRuntimeSchemaError("clock plan 必须为对象")
        existing = plan.get("relationship_runtime")
        if existing is not None and existing != self.scope_record():
            raise RelationshipRuntimeSchemaError("clock plan 不得覆盖为另一条 relationship scope")
        tagged = dict(plan)
        # plan 仍是 owner 的唯一 P3 计划；这个字段只说明它当前作用的 relation state。
        tagged["relationship_runtime"] = self.scope_record()
        return tagged

    def _relation_clock_plan(self, plan):
        if not isinstance(plan, Mapping):
            return None
        if plan.get("relationship_runtime") != self.scope_record():
            return None
        return dict(plan)

    def _read_relation_runtime_rows(self, reader_name: str):
        rows = getattr(self.owner_store, reader_name)()
        scope = self.scope_record()
        return [dict(row) for row in rows if row.get("relationship_runtime") == scope]

    # -- actor-owned continuous memory lane --
    def _tag_relation_memory_record(self, record: Mapping[str, object]) -> dict[str, object]:
        """为正式关系链的 owner memory 留下来源，而不把历史切进 relation Store。

        tag 只是不可变来源：后续 recall 可以从 Actor 的连续历史中选中它，但任何 P4
        影响仍必须写入 *当前* relation runtime。这里不做选择、不读状态，也不允许
        调用方把别的 relation 的 tag 伪装成当前写入。
        """
        if not isinstance(record, Mapping):
            raise RelationshipRuntimeSchemaError("relation memory record 必须为对象")
        if "relationship_runtime" in record:
            raise RelationshipRuntimeSchemaError("relation memory record 不得携带已有 relationship_runtime")
        tagged = dict(record)
        tagged["relationship_runtime"] = self.scope_record()
        return tagged

    def append_relation_message(self, record: Mapping[str, object]):
        return self.owner_store.append_message(self._tag_relation_memory_record(record))

    def read_messages(self):
        return self.owner_store.read_messages()

    def append_relation_archive(self, record: Mapping[str, object]):
        return self.owner_store.append_archive(self._tag_relation_memory_record(record))

    def read_archive(self):
        return self.owner_store.read_archive()

    def runner_store(self) -> "RelationshipRunnerStore":
        """返回已审计的 Runner Store 视图；不产生、推进或调度任何事件。"""
        return RelationshipRunnerStore(self)


class RelationshipRunnerStore:
    """供一个关系 Runner 使用的受限 Store facade。

    它把 relation state/snapshot 写入关系 lane；clock/event/action/runtime receipt、连续
    历史和身份档案均留在 owner lane。后者每行携带 relation provenance，但仍构成一个
    Actor 的总账。此类不含 Conversation/Router/tick 权限；调用方仍须根据 immutable
    addressed source 从 registry 获取正确的 relation，不能把它当作群状态机。
    """

    _RUNTIME_METHODS = frozenset({
        "append_boundary", "append_breakpoint", "append_cold", "append_constraint",
        "append_interrupt", "append_llm_call", "append_math_trace", "append_outcome",
        "append_prompt_trace", "append_recall_evidence", "append_telemetry",
        "read_boundary", "read_breakpoints", "read_cold_log", "read_constraints",
        "read_interrupts", "read_llm_calls", "read_outcomes", "read_prompt_traces",
        "read_recall_evidence", "read_telemetry",
    })

    def __init__(self, relationship: RelationshipRuntimeStore):
        if not isinstance(relationship, RelationshipRuntimeStore):
            raise RelationshipRuntimeSchemaError("runner store 必须绑定 RelationshipRuntimeStore")
        self.relationship = relationship
        self.owner_store = relationship.owner_store
        self.runtime_store = relationship.runtime_store
        # Runner 的 Actor identity、时间与 append-only 事实必须是 owner 的单线。
        self.user_id = self.owner_store.user_id
        self.base_dir = self.owner_store.base_dir
        self.user_dir = self.owner_store.user_dir
        self.state_path = self.runtime_store.state_path
        self.clock_plan_path = self.owner_store.clock_plan_path
        self.snapshots_dir = self.runtime_store.snapshots_dir
        self.profile_path = self.owner_store.profile_path
        self.persona_path = self.owner_store.persona_path
        self.user_card_path = self.owner_store.user_card_path
        self.messages_path = self.owner_store.messages_path
        self.archive_path = self.owner_store.archive_path

    def _tag_owner_record(self, record: Mapping[str, object]) -> dict[str, object]:
        if not isinstance(record, Mapping):
            raise RelationshipRuntimeSchemaError("owner history record 必须为对象")
        existing = record.get("relationship_runtime")
        if existing is not None:
            if existing != self.relationship.scope_record():
                raise RelationshipRuntimeSchemaError("关系 Runner 不得写入另一条 relationship_runtime history")
            return dict(record)
        # group-visible observation 是 owner 看见的事实，不是当前 relation 的 F03 产物。
        if record.get("group_visibility_provenance") is not None:
            return dict(record)
        tagged = dict(record)
        tagged["relationship_runtime"] = self.relationship.scope_record()
        return tagged

    # Owner identity and continuous history.
    def save_profile(self, profile):
        return self.owner_store.save_profile(profile)

    def load_profile(self):
        return self.owner_store.load_profile()

    def save_persona_card(self, text):
        return self.owner_store.save_persona_card(text)

    def load_persona_card(self):
        return self.owner_store.load_persona_card()

    def append_message(self, record):
        return self.owner_store.append_message(self._tag_owner_record(record))

    def read_messages(self):
        return self.owner_store.read_messages()

    def read_recent_messages(self, limit):
        return self.owner_store.read_recent_messages(limit)

    def read_recent_message_window(self, limit):
        return self.owner_store.read_recent_message_window(limit)

    def append_archive(self, record):
        return self.owner_store.append_archive(self._tag_owner_record(record))

    def read_archive(self):
        return self.owner_store.read_archive()

    def append_self_ledger(self, record):
        return self.owner_store.append_self_ledger(self._tag_owner_record(record))

    def read_self_ledger(self):
        return self.owner_store.read_self_ledger()

    # Relation root state and all runtime receipts.
    def save_state(self, state):
        return self.runtime_store.save_state(state)

    def load_state(self):
        return self.runtime_store.load_state()

    def save_clock_plan(self, plan):
        return self.relationship.save_clock_plan(plan)

    def load_clock_plan(self):
        return self.relationship.load_clock_plan()

    def save_snapshot(self, state, turn_id):
        return self.runtime_store.save_snapshot(state, turn_id)

    def list_snapshots(self):
        return self.runtime_store.list_snapshots()

    def append_event(self, record):
        return self.relationship.append_event(record)

    def read_events(self):
        return self.relationship.read_events()

    def append_action_receipt(self, record):
        return self.relationship.append_action_receipt(record)

    def read_action_receipts(self):
        return self.relationship.read_action_receipts()

    def reserve_npc_action_id(self, ts):
        return self.relationship.reserve_npc_action_id(ts)

    def append_ledger_recall(self, record):
        return self.owner_store.append_ledger_recall(self._tag_owner_record(record))

    def read_ledger_recalls(self):
        scope = self.relationship.scope_record()
        return [dict(row) for row in self.owner_store.read_ledger_recalls()
                if row.get("relationship_runtime") == scope]

    def recover_npc_action_receipts(self):
        """只从 owner history 中恢复本 relation 已提交却缺 receipt 的输出。"""
        known = {
            str(row.get("action_id")) for row in self.read_action_receipts()
            if row.get("action_id") is not None
        }
        recovered = []
        scope = self.relationship.scope_record()
        for message in self.owner_store.read_messages():
            if (message.get("role") != "npc"
                    or message.get("relationship_runtime") != scope):
                continue
            action_id = message.get("action_id")
            if action_id is None or str(action_id) in known:
                continue
            receipt = {
                "schema_version": 1,
                "action_id": str(action_id),
                "ts": int(message.get("ts", 0) or 0),
                "source": str(message.get("source") or "reply"),
                "params_sha": str(message.get("params_sha") or "unknown"),
                "recall_refs": [],
                "recovery": {
                    "kind": "message_without_action_receipt",
                    "recovered_at": int(time.time() * 1000),
                    "missing_fields": ["recall_mode", "recall_refs", "recall_strategy",
                                       "state_snapshot_turn"],
                },
            }
            for field in ("turn_id", "cause_id"):
                if message.get(field) is not None:
                    receipt[field] = str(message[field])
            self.append_action_receipt(receipt)
            known.add(str(action_id))
            recovered.append(receipt)
        return recovered

    def read_json(self, path):
        candidate = str(path)
        if candidate.startswith(self.runtime_store.user_dir):
            return self.runtime_store.read_json(path)
        if candidate.startswith(self.owner_store.user_dir):
            return self.owner_store.read_json(path)
        raise RelationshipRuntimeSchemaError("relation Runner 不能读取 owner/runtime 之外的路径")

    def write_json(self, path, value):
        candidate = str(path)
        if candidate.startswith(self.runtime_store.user_dir):
            return self.runtime_store.write_json(path, value)
        if candidate.startswith(self.owner_store.user_dir):
            return self.owner_store.write_json(path, value)
        raise RelationshipRuntimeSchemaError("relation Runner 不能写入 owner/runtime 之外的路径")

    def stream_paths(self):
        return self.owner_store.stream_paths()

    def __getattr__(self, name):
        if name in self._RUNTIME_METHODS:
            method = getattr(self.owner_store, name)
            if name.startswith("append_"):
                return lambda record: method(self.relationship._tag_actor_runtime_record(record))
            if name.startswith("read_"):
                scope = self.relationship.scope_record()
                return lambda: [dict(row) for row in method()
                                if row.get("relationship_runtime") == scope]
            return method
        raise AttributeError(name)


class RelationshipRuntimeRegistry:
    """按 Actor owner 持有多条已实例化的定向关系运行时。

    它是无调度的装配表：没有 Conversation、正文、状态计算、tick 或 relay 权限。
    未来 coordinator 只能依据已冻结 source 的 sender identity 取对应 runtime，而不能
    通过文本、成员顺序或“群气氛”猜测应该使用哪条关系。
    """

    def __init__(self, owner_stores: Mapping[str, UserStore]):
        if not isinstance(owner_stores, Mapping):
            raise RelationshipRuntimeSchemaError("owner_stores 必须为映射")
        normalized = {}
        for owner_id, store in owner_stores.items():
            normalized_owner = _participant_id(owner_id, "owner participant ID")
            if not isinstance(store, UserStore):
                raise RelationshipRuntimeSchemaError("每个 owner 必须绑定 UserStore")
            if normalized_owner in normalized:
                raise RelationshipRuntimeSchemaError("owner participant ID 重复")
            normalized[normalized_owner] = store
        self._owner_stores = normalized
        self._runtimes: dict[DirectedRelationshipKey, RelationshipRuntimeStore] = {}

    def runtime_for(self, owner_participant_id: str,
                    counterparty_participant_id: str) -> RelationshipRuntimeStore:
        key = DirectedRelationshipKey(owner_participant_id, counterparty_participant_id)
        owner_store = self._owner_stores.get(key.owner_participant_id)
        if owner_store is None:
            raise RelationshipRuntimeSchemaError("relation runtime 没有 owner Store")
        runtime = self._runtimes.get(key)
        if runtime is None:
            runtime = RelationshipRuntimeStore(owner_store, key)
            self._runtimes[key] = runtime
        return runtime

    def keys(self) -> tuple[DirectedRelationshipKey, ...]:
        return tuple(sorted(
            self._runtimes,
            key=lambda key: (key.owner_participant_id, key.counterparty_participant_id),
        ))


class RelationshipRunnerRegistry:
    """按明确 directed relation 延迟装配 Runner；没有群级调度能力。"""

    def __init__(self, runtime_registry: RelationshipRuntimeRegistry, runner_factory):
        if not isinstance(runtime_registry, RelationshipRuntimeRegistry):
            raise RelationshipRuntimeSchemaError("runtime_registry 必须为 RelationshipRuntimeRegistry")
        if not callable(runner_factory):
            raise RelationshipRuntimeSchemaError("runner_factory 必须可调用")
        self.runtime_registry = runtime_registry
        self.runner_factory = runner_factory
        self._runners = {}

    def runner_for(self, owner_participant_id: str, counterparty_participant_id: str):
        key = DirectedRelationshipKey(owner_participant_id, counterparty_participant_id)
        runner = self._runners.get(key)
        if runner is None:
            runtime = self.runtime_registry.runtime_for(
                key.owner_participant_id, key.counterparty_participant_id)
            runner = self.runner_factory(runtime.runner_store())
            store = getattr(runner, "store", None)
            if not isinstance(store, RelationshipRunnerStore) or store.relationship is not runtime:
                raise RelationshipRuntimeSchemaError(
                    "relation runner factory 必须返回绑定同一 runtime 的 Runner")
            self._runners[key] = runner
        return runner

    def keys(self) -> tuple[DirectedRelationshipKey, ...]:
        return tuple(sorted(
            self._runners,
            key=lambda key: (key.owner_participant_id, key.counterparty_participant_id),
        ))
