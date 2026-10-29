"""CX six/seven conversation experiment projection compatibility adapter."""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from framework.model_api import freeze_model_payload, model_data_fingerprint, thaw_model_data
from ops.product_required_reply_host import ProductRequiredReplyResult

PUBLIC_EXPERIMENT_EXCHANGE_VERSION = "tide-public-experiment-exchange-v1"


def _verified_receipt(value: Mapping[str, Any], label: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be receipt data")
    record = thaw_model_data(value)
    material = dict(record)
    fingerprint = material.pop("fingerprint", None)
    if not isinstance(fingerprint, str) or not fingerprint:
        raise ValueError(f"{label} has no fingerprint")
    if model_data_fingerprint(material) != fingerprint:
        raise ValueError(f"{label} fingerprint is invalid")
    return record


def _exchange_material(*, version, committed_output, runtime_receipts,
                       action_receipt, route_receipt):
    return {
        "version": version,
        "committed_output": thaw_model_data(committed_output),
        "runtime_receipts": thaw_model_data(runtime_receipts),
        "action_receipt": thaw_model_data(action_receipt),
        "route_receipt": thaw_model_data(route_receipt),
    }


@dataclass(frozen=True)
class PublicExperimentExchange:
    version: str
    committed_output: Mapping[str, Any]
    runtime_receipts: Mapping[str, Mapping[str, Any]]
    action_receipt: Mapping[str, Any]
    route_receipt: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        for field in ("committed_output", "runtime_receipts", "action_receipt", "route_receipt"):
            object.__setattr__(self, field, freeze_model_payload(getattr(self, field)))

    def verify_integrity(self) -> None:
        if self.version != PUBLIC_EXPERIMENT_EXCHANGE_VERSION:
            raise ValueError("public experiment exchange version is invalid")
        runtime_receipts = thaw_model_data(self.runtime_receipts)
        if set(runtime_receipts) != {"input", "output"}:
            raise ValueError("public experiment exchange requires input and output receipts")
        input_receipt = _verified_receipt(runtime_receipts["input"], "input runtime lifecycle receipt")
        output_receipt = _verified_receipt(runtime_receipts["output"], "output runtime lifecycle receipt")
        action_receipt = _verified_receipt(self.action_receipt, "action receipt")
        route_receipt = _verified_receipt(self.route_receipt, "route receipt")
        output = thaw_model_data(self.committed_output)
        checks = (
            output.get("kind") == "message",
            bool(str(output.get("text") or "").strip()),
            action_receipt.get("version") == "runtime-product-action-commit-v1",
            action_receipt.get("status") == "canonical_source_committed",
            input_receipt.get("version") == "runtime-product-fact-lifecycle-v1",
            output_receipt.get("version") == "runtime-product-fact-lifecycle-v1",
            route_receipt.get("version") == "runtime-product-output-route-v1",
            output.get("conversation_id") == action_receipt.get("conversation_id"),
            output.get("event_id") == action_receipt.get("event_id"),
            output.get("sender_action_id") == action_receipt.get("action_id"),
            input_receipt.get("event_id") == action_receipt.get("source_event_id"),
            output_receipt.get("event_id") == output.get("event_id"),
            output_receipt.get("action_id") == output.get("sender_action_id"),
            output_receipt.get("conversation_id") == output.get("conversation_id"),
            route_receipt.get("event_id") == output.get("event_id"),
            route_receipt.get("action_id") == output.get("sender_action_id"),
            route_receipt.get("conversation_id") == output.get("conversation_id"),
            route_receipt.get("action_receipt_fingerprint") == action_receipt.get("fingerprint"),
            route_receipt.get("lifecycle_receipt_fingerprint") == model_data_fingerprint(output_receipt),
        )
        if not all(checks):
            raise ValueError("public experiment exchange lineage is inconsistent")
        material = _exchange_material(
            version=self.version, committed_output=self.committed_output,
            runtime_receipts=self.runtime_receipts,
            action_receipt=self.action_receipt, route_receipt=self.route_receipt)
        if model_data_fingerprint(material) != self.fingerprint:
            raise ValueError("public experiment exchange fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return dict(_exchange_material(
            version=self.version, committed_output=self.committed_output,
            runtime_receipts=self.runtime_receipts,
            action_receipt=self.action_receipt, route_receipt=self.route_receipt),
            fingerprint=self.fingerprint)


def project_required_reply_result(result: ProductRequiredReplyResult) -> PublicExperimentExchange:
    if not isinstance(result, ProductRequiredReplyResult):
        raise TypeError("public experiment projection requires ProductRequiredReplyResult")
    material = _exchange_material(
        version=PUBLIC_EXPERIMENT_EXCHANGE_VERSION,
        committed_output=result.committed_action.event.as_record(),
        runtime_receipts={
            "input": thaw_model_data(result.lifecycle.receipt),
            "output": thaw_model_data(result.output_lifecycle.lifecycle.receipt),
        },
        action_receipt=thaw_model_data(result.committed_action.receipt),
        route_receipt=thaw_model_data(result.output_lifecycle.route.receipt),
    )
    exchange = PublicExperimentExchange(
        **material, fingerprint=model_data_fingerprint(material))
    exchange.verify_integrity()
    return exchange


class ProductExperimentInterface:
    def __init__(self, submit_required_reply: Callable[..., object]) -> None:
        if not callable(submit_required_reply):
            raise TypeError("public experiment interface requires a submitter")
        self._submit_required_reply = submit_required_reply

    def submit_external_event(self, text: str, *, coordinate: int) -> PublicExperimentExchange:
        if isinstance(coordinate, bool) or not isinstance(coordinate, int) or coordinate < 0:
            raise ValueError("public experiment coordinate must be non-negative")
        return project_required_reply_result(
            self._submit_required_reply(text, now_ms=coordinate))


__all__ = (
    "PUBLIC_EXPERIMENT_EXCHANGE_VERSION", "ProductExperimentInterface",
    "PublicExperimentExchange", "project_required_reply_result",
)
