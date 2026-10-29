"""Dimension-agnostic, read-only model diagnostic descriptors and exports."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from framework.model_api import (
    ModelInvocationContext,
    ModelStageExecution,
    ModelStageReceipt,
    freeze_model_payload,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import same_model_fact_identity
from framework.model_api.invocation_v2 import (
    ModelInvocationContextV2,
    ModelStageReceiptV2,
)
from framework.runtime_variant import RuntimeVariantBinding


DIAGNOSTIC_DESCRIPTOR_SCHEMA_VERSION = "model-diagnostic-descriptor-v1"
DIAGNOSTIC_SNAPSHOT_VERSION = "diagnostic-snapshot-export-v1"


def _identifier(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"{field} must be a non-empty identifier")
    return normalized


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a finite number")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be a finite number") from exc
    if not math.isfinite(result):
        raise ValueError(f"{field} must be a finite number")
    return result


def _same_context(
    receipt: ModelStageReceipt | ModelStageReceiptV2,
    context: ModelInvocationContext | ModelInvocationContextV2,
) -> bool:
    return same_model_fact_identity(context, receipt)


@dataclass(frozen=True)
class DiagnosticMetric:
    metric_id: str
    label: str
    source_product_id: str
    source_path: tuple[str, ...]
    display_low: float
    display_high: float
    precision: int

    def to_record(self) -> dict[str, Any]:
        return {
            "metric_id": self.metric_id,
            "label": self.label,
            "source": {
                "product_id": self.source_product_id,
                "path": list(self.source_path),
            },
            "display_domain": {
                "low": self.display_low, "high": self.display_high},
            "precision": self.precision,
        }


@dataclass(frozen=True)
class DiagnosticPanel:
    panel_id: str
    label: str
    chart: str
    series: tuple[DiagnosticMetric, ...]

    def to_record(self) -> dict[str, Any]:
        return {
            "panel_id": self.panel_id,
            "label": self.label,
            "chart": self.chart,
            "series": [item.to_record() for item in self.series],
        }


@dataclass(frozen=True)
class DiagnosticDescriptor:
    schema_version: str
    version: str
    authority: str
    panels: tuple[DiagnosticPanel, ...]
    stage_receipt: ModelStageReceipt | ModelStageReceiptV2
    fingerprint: str

    def _material(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "version": self.version,
            "authority": self.authority,
            "panels": [item.to_record() for item in self.panels],
            "stage_receipt_fingerprint": model_data_fingerprint(
                self.stage_receipt.to_record()),
        }

    def verify_integrity(self) -> None:
        if self.schema_version != DIAGNOSTIC_DESCRIPTOR_SCHEMA_VERSION:
            raise ValueError("diagnostic descriptor schema version is invalid")
        if self.authority != "read_only":
            raise ValueError("diagnostic descriptor authority must be read_only")
        if model_data_fingerprint(self._material()) != self.fingerprint:
            raise ValueError("diagnostic descriptor fingerprint is invalid")


@dataclass(frozen=True)
class CommittedDiagnosticSource:
    source_id: str
    payload: Mapping[str, Any]
    source_receipt_fingerprint: str
    fingerprint: str

    @classmethod
    def create(
        cls, *, source_id: str, payload: Mapping[str, Any],
        source_receipt_fingerprint: str,
    ) -> "CommittedDiagnosticSource":
        normalized_id = _identifier(source_id, "diagnostic source_id")
        receipt = _identifier(
            source_receipt_fingerprint, "source_receipt_fingerprint")
        frozen = freeze_model_payload(payload)
        material = {
            "source_id": normalized_id,
            "payload_fingerprint": model_data_fingerprint(frozen),
            "source_receipt_fingerprint": receipt,
        }
        return cls(
            source_id=normalized_id,
            payload=frozen,
            source_receipt_fingerprint=receipt,
            fingerprint=model_data_fingerprint(material),
        )

    def verify_integrity(self) -> None:
        material = {
            "source_id": _identifier(self.source_id, "diagnostic source_id"),
            "payload_fingerprint": model_data_fingerprint(self.payload),
            "source_receipt_fingerprint": _identifier(
                self.source_receipt_fingerprint,
                "source_receipt_fingerprint"),
        }
        if model_data_fingerprint(material) != self.fingerprint:
            raise ValueError("diagnostic source integrity fingerprint is invalid")


@dataclass(frozen=True)
class DiagnosticSnapshotExport:
    snapshot: Mapping[str, Any]
    receipt: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "snapshot", freeze_model_payload(self.snapshot))
        object.__setattr__(
            self, "receipt", MappingProxyType(dict(self.receipt)))


def read_diagnostic_descriptor(
    execution: ModelStageExecution, *,
    expected_context: ModelInvocationContext | ModelInvocationContextV2,
) -> DiagnosticDescriptor:
    if not isinstance(execution, ModelStageExecution):
        raise ValueError("diagnostic descriptor requires a stage execution")
    if not isinstance(
        expected_context, (ModelInvocationContext, ModelInvocationContextV2),
    ):
        raise ValueError("expected_context must be a model invocation context")
    receipt = execution.receipt
    if receipt.stage != "diagnostic_descriptor":
        raise ValueError("diagnostic descriptor requires its declared stage")
    if not _same_context(receipt, expected_context):
        raise ValueError("diagnostic descriptor receipt context is invalid")
    if receipt.output_fingerprint != model_data_fingerprint(execution.payload):
        raise ValueError("diagnostic descriptor payload differs from receipt")
    if set(execution.payload) != {"diagnostic_descriptor"}:
        raise ValueError("diagnostic descriptor stage output is invalid")
    raw = execution.payload["diagnostic_descriptor"]
    if not isinstance(raw, Mapping) or set(raw) != {
        "schema_version", "version", "authority", "panels",
    }:
        raise ValueError("diagnostic descriptor fields are invalid")
    if raw.get("schema_version") != DIAGNOSTIC_DESCRIPTOR_SCHEMA_VERSION:
        raise ValueError("diagnostic descriptor schema version is invalid")
    if raw.get("authority") != "read_only":
        raise ValueError("diagnostic descriptor authority must be read_only")
    version = _identifier(raw.get("version"), "diagnostic version")
    raw_panels = raw.get("panels")
    if isinstance(raw_panels, (str, bytes)) or not isinstance(
            raw_panels, Sequence):
        raise ValueError("diagnostic panels must be a sequence")
    panels = []
    panel_ids = set()
    metric_ids = set()
    for raw_panel in raw_panels:
        if not isinstance(raw_panel, Mapping) or set(raw_panel) != {
            "panel_id", "label", "chart", "series",
        }:
            raise ValueError("diagnostic panel fields are invalid")
        panel_id = _identifier(raw_panel.get("panel_id"), "panel_id")
        if panel_id in panel_ids:
            raise ValueError("diagnostic panel IDs cannot repeat")
        panel_ids.add(panel_id)
        raw_series = raw_panel.get("series")
        if isinstance(raw_series, (str, bytes)) or not isinstance(
                raw_series, Sequence) or not raw_series:
            raise ValueError("diagnostic panel series must be non-empty")
        series = []
        for raw_metric in raw_series:
            if not isinstance(raw_metric, Mapping) or set(raw_metric) != {
                "metric_id", "label", "source", "display_domain", "precision",
            }:
                raise ValueError("diagnostic metric fields are invalid")
            metric_id = _identifier(raw_metric.get("metric_id"), "metric_id")
            if metric_id in metric_ids:
                raise ValueError("diagnostic metric IDs cannot repeat")
            metric_ids.add(metric_id)
            source = raw_metric.get("source")
            if not isinstance(source, Mapping) or set(source) != {
                    "product_id", "path"}:
                raise ValueError("diagnostic metric source is invalid")
            path = source.get("path")
            if isinstance(path, (str, bytes)) or not isinstance(
                    path, Sequence) or not path:
                raise ValueError("diagnostic source path must be non-empty")
            normalized_path = tuple(
                _identifier(item, "diagnostic source path") for item in path)
            domain = raw_metric.get("display_domain")
            if not isinstance(domain, Mapping) or set(domain) != {"low", "high"}:
                raise ValueError("diagnostic display domain is invalid")
            low = _finite(domain.get("low"), "display domain low")
            high = _finite(domain.get("high"), "display domain high")
            if not low < high:
                raise ValueError("diagnostic display domain must increase")
            precision = raw_metric.get("precision")
            if isinstance(precision, bool) or not isinstance(precision, int):
                raise ValueError("diagnostic precision must be an integer")
            if not 0 <= precision <= 12:
                raise ValueError("diagnostic precision is outside supported range")
            series.append(DiagnosticMetric(
                metric_id=metric_id,
                label=_identifier(raw_metric.get("label"), "metric label"),
                source_product_id=_identifier(
                    source.get("product_id"), "source product_id"),
                source_path=normalized_path,
                display_low=low,
                display_high=high,
                precision=precision,
            ))
        panels.append(DiagnosticPanel(
            panel_id=panel_id,
            label=_identifier(raw_panel.get("label"), "panel label"),
            chart=_identifier(raw_panel.get("chart"), "panel chart"),
            series=tuple(series),
        ))
    material = {
        "schema_version": DIAGNOSTIC_DESCRIPTOR_SCHEMA_VERSION,
        "version": version,
        "authority": "read_only",
        "panels": [item.to_record() for item in panels],
        "stage_receipt_fingerprint": model_data_fingerprint(
            receipt.to_record()),
    }
    descriptor = DiagnosticDescriptor(
        schema_version=DIAGNOSTIC_DESCRIPTOR_SCHEMA_VERSION,
        version=version,
        authority="read_only",
        panels=tuple(panels),
        stage_receipt=receipt,
        fingerprint=model_data_fingerprint(material),
    )
    descriptor.verify_integrity()
    return descriptor


def _verify_variant(binding: RuntimeVariantBinding) -> None:
    if not isinstance(binding, RuntimeVariantBinding):
        raise ValueError("diagnostic export requires RuntimeVariantBinding")
    record = binding.to_record()
    fingerprint = record.pop("combination_fingerprint")
    if fingerprint != binding.fingerprint or (
            model_data_fingerprint(record) != fingerprint):
        raise ValueError("runtime variant fingerprint is invalid")


def _resolve(payload: Mapping[str, Any], path: tuple[str, ...]) -> float:
    current: object = payload
    for part in path:
        if not isinstance(current, Mapping) or part not in current:
            raise ValueError("diagnostic source path field is missing")
        current = current[part]
    return _finite(current, "diagnostic metric value")


def export_diagnostic_snapshot(
    *, descriptor: DiagnosticDescriptor,
    runtime_variant: RuntimeVariantBinding,
    sources: Mapping[str, CommittedDiagnosticSource],
) -> DiagnosticSnapshotExport:
    if not isinstance(descriptor, DiagnosticDescriptor):
        raise ValueError("diagnostic export requires DiagnosticDescriptor")
    descriptor.verify_integrity()
    _verify_variant(runtime_variant)
    if not isinstance(sources, Mapping):
        raise ValueError("diagnostic sources must be a mapping")
    normalized = {}
    for source_id, source in sources.items():
        key = _identifier(source_id, "diagnostic source key")
        if not isinstance(source, CommittedDiagnosticSource):
            raise ValueError("diagnostic source value is invalid")
        source.verify_integrity()
        if source.source_id != key:
            raise ValueError("diagnostic source key differs from source identity")
        normalized[key] = source
    required = {
        metric.source_product_id
        for panel in descriptor.panels for metric in panel.series
    }
    if set(normalized) != required:
        raise ValueError("diagnostic source set differs from descriptor")
    panels = []
    for panel in descriptor.panels:
        series = []
        for metric in panel.series:
            series.append({
                "metric_id": metric.metric_id,
                "label": metric.label,
                "value": _resolve(
                    normalized[metric.source_product_id].payload,
                    metric.source_path),
                "display_domain": {
                    "low": metric.display_low, "high": metric.display_high},
                "precision": metric.precision,
            })
        panels.append({
            "panel_id": panel.panel_id,
            "label": panel.label,
            "chart": panel.chart,
            "series": series,
        })
    snapshot = {
        "version": DIAGNOSTIC_SNAPSHOT_VERSION,
        "descriptor_version": descriptor.version,
        "panels": panels,
    }
    material = {
        "version": DIAGNOSTIC_SNAPSHOT_VERSION,
        "runtime_variant_fingerprint": runtime_variant.fingerprint,
        "descriptor_fingerprint": descriptor.fingerprint,
        "source_lineage": {
            source_id: {
                "source_receipt_fingerprint": source.source_receipt_fingerprint,
                "payload_fingerprint": model_data_fingerprint(source.payload),
                "source_fingerprint": source.fingerprint,
            }
            for source_id, source in sorted(normalized.items())
        },
        "snapshot_fingerprint": model_data_fingerprint(snapshot),
        "read_only": True,
        "state_mutated": False,
        "action_authorized": False,
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return DiagnosticSnapshotExport(snapshot=snapshot, receipt=receipt)


__all__ = (
    "CommittedDiagnosticSource",
    "DIAGNOSTIC_DESCRIPTOR_SCHEMA_VERSION",
    "DIAGNOSTIC_SNAPSHOT_VERSION",
    "DiagnosticDescriptor",
    "DiagnosticMetric",
    "DiagnosticPanel",
    "DiagnosticSnapshotExport",
    "export_diagnostic_snapshot",
    "read_diagnostic_descriptor",
)
