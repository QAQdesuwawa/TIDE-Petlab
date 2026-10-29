"""Frozen JSON boundary for package-neutral cross-repository experiments."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)


BLACKBOX_REQUEST_VERSION = "tide-framework-blackbox-request-v1"
BLACKBOX_RESULT_VERSION = "tide-framework-blackbox-result-v1"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


@dataclass(frozen=True)
class FrameworkBlackboxRequest:
    version: str
    adapter_id: str
    case_id: str
    payload: Any
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "payload",
            freeze_model_payload({"value": self.payload})["value"],
        )

    @classmethod
    def create(
        cls, *, adapter_id: str, case_id: str, payload: Any,
    ) -> "FrameworkBlackboxRequest":
        material = {
            "version": BLACKBOX_REQUEST_VERSION,
            "adapter_id": _identifier(adapter_id, "adapter_id"),
            "case_id": _identifier(case_id, "case_id"),
            "payload": thaw_model_data(
                freeze_model_payload({"value": payload})["value"]
            ),
        }
        return cls(
            version=material["version"],
            adapter_id=material["adapter_id"],
            case_id=material["case_id"],
            payload=material["payload"],
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        expected = FrameworkBlackboxRequest.create(
            adapter_id=self.adapter_id,
            case_id=self.case_id,
            payload=self.payload,
        )
        if expected != self:
            raise ValueError("framework blackbox request fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "adapter_id": self.adapter_id,
            "case_id": self.case_id,
            "payload": thaw_model_data(self.payload),
            "fingerprint": self.fingerprint,
        }


def read_framework_blackbox_request(
    record: Mapping[str, Any],
) -> FrameworkBlackboxRequest:
    if not isinstance(record, Mapping):
        raise TypeError("framework blackbox request must be a mapping")
    expected_fields = {
        "version", "adapter_id", "case_id", "payload", "fingerprint",
    }
    if set(record) != expected_fields:
        raise ValueError("framework blackbox request fields are invalid")
    if record.get("version") != BLACKBOX_REQUEST_VERSION:
        raise ValueError("framework blackbox request version is unsupported")
    request = FrameworkBlackboxRequest(
        version=str(record["version"]),
        adapter_id=_identifier(record["adapter_id"], "adapter_id"),
        case_id=_identifier(record["case_id"], "case_id"),
        payload=record["payload"],
        fingerprint=_identifier(record["fingerprint"], "fingerprint"),
    )
    request.verify_integrity()
    return request


__all__ = (
    "BLACKBOX_REQUEST_VERSION",
    "BLACKBOX_RESULT_VERSION",
    "FrameworkBlackboxRequest",
    "read_framework_blackbox_request",
)
