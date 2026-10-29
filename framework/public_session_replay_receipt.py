"""Package-neutral proof of an idempotent committed-checkpoint replay."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data


PUBLIC_SESSION_REPLAY_RECEIPT_VERSION = (
    "framework-public-session-replay-receipt-v1"
)
_FIELDS = {
    "version", "session_id", "runtime_identity", "request_fingerprint",
    "result_fingerprint", "source_checkpoint_receipt_fingerprint",
    "source_checkpoint_content_fingerprint", "event_id",
    "accepted_ordinal", "next_benchmark_ordinal", "provider_calls",
    "fingerprint",
}


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"public replay receipt {field} is invalid")
    return normalized


def _ordinal(value: object, field: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"public replay receipt {field} is invalid")
    return value


@dataclass(frozen=True)
class PublicSessionReplayReceipt:
    version: str
    session_id: str
    runtime_identity: str
    request_fingerprint: str
    result_fingerprint: str
    source_checkpoint_receipt_fingerprint: str
    source_checkpoint_content_fingerprint: str
    event_id: str
    accepted_ordinal: int
    next_benchmark_ordinal: int
    provider_calls: int
    fingerprint: str

    @classmethod
    def create(
        cls, *, session_id: str, runtime_identity: str,
        request_fingerprint: str, result_fingerprint: str,
        source_checkpoint_receipt_fingerprint: str,
        source_checkpoint_content_fingerprint: str,
        event_id: str, accepted_ordinal: int,
        next_benchmark_ordinal: int, provider_calls: int = 0,
    ) -> "PublicSessionReplayReceipt":
        accepted = _ordinal(
            accepted_ordinal, "accepted ordinal", minimum=0)
        next_ordinal = _ordinal(
            next_benchmark_ordinal, "next benchmark ordinal", minimum=1)
        if next_ordinal != accepted + 1:
            raise ValueError("public replay receipt ordinal lineage is invalid")
        if provider_calls != 0:
            raise ValueError("public replay receipt provider calls must be zero")
        material = {
            "version": PUBLIC_SESSION_REPLAY_RECEIPT_VERSION,
            "session_id": _identifier(session_id, "session identity"),
            "runtime_identity": _identifier(
                runtime_identity, "runtime identity"),
            "request_fingerprint": _identifier(
                request_fingerprint, "request fingerprint"),
            "result_fingerprint": _identifier(
                result_fingerprint, "result fingerprint"),
            "source_checkpoint_receipt_fingerprint": _identifier(
                source_checkpoint_receipt_fingerprint,
                "source checkpoint receipt fingerprint",
            ),
            "source_checkpoint_content_fingerprint": _identifier(
                source_checkpoint_content_fingerprint,
                "source checkpoint content fingerprint",
            ),
            "event_id": _identifier(event_id, "event identity"),
            "accepted_ordinal": accepted,
            "next_benchmark_ordinal": next_ordinal,
            "provider_calls": 0,
        }
        return cls(
            **material, fingerprint=model_data_fingerprint(material))

    def to_record(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in (
                "version", "session_id", "runtime_identity",
                "request_fingerprint", "result_fingerprint",
                "source_checkpoint_receipt_fingerprint",
                "source_checkpoint_content_fingerprint", "event_id",
                "accepted_ordinal", "next_benchmark_ordinal",
                "provider_calls", "fingerprint",
            )
        }


def verify_public_session_replay_receipt_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ValueError("public replay receipt fields are invalid")
    record = thaw_model_data(value)
    expected = PublicSessionReplayReceipt.create(**{
        field: record[field] for field in _FIELDS if field not in {
            "version", "fingerprint",
        }
    })
    if record.get("version") != expected.version or record != expected.to_record():
        raise ValueError("public replay receipt integrity is invalid")
    return record


__all__ = (
    "PUBLIC_SESSION_REPLAY_RECEIPT_VERSION",
    "PublicSessionReplayReceipt",
    "verify_public_session_replay_receipt_record",
)
