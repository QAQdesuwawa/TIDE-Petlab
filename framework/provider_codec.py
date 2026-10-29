"""Package-neutral receipt for parsing an opaque provider response.

Transport success and package codec success are distinct.  This module knows
neither provider text nor model fields; it only freezes the response binding,
codec contract, parsed payload fingerprint, or content-free failure.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import (
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.provider_host import ProviderResponse


PROVIDER_CODEC_VERSION = "framework-provider-codec-v1"


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be non-empty")
    return result


class ProviderCodecParseFailureSignal(Exception):
    def __init__(self, *, failure_kind: str, failure_source_ref: str):
        self.failure_kind = _identifier(failure_kind, "failure_kind")
        self.failure_source_ref = _identifier(
            failure_source_ref, "failure_source_ref")
        super().__init__(self.failure_kind)


@dataclass(frozen=True)
class ProviderCodecParseReceipt:
    version: str
    parse_ref: str
    status: str
    provider_response_fingerprint: str
    codec_contract: str
    parsed_payload: Mapping[str, Any] | None
    failure_kind: str | None
    failure_source_ref: str | None
    fingerprint: str

    def __post_init__(self) -> None:
        if self.parsed_payload is not None:
            object.__setattr__(self, "parsed_payload", freeze_model_payload(
                self.parsed_payload))

    @classmethod
    def create_success(
        cls, *, response: ProviderResponse, codec_contract: str,
        parsed_payload: Mapping[str, Any],
    ) -> "ProviderCodecParseReceipt":
        if not isinstance(response, ProviderResponse):
            raise TypeError("codec parse requires ProviderResponse")
        if not isinstance(parsed_payload, Mapping) or not parsed_payload:
            raise ValueError("parsed payload must be non-empty structured data")
        payload = freeze_model_payload(parsed_payload)
        return cls._create(
            status="parsed", response=response,
            codec_contract=codec_contract, parsed_payload=payload,
            failure_kind=None, failure_source_ref=None)

    @classmethod
    def create_failure(
        cls, *, response: ProviderResponse, codec_contract: str,
        failure_kind: str, failure_source_ref: str,
    ) -> "ProviderCodecParseReceipt":
        if not isinstance(response, ProviderResponse):
            raise TypeError("codec parse requires ProviderResponse")
        return cls._create(
            status="failed", response=response,
            codec_contract=codec_contract, parsed_payload=None,
            failure_kind=_identifier(failure_kind, "failure_kind"),
            failure_source_ref=_identifier(
                failure_source_ref, "failure_source_ref"))

    @classmethod
    def _create(
        cls, *, status: str, response: ProviderResponse,
        codec_contract: str, parsed_payload: Mapping[str, Any] | None,
        failure_kind: str | None, failure_source_ref: str | None,
    ) -> "ProviderCodecParseReceipt":
        material = {
            "version": PROVIDER_CODEC_VERSION,
            "status": status,
            "provider_response_fingerprint": response.fingerprint,
            "codec_contract": _identifier(codec_contract, "codec_contract"),
            "parsed_payload": (
                None if parsed_payload is None
                else thaw_model_data(parsed_payload)),
            "failure_kind": failure_kind,
            "failure_source_ref": failure_source_ref,
        }
        parse_ref = "provider-codec-parse:" + model_data_fingerprint(material)
        signed = dict(material, parse_ref=parse_ref)
        return cls(
            **{**signed, "parsed_payload": parsed_payload},
            fingerprint=model_data_fingerprint(signed))

    def verify_integrity(self, *, response: ProviderResponse) -> None:
        if self.status == "parsed":
            expected = self.create_success(
                response=response, codec_contract=self.codec_contract,
                parsed_payload=self.parsed_payload)
        elif self.status == "failed":
            expected = self.create_failure(
                response=response, codec_contract=self.codec_contract,
                failure_kind=self.failure_kind,
                failure_source_ref=self.failure_source_ref)
        else:
            raise ValueError("codec parse status is invalid")
        if expected != self:
            raise ValueError("codec parse receipt integrity is invalid")

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "parse_ref": self.parse_ref,
            "status": self.status,
            "provider_response_fingerprint": (
                self.provider_response_fingerprint),
            "codec_contract": self.codec_contract,
            "parsed_payload": (
                None if self.parsed_payload is None
                else thaw_model_data(self.parsed_payload)),
            "failure_kind": self.failure_kind,
            "failure_source_ref": self.failure_source_ref,
            "fingerprint": self.fingerprint,
        }


def execute_provider_codec_parse(
    *, response: ProviderResponse, codec_contract: str,
    parse: Callable[[Mapping[str, Any]], Mapping[str, Any]],
) -> ProviderCodecParseReceipt:
    if not callable(parse):
        raise TypeError("provider codec parser must be callable")
    try:
        parsed = parse(response.response_payload)
    except ProviderCodecParseFailureSignal as exc:
        result = ProviderCodecParseReceipt.create_failure(
            response=response, codec_contract=codec_contract,
            failure_kind=exc.failure_kind,
            failure_source_ref=exc.failure_source_ref)
    else:
        result = ProviderCodecParseReceipt.create_success(
            response=response, codec_contract=codec_contract,
            parsed_payload=parsed)
    result.verify_integrity(response=response)
    return result


__all__ = (
    "PROVIDER_CODEC_VERSION",
    "ProviderCodecParseFailureSignal",
    "ProviderCodecParseReceipt",
    "execute_provider_codec_parse",
)
