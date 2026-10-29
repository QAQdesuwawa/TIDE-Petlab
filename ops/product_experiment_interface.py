"""Public, receipt-first projection of one completed product exchange.

The interface executes no model stage of its own.  It invokes the injected
required-reply host exactly once, then exposes the already committed output
and the receipts produced by that same formal lifecycle.  Experiments can
therefore inspect lineage without reaching into Runner, Store, or package
implementation details.
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


def _exchange_material(
    *,
    version: str,
    committed_output: Mapping[str, Any],
    runtime_receipts: Mapping[str, Mapping[str, Any]],
    action_receipt: Mapping[str, Any],
    route_receipt: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "version": version,
        "committed_output": thaw_model_data(committed_output),
        "runtime_receipts": thaw_model_data(runtime_receipts),
        "action_receipt": thaw_model_data(action_receipt),
        "route_receipt": thaw_model_data(route_receipt),
    }


@dataclass(frozen=True)
class PublicExperimentExchange:
    """Immutable white-box evidence for one completed public exchange."""

    version: str
    committed_output: Mapping[str, Any]
    runtime_receipts: Mapping[str, Mapping[str, Any]]
    action_receipt: Mapping[str, Any]
    route_receipt: Mapping[str, Any]
    fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "committed_output", freeze_model_payload(self.committed_output))
        object.__setattr__(
            self, "runtime_receipts", freeze_model_payload(self.runtime_receipts))
        object.__setattr__(
            self, "action_receipt", freeze_model_payload(self.action_receipt))
        object.__setattr__(
            self, "route_receipt", freeze_model_payload(self.route_receipt))

    def verify_integrity(self) -> None:
        if self.version != PUBLIC_EXPERIMENT_EXCHANGE_VERSION:
            raise ValueError("public experiment exchange version is invalid")
        runtime_receipts = thaw_model_data(self.runtime_receipts)
        if set(runtime_receipts) != {"input", "output"}:
            raise ValueError(
                "public experiment exchange requires input and output receipts")
        input_receipt = _verified_receipt(
            runtime_receipts["input"], "input runtime lifecycle receipt")
        output_receipt = _verified_receipt(
            runtime_receipts["output"], "output runtime lifecycle receipt")
        action_receipt = _verified_receipt(
            self.action_receipt, "action receipt")
        route_receipt = _verified_receipt(
            self.route_receipt, "route receipt")
        output = thaw_model_data(self.committed_output)

        if (
            output.get("kind") != "message"
            or not str(output.get("text") or "").strip()
            or action_receipt.get("version")
            != "runtime-product-action-commit-v1"
            or action_receipt.get("status") != "canonical_source_committed"
            or input_receipt.get("version")
            != "runtime-product-fact-lifecycle-v1"
            or output_receipt.get("version")
            != "runtime-product-fact-lifecycle-v1"
            or route_receipt.get("version")
            != "runtime-product-output-route-v1"
            or output.get("conversation_id")
            != action_receipt.get("conversation_id")
            or output.get("event_id") != action_receipt.get("event_id")
            or output.get("sender_action_id") != action_receipt.get("action_id")
            or input_receipt.get("event_id")
            != action_receipt.get("source_event_id")
            or output_receipt.get("event_id") != output.get("event_id")
            or output_receipt.get("action_id") != output.get("sender_action_id")
            or output_receipt.get("conversation_id")
            != output.get("conversation_id")
            or route_receipt.get("event_id") != output.get("event_id")
            or route_receipt.get("action_id") != output.get("sender_action_id")
            or route_receipt.get("conversation_id")
            != output.get("conversation_id")
            or route_receipt.get("action_receipt_fingerprint")
            != action_receipt.get("fingerprint")
            or route_receipt.get("lifecycle_receipt_fingerprint")
            != model_data_fingerprint(output_receipt)
        ):
            raise ValueError(
                "public experiment exchange lineage is inconsistent")

        material = _exchange_material(
            version=self.version,
            committed_output=self.committed_output,
            runtime_receipts=self.runtime_receipts,
            action_receipt=self.action_receipt,
            route_receipt=self.route_receipt,
        )
        if model_data_fingerprint(material) != self.fingerprint:
            raise ValueError("public experiment exchange fingerprint is invalid")

    def to_record(self) -> dict[str, Any]:
        self.verify_integrity()
        return dict(
            _exchange_material(
                version=self.version,
                committed_output=self.committed_output,
                runtime_receipts=self.runtime_receipts,
                action_receipt=self.action_receipt,
                route_receipt=self.route_receipt,
            ),
            fingerprint=self.fingerprint,
        )


def project_required_reply_result(
    result: ProductRequiredReplyResult,
) -> PublicExperimentExchange:
    """Project one already-completed formal result without re-executing it."""
    if not isinstance(result, ProductRequiredReplyResult):
        raise TypeError(
            "public experiment projection requires ProductRequiredReplyResult")
    committed_output = result.committed_action.event.as_record()
    runtime_receipts = {
        "input": thaw_model_data(result.lifecycle.receipt),
        "output": thaw_model_data(result.output_lifecycle.lifecycle.receipt),
    }
    action_receipt = thaw_model_data(result.committed_action.receipt)
    route_receipt = thaw_model_data(result.output_lifecycle.route.receipt)
    material = _exchange_material(
        version=PUBLIC_EXPERIMENT_EXCHANGE_VERSION,
        committed_output=committed_output,
        runtime_receipts=runtime_receipts,
        action_receipt=action_receipt,
        route_receipt=route_receipt,
    )
    exchange = PublicExperimentExchange(
        **material,
        fingerprint=model_data_fingerprint(material),
    )
    exchange.verify_integrity()
    return exchange


class ProductExperimentInterface:
    """Thin public caller over one injected formal required-reply submitter."""

    def __init__(self, submit_required_reply: Callable[..., object]) -> None:
        if not callable(submit_required_reply):
            raise TypeError("public experiment interface requires a submitter")
        self._submit_required_reply = submit_required_reply

    def submit_external_event(
        self, text: str, *, coordinate: int,
    ) -> PublicExperimentExchange:
        if (
            isinstance(coordinate, bool)
            or not isinstance(coordinate, int)
            or coordinate < 0
        ):
            raise ValueError("public experiment coordinate must be non-negative")
        result = self._submit_required_reply(text, now_ms=coordinate)
        return project_required_reply_result(result)


__all__ = (
    "PUBLIC_EXPERIMENT_EXCHANGE_VERSION",
    "ProductExperimentInterface",
    "PublicExperimentExchange",
    "project_required_reply_result",
)

# Compatibility authority moved to the explicit CX product adapter.  These
# late aliases preserve the historical import path while callers migrate.
from product_adapters.cx_six_seven_v1.experiment_interface import (  # noqa: E402
    PUBLIC_EXPERIMENT_EXCHANGE_VERSION,
    ProductExperimentInterface,
    PublicExperimentExchange,
    project_required_reply_result,
)
