"""Single-owner serial composition of the existing committed Full fact host.

This adapter has no provider, UI, autonomous-send, or disk-write authority.
A product source verifier is required before any formal event is processed.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import threading

from framework.history import HistoryAuthority, HistoryRead
from framework.model_api import model_data_fingerprint, thaw_model_data
from framework.model_api.immutable_record import ImmutableModelRecord
from model_packages.desktop_pet_v1.events import MemoryInstance, PetEvent
from ops.runtime_full_memory_committed_fact_transaction import execute_runtime_full_memory_committed_fact_transaction
from ops.runtime_full_memory_profile_commit import FRAMEWORK_HISTORY_PROFILE_KEY

EVENT_JOURNAL_KEY = "desktop_pet_events_v1"


@dataclass(frozen=True)
class IngestResult:
    memory: MemoryInstance
    transaction: object | None
    replayed: bool


class SinglePetRuntime:
    def __init__(self, *, session, model_runtime, temporal_runtime, owner_ref: str,
                 source_verifier: Callable[[PetEvent], None], memory_query_budget_ms=None):
        if not isinstance(owner_ref, str) or not owner_ref.strip():
            raise ValueError("runtime owner is invalid")
        if not callable(source_verifier):
            raise ValueError("runtime requires a product source verifier")
        session.session_binding.verify_runtimes(model_runtime=model_runtime, temporal_runtime=temporal_runtime)
        self.session = session
        self.model_runtime = model_runtime
        self.temporal_runtime = temporal_runtime
        self.owner_ref = owner_ref
        self.source_verifier = source_verifier
        self.memory_query_budget_ms = memory_query_budget_ms
        self._lock = threading.RLock()
        self._verify_journal()
        from model_packages.desktop_pet_v1.grounded_recall import register_profile
        register_profile(self.session.profile,history=self._history())

    def fork(self):
        """Shadow of this exact already validated session, never a restore API."""
        with self._lock:
            shadow=object.__new__(SinglePetRuntime)
            shadow.__dict__=dict(self.__dict__)
            shadow._lock=threading.RLock()
            return shadow

    def _history(self) -> HistoryAuthority:
        # Framework restoration checks actual content and returns independent
        # dictionaries. Do not hash the same full record again in this adapter.
        with self._lock:
            raw = self.session.profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
            binding = self.session.session_binding.fingerprint
            if raw is None:
                return HistoryAuthority(session_binding_fingerprint=binding)
            history = HistoryAuthority.from_record(raw)
            if history.session_binding_fingerprint != binding:
                raise ValueError('history belongs to a different runtime')
            return history

    def _verify_journal(self):
        raw = self.session.profile.get(EVENT_JOURNAL_KEY, {})
        if not isinstance(raw, Mapping):
            raise ValueError("event journal is invalid")
        history = self._history()
        if set(raw) != {fact.fact_id for fact in history.facts()}:
            raise ValueError("event journal and formal history identities are incomplete")
        for event_id, entry in raw.items():
            if not isinstance(entry, Mapping) or set(entry) != {"event", "measurement_fingerprint", "fact_fingerprint"}:
                raise ValueError("event journal entry schema is invalid")
            event = PetEvent.from_record(entry["event"])
            if event.owner_ref != self.owner_ref or event.event_id != event_id:
                raise ValueError("event journal owner or identity is invalid")
            expected = self.temporal_runtime.normalize_position(event.position.coordinate)
            if expected != event.position:
                raise ValueError("event journal temporal binding is invalid")
            fact = history.fact(event_id)
            if fact.fingerprint != entry["fact_fingerprint"]:
                raise ValueError("event journal fact fingerprint is invalid")
            MemoryInstance(event, fact)

    @property
    def events(self) -> tuple[PetEvent, ...]:
        with self._lock:
            return tuple(PetEvent.from_record(entry["event"])
                         for entry in self.session.profile.get(EVENT_JOURNAL_KEY, {}).values())

    def memory_instance(self, event_id: str) -> MemoryInstance:
        with self._lock:
            entry = self.session.profile.get(EVENT_JOURNAL_KEY, {}).get(event_id)
            if entry is None:
                raise ValueError("memory content is missing")
            fact=self._history().fact(event_id)
            cached=getattr(self,'_owned_memory_reads',{}).get(event_id)
            if (type(entry) is ImmutableModelRecord and cached is not None
                    and cached[0] is entry and cached[1].fact is fact):
                fact.verify_integrity()
                if cached[1].event.fingerprint==cached[2]:return cached[1]
            memory=MemoryInstance(PetEvent.from_record(entry["event"]),fact)
            if type(entry) is ImmutableModelRecord:
                cache=getattr(self,'_owned_memory_reads',{})
                if len(cache)>=512:cache.pop(next(iter(cache)))
                cache[event_id]=(entry,memory,memory.event.fingerprint)
                self._owned_memory_reads=cache
            return memory

    def ingest(self, event: PetEvent, *, measurement_payload: Mapping, response_policy: str | None = None) -> IngestResult:
        with self._lock:
            if not isinstance(event, PetEvent) or event.owner_ref != self.owner_ref:
                raise ValueError("event belongs to a different owner")
            if not isinstance(measurement_payload, Mapping):
                raise ValueError("measurement payload is invalid")
            self.source_verifier(event)
            if response_policy is not None and (event.event_kind != "peer_input" or response_policy not in {"required","eligible"}):
                raise ValueError("response policy is invalid for this event")
            expected = self.temporal_runtime.normalize_position(event.position.coordinate)
            if expected != event.position:
                raise ValueError("event belongs to another temporal binding")
            measurement_fingerprint = model_data_fingerprint(measurement_payload)
            # Only append this event; committed historical rows stay read-only.
            journal = dict(self.session.profile.get(EVENT_JOURNAL_KEY, {}))
            old = journal.get(event.event_id)
            if old is not None:
                if (old["event"]["fingerprint"] != event.fingerprint
                        or old["measurement_fingerprint"] != measurement_fingerprint):
                    raise ValueError("replayed event conflicts with its committed source")
                return IngestResult(self.memory_instance(event.event_id), None, True)
            from .calibration import verify_runtime as verify_calibration
            calibration_context=verify_calibration(self,event,measurement_payload)
            from .input_reference import verify_runtime as verify_input_reference
            verify_input_reference(self,event,measurement_payload)
            from model_packages.desktop_pet_v1.recall_model import select
            grounded_recall=select(measurement_payload.get('pet_memory_recall',{}).get('version'))
            recall_context=(grounded_recall.metadata(self,event,measurement_payload[grounded_recall.KEY])
                if grounded_recall.KEY in measurement_payload else {})
            relation_context={}
            from model_packages.desktop_pet_v1.relation_package import META
            if META in self.session.state.state:
                from .public_disclosure import counterparty,context as disclosure_context
                peer=counterparty(self,event)
                relation_context={'desktop_pet_counterparty_ref':peer,
                    'desktop_pet_relation_source':event.source_binding_ref,
                    **disclosure_context(self,event,measurement_payload,peer)}
            transaction = execute_runtime_full_memory_committed_fact_transaction(
                session=self.session, model_runtime=self.model_runtime, temporal_runtime=self.temporal_runtime,
                target_coordinate=event.position.coordinate, scope_ref=event.scope_ref,
                subject_ref=event.owner_ref, event_ref=event.event_id, action_ref=None,
                measurement_payload={"event": event.to_record(), "measurement_payload": measurement_payload},
                context_metadata={
                    **relation_context,
                    **calibration_context,
                    **recall_context,
                    "committed_event_kind": event.event_kind,
                    "local_turn_id": event.event_id,
                    **({"response_policy":response_policy,
                        "expected_runtime_ordinal":self.session.position.runtime_ordinal}
                       if response_policy is not None else {}),
                    "source_receipt_fingerprint": event.source_binding_ref,
                    "desktop_pet_event_fingerprint": event.fingerprint,
                    "desktop_pet_memory_evidence": "desktop-pet-memory-batch-values-v3",
                    "memory_source_encoding": "memory-shared-value-table-v1",
                    **({"indexed_memory_query":{"version":"desktop-pet-indexed-memory-query-v1",
                                                   "budget_ms":self.memory_query_budget_ms},
                        "history_read_storage":"snapshot-ranges-v2"}
                       if self.memory_query_budget_ms is not None else {}),
                },
            )
            history = HistoryAuthority.from_record(transaction.session.profile[FRAMEWORK_HISTORY_PROFILE_KEY])
            memory = MemoryInstance(event, history.fact(event.event_id))
            journal[event.event_id] = {"event": event.to_record(),
                "measurement_fingerprint": measurement_fingerprint, "fact_fingerprint": memory.fact.fingerprint}
            profile = dict(transaction.session.profile)
            profile[EVENT_JOURNAL_KEY] = journal
            # Publish the complete in-memory profile only after every stage succeeds.
            self.session = replace(transaction.session, profile=profile)
            grounded_recall.register_event(event,memory.fact)
            return IngestResult(memory, transaction, False)

    def read_memory(self, read: HistoryRead, *, event_ids: tuple[str, ...]) -> tuple[MemoryInstance, ...]:
        with self._lock:
            if not isinstance(read, HistoryRead) or read.reader_ref != self.owner_ref:
                raise ValueError("history read belongs to another owner")
            verified = self._history().authorize_read(
                reader_ref=read.reader_ref, trigger_fact_id=read.trigger_fact_id,
                before_runtime_ordinal=read.before_runtime_ordinal,
                requested_fact_ids=read.fact_ids, authorization_ref=read.authorization_ref,
            )
            if verified.fingerprint != read.fingerprint:
                raise ValueError("history read differs from formal authorization")
            if len(event_ids) != len(set(event_ids)) or not set(event_ids).issubset(read.fact_ids):
                raise ValueError("memory content requires an exact authorized subset")
            return tuple(self.memory_instance(event_id) for event_id in event_ids)


__all__ = ("SinglePetRuntime", "IngestResult", "EVENT_JOURNAL_KEY")

