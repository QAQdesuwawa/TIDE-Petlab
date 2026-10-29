"""Legacy millisecond F2 seam for one candidate-local attention witness.

The attention mathematics remains owned by the six/seven package.  This
adapter is compatibility-only because its payload and invocation identity use
the retired ``evaluation_time_ms``/``context.at_ms`` contract.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import ModelInvocationContext

from ..continuous_root_projection import ContinuousRootProjection
from ..core_operators import _context, _plain_mapping, _require_payload_keys
from ..memory_attention import (
    MemoryAccessibilityEvidence,
    ProjectionSnapshot,
    build_memory_attention_witness,
)


def _snapshot(value: object, name: str) -> ProjectionSnapshot:
    record = _plain_mapping(value, name)
    _require_payload_keys(
        record,
        {"event_id", "evaluation_time_ms", "source_receipt_ref", "projection"},
        stage=name,
    )
    projection = _plain_mapping(record["projection"], f"{name}.projection")
    _require_payload_keys(
        projection,
        {"vector", "formula_version", "constant_binding_version", "receipt"},
        stage=f"{name}.projection",
    )
    return ProjectionSnapshot(
        event_id=record["event_id"],
        evaluation_time_ms=record["evaluation_time_ms"],
        source_receipt_ref=record["source_receipt_ref"],
        projection=ContinuousRootProjection(
            vector=_plain_mapping(projection["vector"], f"{name}.vector"),
            formula_version=projection["formula_version"],
            constant_binding_version=projection["constant_binding_version"],
            receipt=_plain_mapping(projection["receipt"], f"{name}.receipt"),
        ),
    )


def _accessibility(value: object) -> MemoryAccessibilityEvidence:
    record = _plain_mapping(value, "accessibility")
    _require_payload_keys(
        record,
        {
            "target_event_id", "trigger_event_id", "evaluation_time_ms",
            "value", "formula_version", "receipt_ref",
        },
        stage="accessibility",
    )
    return MemoryAccessibilityEvidence(**record)


class CxSixSevenMemoryAttentionAdapter:
    """One historical exact ID to one legacy F2 witness, never selection."""

    def attention_witness(
        self, *, payload: Mapping[str, Any], context: ModelInvocationContext,
    ) -> dict[str, Any]:
        context = _context(context)
        _require_payload_keys(
            payload,
            {"historical", "current", "accessibility", "params"},
            stage="memory_attention_witness",
        )
        historical = _snapshot(payload["historical"], "historical")
        current = _snapshot(payload["current"], "current")
        accessibility = _accessibility(payload["accessibility"])
        if (context.event_id is None
                or current.event_id != context.event_id
                or current.evaluation_time_ms != context.at_ms
                or historical.evaluation_time_ms != context.at_ms
                or accessibility.evaluation_time_ms != context.at_ms):
            raise ValueError("C3-A witness does not match invocation context")
        witness = build_memory_attention_witness(
            historical=historical,
            current=current,
            accessibility=accessibility,
            params=_plain_mapping(payload["params"], "params"),
        )
        return {"witness": witness.to_record()}
