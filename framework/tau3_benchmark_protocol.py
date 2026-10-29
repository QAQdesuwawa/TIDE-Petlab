"""Frozen package-neutral request contract for one tau3 benchmark turn."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)


TAU3_TURN_REQUEST_VERSION = "eval-lab-tau3-tide-turn-request-v1"
TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION = (
    "eval-lab-tau3-tide-turn-request-v2")
_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _fingerprint(value: object, field: str) -> str:
    normalized = _identifier(value, field)
    if not _HEX_64.fullmatch(normalized):
        raise ValueError(f"{field} must be a sha256 fingerprint")
    return normalized


def _plain_mapping(value: object, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{field} must be a mapping")
    return thaw_model_data(freeze_model_payload(value))


def _validate_inbound(value: object) -> dict[str, Any]:
    inbound = _plain_mapping(value, "turn.inbound")
    kind = inbound.get("kind")
    if kind == "user_text":
        if set(inbound) != {"kind", "content"}:
            raise ValueError("user text inbound fields are invalid")
        content = str(inbound.get("content") or "").strip()
        if not content:
            raise ValueError("user text inbound is empty")
        return {"kind": kind, "content": content}
    if kind == "tool_results":
        if set(inbound) != {"kind", "results"}:
            raise ValueError("tool-result inbound fields are invalid")
        rows = inbound.get("results")
        if not isinstance(rows, list) or not rows:
            raise ValueError("tool-result inbound is empty")
        normalized = []
        for row in rows:
            item = _plain_mapping(row, "tool result")
            if set(item) != {"id", "content", "error"}:
                raise ValueError("tool-result fields are invalid")
            if type(item["error"]) is not bool:
                raise ValueError("tool-result error flag must be boolean")
            normalized.append({
                "id": _identifier(item["id"], "tool result id"),
                "content": item["content"],
                "error": item["error"],
            })
        return {"kind": kind, "results": normalized}
    raise ValueError("tau3 inbound kind is unsupported")


@dataclass(frozen=True)
class Tau3BenchmarkTurnRequest:
    schema_version: str
    session: Mapping[str, Any]
    turn: Mapping[str, Any]
    public_context: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "session", freeze_model_payload(self.session))
        object.__setattr__(self, "turn", freeze_model_payload(self.turn))
        object.__setattr__(
            self, "public_context", freeze_model_payload(self.public_context))

    @classmethod
    def create(
        cls, *, session: Mapping[str, Any], turn: Mapping[str, Any],
        public_context: Mapping[str, Any],
        schema_version: str = TAU3_TURN_REQUEST_VERSION,
    ) -> "Tau3BenchmarkTurnRequest":
        if schema_version not in {
            TAU3_TURN_REQUEST_VERSION,
            TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION,
        }:
            raise ValueError("tau3 benchmark request version is unsupported")
        session_record = _plain_mapping(session, "session")
        expected_session = {
            "adapter_version", "session_id", "task_identity", "command_seed",
            "runtime_identity", "model_identity", "budget_identity", "domain_policy_digest",
            "tool_space_digest",
        }
        if schema_version == TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION:
            expected_session.add("time_trace_digest")
        if set(session_record) != expected_session:
            raise ValueError("tau3 session fields are invalid")
        if session_record["adapter_version"] != "eval-lab-tau3-tide-adapter-v1":
            raise ValueError("tau3 adapter version is unsupported")
        if isinstance(session_record["command_seed"], bool) or not isinstance(
            session_record["command_seed"], int
        ):
            raise ValueError("tau3 command seed must be an integer")
        normalized_session = {
            "adapter_version": session_record["adapter_version"],
            "session_id": _identifier(session_record["session_id"], "session_id"),
            "task_identity": _identifier(
                session_record["task_identity"], "task_identity"),
            "command_seed": session_record["command_seed"],
            "runtime_identity": _identifier(
                session_record["runtime_identity"], "runtime_identity"),
            "model_identity": _identifier(
                session_record["model_identity"], "model_identity"),
            "budget_identity": _identifier(
                session_record["budget_identity"], "budget_identity"),
            "domain_policy_digest": _fingerprint(
                session_record["domain_policy_digest"], "domain_policy_digest"),
            "tool_space_digest": _fingerprint(
                session_record["tool_space_digest"], "tool_space_digest"),
        }
        if schema_version == TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION:
            normalized_session["time_trace_digest"] = _fingerprint(
                session_record["time_trace_digest"], "time_trace_digest")
        turn_record = _plain_mapping(turn, "turn")
        expected_turn = {"event_id", "ordinal", "inbound"}
        if schema_version == TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION:
            expected_turn.add("temporal_coordinate")
        if set(turn_record) != expected_turn:
            raise ValueError("tau3 turn fields are invalid")
        ordinal = turn_record["ordinal"]
        if isinstance(ordinal, bool) or not isinstance(ordinal, int) or ordinal < 0:
            raise ValueError("tau3 turn ordinal must be non-negative")
        normalized_turn = {
            "event_id": _identifier(turn_record["event_id"], "event_id"),
            "ordinal": ordinal,
            "inbound": _validate_inbound(turn_record["inbound"]),
        }
        if schema_version == TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION:
            coordinate = turn_record["temporal_coordinate"]
            if (
                isinstance(coordinate, bool)
                or not isinstance(coordinate, int)
                or coordinate < 0
            ):
                raise ValueError(
                    "tau3 temporal coordinate must be a non-negative integer")
            normalized_turn["temporal_coordinate"] = coordinate
        context = _plain_mapping(public_context, "public_context")
        if set(context) != {"domain_policy", "tools", "committed_history"}:
            raise ValueError("tau3 public context fields are invalid")
        if not isinstance(context["domain_policy"], str):
            raise ValueError("tau3 domain policy must be text")
        if not isinstance(context["tools"], list):
            raise ValueError("tau3 tools must be a list")
        if not isinstance(context["committed_history"], list):
            raise ValueError("tau3 committed history must be a list")
        normalized_context = thaw_model_data(freeze_model_payload(context))
        if model_data_fingerprint(context["domain_policy"]) != normalized_session[
            "domain_policy_digest"
        ]:
            raise ValueError("tau3 domain policy fingerprint differs")
        if model_data_fingerprint(context["tools"]) != normalized_session[
            "tool_space_digest"
        ]:
            raise ValueError("tau3 tool-space fingerprint differs")
        material = {
            "schema_version": schema_version,
            "session": normalized_session,
            "turn": normalized_turn,
            "public_context": normalized_context,
        }
        return cls(
            schema_version=material["schema_version"],
            session=material["session"], turn=material["turn"],
            public_context=material["public_context"],
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        expected = Tau3BenchmarkTurnRequest.create(
            session=self.session, turn=self.turn,
            public_context=self.public_context,
            schema_version=self.schema_version,
        )
        if expected != self:
            raise ValueError("tau3 benchmark request fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "session": thaw_model_data(self.session),
            "turn": thaw_model_data(self.turn),
            "public_context": thaw_model_data(self.public_context),
            "fingerprint": self.fingerprint,
        }


def read_tau3_benchmark_turn_request(
    record: Mapping[str, Any],
) -> Tau3BenchmarkTurnRequest:
    if not isinstance(record, Mapping):
        raise TypeError("tau3 benchmark request must be a mapping")
    if set(record) != {
        "schema_version", "session", "turn", "public_context", "fingerprint",
    }:
        raise ValueError("tau3 benchmark request fields are invalid")
    if record.get("schema_version") not in {
        TAU3_TURN_REQUEST_VERSION,
        TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION,
    }:
        raise ValueError("tau3 benchmark request version is unsupported")
    request = Tau3BenchmarkTurnRequest(
        schema_version=str(record["schema_version"]), session=record["session"],
        turn=record["turn"], public_context=record["public_context"],
        fingerprint=_identifier(record["fingerprint"], "fingerprint"),
    )
    request.verify_integrity()
    return request


__all__ = (
    "TAU3_TURN_REQUEST_VERSION",
    "TAU3_TURN_REQUEST_CONTROLLED_TIME_VERSION",
    "Tau3BenchmarkTurnRequest",
    "read_tau3_benchmark_turn_request",
)
