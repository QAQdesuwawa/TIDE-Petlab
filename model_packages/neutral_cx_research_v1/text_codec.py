"""Package-owned tau3 text boundary for the neutral C-X research family.

The framework and benchmark host transport these records as opaque model data.
This module alone defines how a frozen public tau3 turn becomes the package's
two-coordinate measurement and how an opaque C-X control becomes one tau3
text or tool action.  It has no provider, state, commit, or evaluator authority.
"""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
from typing import Any

from framework.model_api import thaw_model_data
from framework.tau3_benchmark_protocol import Tau3BenchmarkTurnRequest


MEASUREMENT_CONTRACT = "neutral-cx-tau3-measurement-v2"
REALIZATION_CONTRACT = "neutral-cx-tau3-realization-v1"


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return thaw_model_data(value)


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _unit(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or not 0.0 <= result <= 1.0:
        raise ValueError(f"{field} must be between 0 and 1")
    return result


def _request(value: object) -> Tau3BenchmarkTurnRequest:
    if not isinstance(value, Tau3BenchmarkTurnRequest):
        raise TypeError("neutral C-X codec requires a verified tau3 request")
    value.verify_integrity()
    return value


class NeutralCXBenchmarkTextCodec:
    """Strict provider codec shared by P0/P1/P2 without changing capability."""

    measurement_contract = MEASUREMENT_CONTRACT
    realization_contract = REALIZATION_CONTRACT

    def build_measurement_request(
        self, request: Tau3BenchmarkTurnRequest,
    ) -> dict[str, Any]:
        verified = _request(request)
        return {
            "contract": self.measurement_contract,
            "source_event_ref": str(verified.turn["event_id"]),
            "inbound": thaw_model_data(verified.turn["inbound"]),
            "committed_history": thaw_model_data(
                verified.public_context["committed_history"]),
            "public_tools": thaw_model_data(verified.public_context["tools"]),
            "result_fields": ["positive_signal", "negative_signal"],
        }

    def build_measurement_transport(
        self, envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(envelope, "measurement envelope")
        required = {
            "contract", "source_event_ref", "inbound", "committed_history",
            "public_tools", "result_fields",
        }
        if set(record) != required:
            raise ValueError("neutral C-X measurement request fields are invalid")
        if record["contract"] != self.measurement_contract:
            raise ValueError("neutral C-X measurement contract is invalid")
        if record["result_fields"] != ["positive_signal", "negative_signal"]:
            raise ValueError("neutral C-X measurement result fields are invalid")
        provider_input = {
            "source_event_ref": _identifier(
                record["source_event_ref"], "source_event_ref"),
            "inbound": record["inbound"],
            "committed_history": record["committed_history"],
            "public_tools": record["public_tools"],
        }
        return {
            "system_prompt": (
                "Return exactly one JSON object with exactly two numeric "
                "fields named positive_signal and negative_signal. Each "
                "number is a nonnegative intensity in [0, 1]."
            ),
            "user_content": json.dumps(
                provider_input, ensure_ascii=False, sort_keys=True),
            "temperature": 0.0,
            "require_json": True,
        }

    def parse_measurement_response(
        self, response: Mapping[str, Any],
    ) -> dict[str, float]:
        record = _mapping(response, "measurement response")
        if set(record) != {"positive_signal", "negative_signal"}:
            raise ValueError("neutral C-X measurement response fields are invalid")
        return {
            "positive_signal": _unit(
                record["positive_signal"], "positive_signal"),
            "negative_signal": _unit(
                record["negative_signal"], "negative_signal"),
        }

    def build_realization_request(
        self, *, request: Tau3BenchmarkTurnRequest,
        control: Mapping[str, Any],
    ) -> dict[str, Any]:
        verified = _request(request)
        return {
            "contract": self.realization_contract,
            "source_event_ref": str(verified.turn["event_id"]),
            "inbound": thaw_model_data(verified.turn["inbound"]),
            "committed_history": thaw_model_data(
                verified.public_context["committed_history"]),
            "domain_policy": str(verified.public_context["domain_policy"]),
            "tools": thaw_model_data(verified.public_context["tools"]),
            "control": _mapping(control, "realizer control"),
            "response_shape": {
                "text": {"kind": "text", "content": "string"},
                "tool": {
                    "kind": "tool",
                    "tool_calls": [{
                        "id": "string", "name": "string",
                        "arguments": "object",
                    }],
                },
            },
        }

    def build_realization_transport(
        self, envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(envelope, "realization envelope")
        required = {
            "contract", "source_event_ref", "inbound", "committed_history",
            "domain_policy", "tools", "control", "response_shape",
        }
        if set(record) != required:
            raise ValueError("neutral C-X realization request fields are invalid")
        if record["contract"] != self.realization_contract:
            raise ValueError("neutral C-X realization contract is invalid")
        payload = {
            key: record[key] for key in (
                "source_event_ref", "inbound", "committed_history",
                "domain_policy", "tools", "control", "response_shape",
            )
        }
        return {
            "system_prompt": (
                "Return exactly one JSON action object. It is either a text "
                "action with exactly kind and content, or a tool action with "
                "exactly kind and a non-empty tool_calls array. Each tool call "
                "contains exactly id, name, and object-valued arguments."
            ),
            "user_content": json.dumps(
                payload, ensure_ascii=False, sort_keys=True),
            "temperature": 0.0,
            "require_json": True,
        }

    def parse_realization_response(
        self, response: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(response, "realization response")
        kind = record.get("kind")
        if kind == "text" and set(record) == {"kind", "content"}:
            candidate = {
                "kind": "text",
                "content": _identifier(record["content"], "text content"),
            }
        elif kind == "tool" and set(record) == {"kind", "tool_calls"}:
            raw_calls = record["tool_calls"]
            if not isinstance(raw_calls, list) or not raw_calls:
                raise ValueError("tool action requires a non-empty call list")
            calls = []
            for index, raw in enumerate(raw_calls):
                item = _mapping(raw, f"tool call {index}")
                if set(item) != {"id", "name", "arguments"}:
                    raise ValueError("tool call fields are invalid")
                arguments = _mapping(item["arguments"], "tool arguments")
                calls.append({
                    "id": _identifier(item["id"], "tool call id"),
                    "name": _identifier(item["name"], "tool call name"),
                    "arguments": arguments,
                })
            candidate = {"kind": "tool", "tool_calls": calls}
        else:
            raise ValueError(
                "realization response must contain exactly one action shape")
        return {
            "candidate_payload": candidate,
            "realization_evidence": {
                "codec_contract": self.realization_contract,
                "action_kind": candidate["kind"],
            },
        }


__all__ = (
    "MEASUREMENT_CONTRACT",
    "REALIZATION_CONTRACT",
    "NeutralCXBenchmarkTextCodec",
)
