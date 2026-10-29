"""Product Memory DTOs, persistence journals, and CX67 compatibility exports.

The numeric six-root contracts are owned by the reference package. This module
keeps product address/content/persistence contracts and preserves old imports.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping, Optional

from model_packages.cx_six_seven_v1.memory_numeric_contracts import (
    MEMORY_EVENT_REF_SCHEMA,
    MEMORY_INFLUENCE_EDGE_SCHEMA,
    MEMORY_INFLUENCE_TRANSACTION_SCHEMA,
    MEMORY_RECALL_EVENT_SCHEMA,
    NEUTRAL_MEMORY_READOUT_SCHEMA,
    MemoryEventRef,
    MemoryInfluenceEdge,
    MemoryInfluenceGraphIndex,
    MemoryInfluenceTransaction,
    MemoryRecallEvent,
    NeutralMemoryReadout,
)


NUMERIC_RECALL_WITNESS_SCHEMA = 2
ADDRESS_RECEIPT_SCHEMA = 1
CANONICAL_RECALL_ITEM_SCHEMA = 1
MEMORY_CARD_SCHEMA = 1

def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} 不能为空")
    return normalized


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是非负整数时间")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是非负整数时间") from exc
    if parsed < 0 or parsed != value:
        raise ValueError(f"{field} 必须是非负整数时间")
    return parsed


def _number(value, field):
    try:
        parsed = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} 必须是有限数值") from exc
    if not math.isfinite(parsed):
        raise ValueError(f"{field} 必须是有限数值")
    return parsed


def _freeze_receipt(value, field="receipt"):
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze_receipt(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (tuple, list)):
        return tuple(_freeze_receipt(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{field} 必须可序列化且数值有限")
        return value
    raise ValueError(f"{field} 必须是可序列化的只读 receipt")


def _thaw_receipt(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw_receipt(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw_receipt(item) for item in value]
    return value


def _record(value, schema, required):
    if not isinstance(value, Mapping):
        raise ValueError("memory contract record 必须是映射")
    allowed = {"schema_version", *required}
    if set(value) - allowed:
        raise ValueError("memory contract record 含未知字段")
    missing = set(required) - set(value)
    if missing:
        raise ValueError("memory contract record 缺少字段：" + ", ".join(sorted(missing)))
    if value.get("schema_version") != schema:
        raise ValueError("memory contract schema_version 不受支持")
    return value


@dataclass(frozen=True)
class NumericRecallWitness:
    event_id: str
    activation: float
    condition: float
    priority: float
    eligible: bool
    scheduler_version: str
    neutral_readout_version: str
    receipt: Optional[Mapping[str, object]] = None

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        for field in ("activation", "condition", "priority"):
            object.__setattr__(self, field, _number(getattr(self, field), field))
        if not isinstance(self.eligible, bool):
            raise ValueError("eligible 必须是布尔值")
        object.__setattr__(self, "scheduler_version", _identifier(
            self.scheduler_version, "scheduler_version"))
        object.__setattr__(self, "neutral_readout_version", _identifier(
            self.neutral_readout_version, "neutral_readout_version"))
        receipt = {} if self.receipt is None else self.receipt
        if not isinstance(receipt, Mapping):
            raise ValueError("numeric witness.receipt 必须是映射")
        object.__setattr__(self, "receipt", _freeze_receipt(receipt))

    def to_record(self):
        return {"schema_version": NUMERIC_RECALL_WITNESS_SCHEMA,
                "event_id": self.event_id, "activation": self.activation,
                "condition": self.condition, "priority": self.priority,
                "eligible": self.eligible, "scheduler_version": self.scheduler_version,
                "neutral_readout_version": self.neutral_readout_version,
                "receipt": _thaw_receipt(self.receipt)}

    @classmethod
    def from_record(cls, record):
        keys = ("event_id", "activation", "condition", "priority", "eligible",
                "scheduler_version", "neutral_readout_version")
        if not isinstance(record, Mapping):
            raise ValueError("memory contract record 必须是映射")
        schema = record.get("schema_version")
        if schema == 1:
            row = _record(record, 1, set(keys))
            return cls(**{key: row[key] for key in keys}, receipt={})
        row = _record(record, NUMERIC_RECALL_WITNESS_SCHEMA, {*keys, "receipt"})
        return cls(**{key: row[key] for key in keys}, receipt=row["receipt"])


@dataclass(frozen=True)
class AddressReceipt:
    event_id: str
    status: str
    similarity: Optional[float]
    provider: str
    model: str
    policy_version: str

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        status = str(self.status or "").strip()
        if status not in {"available", "shadow", "unavailable", "failed"}:
            raise ValueError("address status 不受支持")
        object.__setattr__(self, "status", status)
        if status in {"available", "shadow"}:
            object.__setattr__(self, "similarity", _number(self.similarity, "similarity"))
            if not -1.0 <= self.similarity <= 1.0:
                raise ValueError("cosine similarity 必须在 -1 到 1 之间")
            for field in ("provider", "model", "policy_version"):
                object.__setattr__(self, field, _identifier(getattr(self, field), field))
        else:
            if self.similarity is not None:
                raise ValueError("unavailable/failed address 不得伪造 similarity")
            object.__setattr__(self, "provider", str(self.provider or "").strip())
            object.__setattr__(self, "model", str(self.model or "").strip())
            object.__setattr__(self, "policy_version", str(self.policy_version or "").strip())

    def to_record(self):
        return {"schema_version": ADDRESS_RECEIPT_SCHEMA, "event_id": self.event_id,
                "status": self.status, "similarity": self.similarity,
                "provider": self.provider, "model": self.model,
                "policy_version": self.policy_version}

    @classmethod
    def from_record(cls, record):
        keys = ("event_id", "status", "similarity", "provider", "model", "policy_version")
        row = _record(record, ADDRESS_RECEIPT_SCHEMA, set(keys))
        return cls(**{key: row[key] for key in keys})


@dataclass(frozen=True)
class CanonicalRecallItem:
    event_id: str
    numeric_witness: Optional[NumericRecallWitness]
    address_receipt: Optional[AddressReceipt]
    internal_recall_eligible: bool
    content_eligible: bool
    state_eligible: bool

    def __post_init__(self):
        event_id = _identifier(self.event_id, "event_id")
        object.__setattr__(self, "event_id", event_id)
        if self.numeric_witness is None and self.address_receipt is None:
            raise ValueError("canonical recall item 至少需要一条 witness")
        for witness in (self.numeric_witness, self.address_receipt):
            if witness is not None and witness.event_id != event_id:
                raise ValueError("canonical recall witness 必须指向同一 exact event ID")
        for field in ("internal_recall_eligible", "content_eligible", "state_eligible"):
            if not isinstance(getattr(self, field), bool):
                raise ValueError(f"{field} 必须是布尔值")
        if self.state_eligible and self.numeric_witness is None:
            raise ValueError("没有 numeric witness 的事件不能获得 P4 状态资格")

    def to_record(self):
        return {"schema_version": CANONICAL_RECALL_ITEM_SCHEMA,
                "event_id": self.event_id,
                "numeric_witness": self.numeric_witness.to_record() if self.numeric_witness else None,
                "address_receipt": self.address_receipt.to_record() if self.address_receipt else None,
                "internal_recall_eligible": self.internal_recall_eligible,
                "content_eligible": self.content_eligible,
                "state_eligible": self.state_eligible}

    @classmethod
    def from_record(cls, record):
        required = {"event_id", "numeric_witness", "address_receipt",
                    "internal_recall_eligible", "content_eligible", "state_eligible"}
        row = _record(record, CANONICAL_RECALL_ITEM_SCHEMA, required)
        numeric = (NumericRecallWitness.from_record(row["numeric_witness"])
                   if row["numeric_witness"] is not None else None)
        address = (AddressReceipt.from_record(row["address_receipt"])
                   if row["address_receipt"] is not None else None)
        return cls(row["event_id"], numeric, address, row["internal_recall_eligible"],
                   row["content_eligible"], row["state_eligible"])


@dataclass(frozen=True)
class MemoryCard:
    event_id: str
    committed_at_ms: int
    speaker_scope: str
    speaker_participant_id: str
    text: str
    provenance: Mapping[str, object]
    receipt_refs: tuple = ()

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        object.__setattr__(self, "committed_at_ms", _timestamp(
            self.committed_at_ms, "committed_at_ms"))
        object.__setattr__(self, "speaker_scope", _identifier(
            self.speaker_scope, "speaker_scope"))
        object.__setattr__(self, "speaker_participant_id",
                           str(self.speaker_participant_id or "").strip())
        object.__setattr__(self, "text", _identifier(self.text, "text"))
        if not isinstance(self.provenance, Mapping):
            raise ValueError("provenance 必须是映射")
        object.__setattr__(self, "provenance", MappingProxyType(
            deepcopy(dict(self.provenance))))
        object.__setattr__(self, "receipt_refs", tuple(
            _identifier(item, "receipt_ref") for item in self.receipt_refs))

    def to_record(self):
        return {"schema_version": MEMORY_CARD_SCHEMA, "event_id": self.event_id,
                "committed_at_ms": self.committed_at_ms,
                "speaker_scope": self.speaker_scope,
                "speaker_participant_id": self.speaker_participant_id,
                "text": self.text, "provenance": deepcopy(dict(self.provenance)),
                "receipt_refs": list(self.receipt_refs)}

    @classmethod
    def from_record(cls, record):
        required = {"event_id", "committed_at_ms", "speaker_scope",
                    "speaker_participant_id", "text", "provenance", "receipt_refs"}
        row = _record(record, MEMORY_CARD_SCHEMA, required)
        return cls(row["event_id"], row["committed_at_ms"], row["speaker_scope"],
                   row["speaker_participant_id"], row["text"], row["provenance"],
                   tuple(row["receipt_refs"]))


class MemoryInfluenceJournal:
    """ID-only persistence boundary for committed forward recall influence."""

    def __init__(self, store):
        self.store = store

    def read(self):
        return tuple(MemoryInfluenceTransaction.from_record(row)
                     for row in self.store.read_memory_influence_graph())

    def _canonical_node_times(self):
        result = {}
        for row in self.store.read_messages():
            if not isinstance(row, Mapping):
                continue
            node_id = str(row.get("event_id") or "").strip()
            if not node_id:
                continue
            committed_at = _timestamp(row.get("ts"), "message.ts")
            if node_id in result and result[node_id] != committed_at:
                raise ValueError("同一 canonical event ID 出现冲突时间")
            result[node_id] = committed_at
        return result

    def index(self):
        return MemoryInfluenceGraphIndex(
            canonical_node_times=self._canonical_node_times(),
            transactions=self.read(),
        )

    def append(self, transaction):
        if not isinstance(transaction, MemoryInfluenceTransaction):
            raise ValueError("journal 只接受 MemoryInfluenceTransaction")
        existing = self.read()
        MemoryInfluenceGraphIndex(
            canonical_node_times=self._canonical_node_times(),
            transactions=(*existing, transaction),
        )
        self.store.append_memory_influence_transaction(transaction.to_record())
        return transaction


class MemoryRecallJournal:
    """Validate and append Alpha 0.4 recall events without touching Runner state."""

    def __init__(self, store):
        self.store = store

    def read(self):
        return tuple(MemoryRecallEvent.from_record(row)
                     for row in self.store.read_memory_recall_events())

    def _committed_event_times(self):
        result = {}
        for row in self.store.read_messages():
            if not isinstance(row, Mapping):
                continue
            event_id = str(row.get("event_id") or "").strip()
            if not event_id:
                continue
            ts = _timestamp(row.get("ts"), "message.ts")
            if event_id in result and result[event_id] != ts:
                raise ValueError("同一 committed event ID 出现冲突时间")
            result[event_id] = ts
        return result

    def append(self, event):
        if not isinstance(event, MemoryRecallEvent):
            raise ValueError("journal 只接受 MemoryRecallEvent")
        known = self._committed_event_times()
        if event.target_event_id not in known:
            raise ValueError("memory recall target 是未知 committed event ID")
        if event.trigger_event_id not in known:
            raise ValueError("memory recall trigger 是未知 committed event ID")
        target_ts = known[event.target_event_id]
        trigger_ts = known[event.trigger_event_id]
        if not target_ts < trigger_ts <= event.committed_at_ms:
            raise ValueError("memory recall edge 必须由当前 trigger 严格指向过去 target")

        existing = self.read()
        by_id = {row.recall_event_id: row for row in existing}
        if event.recall_event_id in by_id:
            raise ValueError("memory recall event identity 重复")
        edge = (event.trigger_event_id, event.target_event_id)
        if any((row.trigger_event_id, row.target_event_id) == edge for row in existing):
            raise ValueError("memory recall trigger-target edge 重复")
        for parent_id in event.parent_recall_event_ids:
            parent = by_id.get(parent_id)
            if parent is None:
                raise ValueError("memory recall parent 是未知 recall event ID")
            if parent.committed_at_ms >= event.committed_at_ms:
                raise ValueError("memory recall parent 必须严格早于 child")

        self.store.append_memory_recall_event(event.to_record())
        return event
