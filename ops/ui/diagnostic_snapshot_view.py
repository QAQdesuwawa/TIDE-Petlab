"""Pure presentation projection for a verified diagnostic snapshot."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math

from framework.diagnostics import DiagnosticSnapshotExport
from framework.model_api import model_data_fingerprint


@dataclass(frozen=True)
class DiagnosticMetricView:
    metric_id: str
    label: str
    value: float
    display_text: str
    display_low: float
    display_high: float
    position: float
    precision: int


@dataclass(frozen=True)
class DiagnosticPanelView:
    panel_id: str
    label: str
    chart: str
    series: tuple[DiagnosticMetricView, ...]


@dataclass(frozen=True)
class DiagnosticSnapshotView:
    available: bool
    status: str
    snapshot_version: str | None
    descriptor_version: str | None
    panels: tuple[DiagnosticPanelView, ...]
    snapshot_fingerprint: str | None


def _identifier(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value.strip()


def _finite(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{label} must be numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def _verify_export(export: DiagnosticSnapshotExport) -> str:
    if not isinstance(export, DiagnosticSnapshotExport):
        raise ValueError("diagnostic view requires DiagnosticSnapshotExport")
    receipt = export.receipt
    required = {
        "version", "runtime_variant_fingerprint", "descriptor_fingerprint",
        "source_lineage", "snapshot_fingerprint", "read_only",
        "state_mutated", "action_authorized", "fingerprint",
    }
    if set(receipt) != required:
        raise ValueError("diagnostic export receipt fields are invalid")
    material = dict(receipt)
    fingerprint = _identifier(
        material.pop("fingerprint", None), "diagnostic export fingerprint")
    if model_data_fingerprint(material) != fingerprint:
        raise ValueError("diagnostic export receipt fingerprint is invalid")
    if (
        receipt.get("read_only") is not True
        or receipt.get("state_mutated") is not False
        or receipt.get("action_authorized") is not False
    ):
        raise ValueError("diagnostic export does not have read-only authority")
    snapshot_fingerprint = _identifier(
        receipt.get("snapshot_fingerprint"), "snapshot fingerprint")
    if model_data_fingerprint(export.snapshot) != snapshot_fingerprint:
        raise ValueError("diagnostic snapshot fingerprint is invalid")
    return snapshot_fingerprint


def _compile_metric(raw: object) -> DiagnosticMetricView:
    if not isinstance(raw, Mapping) or set(raw) != {
        "metric_id", "label", "value", "display_domain", "precision",
    }:
        raise ValueError("diagnostic metric fields are invalid")
    domain = raw.get("display_domain")
    if not isinstance(domain, Mapping) or set(domain) != {"low", "high"}:
        raise ValueError("diagnostic display domain is invalid")
    low = _finite(domain.get("low"), "diagnostic display low")
    high = _finite(domain.get("high"), "diagnostic display high")
    if not low < high:
        raise ValueError("diagnostic display domain must increase")
    value = _finite(raw.get("value"), "diagnostic metric value")
    precision = raw.get("precision")
    if (
        isinstance(precision, bool)
        or not isinstance(precision, int)
        or not 0 <= precision <= 12
    ):
        raise ValueError("diagnostic precision is outside supported range")
    position = min(1.0, max(0.0, (value - low) / (high - low)))
    return DiagnosticMetricView(
        metric_id=_identifier(raw.get("metric_id"), "diagnostic metric ID"),
        label=_identifier(raw.get("label"), "diagnostic metric label"),
        value=value,
        display_text=f"{value:.{precision}f}",
        display_low=low,
        display_high=high,
        position=position,
        precision=precision,
    )


def compile_diagnostic_snapshot_view(
    export: DiagnosticSnapshotExport | None,
) -> DiagnosticSnapshotView:
    """Compile immutable display data without reading or changing runtime data."""
    if export is None:
        return DiagnosticSnapshotView(
            available=False,
            status="awaiting_committed_observation",
            snapshot_version=None,
            descriptor_version=None,
            panels=(),
            snapshot_fingerprint=None,
        )
    snapshot_fingerprint = _verify_export(export)
    snapshot = export.snapshot
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "version", "descriptor_version", "panels",
    }:
        raise ValueError("diagnostic snapshot fields are invalid")
    if export.receipt.get("version") != snapshot.get("version"):
        raise ValueError("diagnostic snapshot version differs from receipt")
    raw_panels = snapshot.get("panels")
    if isinstance(raw_panels, (str, bytes)) or not isinstance(
            raw_panels, Sequence):
        raise ValueError("diagnostic panels must be a sequence")
    panels = []
    panel_ids: set[str] = set()
    metric_ids: set[str] = set()
    for raw_panel in raw_panels:
        if not isinstance(raw_panel, Mapping) or set(raw_panel) != {
            "panel_id", "label", "chart", "series",
        }:
            raise ValueError("diagnostic panel fields are invalid")
        panel_id = _identifier(raw_panel.get("panel_id"), "diagnostic panel ID")
        if panel_id in panel_ids:
            raise ValueError("diagnostic panel IDs cannot repeat")
        panel_ids.add(panel_id)
        raw_series = raw_panel.get("series")
        if (
            isinstance(raw_series, (str, bytes))
            or not isinstance(raw_series, Sequence)
            or not raw_series
        ):
            raise ValueError("diagnostic panel series must be non-empty")
        series = tuple(_compile_metric(item) for item in raw_series)
        for metric in series:
            if metric.metric_id in metric_ids:
                raise ValueError("diagnostic metric IDs cannot repeat")
            metric_ids.add(metric.metric_id)
        panels.append(DiagnosticPanelView(
            panel_id=panel_id,
            label=_identifier(raw_panel.get("label"), "diagnostic panel label"),
            chart=_identifier(raw_panel.get("chart"), "diagnostic chart"),
            series=series,
        ))
    return DiagnosticSnapshotView(
        available=True,
        status="ready",
        snapshot_version=_identifier(
            snapshot.get("version"), "diagnostic snapshot version"),
        descriptor_version=_identifier(
            snapshot.get("descriptor_version"), "diagnostic descriptor version"),
        panels=tuple(panels),
        snapshot_fingerprint=snapshot_fingerprint,
    )


__all__ = (
    "DiagnosticMetricView",
    "DiagnosticPanelView",
    "DiagnosticSnapshotView",
    "compile_diagnostic_snapshot_view",
)
