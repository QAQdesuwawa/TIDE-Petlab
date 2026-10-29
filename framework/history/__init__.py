"""Model-neutral committed History authority for the paper runtime.

History owns immutable fact identity, authorized strictly-prior reads, actual
recall records, and forward-only influence edges.  It intentionally does not
interpret text, participants, model dimensions, recall scores, card packages,
or temporal units.  Domain packages receive opaque payloads only after the
Kernel/host has authorized a read.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, fields
from typing import Any
from collections import OrderedDict
from threading import RLock
import copy
import hashlib
try:
    import orjson as _native_json
except ImportError:
    _native_json = None
from types import MappingProxyType

def _index_mapping(value):
    if type(value) is MappingProxyType:
        return dict(value)
    raise TypeError("not a plain record")

def _record_cache_key(record):
    # This is an index, never an integrity fingerprint. Cache reuse additionally
    # requires equality with an independent, fully validated record snapshot.
    if _native_json is not None:
        try:
            options = (_native_json.OPT_SORT_KEYS | _native_json.OPT_PASSTHROUGH_SUBCLASS
                       | _native_json.OPT_PASSTHROUGH_DATACLASS | _native_json.OPT_PASSTHROUGH_DATETIME)
            raw = _native_json.dumps(record, option=options, default=_index_mapping)
            return ("native-index", hashlib.sha256(raw).digest())
        except (TypeError, ValueError):
            pass
    return ("canonical-index", model_data_fingerprint(record))

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)


HISTORY_CONTRACT_VERSION = "framework-history-v1"
FRAMEWORK_HISTORY_PROFILE_KEY = "framework_history"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _ordinal(value: object, field: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{field} must be a non-negative integer")
    return value


def _identifiers(value: object, field: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{field} must be an identifier sequence")
    normalized = tuple(_identifier(item, field) for item in value)
    if len(normalized) != len(set(normalized)):
        raise ValueError(f"{field} cannot contain duplicate identifiers")
    return normalized


def _opaque(value: object, field: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be an opaque mapping")
    return freeze_model_payload(value)


@dataclass(frozen=True)
class CommittedFact:
    """One immutable fact accepted by a single bound runtime session."""

    version: str
    fact_id: str
    session_binding_fingerprint: str
    source_ref: str
    scope_ref: str
    visible_to_refs: tuple[str, ...]
    temporal_position_fingerprint: str
    accepted_runtime_ordinal: int
    content_ref: Mapping[str, Any]
    model_payload: Mapping[str, Any]
    settlement_ref: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        fact_id: str,
        session_binding_fingerprint: str,
        source_ref: str,
        scope_ref: str,
        visible_to_refs: Sequence[str],
        temporal_position_fingerprint: str,
        accepted_runtime_ordinal: int,
        content_ref: Mapping[str, Any],
        model_payload: Mapping[str, Any],
        settlement_ref: Mapping[str, Any],
    ) -> "CommittedFact":
        material = {
            "version": HISTORY_CONTRACT_VERSION,
            "fact_id": _identifier(fact_id, "fact_id"),
            "session_binding_fingerprint": _identifier(
                session_binding_fingerprint,
                "session_binding_fingerprint",
            ),
            "source_ref": _identifier(source_ref, "source_ref"),
            "scope_ref": _identifier(scope_ref, "scope_ref"),
            "visible_to_refs": list(_identifiers(
                visible_to_refs, "visible_to_refs")),
            "temporal_position_fingerprint": _identifier(
                temporal_position_fingerprint,
                "temporal_position_fingerprint",
            ),
            "accepted_runtime_ordinal": _ordinal(
                accepted_runtime_ordinal,
                "accepted_runtime_ordinal",
            ),
            "content_ref": thaw_model_data(_opaque(content_ref, "content_ref")),
            "model_payload": thaw_model_data(_opaque(
                model_payload, "model_payload")),
            "settlement_ref": thaw_model_data(_opaque(
                settlement_ref, "settlement_ref")),
        }
        fact = cls(
            version=material["version"],
            fact_id=material["fact_id"],
            session_binding_fingerprint=material[
                "session_binding_fingerprint"],
            source_ref=material["source_ref"],
            scope_ref=material["scope_ref"],
            visible_to_refs=tuple(material["visible_to_refs"]),
            temporal_position_fingerprint=material[
                "temporal_position_fingerprint"],
            accepted_runtime_ordinal=material["accepted_runtime_ordinal"],
            content_ref=_opaque(material["content_ref"], "content_ref"),
            model_payload=_opaque(material["model_payload"], "model_payload"),
            settlement_ref=_opaque(
                material["settlement_ref"], "settlement_ref"),
            fingerprint=model_data_fingerprint(material),
        )

        # Factory owns deeply frozen payloads. This memo never enters records.
        object.__setattr__(fact, '_factory_validation', tuple(
            (field.name, type(getattr(fact, field.name)), getattr(fact, field.name))
            for field in fields(fact)))
        return fact

    def verify_integrity(self) -> None:
        memo = getattr(self, '_factory_validation', None)
        if memo is not None and all(
            type(getattr(self, name)) is value_type and
            (getattr(self, name) is value or getattr(self, name) == value)
            for name, value_type, value in memo):
            return
        restored = CommittedFact.create(
            fact_id=self.fact_id,
            session_binding_fingerprint=self.session_binding_fingerprint,
            source_ref=self.source_ref,
            scope_ref=self.scope_ref,
            visible_to_refs=self.visible_to_refs,
            temporal_position_fingerprint=self.temporal_position_fingerprint,
            accepted_runtime_ordinal=self.accepted_runtime_ordinal,
            content_ref=self.content_ref,
            model_payload=self.model_payload,
            settlement_ref=self.settlement_ref,
        )
        if restored.fingerprint != self.fingerprint:
            raise ValueError("committed fact fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return {
            "version": self.version,
            "fact_id": self.fact_id,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "source_ref": self.source_ref,
            "scope_ref": self.scope_ref,
            "visible_to_refs": list(self.visible_to_refs),
            "temporal_position_fingerprint": (
                self.temporal_position_fingerprint),
            "accepted_runtime_ordinal": self.accepted_runtime_ordinal,
            "content_ref": thaw_model_data(self.content_ref),
            "model_payload": thaw_model_data(self.model_payload),
            "settlement_ref": thaw_model_data(self.settlement_ref),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "CommittedFact":
        if cls is not CommittedFact or not isinstance(record, Mapping):
            return cls._from_record_uncached(record)
        key = _record_cache_key(record)
        with _RESTORED_HISTORY_LOCK:
            cached = _RESTORED_FACTS.get(key)
            if cached is not None and record == cached[0]:
                _RESTORED_FACTS.move_to_end(key)
                return cached[1]
        fact = cls._from_record_uncached(record)
        with _RESTORED_HISTORY_LOCK:
            _RESTORED_FACTS[key] = (_record_snapshot(record), fact)
            while len(_RESTORED_FACTS) > 2048:
                _RESTORED_FACTS.popitem(last=False)
        return fact

    @classmethod
    def _from_record_uncached(cls, record: Mapping[str, Any]) -> "CommittedFact":
        required = {
            "version", "fact_id", "session_binding_fingerprint",
            "source_ref", "scope_ref", "visible_to_refs",
            "temporal_position_fingerprint", "accepted_runtime_ordinal",
            "content_ref", "model_payload", "settlement_ref", "fingerprint",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("committed fact record schema is invalid")
        if record.get("version") != HISTORY_CONTRACT_VERSION:
            raise ValueError("committed fact version is unsupported")
        restored = cls.create(**{
            key: record[key] for key in required
            if key not in {"version", "fingerprint"}
        })
        if restored.fingerprint != record.get("fingerprint"):
            raise ValueError("committed fact fingerprint is invalid")
        return restored


@dataclass(frozen=True)
class HistoryRead:
    """One exact, authorized, read-only view over prior committed facts."""

    version: str
    session_binding_fingerprint: str
    reader_ref: str
    trigger_fact_id: str
    before_runtime_ordinal: int
    facts: tuple[CommittedFact, ...]
    authorization_ref: Mapping[str, Any]
    fingerprint: str

    @property
    def fact_ids(self) -> tuple[str, ...]:
        return tuple(fact.fact_id for fact in self.facts)

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "reader_ref": self.reader_ref,
            "trigger_fact_id": self.trigger_fact_id,
            "before_runtime_ordinal": self.before_runtime_ordinal,
            "fact_ids": list(self.fact_ids),
            "fact_fingerprints": [fact.fingerprint for fact in self.facts],
            "authorization_ref": thaw_model_data(self.authorization_ref),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_record(
        cls,
        record: Mapping[str, Any],
        *,
        facts_by_id: Mapping[str, CommittedFact],
    ) -> "HistoryRead":
        required = {
            "version", "session_binding_fingerprint", "reader_ref",
            "trigger_fact_id", "before_runtime_ordinal", "fact_ids",
            "fact_fingerprints", "authorization_ref", "fingerprint",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("history read record schema is invalid")
        if record.get("version") != HISTORY_CONTRACT_VERSION:
            raise ValueError("history read version is unsupported")
        fact_ids = _identifiers(record.get("fact_ids"), "fact_ids")
        fingerprints = _identifiers(
            record.get("fact_fingerprints"), "fact_fingerprints")
        if len(fact_ids) != len(fingerprints):
            raise ValueError("history read fact fingerprints are incomplete")
        facts = []
        for fact_id, fingerprint in zip(fact_ids, fingerprints):
            fact = facts_by_id.get(fact_id)
            if fact is None or fact.fingerprint != fingerprint:
                raise ValueError("history read fact reference is invalid")
            facts.append(fact)
        authorization = _opaque(
            record.get("authorization_ref"), "authorization_ref")
        material = {
            "version": HISTORY_CONTRACT_VERSION,
            "session_binding_fingerprint": _identifier(
                record.get("session_binding_fingerprint"),
                "session_binding_fingerprint",
            ),
            "reader_ref": _identifier(record.get("reader_ref"), "reader_ref"),
            "trigger_fact_id": _identifier(
                record.get("trigger_fact_id"), "trigger_fact_id"),
            "before_runtime_ordinal": _ordinal(
                record.get("before_runtime_ordinal"),
                "before_runtime_ordinal",
            ),
            "fact_ids": list(fact_ids),
            "fact_fingerprints": list(fingerprints),
            "authorization_ref": thaw_model_data(authorization),
        }
        fingerprint = _identifier(record.get("fingerprint"), "fingerprint")
        if fingerprint != model_data_fingerprint(material):
            raise ValueError("history read fingerprint is invalid")
        return cls(
            version=HISTORY_CONTRACT_VERSION,
            session_binding_fingerprint=material[
                "session_binding_fingerprint"],
            reader_ref=material["reader_ref"],
            trigger_fact_id=material["trigger_fact_id"],
            before_runtime_ordinal=material["before_runtime_ordinal"],
            facts=tuple(facts),
            authorization_ref=authorization,
            fingerprint=fingerprint,
        )


@dataclass(frozen=True)
class ActualRecall:
    """A model decision that recalled an exact subset of one HistoryRead."""

    version: str
    recall_id: str
    trigger_fact_id: str
    history_read_fingerprint: str
    recalled_fact_ids: tuple[str, ...]
    model_stage_receipt_fingerprint: str
    model_output_fingerprint: str
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        recall_id: str,
        trigger_fact_id: str,
        history_read_fingerprint: str,
        recalled_fact_ids: Sequence[str],
        model_stage_receipt_fingerprint: str,
        model_output_fingerprint: str,
    ) -> "ActualRecall":
        material = {
            "version": HISTORY_CONTRACT_VERSION,
            "recall_id": _identifier(recall_id, "recall_id"),
            "trigger_fact_id": _identifier(
                trigger_fact_id, "trigger_fact_id"),
            "history_read_fingerprint": _identifier(
                history_read_fingerprint, "history_read_fingerprint"),
            "recalled_fact_ids": list(_identifiers(
                recalled_fact_ids, "recalled_fact_ids")),
            "model_stage_receipt_fingerprint": _identifier(
                model_stage_receipt_fingerprint,
                "model_stage_receipt_fingerprint",
            ),
            "model_output_fingerprint": _identifier(
                model_output_fingerprint, "model_output_fingerprint"),
        }
        return cls(
            version=material["version"],
            recall_id=material["recall_id"],
            trigger_fact_id=material["trigger_fact_id"],
            history_read_fingerprint=material["history_read_fingerprint"],
            recalled_fact_ids=tuple(material["recalled_fact_ids"]),
            model_stage_receipt_fingerprint=material[
                "model_stage_receipt_fingerprint"],
            model_output_fingerprint=material["model_output_fingerprint"],
            fingerprint=model_data_fingerprint(material),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "recall_id": self.recall_id,
            "trigger_fact_id": self.trigger_fact_id,
            "history_read_fingerprint": self.history_read_fingerprint,
            "recalled_fact_ids": list(self.recalled_fact_ids),
            "model_stage_receipt_fingerprint": (
                self.model_stage_receipt_fingerprint),
            "model_output_fingerprint": self.model_output_fingerprint,
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "ActualRecall":
        required = {
            "version", "recall_id", "trigger_fact_id",
            "history_read_fingerprint", "recalled_fact_ids",
            "model_stage_receipt_fingerprint", "model_output_fingerprint",
            "fingerprint",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("actual recall record schema is invalid")
        if record.get("version") != HISTORY_CONTRACT_VERSION:
            raise ValueError("actual recall version is unsupported")
        restored = cls.create(**{
            key: record[key] for key in required
            if key not in {"version", "fingerprint"}
        })
        if restored.fingerprint != record.get("fingerprint"):
            raise ValueError("actual recall fingerprint is invalid")
        return restored


@dataclass(frozen=True)
class InfluenceEdge:
    """An opaque contribution from one recalled prior fact to a later fact."""

    version: str
    edge_id: str
    recall_id: str
    parent_fact_id: str
    child_fact_id: str
    contribution_payload: Mapping[str, Any]
    evidence: Mapping[str, Any]
    fingerprint: str

    @classmethod
    def create(
        cls,
        *,
        edge_id: str,
        recall_id: str,
        parent_fact_id: str,
        child_fact_id: str,
        contribution_payload: Mapping[str, Any],
        evidence: Mapping[str, Any],
    ) -> "InfluenceEdge":
        material = {
            "version": HISTORY_CONTRACT_VERSION,
            "edge_id": _identifier(edge_id, "edge_id"),
            "recall_id": _identifier(recall_id, "recall_id"),
            "parent_fact_id": _identifier(
                parent_fact_id, "parent_fact_id"),
            "child_fact_id": _identifier(child_fact_id, "child_fact_id"),
            "contribution_payload": thaw_model_data(_opaque(
                contribution_payload, "contribution_payload")),
            "evidence": thaw_model_data(_opaque(evidence, "evidence")),
        }
        return cls(
            version=material["version"],
            edge_id=material["edge_id"],
            recall_id=material["recall_id"],
            parent_fact_id=material["parent_fact_id"],
            child_fact_id=material["child_fact_id"],
            contribution_payload=_opaque(
                material["contribution_payload"], "contribution_payload"),
            evidence=_opaque(material["evidence"], "evidence"),
            fingerprint=model_data_fingerprint(material),
        )

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "edge_id": self.edge_id,
            "recall_id": self.recall_id,
            "parent_fact_id": self.parent_fact_id,
            "child_fact_id": self.child_fact_id,
            "contribution_payload": thaw_model_data(
                self.contribution_payload),
            "evidence": thaw_model_data(self.evidence),
            "fingerprint": self.fingerprint,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "InfluenceEdge":
        required = {
            "version", "edge_id", "recall_id", "parent_fact_id",
            "child_fact_id", "contribution_payload", "evidence",
            "fingerprint",
        }
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("influence edge record schema is invalid")
        if record.get("version") != HISTORY_CONTRACT_VERSION:
            raise ValueError("influence edge version is unsupported")
        restored = cls.create(**{
            key: record[key] for key in required
            if key not in {"version", "fingerprint"}
        })
        if restored.fingerprint != record.get("fingerprint"):
            raise ValueError("influence edge fingerprint is invalid")
        return restored


# Operational restoration reuse; no history selection or commit authority.
_RESTORED_HISTORY = OrderedDict()
_RESTORED_FACTS = OrderedDict()
_PUBLISHED_HISTORY = OrderedDict()
_RESTORED_HISTORY_LOCK = RLock()
_RESTORED_HISTORY_LIMIT = 2

def _record_snapshot(value):
    if isinstance(value, Mapping):
        return {key: _record_snapshot(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_record_snapshot(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_record_snapshot(item) for item in value)
    return value

def _independent_history(history):
    result = copy.copy(history)
    for name in ('_facts', '_reads', '_recalls', '_edges'):
        setattr(result, name, getattr(history, name).copy())
    result._fact_roots = list(history._fact_roots)
    result._fact_positions = dict(history._fact_positions)
    result._fact_ordinals = list(history._fact_ordinals)
    return result


class HistoryAuthority:
    """Reference append/query/verify authority for one runtime binding.

    Production persistence may wrap the same records.  This reference object
    deliberately performs no retrieval ranking and exposes no commit shortcut.
    """

    def __init__(self, *, session_binding_fingerprint: str) -> None:
        self.session_binding_fingerprint = _identifier(
            session_binding_fingerprint, "session_binding_fingerprint")
        self._facts: dict[str, CommittedFact] = {}
        self._fact_positions = {}
        self._fact_ordinals = []
        self._recalls: dict[str, ActualRecall] = {}
        self._reads: dict[str, HistoryRead] = {}
        self._edges: dict[str, InfluenceEdge] = {}
        from .snapshot_storage import _step, VERSION
        self._fact_roots = [_step(VERSION, "facts")]

    def append_fact(self, fact: CommittedFact) -> CommittedFact:
        if not isinstance(fact, CommittedFact):
            raise ValueError("history accepts only CommittedFact")
        fact.verify_integrity()
        if fact.session_binding_fingerprint != self.session_binding_fingerprint:
            raise ValueError("committed fact belongs to another runtime binding")
        existing = self._facts.get(fact.fact_id)
        if existing is not None:
            if existing.fingerprint != fact.fingerprint:
                raise ValueError("conflicting fact identity")
            return existing
        if self._facts and fact.accepted_runtime_ordinal <= self._facts[next(reversed(self._facts))].accepted_runtime_ordinal:
            raise ValueError("committed facts must append in runtime order")
        self._fact_positions[fact.fact_id] = len(self._facts)
        self._fact_ordinals.append(fact.accepted_runtime_ordinal)
        self._facts[fact.fact_id] = fact
        from .snapshot_storage import _step
        self._fact_roots.append(_step(self._fact_roots[-1], fact.fingerprint))
        return fact

    def fact(self, fact_id: str) -> CommittedFact:
        normalized = _identifier(fact_id, "fact_id")
        if normalized not in self._facts:
            raise ValueError("unknown committed fact")
        return self._facts[normalized]

    def facts(self) -> tuple[CommittedFact, ...]:
        return tuple(self._facts.values())

    def authorize_read(
        self,
        *,
        reader_ref: str,
        trigger_fact_id: str,
        before_runtime_ordinal: int,
        requested_fact_ids: Sequence[str],
        authorization_ref: Mapping[str, Any],
    ) -> HistoryRead:
        reader = _identifier(reader_ref, "reader_ref")
        trigger_id = _identifier(trigger_fact_id, "trigger_fact_id")
        before = _ordinal(before_runtime_ordinal, "before_runtime_ordinal")
        trigger = self._facts.get(trigger_id)
        if trigger is not None and trigger.accepted_runtime_ordinal != before:
            raise ValueError("history read boundary differs from trigger fact")
        requested = _identifiers(requested_fact_ids, "requested_fact_ids")
        facts = []
        for fact_id in requested:
            fact = self.fact(fact_id)
            if fact.accepted_runtime_ordinal >= before:
                raise ValueError("history reads must be strictly prior")
            if reader not in fact.visible_to_refs:
                raise ValueError("requested fact is not visible to reader")
            facts.append(fact)
        frozen_authorization = _opaque(
            authorization_ref, "authorization_ref")
        material = {
            "version": HISTORY_CONTRACT_VERSION,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "reader_ref": reader,
            "trigger_fact_id": trigger_id,
            "before_runtime_ordinal": before,
            "fact_ids": list(requested),
            "fact_fingerprints": [fact.fingerprint for fact in facts],
            "authorization_ref": thaw_model_data(frozen_authorization),
        }
        read = HistoryRead(
            version=material["version"],
            session_binding_fingerprint=self.session_binding_fingerprint,
            reader_ref=reader,
            trigger_fact_id=trigger_id,
            before_runtime_ordinal=before,
            facts=tuple(facts),
            authorization_ref=frozen_authorization,
            fingerprint=model_data_fingerprint(material),
        )
        self._reads[read.fingerprint] = read
        return read

    def append_actual_recall(
        self, recall: ActualRecall, *, history_read: HistoryRead,
    ) -> ActualRecall:
        if not isinstance(recall, ActualRecall):
            raise ValueError("history accepts only ActualRecall")
        if not isinstance(history_read, HistoryRead):
            raise ValueError("actual recall requires HistoryRead")
        accepted_read = self._reads.get(history_read.fingerprint)
        if accepted_read is not history_read:
            raise ValueError("actual recall requires an authorized history read")
        if (
            recall.history_read_fingerprint != history_read.fingerprint
            or recall.trigger_fact_id != history_read.trigger_fact_id
        ):
            raise ValueError("actual recall differs from history read")
        if not set(recall.recalled_fact_ids).issubset(set(history_read.fact_ids)):
            raise ValueError("actual recall contains a fact outside history read")
        existing = self._recalls.get(recall.recall_id)
        if existing is not None:
            if existing.fingerprint != recall.fingerprint:
                raise ValueError("conflicting actual recall identity")
            return existing
        self._recalls[recall.recall_id] = recall
        return recall

    def actual_recall(self, recall_id: str) -> ActualRecall:
        normalized = _identifier(recall_id, "recall_id")
        if normalized not in self._recalls:
            raise ValueError("unknown actual recall")
        return self._recalls[normalized]

    def actual_recalls(self) -> tuple[ActualRecall, ...]:
        return tuple(self._recalls.values())

    def append_influence_edge(self, edge: InfluenceEdge) -> InfluenceEdge:
        if not isinstance(edge, InfluenceEdge):
            raise ValueError("history accepts only InfluenceEdge")
        recall = self.actual_recall(edge.recall_id)
        parent = self._facts.get(edge.parent_fact_id)
        child = self._facts.get(edge.child_fact_id)
        if parent is not None and child is not None and (
            parent.accepted_runtime_ordinal >= child.accepted_runtime_ordinal
        ):
            raise ValueError("influence edge must be strictly prior")
        if edge.parent_fact_id not in recall.recalled_fact_ids:
            raise ValueError("influence parent is not a recalled fact")
        if edge.child_fact_id != recall.trigger_fact_id:
            raise ValueError("influence child differs from recall trigger")
        if parent is None or child is None:
            raise ValueError("influence edge references an unknown fact")
        existing = self._edges.get(edge.edge_id)
        if existing is not None:
            if existing.fingerprint != edge.fingerprint:
                raise ValueError("conflicting influence edge identity")
            return existing
        if any(
            item.recall_id == edge.recall_id
            and item.parent_fact_id == edge.parent_fact_id
            and item.child_fact_id == edge.child_fact_id
            for item in self._edges.values()
        ):
            raise ValueError("duplicate recall influence edge")
        self._edges[edge.edge_id] = edge
        return edge

    def influence_edges_for(self, child_fact_id: str) -> tuple[InfluenceEdge, ...]:
        child = _identifier(child_fact_id, "child_fact_id")
        return tuple(
            edge for edge in self._edges.values()
            if edge.child_fact_id == child
        )

    def influence_edges(self) -> tuple[InfluenceEdge, ...]:
        return tuple(self._edges.values())

    def to_record(self, *, compact=None) -> dict[str, Any]:
        """Serialize history; compact mode references exact immutable facts."""
        if compact is None:
            compact = getattr(self, "_compact_storage", False)
        if compact:
            from .snapshot_storage import pack
            record = pack(self)
            snapshot = _independent_history(self)
            snapshot._compact_storage = True
            with _RESTORED_HISTORY_LOCK:
                _PUBLISHED_HISTORY[id(record)] = (record, snapshot)
                while len(_PUBLISHED_HISTORY) > 4:
                    _PUBLISHED_HISTORY.popitem(last=False)
            return record
        material = {
            "version": HISTORY_CONTRACT_VERSION,
            "session_binding_fingerprint": self.session_binding_fingerprint,
            "facts": [fact.to_record() for fact in self._facts.values()],
            "reads": [read.to_record() for read in self._reads.values()],
            "actual_recalls": [
                recall.to_record() for recall in self._recalls.values()
            ],
            "influence_edges": [
                edge.to_record() for edge in self._edges.values()
            ],
        }
        return dict(material, fingerprint=model_data_fingerprint(material))

    @classmethod
    def from_record(cls, record: Mapping[str, Any]) -> "HistoryAuthority":
        if cls is not HistoryAuthority or not isinstance(record, Mapping):
            return cls._from_record_uncached(record)
        # Only our own deeply immutable published records may use identity.
        # Mutable files and foreign proxies still undergo full verification.
        with _RESTORED_HISTORY_LOCK:
            published = _PUBLISHED_HISTORY.get(id(record))
            if published is not None and published[0] is record:
                return _independent_history(published[1])
        # Hash the complete current record, not its supplied fingerprint alone.
        key = _record_cache_key(record)
        with _RESTORED_HISTORY_LOCK:
            cached = _RESTORED_HISTORY.get(key)
            if cached is not None and record == cached[0]:
                _RESTORED_HISTORY.move_to_end(key)
                return _independent_history(cached[1])
        history = cls._from_record_uncached(record)
        with _RESTORED_HISTORY_LOCK:
            _RESTORED_HISTORY[key] = (_record_snapshot(record), history)
            while len(_RESTORED_HISTORY) > _RESTORED_HISTORY_LIMIT:
                _RESTORED_HISTORY.popitem(last=False)
        return _independent_history(history)

    @classmethod
    def _from_record_uncached(cls, record: Mapping[str, Any]) -> "HistoryAuthority":
        required = {
            "version", "session_binding_fingerprint", "facts", "reads",
            "actual_recalls", "influence_edges", "fingerprint",
        }
        from .snapshot_storage import VERSION as snapshot_version, unpack
        if isinstance(record, Mapping) and record.get("version") == snapshot_version:
            history = cls._from_record_uncached(unpack(record))
            history._compact_storage = True
            return history
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("history authority record schema is invalid")
        if record.get("version") != HISTORY_CONTRACT_VERSION:
            raise ValueError("history authority version is unsupported")
        material = {key: record[key] for key in required if key != "fingerprint"}
        if record.get("fingerprint") != model_data_fingerprint(material):
            raise ValueError("history authority fingerprint is invalid")
        history = cls(session_binding_fingerprint=record[
            "session_binding_fingerprint"])
        raw_facts = record.get("facts")
        raw_reads = record.get("reads")
        raw_recalls = record.get("actual_recalls")
        raw_edges = record.get("influence_edges")
        for value, field in (
            (raw_facts, "facts"), (raw_reads, "reads"),
            (raw_recalls, "actual_recalls"),
            (raw_edges, "influence_edges"),
        ):
            if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
                raise ValueError(f"history authority {field} must be a sequence")
        for row in raw_facts:
            history.append_fact(CommittedFact.from_record(row))
        for row in raw_reads:
            restored_read = HistoryRead.from_record(
                row, facts_by_id=history._facts)
            trigger = history._facts.get(restored_read.trigger_fact_id)
            if trigger is not None and trigger.accepted_runtime_ordinal != (
                    restored_read.before_runtime_ordinal):
                raise ValueError("restored history read boundary is invalid")
            for fact in restored_read.facts:
                if fact.accepted_runtime_ordinal >= (
                    restored_read.before_runtime_ordinal
                ):
                    raise ValueError("restored history read is not strictly prior")
                if restored_read.reader_ref not in fact.visible_to_refs:
                    raise ValueError("restored history read contains hidden fact")
            history._reads[restored_read.fingerprint] = restored_read
        for row in raw_recalls:
            recall = ActualRecall.from_record(row)
            read = history._reads.get(recall.history_read_fingerprint)
            if read is None:
                raise ValueError("restored actual recall has no history read")
            history.append_actual_recall(recall, history_read=read)
        for row in raw_edges:
            history.append_influence_edge(InfluenceEdge.from_record(row))
        return history


__all__ = (
    "FRAMEWORK_HISTORY_PROFILE_KEY",
    "HISTORY_CONTRACT_VERSION",
    "ActualRecall",
    "CommittedFact",
    "HistoryAuthority",
    "HistoryRead",
    "InfluenceEdge",
)
