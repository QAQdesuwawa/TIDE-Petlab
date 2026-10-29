"""Package-owned tau3 text boundary for the Full six/seven reference model."""
from __future__ import annotations

from collections.abc import Mapping
import json
import math
from typing import Any

from framework.model_api import thaw_model_data
from framework.tau3_benchmark_protocol import Tau3BenchmarkTurnRequest

from .semantic_measurement import CxSixSevenSemanticCodec
from .semantic_projection import BEHAVIOR_PROTOTYPES, VARIABLE_KEYS


MEASUREMENT_CONTRACT = "cx-six-seven-tau3-measurement-v1"
REALIZATION_CONTRACT = "cx-six-seven-tau3-realization-v1"


def _mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{field} must be a mapping")
    return thaw_model_data(value)


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


def _number(value: object, field: str, *, low: float, high: float) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be numeric")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be numeric") from exc
    if not math.isfinite(result) or not low <= result <= high:
        raise ValueError(f"{field} must be between {low} and {high}")
    return result


def _request(value: object) -> Tau3BenchmarkTurnRequest:
    if not isinstance(value, Tau3BenchmarkTurnRequest):
        raise TypeError("six/seven codec requires a verified tau3 request")
    value.verify_integrity()
    return value


def _inbound_text(inbound: Mapping[str, Any]) -> str:
    record = _mapping(inbound, "inbound")
    if record.get("kind") == "user_text":
        return _identifier(record.get("content"), "inbound content")
    return json.dumps(record, ensure_ascii=False, sort_keys=True)


def _parse_action(record: dict[str, Any]) -> dict[str, Any]:
    kind = record.get("kind")
    if kind == "text" and set(record) == {"kind", "content"}:
        return {
            "kind": "text",
            "content": _identifier(record["content"], "text content"),
        }
    if kind == "tool" and set(record) == {"kind", "tool_calls"}:
        raw_calls = record["tool_calls"]
        if not isinstance(raw_calls, list) or not raw_calls:
            raise ValueError("tool action requires a non-empty call list")
        calls = []
        for index, raw in enumerate(raw_calls):
            item = _mapping(raw, f"tool call {index}")
            if set(item) != {"id", "name", "arguments"}:
                raise ValueError("tool call fields are invalid")
            calls.append({
                "id": _identifier(item["id"], "tool call id"),
                "name": _identifier(item["name"], "tool call name"),
                "arguments": _mapping(item["arguments"], "tool arguments"),
            })
        return {"kind": "tool", "tool_calls": calls}
    raise ValueError("realization response must contain exactly one action shape")


class CxSixSevenBenchmarkTextCodec:
    measurement_contract = MEASUREMENT_CONTRACT
    realization_contract = REALIZATION_CONTRACT

    def build_measurement_request(
        self, request: Tau3BenchmarkTurnRequest,
    ) -> dict[str, Any]:
        verified = _request(request)
        return {
            "contract": self.measurement_contract,
            "source_event_ref": str(verified.turn["event_id"]),
            "event_kind": "peer_input",
            "inbound": thaw_model_data(verified.turn["inbound"]),
            "committed_history": thaw_model_data(
                verified.public_context["committed_history"]),
        }

    def build_measurement_transport(
        self, envelope: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(envelope, "measurement envelope")
        if set(record) != {
            "contract", "source_event_ref", "event_kind", "inbound",
            "committed_history",
        }:
            raise ValueError("six/seven measurement request fields are invalid")
        if record["contract"] != self.measurement_contract:
            raise ValueError("six/seven measurement contract is invalid")
        system, user = CxSixSevenSemanticCodec().build_request(
            text=_inbound_text(record["inbound"]),
            recall_hint=json.dumps(
                record["committed_history"],
                ensure_ascii=False,
                sort_keys=True,
            ),
            event_kind=record["event_kind"],
        )
        return {
            "system_prompt": system,
            "user_content": user,
            "temperature": CxSixSevenSemanticCodec().temperature,
            "require_json": True,
        }

    def parse_measurement_response(
        self, response: Mapping[str, Any],
    ) -> dict[str, Any]:
        record = _mapping(response, "measurement response")
        required = {"variable_deltas", "behavior_weights", "tokens", "confidence"}
        allowed = required | {"reference_frame"}
        if not required.issubset(record) or not set(record).issubset(allowed):
            raise ValueError("six/seven measurement response fields are invalid")
        deltas = _mapping(record["variable_deltas"], "variable_deltas")
        if set(deltas) != set(VARIABLE_KEYS):
            raise ValueError("six/seven variable delta fields are invalid")
        normalized_deltas = {
            key: _number(deltas[key], key, low=-1.0, high=1.0)
            for key in VARIABLE_KEYS
        }
        weights = _mapping(record["behavior_weights"], "behavior_weights")
        if set(weights) != set(BEHAVIOR_PROTOTYPES):
            raise ValueError("six/seven behavior weight fields are invalid")
        normalized_weights = {
            key: _number(weights[key], key, low=0.0, high=1.0)
            for key in BEHAVIOR_PROTOTYPES
        }
        if abs(sum(normalized_weights.values()) - 1.0) > 0.001:
            raise ValueError("six/seven behavior weights must sum to one")
        raw_tokens = record["tokens"]
        if not isinstance(raw_tokens, list) or len(raw_tokens) > 3:
            raise ValueError("six/seven measurement tokens are invalid")
        tokens = []
        for index, raw in enumerate(raw_tokens):
            item = _mapping(raw, f"token {index}")
            if set(item) != {"text", "strength", "evidence"}:
                raise ValueError("six/seven measurement token fields are invalid")
            tokens.append({
                "text": _identifier(item["text"], "token text"),
                "strength": _number(
                    item["strength"], "token strength", low=0.0, high=1.0),
                "evidence": _identifier(item["evidence"], "token evidence"),
            })
        raw_measurement: dict[str, Any] = {
            "variable_deltas": normalized_deltas,
            "behavior_weights": normalized_weights,
            "tokens": tokens,
            "confidence": _number(
                record["confidence"], "confidence", low=0.0, high=1.0),
            "source": "tau3-provider",
            "model": "frozen-runtime-identity",
        }
        if "reference_frame" in record:
            raw_measurement["reference_frame"] = _mapping(
                record["reference_frame"], "reference_frame")
        return {"event_kind": "peer_input", "raw_measurement": raw_measurement}

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
                "tool": {"kind": "tool", "tool_calls": [{
                    "id": "string", "name": "string", "arguments": "object",
                }]},
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
            raise ValueError("six/seven realization request fields are invalid")
        if record["contract"] != self.realization_contract:
            raise ValueError("six/seven realization contract is invalid")
        payload = {key: record[key] for key in required if key != "contract"}
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
        candidate = _parse_action(_mapping(response, "realization response"))
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
    "CxSixSevenBenchmarkTextCodec",
)
