"""Full v2 six/seven reference package with the split Memory pipeline."""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .continuous_root_projection import project_continuous_roots
from .query_budget import check as check_query_budget
from .memory_runtime_order import RUNTIME_MEMORY_ORDER_VERSION, order_record
from .interaction_geometry import ROOT_ORDER, relationship_transition
from .root_dynamics import RootEventResponse
from framework.model_api import (
    BASE_MODEL_CAPABILITIES_V2,
    InteractionModelManifest,
    ModelInvocationContext,
    model_data_fingerprint,
    thaw_model_data,
)
from framework.model_api.identity import context_fact_identity
from .memory_numeric_contracts import (
    MemoryEventRef,
    MemoryInfluenceEdge,
    MemoryInfluenceTransaction,
    NeutralMemoryReadout,
)
from .memory_projection import (
    CanonicalMemoryProjection,
    project_canonical_memory_event,
)
from .memory_forward_readout import forward_live_readout_ref
from .root_dynamics import ROOT_TIME_FLOW_VERSION
from .diagnostic_operators import CxSixSevenDiagnosticAdapter
from .memory_influence_operators import (
    CX_HISTORICAL_AGGREGATION_V1,
    CX_HISTORICAL_AGGREGATION_VERSION,
    CX_HISTORICAL_LIVE_INFLUENCE_VERSION,
    CxSixSevenHistoricalAggregateAdapter,
    CxSixSevenHistoricalInfluenceAdapter,
)
from .memory_recall_operators import (
    CX_ACTUAL_RECALL_DUAL_READOUT_V1,
    CX_ACTUAL_RECALL_DUAL_READOUT_VERSION,
    CxSixSevenMemoryRecallAdapter,
)
from .memory_live_operators import (
    CX_FORWARD_MEMORY_READOUT_V1,
    CX_FORWARD_MEMORY_READOUT_VERSION,
    CxSixSevenMemoryLiveAdapter,
)
from .v2_package import (
    PACKAGE_ID,
    CxSixSevenV2Package,
    _exact_payload,
    _identifier,
    _constant_binding,
    _mapping,
    _root_vector,
    _same_roots,
    _validated_contribution,
)


FULL_PACKAGE_VERSION = "2.4.0-alpha04-profile-memory-input"
CX_FULL_MODEL_CAPABILITIES_V2 = (
    *BASE_MODEL_CAPABILITIES_V2,
    "plan_observation",
    "advance_progress",
    "prepare_memory_inputs",
    "materialize_memory_event",
    "recall_history",
    "read_historical_influence",
    "aggregate_historical_influence",
    "project_semantic_resources",
    "diagnostic_descriptor",
)
STATE_SETTLEMENT_WITH_MEMORY_VERSION = "cx67-state-settlement-with-memory-v1"
CURRENT_EVENT_LIVE_READOUT_VERSION = "cx67-current-event-live-root-v1"
MEMORY_EVENT_MATERIALIZATION_VERSION = "cx67-memory-event-materialization-v1"
_CORE_PARAMETER_FIELDS = {
    "version", "initial_state_default", "event_response", "progress_flow",
    "shared_projection", "action_projection", "semantic_resources",
}
_FULL_PARAMETER_FIELDS = {
    *_CORE_PARAMETER_FIELDS, "actual_recall", "historical_aggregation",
    "memory_forward_readout",
}


def _signed_root_vector(value: object, field: str) -> dict[str, float]:
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


def _evaluation_time_ms(context: object) -> int:
    """Interpret the bound temporal coordinate inside this reference package."""
    legacy = getattr(context, "at_ms", None)
    if legacy is not None:
        value = legacy
    else:
        position = getattr(context, "temporal_position", None)
        value = None if position is None else position.coordinate
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(
            "six/seven full memory requires a non-negative integer "
            "temporal coordinate")
    return value


def _event_ref(context: object) -> str | None:
    return context_fact_identity(context).event_ref


def _legacy_memory_adapter_context(
    context: object, *, evaluation_time_ms: int,
) -> ModelInvocationContext:
    """Translate only at the reference package's legacy memory-operator seam."""
    identity = context_fact_identity(context)
    return ModelInvocationContext(
        session_id=identity.session_id,
        conversation_id=identity.scope_ref,
        participant_id=identity.subject_ref,
        at_ms=evaluation_time_ms,
        event_id=identity.event_ref,
        action_id=identity.action_ref,
        temporal_position=identity.temporal_position,
        runtime_ordinal=identity.runtime_ordinal,
        metadata=context.metadata,
    )


class CxSixSevenFullV2Package(CxSixSevenV2Package):
    """Reference Full profile; Memory math remains package-owned and pure."""

    def __init__(self, *, parameter_package: Mapping[str, Any]) -> None:
        params = _mapping(parameter_package, "parameter_package")
        if set(params) != _FULL_PARAMETER_FIELDS:
            raise ValueError(
                "full parameter_package fields do not match reference contract")
        if model_data_fingerprint(params["actual_recall"]) != (
                model_data_fingerprint(CX_ACTUAL_RECALL_DUAL_READOUT_V1)):
            raise ValueError(
                "actual-recall package does not match its registered version")
        if model_data_fingerprint(params["historical_aggregation"]) != (
                model_data_fingerprint(CX_HISTORICAL_AGGREGATION_V1)):
            raise ValueError(
                "historical-aggregation package does not match its registered version")
        if model_data_fingerprint(params["memory_forward_readout"]) != (
                model_data_fingerprint(CX_FORWARD_MEMORY_READOUT_V1)):
            raise ValueError(
                "memory-forward-readout package does not match its registered version")

        super().__init__(parameter_package={
            field: params[field] for field in _CORE_PARAMETER_FIELDS
        })
        standard_manifest = self.manifest
        operator_versions = dict(standard_manifest.operator_versions)
        operator_versions.update({
            "settle_state": STATE_SETTLEMENT_WITH_MEMORY_VERSION,
            "prepare_memory_inputs": CX_FORWARD_MEMORY_READOUT_VERSION,
            "materialize_memory_event": MEMORY_EVENT_MATERIALIZATION_VERSION,
            "recall_history": CX_ACTUAL_RECALL_DUAL_READOUT_VERSION,
            "read_historical_influence": CX_HISTORICAL_LIVE_INFLUENCE_VERSION,
            "aggregate_historical_influence": (
                CX_HISTORICAL_AGGREGATION_VERSION),
            "diagnostic_descriptor": "cx67-diagnostic-descriptor-v2",
        })
        self.manifest = InteractionModelManifest.create(
            package_id=PACKAGE_ID,
            package_version=FULL_PACKAGE_VERSION,
            api_version=standard_manifest.api_version,
            capability_profile="full",
            capabilities=CX_FULL_MODEL_CAPABILITIES_V2,
            constants_schema=standard_manifest.constants_schema,
            parameters_schema=standard_manifest.parameters_schema,
            state_schema=standard_manifest.state_schema,
            measurement_schema=standard_manifest.measurement_schema,
            shared_projection_schema=standard_manifest.shared_projection_schema,
            progress_delta_schema=standard_manifest.progress_delta_schema,
            parameter_package=params,
            operator_versions=operator_versions,
        )
        self._memory_recall = CxSixSevenMemoryRecallAdapter()
        self._memory_live = CxSixSevenMemoryLiveAdapter()
        self._memory_influence = CxSixSevenHistoricalInfluenceAdapter()
        self._memory_aggregate = CxSixSevenHistoricalAggregateAdapter()
        self._diagnostics = CxSixSevenDiagnosticAdapter()

    def prepare_memory_inputs(self, *, payload, context):
        """Build both Full inputs from one exact committed numeric history."""
        check_query_budget()
        legacy_fields = {
            "candidate_event_ids", "canonical_projections",
            "committed_lineage_transactions",
            "propagation_bindings_by_ref",
            "root_time_parameters_by_version",
        }
        journal_fields = {
            "candidate_event_ids", "candidate_nodes",
            "runtime_lineage_edges",
        }
        payload_fields = set(payload) if isinstance(payload, Mapping) else set()
        if payload_fields not in (legacy_fields, journal_fields):
            raise ValueError(
                "prepare_memory_inputs payload keys do not match contract")
        event_ref = _event_ref(context)
        if event_ref is None or context.temporal_position is None:
            raise ValueError(
                "memory input preparation requires current event and position")
        evaluation_time_ms = _evaluation_time_ms(context)
        raw_ids = payload["candidate_event_ids"]
        if (
            isinstance(raw_ids, (str, bytes))
            or not isinstance(raw_ids, (tuple, list))
        ):
            raise ValueError("candidate_event_ids must be a sequence")
        candidate_ids = tuple(
            _identifier(item, "candidate event ID") for item in raw_ids)
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("candidate_event_ids cannot contain duplicates")
        if event_ref in candidate_ids:
            raise ValueError("current event cannot be a memory candidate")

        runtime_lineage_edges = ()
        if payload_fields == legacy_fields:
            raw_projections = payload["canonical_projections"]
            raw_transactions = payload["committed_lineage_transactions"]
            propagation_bindings = payload["propagation_bindings_by_ref"]
            time_parameters = payload["root_time_parameters_by_version"]
        else:
            raw_nodes = payload["candidate_nodes"]
            if not isinstance(raw_nodes, (tuple, list)):
                raise ValueError("candidate_nodes must be a sequence")
            nodes_by_id = {}
            validated_projections = {}
            raw_projections = []
            time_parameters = {}
            for index, raw_node in enumerate(raw_nodes):
                check_query_budget()
                node = _mapping(raw_node, f"candidate_nodes[{index}]")
                if set(node) != {
                    "event_id", "accepted_runtime_ordinal", "projection",
                    "evolution_binding",
                }:
                    raise ValueError("candidate node fields are invalid")
                event_id = _identifier(node["event_id"], "candidate node event")
                if event_id in nodes_by_id:
                    raise ValueError("candidate nodes contain duplicate events")
                accepted_ordinal = node["accepted_runtime_ordinal"]
                if (
                    isinstance(accepted_ordinal, bool)
                    or not isinstance(accepted_ordinal, int)
                    or accepted_ordinal < 1
                    or context.runtime_ordinal is None
                    or accepted_ordinal > context.runtime_ordinal
                ):
                    raise ValueError(
                        "candidate node accepted runtime ordinal is invalid")
                projection_record = _mapping(
                    node["projection"], "candidate node projection")
                projection = CanonicalMemoryProjection.from_record(
                    projection_record)
                if projection.event.event_id != event_id:
                    raise ValueError("candidate node projection event is invalid")
                evolution = _mapping(
                    node["evolution_binding"], "candidate node evolution")
                evolution_material = dict(evolution)
                evolution_fingerprint = _identifier(
                    evolution_material.pop("fingerprint", None),
                    "candidate node evolution fingerprint",
                )
                if model_data_fingerprint(evolution_material) != (
                        evolution_fingerprint):
                    raise ValueError("candidate node evolution binding is invalid")
                root_time = _mapping(
                    evolution.get("root_time_parameters"),
                    "candidate node root time parameters",
                )
                root_time_version = _identifier(
                    root_time.get("version"), "root time parameter version")
                existing_time = time_parameters.get(root_time_version)
                if existing_time is not None and model_data_fingerprint(
                        existing_time) != model_data_fingerprint(root_time):
                    raise ValueError("root time parameter version conflicts")
                time_parameters[root_time_version] = root_time
                forward = _mapping(
                    evolution.get("forward_readout_parameters"),
                    "candidate node forward readout parameters",
                )
                if model_data_fingerprint(forward) != model_data_fingerprint(
                        self._package_params("memory_forward_readout")):
                    raise ValueError(
                        "candidate node forward readout version is unsupported")
                nodes_by_id[event_id] = node
                validated_projections[event_id] = projection
                raw_projections.append(projection_record)

            runtime_lineage_edges = payload["runtime_lineage_edges"]
            if not isinstance(runtime_lineage_edges, (tuple, list)):
                raise ValueError("runtime_lineage_edges must be a sequence")
            grouped_edges = {}
            lineage_meta = {}
            propagation_bindings = {}
            for index, raw_edge in enumerate(runtime_lineage_edges):
                check_query_budget()
                edge_record = _mapping(
                    raw_edge, f"runtime_lineage_edges[{index}]")
                if set(edge_record) != {
                    "lineage_fingerprint", "acceptance_fingerprint",
                    "aggregate_stage_output_fingerprint",
                    "parent_event_id", "child_event_id",
                    "projected_contribution", "edge_evidence",
                }:
                    raise ValueError("runtime lineage edge fields are invalid")
                parent = _identifier(
                    edge_record["parent_event_id"], "lineage parent")
                child = _identifier(
                    edge_record["child_event_id"], "lineage child")
                if parent not in nodes_by_id or child not in nodes_by_id:
                    raise ValueError(
                        "runtime lineage edge escapes authorized candidates")
                evidence = _mapping(
                    edge_record["edge_evidence"], "lineage edge evidence")
                evolution = _mapping(
                    evidence.get("evolution_binding"),
                    "lineage evolution binding",
                )
                evolution_fingerprint = _identifier(
                    evolution.get("fingerprint"),
                    "lineage evolution fingerprint",
                )
                evolution_material = dict(evolution)
                evolution_material.pop("fingerprint")
                if model_data_fingerprint(evolution_material) != (
                        evolution_fingerprint):
                    raise ValueError("lineage evolution binding is invalid")
                propagation_bindings[evolution_fingerprint] = {
                    "version": evolution["version"],
                    "binding_ref": evolution_fingerprint,
                    "constant_binding": evolution["constant_binding"],
                    "root_time_operator_version": evolution[
                        "root_time_operator_version"],
                    "root_time_parameter_version": evolution[
                        "root_time_parameter_version"],
                }
                root_time = _mapping(
                    evolution.get("root_time_parameters"),
                    "lineage root time parameters",
                )
                time_parameters[root_time["version"]] = root_time
                edge_id = "runtime-edge:" + model_data_fingerprint(
                    edge_record)
                grouped_edges.setdefault(child, []).append(
                    MemoryInfluenceEdge(
                        edge_id=edge_id,
                        parent_node_id=parent,
                        actual_delta=edge_record["projected_contribution"],
                        parent_live_readout_ref=_identifier(
                            evidence.get("parent_live_readout_ref"),
                            "parent live readout ref",
                        ),
                        p4_receipt_ref=_identifier(
                            edge_record["aggregate_stage_output_fingerprint"],
                            "aggregate stage output fingerprint",
                        ),
                        propagation_binding_ref=evolution_fingerprint,
                        formula_version="runtime-full-memory-lineage-adapter-v1",
                        receipt_refs=(
                            _identifier(
                                edge_record["lineage_fingerprint"],
                                "lineage fingerprint",
                            ),
                        ),
                    ))
                lineage_meta[child] = edge_record
            transactions = []
            # Reuse the exact projections already validated above in this call.
            # No time-evolved readout is reused across evaluation positions.
            projections_for_time = validated_projections
            for child, edges in grouped_edges.items():
                meta = lineage_meta[child]
                transactions.append(MemoryInfluenceTransaction(
                    transaction_id=_identifier(
                        meta["lineage_fingerprint"], "lineage fingerprint"),
                    child_anchor_id=child,
                    child_anchor_kind="interaction",
                    committed_at_ms=(
                        projections_for_time[child].event.committed_at_ms),
                    state_transition_receipt_ref=_identifier(
                        meta["acceptance_fingerprint"],
                        "lineage acceptance fingerprint",
                    ),
                    formula_version="runtime-full-memory-lineage-adapter-v1",
                    edges=tuple(edges),
                    receipt_refs=(_identifier(
                        meta["aggregate_stage_output_fingerprint"],
                        "aggregate stage output fingerprint",
                    ),),
                ).to_record())
            raw_transactions = transactions

        if not isinstance(raw_projections, (tuple, list)):
            raise ValueError("canonical_projections must be a sequence")
        projections = (tuple(validated_projections.values())
            if payload_fields == journal_fields else tuple(
                CanonicalMemoryProjection.from_record(
                    _mapping(item, f"canonical_projections[{index}]"))
                for index, item in enumerate(raw_projections)
            ))
        projections_by_id = {
            item.event.event_id: item for item in projections
        }
        if len(projections_by_id) != len(projections):
            raise ValueError("canonical projection set contains duplicate events")
        if set(projections_by_id) != set(candidate_ids):
            raise ValueError(
                "canonical projection set differs from authorized candidates")
        if payload_fields == legacy_fields:
            invalid_candidate_time = any(
                item.event.committed_at_ms >= evaluation_time_ms
                for item in projections
            )
        else:
            invalid_candidate_time = any(
                item.event.committed_at_ms > evaluation_time_ms
                for item in projections
            )
        if invalid_candidate_time:
            raise ValueError(
                "memory candidates must be strictly earlier than current event")

        adapter_context = _legacy_memory_adapter_context(
            context, evaluation_time_ms=evaluation_time_ms)
        readouts_by_id = self._memory_live._validated_readouts(
            payload={
                "projections": tuple(raw_projections),
                "transactions": tuple(raw_transactions),
                "propagation_bindings_by_ref": propagation_bindings,
                "root_time_parameters_by_version": time_parameters,
                "params": self._package_params("memory_forward_readout"),
                **({"runtime_order": {"version": RUNTIME_MEMORY_ORDER_VERSION,
                    "canonical_node_ordinals": {event_id: node["accepted_runtime_ordinal"]
                        for event_id, node in nodes_by_id.items()}}}
                    if payload_fields != legacy_fields else {}),
            },
            context=adapter_context,
        )
        if set(readouts_by_id) != set(candidate_ids):
            raise ValueError("live readout set differs from authorized candidates")

        candidates = []
        for event_id in candidate_ids:
            check_query_budget()
            readout = readouts_by_id[event_id]
            intrinsic = _mapping(
                readout.receipt.get("intrinsic_term"),
                "historical readout intrinsic term",
            )
            before = _root_vector(
                intrinsic.get("phi_before_event"),
                "historical readout phi_before_event",
            )
            after = {
                axis: before[axis] + float(readout.live_vector[axis])
                for axis in ROOT_ORDER
            }
            projection = project_continuous_roots(
                transition=relationship_transition(before, after),
                constant_binding=projections_by_id[event_id].constant_binding,
                params=self._package_params("shared_projection"),
            )
            live_ref = _identifier(
                readout.receipt.get("readout_ref"), "live readout ref")
            candidates.append({
                "event_id": event_id,
                "recall_input": {
                    "event_id": event_id,
                    "evaluation_time_ms": evaluation_time_ms,
                    "live_readout_ref": live_ref,
                    "temporal_position_fingerprint": (
                        context.temporal_position.fingerprint),
                    "projection": projection.to_record(),
                },
                "influence_input": readout.to_record(),
            })
        return {
            "memory_inputs": {
                "version": "cx67-full-memory-input-product-v1",
                "candidate_event_ids": list(candidate_ids),
                "candidates": candidates,
                "state_commit_authority": False,
                "memory_commit_authority": False,
            },
        }

    def materialize_memory_event(self, *, payload, context):
        """Project one accepted event contribution into a future Memory node."""
        _exact_payload(
            payload,
            {"parameters", "event_contribution", "acceptance_fingerprint"},
            "materialize_memory_event",
        )
        event_ref = _event_ref(context)
        if event_ref is None or context.temporal_position is None:
            raise ValueError(
                "memory event materialization requires event and position")
        binding = _constant_binding(payload["parameters"])
        event = _validated_contribution(
            payload["event_contribution"], kind="event")
        acceptance_fingerprint = _identifier(
            payload["acceptance_fingerprint"], "acceptance_fingerprint")
        projection = project_canonical_memory_event(
            event_id=event_ref,
            committed_at_ms=_evaluation_time_ms(context),
            constant_binding=binding,
            stimulus_receipt_ref=event["source_measurement_fingerprint"],
            root_response_receipt_ref=event["fingerprint"],
            response=RootEventResponse(
                delta_x_event=event["delta"],
                x_after=event["x_after"],
                receipt=event["operator_receipt"],
            ),
            root_time_parameter_version=_identifier(
                self._package_params("progress_flow").get("version"),
                "progress_flow.version",
            ),
        )
        evolution_binding = {
            "version": "cx67-memory-node-evolution-binding-v1",
            "root_time_parameters": self._package_params("progress_flow"),
            "forward_readout_parameters": self._package_params(
                "memory_forward_readout"),
        }
        evolution_binding["fingerprint"] = model_data_fingerprint(
            evolution_binding)
        return {
            "memory_node": {
                "version": MEMORY_EVENT_MATERIALIZATION_VERSION,
                "event_id": event_ref,
                "temporal_position_fingerprint": (
                    context.temporal_position.fingerprint),
                "acceptance_fingerprint": acceptance_fingerprint,
                "projection": projection.to_record(),
                "evolution_binding": evolution_binding,
                "state_commit_authority": False,
                "memory_commit_authority": False,
            },
        }

    @staticmethod
    def _historical_aggregate(value: object) -> tuple[dict[str, Any], str]:
        record = _mapping(value, "historical_aggregate")
        if set(record) != {
            "version", "recalled_event_ids",
            "source_influence_output_fingerprint", "aggregate",
        }:
            raise ValueError("historical aggregate fields do not match contract")
        if record["version"] != CX_HISTORICAL_AGGREGATION_VERSION:
            raise ValueError("historical aggregate version is unsupported")
        ids = record["recalled_event_ids"]
        if (
            isinstance(ids, (str, bytes))
            or not isinstance(ids, (tuple, list))
            or len(ids) != len(set(ids))
        ):
            raise ValueError("historical aggregate exact IDs are invalid")
        for event_id in ids:
            _identifier(event_id, "historical aggregate event ID")
        _identifier(
            record["source_influence_output_fingerprint"],
            "historical aggregate source influence fingerprint")
        aggregate = _mapping(record["aggregate"], "historical_aggregate.aggregate")
        expected = {
            "raw_sum_vector", "raw_norm", "fusion_radius",
            "projection_factor", "delta_x_memory",
            "event_projected_contributions", "per_event_rule",
            "global_projection_rule", "global_projection_applied_once",
            "candidate_specific_rescale_performed", "state_commit_authority",
        }
        if set(aggregate) != expected:
            raise ValueError("historical aggregate payload fields are invalid")
        if aggregate["state_commit_authority"] is not False:
            raise ValueError("historical aggregate cannot carry commit authority")
        if aggregate["global_projection_applied_once"] is not True:
            raise ValueError("historical aggregate requires one global projection")
        if aggregate["candidate_specific_rescale_performed"] is not False:
            raise ValueError("historical aggregate cannot rescale selected candidates")
        if aggregate["per_event_rule"] != "settled_live_influence_identity":
            raise ValueError("historical aggregate per-event rule is invalid")
        if aggregate["global_projection_rule"] != "single_global_radial_projection":
            raise ValueError("historical aggregate global projection rule is invalid")

        raw_sum = _signed_root_vector(
            aggregate["raw_sum_vector"], "historical aggregate raw sum")
        delta = _signed_root_vector(
            aggregate["delta_x_memory"], "historical aggregate delta")
        try:
            raw_norm = float(aggregate["raw_norm"])
            radius = float(aggregate["fusion_radius"])
            factor = float(aggregate["projection_factor"])
        except (TypeError, ValueError) as exc:
            raise ValueError("historical aggregate projection values must be numeric") from exc
        if not all(math.isfinite(item) for item in (raw_norm, radius, factor)):
            raise ValueError("historical aggregate projection values must be finite")
        expected_norm = math.sqrt(sum(value * value for value in raw_sum.values()))
        expected_factor = max(1.0, expected_norm / radius) if radius > 0.0 else -1.0
        if (
            radius <= 0.0
            or not math.isclose(raw_norm, expected_norm, rel_tol=0.0, abs_tol=1e-12)
            or not math.isclose(factor, expected_factor, rel_tol=0.0, abs_tol=1e-12)
            or any(not math.isclose(
                delta[axis], raw_sum[axis] / factor,
                rel_tol=0.0, abs_tol=1e-12) for axis in ROOT_ORDER)
        ):
            raise ValueError("historical aggregate global projection is inconsistent")

        projected = _mapping(
            aggregate["event_projected_contributions"],
            "historical aggregate event contributions")
        if set(projected) != set(ids):
            raise ValueError("historical aggregate event contribution IDs differ")
        projected_vectors = {
            event_id: _signed_root_vector(
                projected[event_id],
                f"historical aggregate contribution {event_id}")
            for event_id in ids
        }
        if any(not math.isclose(
            sum(projected_vectors[event_id][axis] for event_id in ids),
            delta[axis], rel_tol=0.0, abs_tol=1e-12)
               for axis in ROOT_ORDER):
            raise ValueError(
                "historical aggregate event contributions do not sum to delta")
        normalized = dict(record)
        normalized["aggregate"] = dict(aggregate)
        normalized["aggregate"]["raw_sum_vector"] = raw_sum
        normalized["aggregate"]["delta_x_memory"] = delta
        normalized["aggregate"]["event_projected_contributions"] = (
            projected_vectors)
        return normalized, model_data_fingerprint(normalized)

    def settle_state(self, *, payload, context):
        if isinstance(payload, Mapping) and set(payload) == {
            "state", "progress_contribution", "event_contribution",
        }:
            return super().settle_state(payload=payload, context=context)
        _exact_payload(
            payload,
            {
                "state", "parameters", "progress_contribution", "event_contribution",
                "historical_aggregate",
            },
            "settle_state",
        )
        state_before = _root_vector(payload["state"], "state")
        _constant_binding(payload["parameters"])
        current = state_before
        applied: list[str] = []
        contribution_fingerprints: dict[str, str] = {}
        for kind in ("progress", "event"):
            value = payload[f"{kind}_contribution"]
            if value is None:
                continue
            contribution = _validated_contribution(value, kind=kind)
            if not _same_roots(current, contribution["x_before"]):
                raise ValueError(
                    f"{kind} contribution must continue the settled state")
            current = contribution["x_after"]
            applied.append(kind)
            contribution_fingerprints[kind] = contribution["fingerprint"]

        historical, historical_fingerprint = self._historical_aggregate(
            payload["historical_aggregate"])
        memory_delta = historical["aggregate"]["delta_x_memory"]
        current = {
            axis: min(1.0, max(0.0, current[axis] + memory_delta[axis]))
            for axis in ROOT_ORDER
        }
        applied.append("memory")
        contribution_fingerprints["memory"] = historical_fingerprint
        record = {
            "version": STATE_SETTLEMENT_WITH_MEMORY_VERSION,
            "state_before": state_before,
            "state_candidate": current,
            "net_delta": {
                axis: current[axis] - state_before[axis] for axis in ROOT_ORDER
            },
            "applied_contributions": applied,
            "contribution_fingerprints": contribution_fingerprints,
            "memory_recalled_event_ids": list(historical["recalled_event_ids"]),
            "memory_projection_applied_upstream_once": True,
            "state_commit_authority": False,
        }
        record["fingerprint"] = model_data_fingerprint(record)
        return {"settlement": record}

    def recall_history(self, *, payload, context):
        _exact_payload(
            payload,
            {
                "candidate_event_ids", "historical_live", "parameters",
                "event_contribution",
            },
            "recall_history",
        )
        event_ref = _event_ref(context)
        if event_ref is None:
            raise ValueError(
                "full recall requires current event identity and evaluation time")
        evaluation_time_ms = _evaluation_time_ms(context)
        event = _validated_contribution(
            payload["event_contribution"], kind="event")
        binding = _constant_binding(payload["parameters"])
        transition = relationship_transition(event["x_before"], event["x_after"])
        current_projection = project_continuous_roots(
            transition=transition,
            constant_binding=binding,
            params=self._package_params("shared_projection"),
        )
        record = {
            "candidate_event_ids": payload["candidate_event_ids"],
            "historical_live": payload["historical_live"],
            "current": {
                "event_id": event_ref,
                "evaluation_time_ms": evaluation_time_ms,
                "source_receipt_ref": event["fingerprint"],
                "projection": current_projection.to_record(),
            },
            "params": self._package_params("actual_recall"),
        }
        adapter_context = _legacy_memory_adapter_context(
            context, evaluation_time_ms=evaluation_time_ms)
        return self._memory_recall.recall_history(
            payload=record, context=adapter_context)

    def read_historical_influence(self, *, payload, context):
        _exact_payload(
            payload,
            {
                "recalled_event_ids", "historical_readouts",
                "event_contribution", "parameters",
            },
            "read_historical_influence",
        )
        event_ref = _event_ref(context)
        if event_ref is None:
            raise ValueError(
                "full historical influence requires event identity and evaluation time")
        evaluation_time_ms = _evaluation_time_ms(context)
        event = _validated_contribution(
            payload["event_contribution"], kind="event")
        binding = _constant_binding(payload["parameters"])
        raw_history = payload["historical_readouts"]
        if not isinstance(raw_history, (tuple, list)):
            raise ValueError("historical_readouts must be a sequence")
        recalled_ids = payload["recalled_event_ids"]
        if (
            isinstance(recalled_ids, (str, bytes))
            or not isinstance(recalled_ids, (tuple, list))
        ):
            raise ValueError("recalled_event_ids must be a sequence")
        recalled_ids = tuple(
            _identifier(item, "recalled event ID") for item in recalled_ids)
        if len(recalled_ids) != len(set(recalled_ids)):
            raise ValueError("recalled_event_ids cannot contain duplicates")
        parsed_history = tuple(
            NeutralMemoryReadout.from_record(
                _mapping(item, f"historical_readouts[{index}]"))
            for index, item in enumerate(raw_history)
        )
        history_by_id = {
            item.event.event_id: item for item in parsed_history
        }
        if len(history_by_id) != len(parsed_history):
            raise ValueError("historical_readouts contain duplicate event IDs")
        if not set(recalled_ids).issubset(history_by_id):
            raise ValueError(
                "historical_readouts omit a recalled event")
        selected_history = tuple(
            history_by_id[event_id] for event_id in recalled_ids)
        live_version = CURRENT_EVENT_LIVE_READOUT_VERSION
        if selected_history:
            live_version = selected_history[0].formula_version
        current_vector = event["x_after"]
        current = NeutralMemoryReadout(
            event=MemoryEventRef(event_ref, evaluation_time_ms),
            as_of_ms=evaluation_time_ms,
            live_vector=current_vector,
            magnitude=(
                math.sqrt(sum(value * value for value in current_vector.values()))
                / math.sqrt(len(ROOT_ORDER))
            ),
            formula_version=live_version,
            lineage=(event_ref,),
            receipt={
                "source_event_contribution_fingerprint": event["fingerprint"],
                "temporal_position_fingerprint": (
                    None if context.temporal_position is None
                    else context.temporal_position.fingerprint
                ),
                "final_vector": current_vector,
                **({"runtime_order": order_record(context.runtime_ordinal + 1)}
                   if any("runtime_order" in item.receipt for item in selected_history) else {}),
            },
        )
        record = {
            "recalled_event_ids": recalled_ids,
            "historical_readouts": [
                item.to_record() for item in selected_history
            ],
            "current_readout": current.to_record(),
        }
        adapter_context = _legacy_memory_adapter_context(
            context, evaluation_time_ms=evaluation_time_ms)
        result = self._memory_influence.read_historical_influence(
            payload=record, context=adapter_context)
        product = dict(result["historical_influence"])
        product["recalled_event_ids"] = list(recalled_ids)
        edges = []
        progress_params = self._package_params("progress_flow")
        forward_params = self._package_params("memory_forward_readout")
        propagation_binding = {
            "version": "cx67-memory-edge-evolution-binding-v1",
            "constant_binding": binding,
            "root_time_operator_version": ROOT_TIME_FLOW_VERSION,
            "root_time_parameter_version": _identifier(
                progress_params.get("version"), "progress_flow.version"),
            "root_time_parameters": progress_params,
            "forward_readout_parameter_version": _identifier(
                forward_params.get("version"),
                "memory_forward_readout.version",
            ),
        }
        propagation_binding["fingerprint"] = model_data_fingerprint(
            propagation_binding)
        by_id = {item.event.event_id: item for item in selected_history}
        for raw_edge in product["later_edges"]:
            edge = dict(raw_edge)
            parent_id = _identifier(
                edge.get("parent_event_id"), "later edge parent")
            evidence = dict(edge["edge_evidence"])
            evidence.update({
                "parent_live_readout_ref": by_id[parent_id].receipt.get("readout_ref") or
                    forward_live_readout_ref(parent_id, evaluation_time_ms, forward_params["version"]),
                "parent_live_readout_fingerprint": model_data_fingerprint(
                    by_id[parent_id].to_record()),
                "evolution_binding": propagation_binding,
            })
            edge["edge_evidence"] = evidence
            edges.append(edge)
        product["later_edges"] = edges
        return {"historical_influence": product}

    def aggregate_historical_influence(self, *, payload, context):
        _exact_payload(
            payload,
            {
                "recalled_event_ids", "event_influences",
                "source_influence_output_fingerprint",
            },
            "aggregate_historical_influence",
        )
        record = _mapping(payload, "aggregate_historical_influence")
        record["params"] = self._package_params("historical_aggregation")
        adapter_context = _legacy_memory_adapter_context(
            context, evaluation_time_ms=_evaluation_time_ms(context))
        result = self._memory_aggregate.aggregate_historical_influence(
            payload=record, context=adapter_context)
        product = dict(result["historical_aggregate"])
        product["recalled_event_ids"] = list(payload["recalled_event_ids"])
        return {"historical_aggregate": product}

    def diagnostic_descriptor(self, *, payload, context):
        return self._diagnostics.diagnostic_descriptor(
            payload=payload, context=context)


__all__ = (
    "CX_FULL_MODEL_CAPABILITIES_V2",
    "CxSixSevenFullV2Package",
    "CURRENT_EVENT_LIVE_READOUT_VERSION",
    "FULL_PACKAGE_VERSION",
    "MEMORY_EVENT_MATERIALIZATION_VERSION",
    "STATE_SETTLEMENT_WITH_MEMORY_VERSION",
)
