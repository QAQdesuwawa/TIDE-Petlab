"""Alpha 0.4 committed-event to settled-stage producer boundary.

The boundary is deliberately narrower than a memory algorithm.  A configured
producer owns the already versioned C1--C5 runtime packages and returns their
settled DTOs for one exact committed event.  This module validates identity,
time and package-version lineage only; it does not read legacy recall receipts,
recompute stages, hydrate text, schedule output, or inspect writer/UI state.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ops.conversation_events import CommittedConversationEvent
from ops.memory_actual_join import ActualRecallCanonicalBatch
from ops.memory_actual_state import ActualRecallStateImpactBatch
from ops.memory_card_attention import MemoryCardAttentionOccupation
from ops.memory_card_hydration import MemoryCardHydrationBatch
from ops.memory_influence_commit import MemoryInfluenceCommitPlan
from ops.memory_join import CanonicalRecallBatch
from ops.memory_root_transition import ActualRecallRootTransition
from ops.memory_state_impact import RecallStateImpactBatch


MEMORY_PRODUCT_STAGE_PRODUCER_VERSION = "memory-product-stage-producer-v1"
_SUPPORTED_CANONICAL_BATCH_TYPES = (
    CanonicalRecallBatch,
    ActualRecallCanonicalBatch,
)
_SUPPORTED_IMPACT_BATCH_TYPES = (
    RecallStateImpactBatch,
    ActualRecallStateImpactBatch,
)


def _identifier(value, field):
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} cannot be empty")
    return normalized


def _timestamp(value, field):
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a non-negative integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a non-negative integer") from exc
    if parsed < 0 or parsed != value:
        raise ValueError(f"{field} must be a non-negative integer")
    return parsed


@dataclass(frozen=True)
class SettledMemoryProductStages:
    """One producer result whose stage DTOs are already fully settled."""

    trigger_event_id: str
    evaluation_time_ms: int
    producer_contract_version: str
    runtime_binding_version: str
    canonical_batch: CanonicalRecallBatch | ActualRecallCanonicalBatch
    impact_batch: Optional[RecallStateImpactBatch | ActualRecallStateImpactBatch] = None
    root_transition: Optional[ActualRecallRootTransition] = None
    influence_plan: Optional[MemoryInfluenceCommitPlan] = None
    attention_plan: Optional[MemoryCardAttentionOccupation] = None
    hydration_batch: Optional[MemoryCardHydrationBatch] = None

    def __post_init__(self):
        object.__setattr__(self, "trigger_event_id", _identifier(
            self.trigger_event_id, "trigger_event_id"))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        contract = _identifier(
            self.producer_contract_version, "producer_contract_version")
        if contract != MEMORY_PRODUCT_STAGE_PRODUCER_VERSION:
            raise ValueError("memory product stage producer contract is unsupported")
        object.__setattr__(self, "producer_contract_version", contract)
        object.__setattr__(self, "runtime_binding_version", _identifier(
            self.runtime_binding_version, "runtime_binding_version"))
        if not isinstance(self.canonical_batch, _SUPPORTED_CANONICAL_BATCH_TYPES):
            raise ValueError("canonical_batch type is unsupported")
        optional_types = (
            ("impact_batch", _SUPPORTED_IMPACT_BATCH_TYPES),
            ("root_transition", ActualRecallRootTransition),
            ("influence_plan", MemoryInfluenceCommitPlan),
            ("attention_plan", MemoryCardAttentionOccupation),
            ("hydration_batch", MemoryCardHydrationBatch),
        )
        for field, expected in optional_types:
            value = getattr(self, field)
            if value is not None and not isinstance(value, expected):
                expected_name = (
                    " or ".join(item.__name__ for item in expected)
                    if isinstance(expected, tuple) else expected.__name__)
                raise ValueError(f"{field} must be {expected_name} or None")
        if isinstance(self.impact_batch, ActualRecallStateImpactBatch):
            if not isinstance(self.root_transition, ActualRecallRootTransition):
                raise ValueError("actual recall v2 stages require one root transition")
        elif self.root_transition is not None:
            raise ValueError("legacy stages cannot carry actual-recall root transition")

    def production_receipt(self, *, owner_participant_id,
                           family_binding_version):
        """Return the text-free lineage persisted inside the D1 transaction."""
        return {
            "contract_version": self.producer_contract_version,
            "runtime_binding_version": self.runtime_binding_version,
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "owner_participant_id": _identifier(
                owner_participant_id, "owner_participant_id"),
            "family_binding_version": _identifier(
                family_binding_version, "family_binding_version"),
            "writer_read": False,
            "tick_read": False,
            "ui_read": False,
        }


def memory_product_stage_producer_identity(producer):
    """Validate one injected producer without invoking it or inventing defaults."""
    if producer is None:
        raise RuntimeError("Alpha 0.4 memory product stage producer is not configured")
    produce = getattr(producer, "produce", None)
    if not callable(produce):
        raise ValueError("memory product stage producer must define produce()")
    contract = _identifier(
        getattr(producer, "contract_version", None), "contract_version")
    if contract != MEMORY_PRODUCT_STAGE_PRODUCER_VERSION:
        raise ValueError("memory product stage producer contract is unsupported")
    return {
        "contract_version": contract,
        "runtime_binding_version": _identifier(
            getattr(producer, "runtime_binding_version", None),
            "runtime_binding_version"),
    }


def produce_memory_product_stages(
        *, producer, trigger_event, owner_participant_id,
        family_binding_version) -> SettledMemoryProductStages:
    """Invoke one validated producer and reject stale/cross-event results."""
    if not isinstance(trigger_event, CommittedConversationEvent):
        raise ValueError("trigger_event must be a CommittedConversationEvent")
    owner_id = _identifier(owner_participant_id, "owner_participant_id")
    family_version = _identifier(
        family_binding_version, "family_binding_version")
    producer_identity = memory_product_stage_producer_identity(producer)
    result = producer.produce(
        trigger_event=trigger_event,
        owner_participant_id=owner_id,
        family_binding_version=family_version,
    )
    if not isinstance(result, SettledMemoryProductStages):
        raise ValueError(
            "memory product stage producer must return SettledMemoryProductStages")
    if result.producer_contract_version != producer_identity["contract_version"]:
        raise ValueError("settled stages producer contract does not match producer")
    if result.runtime_binding_version != producer_identity["runtime_binding_version"]:
        raise ValueError("settled stages runtime binding does not match producer")
    if result.trigger_event_id != trigger_event.event_id:
        raise ValueError("settled stages trigger does not match committed event")
    if result.evaluation_time_ms != trigger_event.committed_at_ms:
        raise ValueError("settled stages time does not match committed event")
    return result
