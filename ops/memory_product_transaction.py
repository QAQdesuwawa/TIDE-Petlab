"""Alpha 0.4 D1 owner-local atomic product memory transaction envelope.

The envelope validates already-settled C1--C5 products and publishes one
text-free record.  It does not recompute recall, P4, attention, hydration, or
writer inputs.  The optional influence transaction is embedded in the same
record so a failed append cannot expose a partial set of memory edges.
"""
from __future__ import annotations

from dataclasses import dataclass
import copy
import math
from types import MappingProxyType
from typing import Mapping, Optional

from ops.conversation_events import (
    CommittedConversationEvent,
    ConversationEventSchemaError,
    canonical_npc_output_event_id,
)
from ops.memory_actual_join import ActualRecallCanonicalBatch
from ops.memory_actual_state import ActualRecallStateImpactBatch
from ops.memory_card_attention import MemoryCardAttentionOccupation
from ops.memory_card_hydration import MemoryCardHydrationBatch
from ops.memory_contracts import MemoryInfluenceTransaction
from ops.memory_influence_commit import MemoryInfluenceCommitPlan
from ops.memory_join import CanonicalRecallBatch
from ops.memory_root_transition import ActualRecallRootTransition
from ops.memory_state_impact import RecallStateImpactBatch


MEMORY_PRODUCT_TRANSACTION_VERSION = "memory-product-transaction-v2"
LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION = "memory-product-transaction-v1"
_SUPPORTED_CANONICAL_BATCH_TYPES = (
    CanonicalRecallBatch,
    ActualRecallCanonicalBatch,
)
_SUPPORTED_IMPACT_BATCH_TYPES = (
    RecallStateImpactBatch,
    ActualRecallStateImpactBatch,
)


def _identifier(value, field):
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    return result


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if result < 0 or result != value:
        raise ValueError(f"{field} must be a non-negative integer")
    return result


def _freeze(value, field="value"):
    if isinstance(value, Mapping):
        return MappingProxyType({
            str(key): _freeze(item, f"{field}.{key}")
            for key, item in value.items()
        })
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item, field) for item in value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError(f"{field} must contain only finite serializable values")


def _thaw(value):
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


def _ids(values, field):
    result = tuple(_identifier(value, field) for value in values)
    if len(set(result)) != len(result):
        raise ValueError(f"{field} values must be unique")
    return result


@dataclass(frozen=True)
class MemoryProductTransaction:
    transaction_id: str
    owner_participant_id: str
    trigger_event_id: str
    committed_at_ms: int
    family_binding_version: str
    formula_version: str
    canonical_event_ids: tuple[str, ...]
    state_impact_event_ids: tuple[str, ...]
    influence_transaction: Optional[MemoryInfluenceTransaction]
    selected_for_hydration_ids: tuple[str, ...]
    hydrated_card_ids: tuple[str, ...]
    source_versions: Mapping[str, object]
    receipt: Mapping[str, object]
    root_transition: Optional[ActualRecallRootTransition] = None

    def __post_init__(self):
        for field in (
            "transaction_id", "owner_participant_id", "trigger_event_id",
            "family_binding_version", "formula_version",
        ):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "committed_at_ms", _timestamp(
            self.committed_at_ms, "committed_at_ms"))
        canonical = _ids(self.canonical_event_ids, "canonical_event_id")
        state_ids = _ids(self.state_impact_event_ids, "state_impact_event_id")
        selected = _ids(
            self.selected_for_hydration_ids, "selected_for_hydration_id")
        hydrated = _ids(self.hydrated_card_ids, "hydrated_card_id")
        if not set(state_ids).issubset(set(canonical)):
            raise ValueError("state impact IDs must be canonical event IDs")
        if not set(selected).issubset(set(canonical)):
            raise ValueError("hydration selection IDs must be canonical event IDs")
        if hydrated != selected:
            raise ValueError("hydrated card IDs must exactly preserve attention selection")
        object.__setattr__(self, "canonical_event_ids", canonical)
        object.__setattr__(self, "state_impact_event_ids", state_ids)
        object.__setattr__(self, "selected_for_hydration_ids", selected)
        object.__setattr__(self, "hydrated_card_ids", hydrated)
        influence = self.influence_transaction
        if self.formula_version not in {
                LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION,
                MEMORY_PRODUCT_TRANSACTION_VERSION}:
            raise ValueError("memory product transaction version is unsupported")
        transition = self.root_transition
        if transition is not None and not isinstance(
                transition, ActualRecallRootTransition):
            raise ValueError("root_transition must be ActualRecallRootTransition or None")
        if self.formula_version == LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION:
            if transition is not None:
                raise ValueError("legacy memory product transaction cannot carry root transition")
        elif state_ids and transition is None:
            raise ValueError("v2 state impact IDs require one root transition")
        if transition is not None:
            if (transition.trigger_event_id != self.trigger_event_id
                    or transition.evaluation_time_ms != self.committed_at_ms):
                raise ValueError("root transition trigger and time must match transaction")
            if set(transition.source_event_ids) != set(state_ids):
                raise ValueError("root transition source IDs must match state impact IDs")
        if influence is not None and not isinstance(influence, MemoryInfluenceTransaction):
            raise ValueError("influence_transaction must be a MemoryInfluenceTransaction")
        if state_ids and influence is None:
            raise ValueError("state impact IDs require one embedded influence transaction")
        if not state_ids and influence is not None:
            raise ValueError("influence transaction cannot exist without state impact IDs")
        if influence is not None:
            parent_ids = tuple(edge.parent_node_id for edge in influence.edges)
            if set(parent_ids) != set(state_ids):
                raise ValueError("influence parent IDs must exactly match state impact IDs")
            if influence.child_anchor_id != self.trigger_event_id:
                raise ValueError("influence child must be the transaction trigger event")
            if influence.committed_at_ms != self.committed_at_ms:
                raise ValueError("influence time must match the transaction time")
            if (transition is not None
                    and influence.state_transition_receipt_ref
                    != transition.transition_id):
                raise ValueError("influence must reference the bundled root transition")
        for field in ("source_versions", "receipt"):
            value = getattr(self, field)
            if not isinstance(value, Mapping):
                raise ValueError(f"{field} must be a mapping")
            object.__setattr__(self, field, _freeze(dict(value), field))

    @property
    def influence_transaction_id(self):
        return (self.influence_transaction.transaction_id
                if self.influence_transaction is not None else None)

    def to_record(self):
        record = {
            "schema_version": (2 if self.formula_version
                               == MEMORY_PRODUCT_TRANSACTION_VERSION else 1),
            "record_kind": "memory_product_transaction",
            "transaction_id": self.transaction_id,
            "owner_participant_id": self.owner_participant_id,
            "trigger_event_id": self.trigger_event_id,
            "committed_at_ms": self.committed_at_ms,
            "family_binding_version": self.family_binding_version,
            "formula_version": self.formula_version,
            "canonical_event_ids": list(self.canonical_event_ids),
            "state_impact_event_ids": list(self.state_impact_event_ids),
            "influence_transaction": (
                self.influence_transaction.to_record()
                if self.influence_transaction is not None else None),
            "selected_for_hydration_ids": list(self.selected_for_hydration_ids),
            "hydrated_card_ids": list(self.hydrated_card_ids),
            "source_versions": _thaw(self.source_versions),
            "receipt": _thaw(self.receipt),
        }
        if self.formula_version == MEMORY_PRODUCT_TRANSACTION_VERSION:
            record["root_transition"] = (
                self.root_transition.to_record()
                if self.root_transition is not None else None)
        return record

    @classmethod
    def from_record(cls, record):
        required = {
            "schema_version", "record_kind", "transaction_id",
            "owner_participant_id", "trigger_event_id", "committed_at_ms",
            "family_binding_version", "formula_version", "canonical_event_ids",
            "state_impact_event_ids", "influence_transaction",
            "selected_for_hydration_ids", "hydrated_card_ids",
            "source_versions", "receipt",
        }
        schema_version = record.get("schema_version") if isinstance(record, Mapping) else None
        if schema_version == 2:
            required = set(required) | {"root_transition"}
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError("memory product transaction schema is unsupported")
        if schema_version not in {1, 2} or record.get("record_kind") != (
                "memory_product_transaction"):
            raise ValueError("memory product transaction schema is unsupported")
        if (schema_version == 1 and record.get("formula_version")
                != LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION):
            raise ValueError("legacy memory product transaction version is unsupported")
        if (schema_version == 2 and record.get("formula_version")
                != MEMORY_PRODUCT_TRANSACTION_VERSION):
            raise ValueError("memory product transaction v2 version is unsupported")
        influence_record = record["influence_transaction"]
        root_record = record.get("root_transition")
        return cls(
            transaction_id=record["transaction_id"],
            owner_participant_id=record["owner_participant_id"],
            trigger_event_id=record["trigger_event_id"],
            committed_at_ms=record["committed_at_ms"],
            family_binding_version=record["family_binding_version"],
            formula_version=record["formula_version"],
            canonical_event_ids=tuple(record["canonical_event_ids"]),
            state_impact_event_ids=tuple(record["state_impact_event_ids"]),
            influence_transaction=(
                MemoryInfluenceTransaction.from_record(influence_record)
                if influence_record is not None else None),
            selected_for_hydration_ids=tuple(record["selected_for_hydration_ids"]),
            hydrated_card_ids=tuple(record["hydrated_card_ids"]),
            source_versions=record["source_versions"],
            receipt=record["receipt"],
            root_transition=(
                ActualRecallRootTransition.from_record(root_record)
                if root_record is not None else None),
        )


def build_memory_product_transaction(
        *, transaction_id, owner_participant_id,
        trigger_event: CommittedConversationEvent,
        canonical_batch: CanonicalRecallBatch | ActualRecallCanonicalBatch,
        impact_batch: Optional[RecallStateImpactBatch | ActualRecallStateImpactBatch],
        root_transition: Optional[ActualRecallRootTransition] = None,
        influence_plan: Optional[MemoryInfluenceCommitPlan],
        attention_plan: Optional[MemoryCardAttentionOccupation],
        hydration_batch: Optional[MemoryCardHydrationBatch],
        family_binding_version, stage_production_receipt=None
        ) -> MemoryProductTransaction:
    """Validate a complete owner-local C1--C5 result without recomputation."""
    if not isinstance(trigger_event, CommittedConversationEvent):
        raise ValueError("trigger_event must be a CommittedConversationEvent")
    if not isinstance(canonical_batch, _SUPPORTED_CANONICAL_BATCH_TYPES):
        raise ValueError("canonical_batch type is unsupported")
    owner_id = _identifier(owner_participant_id, "owner_participant_id")
    if owner_id not in trigger_event.member_snapshot_ids:
        raise ValueError("trigger event is outside the owner-local conversation scope")
    if (trigger_event.sender_participant_id != owner_id
            and owner_id not in trigger_event.visible_recipient_ids):
        raise ValueError("trigger event was not visible in the owner-local scope")

    canonical_ids = tuple(item.event_id for item in canonical_batch.items)
    state_ids = tuple(
        item.event_id for item in canonical_batch.items if item.state_eligible)
    at_ms = trigger_event.committed_at_ms
    trigger_id = trigger_event.event_id

    if bool(state_ids) != (impact_batch is not None and influence_plan is not None):
        raise ValueError(
            "state-eligible canonical IDs require both impact and influence stages")
    if (impact_batch is None) != (influence_plan is None):
        raise ValueError("impact and influence stages must be present together")
    influence_transaction = None
    if impact_batch is not None:
        if not isinstance(impact_batch, _SUPPORTED_IMPACT_BATCH_TYPES):
            raise ValueError("impact_batch type is unsupported")
        if not isinstance(influence_plan, MemoryInfluenceCommitPlan):
            raise ValueError("influence_plan must be MemoryInfluenceCommitPlan")
        if isinstance(impact_batch, ActualRecallStateImpactBatch):
            if not isinstance(root_transition, ActualRecallRootTransition):
                raise ValueError("actual recall v2 requires one root transition")
        elif root_transition is not None:
            raise ValueError("legacy impact cannot carry an actual-recall root transition")
        impact_ids = tuple(item.event_id for item in impact_batch.items)
        if set(impact_ids) != set(state_ids):
            raise ValueError("impact exact ID set must match state-eligible canonical IDs")
        if (impact_batch.trigger_event_id != trigger_id
                or impact_batch.evaluation_time_ms != at_ms):
            raise ValueError("impact trigger and time must match the committed event")
        influence_transaction = influence_plan.transaction
        if (set(edge.parent_node_id for edge in influence_transaction.edges)
                != set(state_ids)):
            raise ValueError("influence parent set must match state impact IDs")
        if (influence_transaction.child_anchor_id != trigger_id
                or influence_transaction.committed_at_ms != at_ms):
            raise ValueError("influence child and time must match the committed event")
        if root_transition is not None:
            if (root_transition.trigger_event_id != trigger_id
                    or root_transition.evaluation_time_ms != at_ms
                    or set(root_transition.source_event_ids) != set(state_ids)):
                raise ValueError("root transition identity must match product state IDs")
            if (influence_transaction.state_transition_receipt_ref
                    != root_transition.transition_id):
                raise ValueError("influence must reference the product root transition")

    if bool(canonical_ids) != (
            attention_plan is not None and hydration_batch is not None):
        raise ValueError(
            "canonical content IDs require both attention and hydration stages")
    if (attention_plan is None) != (hydration_batch is None):
        raise ValueError("attention and hydration stages must be present together")
    selected_ids = ()
    hydrated_ids = ()
    if attention_plan is not None:
        if not isinstance(attention_plan, MemoryCardAttentionOccupation):
            raise ValueError("attention_plan must be MemoryCardAttentionOccupation")
        if not isinstance(hydration_batch, MemoryCardHydrationBatch):
            raise ValueError("hydration_batch must be MemoryCardHydrationBatch")
        if set(attention_plan.recalled_event_ids) != set(canonical_ids):
            raise ValueError("attention exact ID set must match canonical IDs")
        if (attention_plan.trigger_event_id != trigger_id
                or attention_plan.evaluation_time_ms != at_ms):
            raise ValueError("attention trigger and time must match the committed event")
        if (hydration_batch.trigger_event_id != trigger_id
                or hydration_batch.evaluation_time_ms != at_ms):
            raise ValueError("hydration trigger and time must match the committed event")
        hydration_canonical = tuple(
            hydration_batch.receipt.get("canonical_event_ids") or ())
        if set(hydration_canonical) != set(canonical_ids):
            raise ValueError("hydration canonical ID set must match canonical IDs")
        selected_ids = tuple(attention_plan.selected_for_hydration_ids)
        hydrated_ids = tuple(card.event_id for card in hydration_batch.cards)
        if hydrated_ids != selected_ids:
            raise ValueError("hydration cards must preserve attention selection order")

    versions = {
        "canonical_join": canonical_batch.operator_version,
        "state_impact": impact_batch.operator_version if impact_batch else None,
        "influence_commit": influence_plan.formula_version if influence_plan else None,
        "attention": attention_plan.formula_version if attention_plan else None,
        "hydration": hydration_batch.formula_version if hydration_batch else None,
    }
    if isinstance(canonical_batch, ActualRecallCanonicalBatch):
        versions["root_transition"] = (
            root_transition.operator_version if root_transition else None)
    receipt = {
        "operator_version": (
            MEMORY_PRODUCT_TRANSACTION_VERSION
            if isinstance(canonical_batch, ActualRecallCanonicalBatch)
            else LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION),
        "scope": "owner_local_memory_product_transaction",
        "owner_participant_id": owner_id,
        "trigger_event_id": trigger_id,
        "committed_at_ms": at_ms,
        "canonical_event_ids": canonical_ids,
        "state_impact_event_ids": state_ids,
        "selected_for_hydration_ids": selected_ids,
        "hydrated_card_ids": hydrated_ids,
        "state_impact_included": bool(state_ids),
        "p4_applied": False,
        "root_state_transition_committed": False,
        "memory_edges_published": influence_transaction is not None,
        "content_embedded": False,
        "writer_read": False,
        "tick_read": False,
        "ui_read": False,
        "formula_recomputed": False,
    }
    if stage_production_receipt is not None:
        required = {
            "contract_version", "runtime_binding_version", "trigger_event_id",
            "evaluation_time_ms", "owner_participant_id",
            "family_binding_version", "writer_read", "tick_read", "ui_read",
        }
        if (not isinstance(stage_production_receipt, Mapping)
                or set(stage_production_receipt) != required):
            raise ValueError("stage production receipt schema is unsupported")
        stage_receipt = dict(stage_production_receipt)
        if (_identifier(stage_receipt["trigger_event_id"], "trigger_event_id")
                != trigger_id
                or _timestamp(stage_receipt["evaluation_time_ms"],
                              "evaluation_time_ms") != at_ms
                or _identifier(stage_receipt["owner_participant_id"],
                               "owner_participant_id") != owner_id
                or _identifier(stage_receipt["family_binding_version"],
                               "family_binding_version")
                != _identifier(family_binding_version,
                               "family_binding_version")):
            raise ValueError("stage production receipt conflicts with transaction")
        if any(stage_receipt[field] is not False for field in (
                "writer_read", "tick_read", "ui_read")):
            raise ValueError("stage production cannot read writer, tick, or UI")
        from ops.memory_product_runtime import (
            MEMORY_PRODUCT_STAGE_PRODUCER_VERSION,
        )
        if (_identifier(stage_receipt["contract_version"], "contract_version")
                != MEMORY_PRODUCT_STAGE_PRODUCER_VERSION):
            raise ValueError("stage production contract is unsupported")
        _identifier(stage_receipt["runtime_binding_version"],
                    "runtime_binding_version")
        receipt["stage_production"] = stage_receipt
    return MemoryProductTransaction(
        transaction_id=transaction_id,
        owner_participant_id=owner_id,
        trigger_event_id=trigger_id,
        committed_at_ms=at_ms,
        family_binding_version=family_binding_version,
        formula_version=(
            MEMORY_PRODUCT_TRANSACTION_VERSION
            if isinstance(canonical_batch, ActualRecallCanonicalBatch)
            else LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION),
        canonical_event_ids=canonical_ids,
        state_impact_event_ids=state_ids,
        influence_transaction=influence_transaction,
        selected_for_hydration_ids=selected_ids,
        hydrated_card_ids=hydrated_ids,
        source_versions=versions,
        receipt=receipt,
        root_transition=root_transition,
    )


class MemoryProductTransactionJournal:
    """Owner-local exact-ID commit boundary over one append-only Store stream."""

    _PENDING_STATE_KEY = "_pending_memory_product_commit"
    _LAST_STATE_KEY = "last_memory_product_transaction_id"
    _PREPARED_MUTABLE_FIELDS = frozenset({
        "vars", "prev_vars", "P", "E", "H_max", "exploded",
        "exploded_at", "last_drives", "last_end_drives",
    })

    def __init__(self, store):
        self.store = store

    def read(self):
        return tuple(MemoryProductTransaction.from_record(row)
                     for row in self.store.read_memory_product_transactions())

    @staticmethod
    def _matches_committed_trigger(row, transaction):
        """Resolve one canonical trigger from committed local identity facts.

        An incoming source event keeps its exact identity in delivery
        provenance; an owner output only obtains its source-event identity
        after F16 publication, so it is reconstructed from the same immutable
        conversation/sender/action triple used by the publisher.  No text or
        time-neighbour heuristic participates in either branch.
        """
        if str(row.get("event_id") or "") == transaction.trigger_event_id:
            return True

        provenance = row.get("conversation_provenance")
        if isinstance(provenance, Mapping):
            return bool(
                str(provenance.get("event_id") or "")
                == transaction.trigger_event_id
                and str(provenance.get("recipient_participant_id") or "")
                == transaction.owner_participant_id
            )

        context = row.get("conversation_context")
        identity = row.get("output_identity")
        action_id = str(row.get("action_id") or "").strip()
        if not (row.get("role") == "npc" and isinstance(context, Mapping)
                and isinstance(identity, Mapping) and action_id):
            return False
        owner_id = transaction.owner_participant_id
        if (str(identity.get("participant_id") or "") != owner_id
                or str(context.get("owner_participant_id") or "") != owner_id
                or str(context.get("sender_participant_id") or "") != owner_id):
            return False
        try:
            source_event_id = canonical_npc_output_event_id(
                conversation_id=context.get("conversation_id"),
                sender_participant_id=owner_id,
                action_id=action_id,
            )
        except ConversationEventSchemaError:
            return False
        return source_event_id == transaction.trigger_event_id

    def _require_committed_trigger(self, transaction):
        matches = [
            row for row in self.store.read_messages()
            if self._matches_committed_trigger(row, transaction)
        ]
        if len(matches) != 1:
            raise ValueError(
                "memory product transaction requires one committed trigger event")
        row = matches[0]
        raw_time = row.get("committed_at_ms", row.get("ts"))
        if _timestamp(raw_time, "committed trigger time") != transaction.committed_at_ms:
            raise ValueError("committed trigger time conflicts with memory transaction")

    @staticmethod
    def _same_root_vector(left, right):
        if not isinstance(left, Mapping) or not isinstance(right, Mapping):
            return False
        if set(left) != set(right):
            return False
        return all(math.isclose(
            float(left[axis]), float(right[axis]), rel_tol=1e-12, abs_tol=1e-12)
            for axis in left)

    @classmethod
    def committed_record(cls, transaction: MemoryProductTransaction):
        """Materialize the durable record that may truthfully claim P4 commit.

        The builder's record deliberately says ``p4_applied=false`` because no
        state write has happened yet.  Only this journal boundary may flip the
        receipt, and it does so without changing any numeric transition.
        """
        if not isinstance(transaction, MemoryProductTransaction):
            raise ValueError("transaction must be a MemoryProductTransaction")
        record = copy.deepcopy(transaction.to_record())
        transition = transaction.root_transition
        if transition is None:
            return record
        record["receipt"]["p4_applied"] = True
        record["receipt"]["root_state_transition_committed"] = True
        record["root_transition"]["receipt"]["commit_status"] = "committed"
        # Re-parse the exact durable shape now, before it can enter a state
        # marker or append-only stream.
        MemoryProductTransaction.from_record(record)
        return record

    @classmethod
    def _validate_prepared_state(cls, *, before, prepared, transition):
        if not isinstance(before, Mapping) or not isinstance(prepared, Mapping):
            raise ValueError("prepared root state must be a complete state mapping")
        if set(before) != set(prepared):
            raise ValueError("prepared root state must preserve the state schema")
        if not cls._same_root_vector(before.get("vars"), transition.x_before):
            raise ValueError("stored root state does not match transition x_before")
        if not cls._same_root_vector(prepared.get("vars"), transition.x_after):
            raise ValueError("prepared root state does not match transition x_after")
        changed = {
            key for key in before if before.get(key) != prepared.get(key)
        }
        forbidden = changed - cls._PREPARED_MUTABLE_FIELDS
        if forbidden:
            raise ValueError(
                "prepared root state changes fields outside the root transition: "
                + ", ".join(sorted(forbidden)))

    def recover_pending_root_commit(self):
        """Finish one state-first commit marker without reapplying its delta."""
        with self.store._lock:
            state = self.store.load_state()
            marker = (state or {}).get(self._PENDING_STATE_KEY)
            if marker is None:
                return {"status": "no_pending"}
            transaction = MemoryProductTransaction.from_record(marker)
            transition = transaction.root_transition
            if transition is None:
                raise ValueError("pending memory product has no root transition")
            if not self._same_root_vector(state.get("vars"), transition.x_after):
                raise ValueError("pending memory product state is not transition x_after")
            self._require_committed_trigger(transaction)
            append_status = self.store.append_memory_product_transaction(dict(marker))
            settled = copy.deepcopy(state)
            settled.pop(self._PENDING_STATE_KEY, None)
            settled[self._LAST_STATE_KEY] = transaction.transaction_id
            self.store.save_state(settled)
            return {
                "status": "recovered",
                "append_status": append_status,
                "transaction_id": transaction.transaction_id,
                "trigger_event_id": transaction.trigger_event_id,
                "owner_participant_id": transaction.owner_participant_id,
            }

    def committed_status(self, transaction: MemoryProductTransaction):
        """Return an exact durable replay result without applying root state."""
        if not isinstance(transaction, MemoryProductTransaction):
            raise ValueError("transaction must be a MemoryProductTransaction")
        durable_record = self.committed_record(transaction)
        with self.store._lock:
            matches = [
                row for row in self.store.read_memory_product_transactions()
                if str(row.get("transaction_id") or "") == transaction.transaction_id
            ]
            if not matches:
                return None
            # Reuse the Store's exact-record conflict check.
            status = self.store.append_memory_product_transaction(durable_record)
            transition = transaction.root_transition
            if transition is not None:
                state = self.store.load_state()
                if (not isinstance(state, Mapping)
                        or state.get(self._LAST_STATE_KEY) != transaction.transaction_id
                        or not self._same_root_vector(
                            state.get("vars"), transition.x_after)):
                    raise ValueError(
                        "committed memory product is missing its root state watermark")
            return {
                "status": status,
                "transaction_id": transaction.transaction_id,
                "trigger_event_id": transaction.trigger_event_id,
                "owner_participant_id": transaction.owner_participant_id,
            }

    def commit(self, transaction: MemoryProductTransaction, *, prepared_state=None):
        if not isinstance(transaction, MemoryProductTransaction):
            raise ValueError("transaction must be a MemoryProductTransaction")
        self._require_committed_trigger(transaction)
        transition = transaction.root_transition
        if transition is None:
            status = self.store.append_memory_product_transaction(
                transaction.to_record())
            return {
                "status": status,
                "transaction_id": transaction.transaction_id,
                "trigger_event_id": transaction.trigger_event_id,
                "owner_participant_id": transaction.owner_participant_id,
            }
        if prepared_state is None:
            raise ValueError("prepared root state is required for a v2 state transaction")

        durable_record = self.committed_record(transaction)
        with self.store._lock:
            current = self.store.load_state()
            if not isinstance(current, Mapping):
                raise ValueError("stored root state is required for a v2 state transaction")
            marker = current.get(self._PENDING_STATE_KEY)
            if marker is not None:
                pending_id = str(marker.get("transaction_id") or "")
                if pending_id != transaction.transaction_id:
                    raise ValueError("another memory product root commit is pending")
                recovered = self.recover_pending_root_commit()
                recovered["status"] = "already_committed"
                return recovered

            existing = self.committed_status(transaction)
            if existing is not None:
                return existing

            self._validate_prepared_state(
                before=current, prepared=prepared_state, transition=transition)
            pending_state = copy.deepcopy(dict(prepared_state))
            pending_state[self._PENDING_STATE_KEY] = durable_record
            self.store.save_state(pending_state)
            try:
                status = self.store.append_memory_product_transaction(durable_record)
            except Exception:
                # A synchronous append failure has not published any product
                # row, so restore the exact pre-transition snapshot.  A process
                # crash cannot execute this branch; its durable marker is
                # intentionally recovered by ``recover_pending_root_commit``.
                self.store.save_state(copy.deepcopy(dict(current)))
                raise
            settled = copy.deepcopy(pending_state)
            settled.pop(self._PENDING_STATE_KEY, None)
            settled[self._LAST_STATE_KEY] = transaction.transaction_id
            self.store.save_state(settled)
        return {
            "status": status,
            "transaction_id": transaction.transaction_id,
            "trigger_event_id": transaction.trigger_event_id,
            "owner_participant_id": transaction.owner_participant_id,
        }
