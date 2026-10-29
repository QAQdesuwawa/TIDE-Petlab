"""Package-neutral proof that one registered session checkpoint persisted."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data


PUBLIC_SESSION_CHECKPOINT_RECEIPT_VERSION = (
    "framework-public-session-checkpoint-receipt-v1"
)
_FIELDS = {
    "version", "session_id", "runtime_identity", "request_fingerprint",
    "result_fingerprint", "runtime_snapshot_fingerprint",
    "checkpoint_content_fingerprint",
    "next_benchmark_ordinal", "last_event_id", "recovery_mode",
    "predecessor_checkpoint_fingerprint", "fingerprint",
}


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"public checkpoint receipt {field} is invalid")
    return normalized


@dataclass(frozen=True)
class PublicSessionCheckpointReceipt:
    version: str
    session_id: str
    runtime_identity: str
    request_fingerprint: str
    result_fingerprint: str
    runtime_snapshot_fingerprint: str
    checkpoint_content_fingerprint: str
    next_benchmark_ordinal: int
    last_event_id: str
    recovery_mode: str
    predecessor_checkpoint_fingerprint: str | None
    fingerprint: str

    @classmethod
    def create(
        cls, *, session_id: str, runtime_identity: str,
        request_fingerprint: str, result_fingerprint: str,
        runtime_snapshot_fingerprint: str, next_benchmark_ordinal: int,
        checkpoint_content_fingerprint: str,
        last_event_id: str, recovery_mode: str,
        predecessor_checkpoint_fingerprint: str | None,
    ) -> "PublicSessionCheckpointReceipt":
        if (
            isinstance(next_benchmark_ordinal, bool)
            or not isinstance(next_benchmark_ordinal, int)
            or next_benchmark_ordinal < 1
        ):
            raise ValueError("public checkpoint receipt ordinal is invalid")
        mode = _identifier(recovery_mode, "recovery mode")
        if mode not in {"fresh", "restored"}:
            raise ValueError("public checkpoint receipt recovery mode is invalid")
        predecessor = (
            None if predecessor_checkpoint_fingerprint is None else
            _identifier(
                predecessor_checkpoint_fingerprint,
                "predecessor checkpoint fingerprint",
            )
        )
        if (mode == "fresh") != (predecessor is None):
            raise ValueError(
                "public checkpoint receipt predecessor lineage is invalid")
        material = {
            "version": PUBLIC_SESSION_CHECKPOINT_RECEIPT_VERSION,
            "session_id": _identifier(session_id, "session identity"),
            "runtime_identity": _identifier(
                runtime_identity, "runtime identity"),
            "request_fingerprint": _identifier(
                request_fingerprint, "request fingerprint"),
            "result_fingerprint": _identifier(
                result_fingerprint, "result fingerprint"),
            "runtime_snapshot_fingerprint": _identifier(
                runtime_snapshot_fingerprint,
                "runtime snapshot fingerprint",
            ),
            "checkpoint_content_fingerprint": _identifier(
                checkpoint_content_fingerprint,
                "checkpoint content fingerprint",
            ),
            "next_benchmark_ordinal": next_benchmark_ordinal,
            "last_event_id": _identifier(last_event_id, "event identity"),
            "recovery_mode": mode,
            "predecessor_checkpoint_fingerprint": predecessor,
        }
        return cls(
            **material, fingerprint=model_data_fingerprint(material))

    def to_record(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in (
                "version", "session_id", "runtime_identity",
                "request_fingerprint", "result_fingerprint",
                "runtime_snapshot_fingerprint", "checkpoint_content_fingerprint",
                "next_benchmark_ordinal",
                "last_event_id", "recovery_mode",
                "predecessor_checkpoint_fingerprint", "fingerprint",
            )
        }


def verify_public_session_checkpoint_receipt_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise ValueError("public checkpoint receipt fields are invalid")
    record = thaw_model_data(value)
    expected = PublicSessionCheckpointReceipt.create(**{
        field: record[field] for field in _FIELDS if field not in {
            "version", "fingerprint",
        }
    })
    if record.get("version") != expected.version or record != expected.to_record():
        raise ValueError("public checkpoint receipt integrity is invalid")
    return record


__all__ = (
    "PUBLIC_SESSION_CHECKPOINT_RECEIPT_VERSION",
    "PublicSessionCheckpointReceipt",
    "verify_public_session_checkpoint_receipt_record",
)
