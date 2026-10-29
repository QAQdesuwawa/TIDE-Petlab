"""Paper-exact E2 treatment identities and the compliant runtime ablation.

Only the forward-historical-influence treatment remains a compliant TIDE
runtime.  The other two paper mechanisms are deliberately non-compliant
contract-negative controls; this module gives them identities but does not
misrepresent them as model packages that passed the public lifecycle.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import math
from types import MappingProxyType
from typing import Any

from framework.model_api import (
    InteractionModelManifest,
    model_data_fingerprint,
)
from framework.model_api.identity import context_fact_identity
from framework.runtime_variant import RuntimeVariantDefinition

from .full_v2_package import (
    CX_FULL_MODEL_CAPABILITIES_V2,
    FULL_PACKAGE_VERSION,
    CxSixSevenFullV2Package,
    _evaluation_time_ms,
)
from .interaction_geometry import ROOT_ORDER
from .memory_numeric_contracts import NeutralMemoryReadout
from .memory_influence_operators import CX_HISTORICAL_AGGREGATION_VERSION
from .v2_package import (
    PACKAGE_ID,
    _exact_payload,
    _identifier,
    _mapping,
)


E2_FORWARD_HISTORY_REPLACEMENT_PACKAGE_VERSION = (
    "2.4.0-alpha04-e2-ordinary-retrieval-summary"
)
E2_FORWARD_HISTORY_REPLACEMENT_INFLUENCE_VERSION = (
    "e2-ordinary-retrieval-summary-influence-v1"
)
E2_FORWARD_HISTORY_REPLACEMENT_AGGREGATE_VERSION = (
    "e2-ordinary-retrieval-summary-aggregate-v1"
)


@dataclass(frozen=True)
class E2TreatmentSpec:
    treatment_id: str
    paper_mechanism_id: str | None
    analysis_role: str
    compliant_tide_runtime: bool
    implementation_id: str
    replacement_semantics: str
    must_not_enter_primary_analysis: bool


_TREATMENTS = (
    E2TreatmentSpec(
        treatment_id="tide_full",
        paper_mechanism_id=None,
        analysis_role="full_reference",
        compliant_tide_runtime=True,
        implementation_id="tide.cx-six-seven.full.reference-v1",
        replacement_semantics="full_reference",
        must_not_enter_primary_analysis=False,
    ),
    E2TreatmentSpec(
        treatment_id="without_persistent_state_authority",
        paper_mechanism_id="persistent_state_authority",
        analysis_role="contract_negative_ablation_control",
        compliant_tide_runtime=False,
        implementation_id="tide.e2.negative.state-authority-removed-v1",
        replacement_semantics="model_reconstructed_state",
        must_not_enter_primary_analysis=True,
    ),
    E2TreatmentSpec(
        treatment_id="without_commit_gate",
        paper_mechanism_id="commit_gate",
        analysis_role="contract_negative_ablation_control",
        compliant_tide_runtime=False,
        implementation_id="tide.e2.negative.commit-gate-bypassed-v1",
        replacement_semantics="candidate_commit_boundary_weakened",
        must_not_enter_primary_analysis=True,
    ),
    E2TreatmentSpec(
        treatment_id="without_forward_historical_influence",
        paper_mechanism_id="forward_historical_influence",
        analysis_role="primary_runtime_ablation",
        compliant_tide_runtime=True,
        implementation_id="tide.cx-six-seven.e2.ordinary-retrieval-summary-v1",
        replacement_semantics="ordinary_retrieval_summary",
        must_not_enter_primary_analysis=False,
    ),
)


def e2_treatment_specs() -> Mapping[str, E2TreatmentSpec]:
    return MappingProxyType({item.treatment_id: item for item in _TREATMENTS})


def e2_runtime_variant_definition(treatment_id: str) -> RuntimeVariantDefinition:
    specs = e2_treatment_specs()
    normalized = _identifier(treatment_id, "E2 treatment ID")
    if normalized not in specs:
        raise ValueError(f"unknown E2 treatment: {normalized}")
    spec = specs[normalized]
    mechanisms = {
        "persistent_state_authority": "state-authority-v1",
        "commit_gate": "commit-gate-v1",
        "forward_historical_influence": "cx67-forward-history-v1",
    }
    if spec.paper_mechanism_id == "persistent_state_authority":
        mechanisms["persistent_state_authority"] = (
            "e2-negative-model-reconstructed-state-v1")
    elif spec.paper_mechanism_id == "commit_gate":
        mechanisms["commit_gate"] = "e2-negative-commit-gate-bypassed-v1"
    elif spec.paper_mechanism_id == "forward_historical_influence":
        mechanisms["forward_historical_influence"] = (
            E2_FORWARD_HISTORY_REPLACEMENT_INFLUENCE_VERSION)
    return RuntimeVariantDefinition.create(
        implementation_id=spec.implementation_id,
        kernel_implementation_id="tide.runtime-kernel.v1",
        mechanism_implementations=mechanisms,
        realizer_profile_id="cx-six-seven.reference.offline-v1",
        resource_policy={
            "semantic_control": "enabled",
            "readonly_facts": "enabled",
            "full_memory": "enabled",
        },
    )


def _root_vector(value: object, field: str) -> dict[str, float]:
    record = _mapping(value, field)
    if set(record) != set(ROOT_ORDER):
        raise ValueError(f"{field} must contain exactly the six root axes")
    result = {}
    for axis in ROOT_ORDER:
        try:
            item = float(record[axis])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{field}.{axis} must be numeric") from exc
        if not math.isfinite(item):
            raise ValueError(f"{field}.{axis} must be finite")
        result[axis] = item
    return result


class E2ForwardHistoryReplacementPackage(CxSixSevenFullV2Package):
    """Full package with only forward-history influence replaced.

    Recall, state authority, Commit, capabilities, schemas and parameter
    package stay fixed.  Selected records are reduced to one ordinary equal
    retrieval summary; no base forward-edge operator or recursive historical
    propagation is called.
    """

    def __init__(self, *, parameter_package: Mapping[str, Any]) -> None:
        super().__init__(parameter_package=parameter_package)
        full_manifest = self.manifest
        operator_versions = dict(full_manifest.operator_versions)
        operator_versions["read_historical_influence"] = (
            E2_FORWARD_HISTORY_REPLACEMENT_INFLUENCE_VERSION)
        operator_versions["aggregate_historical_influence"] = (
            E2_FORWARD_HISTORY_REPLACEMENT_AGGREGATE_VERSION)
        self.manifest = InteractionModelManifest.create(
            package_id=PACKAGE_ID,
            package_version=E2_FORWARD_HISTORY_REPLACEMENT_PACKAGE_VERSION,
            api_version=full_manifest.api_version,
            capability_profile=full_manifest.capability_profile,
            capabilities=CX_FULL_MODEL_CAPABILITIES_V2,
            constants_schema=full_manifest.constants_schema,
            parameters_schema=full_manifest.parameters_schema,
            state_schema=full_manifest.state_schema,
            measurement_schema=full_manifest.measurement_schema,
            shared_projection_schema=full_manifest.shared_projection_schema,
            progress_delta_schema=full_manifest.progress_delta_schema,
            parameter_package=full_manifest.parameter_package,
            operator_versions=operator_versions,
        )

    def read_historical_influence(self, *, payload, context):
        _exact_payload(
            payload,
            {
                "recalled_event_ids", "historical_readouts",
                "event_contribution", "parameters",
            },
            "read_historical_influence",
        )
        identity = context_fact_identity(context)
        event_ref = _identifier(identity.event_ref, "current event identity")
        evaluation_time = _evaluation_time_ms(context)
        raw_ids = payload["recalled_event_ids"]
        raw_history = payload["historical_readouts"]
        if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, Sequence):
            raise ValueError("recalled_event_ids must be a sequence")
        if isinstance(raw_history, (str, bytes)) or not isinstance(
            raw_history, Sequence
        ):
            raise ValueError("historical_readouts must be a sequence")
        recalled = tuple(_identifier(item, "recalled event ID") for item in raw_ids)
        if len(recalled) != len(set(recalled)):
            raise ValueError("recalled_event_ids cannot contain duplicates")
        readouts = tuple(
            NeutralMemoryReadout.from_record(
                _mapping(item, f"historical_readouts[{index}]"))
            for index, item in enumerate(raw_history)
        )
        by_id = {item.event.event_id: item for item in readouts}
        if len(by_id) != len(readouts) or not set(recalled).issubset(by_id):
            raise ValueError("historical_readouts do not cover exact recalled IDs")
        selected = tuple(by_id[event_id] for event_id in recalled)
        for item in selected:
            if item.event.committed_at_ms >= evaluation_time:
                raise ValueError("historical event must precede current event")
            if item.as_of_ms != evaluation_time:
                raise ValueError("historical readout evaluation time differs")

        divisor = float(len(selected)) if selected else 1.0
        influences = {}
        edges = []
        for item in selected:
            source_record = item.to_record()
            source_fingerprint = model_data_fingerprint(source_record)
            influences[item.event.event_id] = {
                "retrieval_summary_share_vector": {
                    axis: item.live_vector[axis] / divisor for axis in ROOT_ORDER
                },
                "source_readout_fingerprint": source_fingerprint,
                "replacement_semantics": "ordinary_retrieval_summary",
            }
            edges.append({
                "parent_event_id": item.event.event_id,
                "child_event_id": event_ref,
                "edge_evidence": {
                    "version": E2_FORWARD_HISTORY_REPLACEMENT_INFLUENCE_VERSION,
                    "source_readout_fingerprint": source_fingerprint,
                    "replacement_semantics": "ordinary_retrieval_summary",
                    "state_commit_authority": False,
                    "actual_delta_pending_aggregate": True,
                },
            })
        return {
            "historical_influence": {
                "version": E2_FORWARD_HISTORY_REPLACEMENT_INFLUENCE_VERSION,
                "recalled_event_ids": list(recalled),
                "event_influences": influences,
                "later_edges": edges,
            },
        }

    def aggregate_historical_influence(self, *, payload, context):
        _exact_payload(
            payload,
            {
                "recalled_event_ids", "event_influences",
                "source_influence_output_fingerprint",
            },
            "aggregate_historical_influence",
        )
        identity = context_fact_identity(context)
        _identifier(identity.event_ref, "current event identity")
        raw_ids = payload["recalled_event_ids"]
        if isinstance(raw_ids, (str, bytes)) or not isinstance(raw_ids, Sequence):
            raise ValueError("recalled_event_ids must be a sequence")
        recalled = tuple(_identifier(item, "recalled event ID") for item in raw_ids)
        raw_influences = _mapping(payload["event_influences"], "event_influences")
        if set(raw_influences) != set(recalled):
            raise ValueError("event influences differ from recalled event IDs")
        contributions = {}
        raw_sum = {axis: 0.0 for axis in ROOT_ORDER}
        for event_id in recalled:
            item = _mapping(raw_influences[event_id], f"event_influences.{event_id}")
            if set(item) != {
                "retrieval_summary_share_vector", "source_readout_fingerprint",
                "replacement_semantics",
            }:
                raise ValueError("retrieval summary influence fields are invalid")
            if item["replacement_semantics"] != "ordinary_retrieval_summary":
                raise ValueError("retrieval summary semantics are invalid")
            _identifier(
                item["source_readout_fingerprint"],
                f"event_influences.{event_id}.source_readout_fingerprint",
            )
            vector = _root_vector(
                item["retrieval_summary_share_vector"],
                f"event_influences.{event_id}.retrieval_summary_share_vector",
            )
            contributions[event_id] = vector
            for axis in ROOT_ORDER:
                raw_sum[axis] += vector[axis]
        raw_norm = math.sqrt(sum(value * value for value in raw_sum.values()))
        radius = 1.0
        factor = max(1.0, raw_norm / radius)
        projected = {
            event_id: {
                axis: vector[axis] / factor for axis in ROOT_ORDER
            }
            for event_id, vector in contributions.items()
        }
        delta = {axis: raw_sum[axis] / factor for axis in ROOT_ORDER}
        aggregate = {
            "raw_sum_vector": raw_sum,
            "raw_norm": raw_norm,
            "fusion_radius": radius,
            "projection_factor": factor,
            "delta_x_memory": delta,
            "event_projected_contributions": projected,
            "per_event_rule": "settled_live_influence_identity",
            "global_projection_rule": "single_global_radial_projection",
            "global_projection_applied_once": True,
            "candidate_specific_rescale_performed": False,
            "state_commit_authority": False,
            "replacement_semantics": "ordinary_retrieval_summary",
        }
        return {
            "historical_aggregate": {
                "version": E2_FORWARD_HISTORY_REPLACEMENT_AGGREGATE_VERSION,
                "recalled_event_ids": list(recalled),
                "source_influence_output_fingerprint": _identifier(
                    payload["source_influence_output_fingerprint"],
                    "source_influence_output_fingerprint",
                ),
                "aggregate": aggregate,
            },
        }

    @staticmethod
    def _historical_aggregate(value: object) -> tuple[dict[str, Any], str]:
        record = _mapping(value, "historical_aggregate")
        if record.get("version") != E2_FORWARD_HISTORY_REPLACEMENT_AGGREGATE_VERSION:
            raise ValueError("historical aggregate version is unsupported")
        aggregate = _mapping(record.get("aggregate"), "historical_aggregate.aggregate")
        if aggregate.get("replacement_semantics") != "ordinary_retrieval_summary":
            raise ValueError("historical aggregate replacement semantics are invalid")
        base_record = dict(record)
        base_record["version"] = CX_HISTORICAL_AGGREGATION_VERSION
        base_aggregate = dict(aggregate)
        base_aggregate.pop("replacement_semantics", None)
        base_record["aggregate"] = base_aggregate
        CxSixSevenFullV2Package._historical_aggregate(base_record)
        normalized = dict(record)
        normalized["aggregate"] = dict(aggregate)
        return normalized, model_data_fingerprint(normalized)


__all__ = (
    "E2_FORWARD_HISTORY_REPLACEMENT_AGGREGATE_VERSION",
    "E2_FORWARD_HISTORY_REPLACEMENT_INFLUENCE_VERSION",
    "E2_FORWARD_HISTORY_REPLACEMENT_PACKAGE_VERSION",
    "E2ForwardHistoryReplacementPackage",
    "E2TreatmentSpec",
    "e2_runtime_variant_definition",
    "e2_treatment_specs",
)
