"""Package-owned text boundary for the neutral-signal witness.

This deliberately uses vector rows and segmented realization rather than the
minimal witness's flat two-field input and one-field output.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
from typing import Any

from . import RESOURCE_VERSION


MEASUREMENT_CONTRACT = "neutral-signal-text-vector-v1"
MEASUREMENT_RESPONSE_SCHEMA = "neutral-signal-vector-response-v1"
REALIZATION_CONTRACT = "neutral-signal-segment-realization-v1"
REALIZATION_RESPONSE_SCHEMA = "neutral-signal-segment-response-v1"
_CHANNELS = ("support", "opposition", "novelty")


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return dict(value)


def _bounded(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a number") from exc
    if not math.isfinite(result) or not -1.0 <= result <= 1.0:
        raise ValueError(f"{field} must be between -1 and 1")
    return result


class NeutralSignalTextCodec:
    """Strict provider codec for vector input and segmented output."""

    def build_measurement_request(
        self, *, source_ref: str, text: str,
    ) -> dict[str, Any]:
        return {
            "contract": MEASUREMENT_CONTRACT,
            "observation": {
                "source": _identifier(source_ref, "source_ref"),
                "content": _identifier(text, "text"),
            },
            "vector_channels": tuple(
                {"name": name, "domain": (-1.0, 1.0)}
                for name in _CHANNELS
            ),
            "response_schema": MEASUREMENT_RESPONSE_SCHEMA,
        }

    def parse_measurement_response(
        self, response: Mapping[str, Any],
    ) -> dict[str, float]:
        record = _mapping(response, "measurement response")
        if set(record) != {"schema", "signals"}:
            raise ValueError("neutral measurement response fields are invalid")
        if record["schema"] != MEASUREMENT_RESPONSE_SCHEMA:
            raise ValueError("neutral measurement response schema is invalid")
        signals: dict[str, float] = {}
        for index, raw in enumerate(record["signals"]):
            item = _mapping(raw, f"signal {index}")
            if set(item) != {"channel", "magnitude"}:
                raise ValueError("neutral signal row fields are invalid")
            channel = _identifier(item["channel"], "signal channel")
            if channel not in _CHANNELS or channel in signals:
                raise ValueError("neutral signal channels are invalid")
            signals[channel] = _bounded(
                item["magnitude"], f"{channel} magnitude")
        if set(signals) != set(_CHANNELS):
            raise ValueError("neutral measurement requires every channel")
        return {
            "support_signal": signals["support"],
            "opposition_signal": signals["opposition"],
            "novelty_signal": signals["novelty"],
        }

    def build_measurement_transport(
        self, request: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(request, "measurement request")
        if set(record) != {
            "contract", "observation", "vector_channels", "response_schema",
        }:
            raise ValueError("neutral measurement request fields are invalid")
        if record["contract"] != MEASUREMENT_CONTRACT:
            raise ValueError("neutral measurement contract is invalid")
        return {
            "system_prompt": (
                "Produce one JSON object with exactly two top-level fields "
                "named \"schema\" and \"signals\". The schema value is "
                "\"neutral-signal-vector-response-v1\". The signals value is "
                "an array of exactly three objects; every object has exactly "
                "two fields named \"channel\" and \"magnitude\". Channel "
                "values are \"support\", \"opposition\", and \"novelty\", "
                "each exactly once. Every magnitude is numeric and in "
                "[-1, 1]. Do not use \"name\" as an output field."
            ),
            "user_content": json.dumps({
                "vector_measurement": record,
            }, ensure_ascii=False, sort_keys=True),
            "temperature": 0.0,
            "require_json": True,
        }

    def assemble_realizer_control(
        self, expression_resource_batch: Mapping[str, Any],
    ) -> dict[str, Any]:
        batch = _mapping(expression_resource_batch, "expression resources")
        if batch.get("version") != RESOURCE_VERSION:
            raise ValueError("neutral expression resource version is invalid")
        selection = _mapping(batch.get("selection"), "resource selection")
        selected_ids = tuple(selection.get("selected_resource_ids", ()))
        resources = {
            str(item.get("resource_id")): item
            for item in batch.get("resources", ())
            if isinstance(item, Mapping)
        }
        directives = []
        for resource_id in selected_ids:
            item = resources.get(str(resource_id))
            if item is None:
                raise ValueError("selected neutral resource is absent")
            descriptor = _mapping(item.get("descriptor"), "resource descriptor")
            directives.append({
                "resource_ref": str(resource_id),
                "direction": _identifier(
                    descriptor.get("direction"), "resource direction"),
                "weight": float(item.get("score")),
            })
        return {
            "contract": REALIZATION_CONTRACT,
            "transition_directives": tuple(directives),
            "output_schema": {
                "schema": REALIZATION_RESPONSE_SCHEMA,
                "segment_fields": ("type", "value"),
            },
        }

    def parse_realization_response(
        self, response: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(response, "realization response")
        if set(record) != {"schema", "segments", "provider_trace_ref"}:
            raise ValueError("neutral realization response fields are invalid")
        if record["schema"] != REALIZATION_RESPONSE_SCHEMA:
            raise ValueError("neutral realization response schema is invalid")
        segments = []
        for index, raw in enumerate(record["segments"]):
            item = _mapping(raw, f"segment {index}")
            if set(item) != {"type", "value"}:
                raise ValueError("neutral segment fields are invalid")
            segments.append({
                "type": _identifier(item["type"], "segment type"),
                "value": _identifier(item["value"], "segment value"),
            })
        if not segments:
            raise ValueError("neutral realization requires a segment")
        trace_ref = _identifier(
            record["provider_trace_ref"], "provider_trace_ref")
        return {
            "candidate_payload": {
                "schema": REALIZATION_RESPONSE_SCHEMA,
                "segments": tuple(segments),
            },
            "realization_evidence": {
                "codec_contract": REALIZATION_CONTRACT,
                "provider_trace_ref": trace_ref,
                "segment_count": len(segments),
            },
        }

    def build_realization_transport(
        self, *, control: Mapping[str, Any],
        readonly_facts: Mapping[str, Any],
    ) -> dict[str, Any]:
        control_record = _mapping(control, "realizer control")
        if control_record.get("contract") != REALIZATION_CONTRACT:
            raise ValueError("neutral realization contract is invalid")
        facts = _mapping(readonly_facts, "readonly facts")
        return {
            "system_prompt": (
                "Produce one JSON object with exactly three top-level fields "
                "named \"schema\", \"segments\", and "
                "\"provider_trace_ref\". The schema value is "
                "\"neutral-signal-segment-response-v1\". Segments is a "
                "non-empty array whose objects each contain exactly two "
                "non-empty string fields named \"type\" and \"value\". "
                "Provider_trace_ref is a non-empty string."
            ),
            "user_content": json.dumps({
                "segmented_control": control_record,
                "facts": facts,
            }, ensure_ascii=False, sort_keys=True),
            "temperature": 0.2,
            "require_json": True,
        }


__all__ = (
    "MEASUREMENT_CONTRACT",
    "MEASUREMENT_RESPONSE_SCHEMA",
    "REALIZATION_CONTRACT",
    "REALIZATION_RESPONSE_SCHEMA",
    "NeutralSignalTextCodec",
)
