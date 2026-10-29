"""Six/seven reference-model semantic resources before Writer assembly.

The mapper consumes only the already-published shared three-direction
projection plus opaque, previously committed expression remainders.  It does
not reread roots or constants, hydrate content, build Writer cards, commit
remainders, or acquire action authority.
"""
from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any

from .expression_emission import (
    DEFAULT_EMISSION_UNIT,
    MAPPING_DESCRIPTOR_VERSION,
    compile_expression_emission_plan,
    normalize_mapping_offset,
)


SEMANTIC_RESOURCE_PARAMETERS_VERSION = "cx67-semantic-resources-v1"
EXPRESSION_RESOURCE_BATCH_VERSION = "cx67-expression-resource-batch-v1"
EXPRESSION_RESOURCE_SELECTION_VERSION = "cx67-expression-resource-selection-v1"
PROJECTION_AXES = ("exchange", "withhold", "tension")
_AXIS_LABELS = {
    "exchange": "索取趋向",
    "withhold": "防御趋向",
    "tension": "情绪张力",
}
_EPSILON = 1e-12


def _identifier(value: object, field: str) -> str:
    result = str(value or "").strip()
    if not result:
        raise ValueError(f"{field} must be a non-empty identifier")
    return result


def _finite(value: object, field: str) -> float:
    if isinstance(value, bool):
        raise ValueError(f"{field} must be finite")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field} must be finite") from exc
    if not math.isfinite(number):
        raise ValueError(f"{field} must be finite")
    return number


def _parameters(value: object) -> tuple[str, float]:
    if not isinstance(value, Mapping) or set(value) != {"version", "emission_unit"}:
        raise ValueError("semantic resource parameters do not match contract")
    version = _identifier(value.get("version"), "semantic_resources.version")
    if version != SEMANTIC_RESOURCE_PARAMETERS_VERSION:
        raise ValueError("unknown semantic resource parameter version")
    unit = _finite(value.get("emission_unit"), "semantic_resources.emission_unit")
    if not math.isclose(
        unit, DEFAULT_EMISSION_UNIT, rel_tol=0.0, abs_tol=1e-15,
    ):
        raise ValueError("semantic resource parameters differ from registered version")
    return version, unit


def _projection_vector(shared_projection: object) -> dict[str, float]:
    if not isinstance(shared_projection, Mapping):
        raise ValueError("shared_projection must be a mapping")
    vector = shared_projection.get("vector")
    if not isinstance(vector, Mapping) or set(vector) != set(PROJECTION_AXES):
        raise ValueError("shared projection vector must contain exactly three axes")
    result = {
        axis: _finite(vector[axis], f"shared_projection.vector.{axis}")
        for axis in PROJECTION_AXES
    }
    if any(not -1.0 <= item <= 1.0 for item in result.values()):
        raise ValueError("shared projection vector must stay in the signed unit cube")
    return result


def _prominence(share: float) -> str:
    if share >= 0.65:
        return "显著"
    if share >= 0.30:
        return "在场"
    return "微弱"


def _orientation(value: float) -> str:
    if value > _EPSILON:
        return "positive"
    if value < -_EPSILON:
        return "negative"
    return "neutral"


def project_expression_resources(
    *,
    shared_projection: Mapping[str, Any],
    source_shared_projection_output_fingerprint: str,
    committed_remainders: Mapping[str, Any],
    params: Mapping[str, Any],
) -> dict[str, Any]:
    """Map one shared projection to objective, model-selected resources."""
    parameter_version, emission_unit = _parameters(params)
    source_fingerprint = _identifier(
        source_shared_projection_output_fingerprint,
        "source_shared_projection_output_fingerprint",
    )
    if not isinstance(committed_remainders, Mapping):
        raise ValueError("committed_remainders must be a mapping")
    vector = _projection_vector(shared_projection)
    magnitude = math.sqrt(sum(value * value for value in vector.values()))

    resources: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    if magnitude > _EPSILON:
        for axis in PROJECTION_AXES:
            raw_value = vector[axis]
            share = abs(raw_value) / magnitude
            resource_id = f"cx67:shared-projection:{axis}"
            candidate = {
                "version": MAPPING_DESCRIPTOR_VERSION,
                "token_key": resource_id,
                "source": "shared_projection",
                "axis": axis,
                "score": share,
                "domain": {"low": 0.0, "high": 1.0},
                "neutral": 1.0 / 3.0,
                "input_receipt": {
                    "source_shared_projection_output_fingerprint":
                        source_fingerprint,
                    "shared_projection_fingerprint": _identifier(
                        shared_projection.get("fingerprint"),
                        "shared_projection.fingerprint",
                    ),
                },
            }
            deviation = normalize_mapping_offset(candidate)
            if deviation is None:
                raise ValueError("semantic resource mapping produced an invalid descriptor")
            candidates.append(candidate)
            resources.append({
                "resource_id": resource_id,
                "lane": "current_interaction_projection",
                "descriptor": {
                    "kind": "continuous_direction",
                    "axis": axis,
                    "label": _AXIS_LABELS[axis],
                    "orientation": _orientation(raw_value),
                    "prominence": _prominence(share),
                },
                "score": share,
                "domain": {"low": 0.0, "high": 1.0},
                "neutral": 1.0 / 3.0,
                "deviation": deviation,
                "estimated_cost": 1.0,
                "source_receipt_refs": [source_fingerprint],
            })

    plan = compile_expression_emission_plan(
        candidates,
        committed_remainders=committed_remainders,
        emission_unit=emission_unit,
    )
    selected_ids = list(plan["selected_token_keys"])
    selected_set = set(selected_ids)
    candidate_evidence = {
        str(item["token_key"]): item for item in plan["candidates"]
    }
    package_evidence: dict[str, tuple[Mapping[str, Any], str | None]] = {}
    for package in plan["packages"]:
        if not isinstance(package, Mapping):
            raise ValueError("semantic resource selection package is invalid")
        deferred = {
            str(item.get("token_key")): str(item.get("reason"))
            for item in package.get("deferred", [])
            if isinstance(item, Mapping)
        }
        for resource_id in package.get("emitted_token_keys", []):
            package_evidence[str(resource_id)] = (package, None)
        for resource_id, reason in deferred.items():
            package_evidence[resource_id] = (package, reason)

    evidence: dict[str, dict[str, Any]] = {}
    for resource in resources:
        resource_id = str(resource["resource_id"])
        package, reason = package_evidence[resource_id]
        evidence[resource_id] = {
            "selected": resource_id in selected_set,
            "current_deviation": candidate_evidence[resource_id]["current_offset"],
            "package_key": package["package_key"],
            "continuous_quota": package["continuous_quota"],
            "requested_slots": package["requested_slots"],
            "decision": "selected" if resource_id in selected_set else reason,
        }

    return {
        "expression_resource_batch": {
            "version": EXPRESSION_RESOURCE_BATCH_VERSION,
            "source_shared_projection_output_fingerprint": source_fingerprint,
            "resources": resources,
            "selection": {
                "version": EXPRESSION_RESOURCE_SELECTION_VERSION,
                "selected_resource_ids": selected_ids,
                "resource_evidence": evidence,
            },
        },
        "mapping_receipt": {
            "version": SEMANTIC_RESOURCE_PARAMETERS_VERSION,
            "parameter_version": parameter_version,
            "source_shared_projection_output_fingerprint": source_fingerprint,
            "emission_plan_version": plan["version"],
            "emission_unit": emission_unit,
            "resource_count": len(resources),
            "selected_resource_ids": selected_ids,
            "derived_only": True,
            "writer_packet_authority": False,
            "remainder_commit_authority": False,
        },
    }


__all__ = (
    "EXPRESSION_RESOURCE_BATCH_VERSION",
    "EXPRESSION_RESOURCE_SELECTION_VERSION",
    "SEMANTIC_RESOURCE_PARAMETERS_VERSION",
    "project_expression_resources",
)
