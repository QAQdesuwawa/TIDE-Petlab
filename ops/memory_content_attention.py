"""Alpha 0.4 content-only attention over joined numeric and address evidence.

This pure layer closes the expression-side gap left by numeric-only C3-A
attention.  Numeric-only and dual-hit canonical memories preserve the existing
C3-A raw drive.  Address-only memories receive a separately versioned content
strength derived from an available cosine-similarity receipt.  A dual hit never
adds the two values.

The resulting witnesses are used only by MemoryCard hydration attention.  They
cannot change roots, P4, dynamic tick, send eligibility, committed history, or
the canonical recall set.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Mapping

from ops.memory_attention import MEMORY_ATTENTION_VERSION, MemoryAttentionWitness
from ops.memory_actual_join import ActualRecallCanonicalBatch
from ops.memory_card_attention import (
    MemoryCardAttentionOccupation,
    _settle_active_set,
)
from ops.memory_contracts import AddressReceipt
from ops.memory_join import CanonicalRecallBatch


_SUPPORTED_CANONICAL_BATCH_TYPES = (
    CanonicalRecallBatch,
    ActualRecallCanonicalBatch,
)


ADDRESS_CONTENT_STRENGTH_VERSION = "address-content-strength-affine-cosine-v1"
CONTENT_ATTENTION_WITNESS_VERSION = "memory-content-attention-witness-v1"
CONTENT_MEMORY_CARD_ATTENTION_VERSION = "memory-card-content-attention-lca-v2"
CONTENT_MEMORY_CARD_ATTENTION_V2 = {
    "version": CONTENT_MEMORY_CARD_ATTENTION_VERSION,
    "input_witness_version": CONTENT_ATTENTION_WITNESS_VERSION,
    "leak": 1.0,
    "lateral_inhibition": 0.5,
    "solver_tolerance": 1e-12,
}


def _identifier(value, field):
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} cannot be empty")
    return result


def _finite(value, field):
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be finite")
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


def _parameter_package(params):
    if not isinstance(params, Mapping):
        raise ValueError("an explicit versioned content-attention package is required")
    required = {
        "version", "input_witness_version", "leak",
        "lateral_inhibition", "solver_tolerance",
    }
    missing = required - set(params)
    if missing:
        raise ValueError("content-attention package is missing: "
                         + ", ".join(sorted(missing)))
    version = _identifier(params.get("version"), "params.version")
    witness_version = _identifier(
        params.get("input_witness_version"), "params.input_witness_version")
    leak = _finite(params.get("leak"), "params.leak")
    inhibition = _finite(
        params.get("lateral_inhibition"), "params.lateral_inhibition")
    tolerance = _finite(params.get("solver_tolerance"), "params.solver_tolerance")
    if version != CONTENT_MEMORY_CARD_ATTENTION_VERSION:
        raise ValueError("unknown content-attention version")
    if witness_version != CONTENT_ATTENTION_WITNESS_VERSION:
        raise ValueError("content-attention input witness version is unregistered")
    if leak <= 0.0 or inhibition < 0.0 or inhibition >= leak:
        raise ValueError("params require leak > lateral_inhibition >= 0")
    if tolerance <= 0.0:
        raise ValueError("params.solver_tolerance must be positive")
    for key, value in (("leak", leak), ("lateral_inhibition", inhibition),
                       ("solver_tolerance", tolerance)):
        if not math.isclose(
                value, float(CONTENT_MEMORY_CARD_ATTENTION_V2[key]),
                rel_tol=0.0, abs_tol=1e-15):
            raise ValueError(
                "content-attention package does not match its registered version")
    return version, witness_version, leak, inhibition, tolerance


@dataclass(frozen=True)
class AddressContentStrengthReceipt:
    event_id: str
    content_strength: float
    formula_version: str
    source_policy_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        object.__setattr__(self, "event_id", _identifier(self.event_id, "event_id"))
        strength = _finite(self.content_strength, "content_strength")
        if not 0.0 <= strength <= 1.0:
            raise ValueError("content_strength must be between zero and one")
        object.__setattr__(self, "content_strength", strength)
        object.__setattr__(self, "formula_version", _identifier(
            self.formula_version, "formula_version"))
        object.__setattr__(self, "source_policy_version", _identifier(
            self.source_policy_version, "source_policy_version"))
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self):
        return {
            "event_id": self.event_id,
            "content_strength": self.content_strength,
            "formula_version": self.formula_version,
            "source_policy_version": self.source_policy_version,
            "receipt": _thaw(self.receipt),
        }


@dataclass(frozen=True)
class ContentAttentionWitness:
    target_event_id: str
    trigger_event_id: str
    evaluation_time_ms: int
    drive: float
    drive_source: str
    formula_version: str
    source_formula_version: str
    receipt: Mapping[str, object]

    def __post_init__(self):
        for field in ("target_event_id", "trigger_event_id", "formula_version",
                      "source_formula_version"):
            object.__setattr__(self, field, _identifier(getattr(self, field), field))
        object.__setattr__(self, "evaluation_time_ms", _timestamp(
            self.evaluation_time_ms, "evaluation_time_ms"))
        drive = _finite(self.drive, "drive")
        if not 0.0 <= drive <= 1.0:
            raise ValueError("drive must be between zero and one")
        object.__setattr__(self, "drive", drive)
        source = str(self.drive_source or "").strip()
        if source not in {"numeric", "address"}:
            raise ValueError("drive_source must be numeric or address")
        object.__setattr__(self, "drive_source", source)
        if not isinstance(self.receipt, Mapping):
            raise ValueError("receipt must be a mapping")
        object.__setattr__(self, "receipt", _freeze(dict(self.receipt), "receipt"))

    def to_record(self):
        return {
            "target_event_id": self.target_event_id,
            "trigger_event_id": self.trigger_event_id,
            "evaluation_time_ms": self.evaluation_time_ms,
            "drive": self.drive,
            "drive_source": self.drive_source,
            "formula_version": self.formula_version,
            "source_formula_version": self.source_formula_version,
            "receipt": _thaw(self.receipt),
        }


def derive_address_content_strength(
        address_receipt: AddressReceipt) -> AddressContentStrengthReceipt:
    """Map one qualified cosine receipt into expression-only content strength."""
    if not isinstance(address_receipt, AddressReceipt):
        raise ValueError("address_receipt must be an AddressReceipt")
    if address_receipt.status != "available":
        raise ValueError("only an available address receipt can publish content strength")
    strength = 0.5 * (float(address_receipt.similarity) + 1.0)
    return AddressContentStrengthReceipt(
        event_id=address_receipt.event_id,
        content_strength=strength,
        formula_version=ADDRESS_CONTENT_STRENGTH_VERSION,
        source_policy_version=address_receipt.policy_version,
        receipt={
            "operator_version": ADDRESS_CONTENT_STRENGTH_VERSION,
            "scope": "expression_content_attention_only",
            "event_id": address_receipt.event_id,
            "source_status": address_receipt.status,
            "source_similarity": address_receipt.similarity,
            "source_provider": address_receipt.provider,
            "source_model": address_receipt.model,
            "source_policy_version": address_receipt.policy_version,
            "formula": "0.5 * (cosine_similarity + 1.0)",
            "content_strength": strength,
            "state_eligible": False,
            "p4_authority_changed": False,
            "send_eligibility_changed": False,
        },
    )


def build_content_attention_witnesses(
        *, canonical_batch: CanonicalRecallBatch | ActualRecallCanonicalBatch,
        numeric_attention_witnesses,
        address_strength_receipts,
        trigger_event_id: str,
        evaluation_time_ms: int) -> tuple[ContentAttentionWitness, ...]:
    """Bind every canonical content item to exactly one authorized drive source."""
    if not isinstance(canonical_batch, _SUPPORTED_CANONICAL_BATCH_TYPES):
        raise ValueError("canonical_batch type is unsupported")
    trigger_id = _identifier(trigger_event_id, "trigger_event_id")
    at_ms = _timestamp(evaluation_time_ms, "evaluation_time_ms")

    numeric_by_id = {}
    for witness in tuple(numeric_attention_witnesses or ()):
        if not isinstance(witness, MemoryAttentionWitness):
            raise ValueError(
                "numeric_attention_witnesses must contain MemoryAttentionWitness values")
        if witness.target_event_id in numeric_by_id:
            raise ValueError("numeric attention witness set contains duplicate exact IDs")
        if witness.formula_version != MEMORY_ATTENTION_VERSION:
            raise ValueError("numeric attention witness version is unregistered")
        if witness.trigger_event_id != trigger_id or witness.evaluation_time_ms != at_ms:
            raise ValueError(
                "numeric attention witnesses must share the requested trigger and time")
        numeric_by_id[witness.target_event_id] = witness

    address_by_id = {}
    for receipt in tuple(address_strength_receipts or ()):
        if not isinstance(receipt, AddressContentStrengthReceipt):
            raise ValueError(
                "address_strength_receipts must contain AddressContentStrengthReceipt values")
        if receipt.event_id in address_by_id:
            raise ValueError("address strength receipt set contains duplicate exact IDs")
        if receipt.formula_version != ADDRESS_CONTENT_STRENGTH_VERSION:
            raise ValueError("address content strength version is unregistered")
        address_by_id[receipt.event_id] = receipt

    numeric_expected = {
        item.event_id for item in canonical_batch.items if item.state_eligible
    }
    address_expected = {
        item.event_id for item in canonical_batch.items
        if item.content_eligible and not item.state_eligible
    }
    if set(numeric_by_id) != numeric_expected:
        raise ValueError(
            "numeric attention witness set must exactly match state-eligible canonical IDs")
    if set(address_by_id) != address_expected:
        raise ValueError(
            "address strength receipt set must exactly match address-only canonical IDs")

    witnesses = []
    for item in sorted(canonical_batch.items, key=lambda row: row.event_id):
        if item.state_eligible:
            numeric = numeric_by_id[item.event_id]
            dual = bool(item.address_receipt is not None
                        and item.address_receipt.status == "available")
            drive = numeric.raw_drive
            source_formula = numeric.formula_version
            if isinstance(canonical_batch, ActualRecallCanonicalBatch):
                lane = "dual" if dual else "actual_recall_only"
            else:
                lane = "dual" if dual else "numeric_only"
            source_receipt = numeric.receipt
        else:
            address = address_by_id[item.event_id]
            drive = address.content_strength
            source_formula = address.formula_version
            lane = "address_only"
            source_receipt = address.receipt
        witnesses.append(ContentAttentionWitness(
            target_event_id=item.event_id,
            trigger_event_id=trigger_id,
            evaluation_time_ms=at_ms,
            drive=drive,
            drive_source="numeric" if item.state_eligible else "address",
            formula_version=CONTENT_ATTENTION_WITNESS_VERSION,
            source_formula_version=source_formula,
            receipt={
                "operator_version": CONTENT_ATTENTION_WITNESS_VERSION,
                "target_event_id": item.event_id,
                "trigger_event_id": trigger_id,
                "evaluation_time_ms": at_ms,
                "canonical_join_version": canonical_batch.operator_version,
                "evidence_lane": lane,
                "drive_source": "numeric" if item.state_eligible else "address",
                "drive": drive,
                "source_formula_version": source_formula,
                "source_receipt": source_receipt,
                "content_eligible": True,
                "state_eligible": item.state_eligible,
                "address_added_to_numeric": False,
                "canonical_recall_mutated": False,
                "p4_authority_changed": False,
                "send_eligibility_changed": False,
            },
        ))
    return tuple(witnesses)


def settle_content_memory_card_attention(
        *, witnesses,
        params: Mapping[str, object]) -> MemoryCardAttentionOccupation:
    """Settle content hydration occupation without changing recall or state."""
    version, witness_version, leak, inhibition, tolerance = _parameter_package(params)
    rows = []
    seen = set()
    trigger_event_id = None
    evaluation_time_ms = None
    for witness in tuple(witnesses or ()):
        if not isinstance(witness, ContentAttentionWitness):
            raise ValueError("witnesses must contain ContentAttentionWitness values")
        if witness.formula_version != witness_version:
            raise ValueError("content attention witness version does not match package")
        if witness.target_event_id in seen:
            raise ValueError("duplicate target_event_id in content-attention input")
        seen.add(witness.target_event_id)
        if trigger_event_id is None:
            trigger_event_id = witness.trigger_event_id
            evaluation_time_ms = witness.evaluation_time_ms
        elif (witness.trigger_event_id != trigger_event_id
              or witness.evaluation_time_ms != evaluation_time_ms):
            raise ValueError("all witnesses must share the same trigger and evaluation time")
        rows.append({
            "event_id": witness.target_event_id,
            "raw_drive": float(witness.drive),
            "drive_source": witness.drive_source,
            "source_formula_version": witness.source_formula_version,
            "witness_formula_version": witness.formula_version,
        })
    if not rows:
        raise ValueError("at least one content-attention witness is required")
    rows.sort(key=lambda row: row["event_id"])
    active, activations, total_activation, cutoff, passes = _settle_active_set(
        rows, leak=leak, inhibition=inhibition, tolerance=tolerance)

    decisions = []
    max_residual = 0.0
    for row in rows:
        event_id, drive = row["event_id"], row["raw_drive"]
        activation = activations.get(event_id, 0.0)
        occupied = event_id in active
        other_activation = max(0.0, total_activation - activation)
        leak_pressure = leak * activation
        lateral_pressure = inhibition * other_activation
        residual = drive - leak_pressure - lateral_pressure
        if occupied:
            max_residual = max(max_residual, abs(residual))
            reason = "positive_equilibrium_activation"
        elif drive <= tolerance:
            reason = "zero_drive"
        else:
            reason = "attention_occupied_by_stronger_content_events"
        decisions.append({
            "event_id": event_id,
            "input_witness_version": row["witness_formula_version"],
            "drive_source": row["drive_source"],
            "source_formula_version": row["source_formula_version"],
            "raw_drive": drive,
            "settled_activation": activation,
            "leak_pressure": leak_pressure,
            "lateral_pressure": lateral_pressure,
            "equilibrium_residual": residual,
            "occupied": occupied,
            "hydration_eligible": occupied,
            "reason": reason,
            "actual_recall_preserved": True,
            "p4_authority_changed": False,
            "send_eligibility_changed": False,
        })

    recalled_ids = tuple(row["event_id"] for row in rows)
    selected_ids = tuple(event_id for event_id in recalled_ids if event_id in active)
    return MemoryCardAttentionOccupation(
        trigger_event_id=trigger_event_id,
        evaluation_time_ms=evaluation_time_ms,
        formula_version=version,
        recalled_event_ids=recalled_ids,
        selected_for_hydration_ids=selected_ids,
        receipt={
            "operator_version": version,
            "parameter_version": version,
            "input_witness_version": witness_version,
            "scope": "writer_memory_card_hydration_only",
            "trigger_event_id": trigger_event_id,
            "evaluation_time_ms": evaluation_time_ms,
            "parameters": {
                "leak": leak,
                "lateral_inhibition": inhibition,
                "solver_tolerance": tolerance,
            },
            "equilibrium": {
                "active_count": len(active),
                "total_activation": total_activation,
                "shared_cutoff": cutoff,
                "active_set_passes": passes,
                "max_active_residual": max_residual,
            },
            "recalled_event_ids": recalled_ids,
            "selected_for_hydration_ids": selected_ids,
            "decisions": tuple(decisions),
            "fixed_count_used": False,
            "content_hydrated": False,
            "actual_recall_mutated": False,
            "numeric_witness_mutated": False,
            "p4_input_mutated": False,
            "writer_feedback_read": False,
            "send_eligibility_changed": False,
        },
    )
