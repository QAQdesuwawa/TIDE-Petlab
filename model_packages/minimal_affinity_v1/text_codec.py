"""Package-owned text boundary for the minimal-affinity witness.

The framework treats every value produced here as opaque model data.  This
module alone names the provider fields and translates them to the package's
two measurement coordinates and one-field realization shape.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
from typing import Any

from . import RESOURCE_VERSION


MEASUREMENT_CONTRACT = "minimal-affinity-text-measurement-v1"
REALIZATION_CONTRACT = "minimal-affinity-text-realization-v1"


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


class MinimalAffinityTextCodec:
    """Strict provider codec for the minimal two-axis text witness."""

    def build_measurement_request(
        self, *, source_ref: str, text: str,
    ) -> dict[str, Any]:
        return {
            "contract": MEASUREMENT_CONTRACT,
            "input": {
                "source_ref": _identifier(source_ref, "source_ref"),
                "text": _identifier(text, "text"),
            },
            "result_fields": ("affinity", "conflict"),
        }

    def parse_measurement_response(
        self, response: Mapping[str, Any],
    ) -> dict[str, float]:
        record = _mapping(response, "measurement response")
        if set(record) != {"affinity", "conflict"}:
            raise ValueError("minimal measurement response fields are invalid")
        return {
            "affinity_stimulus": _bounded(
                record["affinity"], "affinity"),
            "conflict_stimulus": _bounded(
                record["conflict"], "conflict"),
        }

    def build_measurement_transport(
        self, request: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Render this package's own measurement contract for a text provider."""
        record = _mapping(request, "measurement request")
        if set(record) != {"contract", "input", "result_fields"}:
            raise ValueError("minimal measurement request fields are invalid")
        if record["contract"] != MEASUREMENT_CONTRACT:
            raise ValueError("minimal measurement contract is invalid")
        input_record = _mapping(record["input"], "measurement input")
        if set(input_record) != {"source_ref", "text"}:
            raise ValueError("minimal measurement input fields are invalid")
        return {
            "system_prompt": (
                "Return one JSON object containing exactly two numeric fields: "
                "affinity and conflict. Each value is in [-1, 1]."
            ),
            "user_content": json.dumps(
                record, ensure_ascii=False, sort_keys=True),
            "temperature": 0.0,
            "require_json": True,
        }

    def assemble_realizer_control(
        self, expression_resource_batch: Mapping[str, Any],
    ) -> dict[str, Any]:
        batch = _mapping(expression_resource_batch, "expression resources")
        if batch.get("version") != RESOURCE_VERSION:
            raise ValueError("minimal expression resource version is invalid")
        selection = _mapping(batch.get("selection"), "resource selection")
        selected_ids = tuple(selection.get("selected_resource_ids", ()))
        resources = {
            str(item.get("resource_id")): item
            for item in batch.get("resources", ())
            if isinstance(item, Mapping)
        }
        tendencies = []
        for resource_id in selected_ids:
            item = resources.get(str(resource_id))
            if item is None:
                raise ValueError("selected minimal resource is absent")
            descriptor = _mapping(item.get("descriptor"), "resource descriptor")
            tendencies.append(_identifier(
                descriptor.get("direction"), "resource direction"))
        return {
            "contract": REALIZATION_CONTRACT,
            "selected_tendencies": tuple(tendencies),
            "output_field": "utterance",
        }

    def parse_realization_response(
        self, response: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(response, "realization response")
        if set(record) != {"utterance"}:
            raise ValueError("minimal realization response fields are invalid")
        utterance = _identifier(record["utterance"], "utterance")
        return {
            "candidate_payload": {"utterance": utterance},
            "realization_evidence": {
                "codec_contract": REALIZATION_CONTRACT,
                "parsed_fields": ("utterance",),
            },
        }

    def build_realization_transport(
        self, *, control: Mapping[str, Any],
        readonly_facts: Mapping[str, Any],
    ) -> dict[str, Any]:
        control_record = _mapping(control, "realizer control")
        if control_record.get("contract") != REALIZATION_CONTRACT:
            raise ValueError("minimal realization contract is invalid")
        facts = _mapping(readonly_facts, "readonly facts")
        return {
            "system_prompt": (
                "Return one JSON object containing exactly one non-empty "
                "string field named utterance."
            ),
            "user_content": json.dumps({
                "control": control_record,
                "readonly_facts": facts,
            }, ensure_ascii=False, sort_keys=True),
            "temperature": 0.2,
            "require_json": True,
        }


__all__ = (
    "MEASUREMENT_CONTRACT",
    "REALIZATION_CONTRACT",
    "MinimalAffinityTextCodec",
)
