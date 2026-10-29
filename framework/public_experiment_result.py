"""Strict package-neutral verifier for an exported experiment turn result."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from framework.model_api import model_data_fingerprint, thaw_model_data
from framework.public_experiment_receipt import (
    verify_public_experiment_receipt_record,
)


PUBLIC_EXPERIMENT_RESULT_VERSION = "eval-lab-tau3-tide-turn-result-v2"
_RESULT_FIELDS = {
    "schema_version", "status", "session_id", "event_id",
    "accepted_ordinal", "next_runtime_ordinal", "action", "receipt_refs",
    "usage", "runtime_manifest", "treatment_manifest",
    "framework_receipt", "fingerprint",
}
_RUNTIME_FIELDS = {
    "schema_version", "declared_runtime_identity", "declared_model_identity",
    "session_binding_fingerprint", "model_binding_fingerprint",
    "temporal_binding_fingerprint", "runtime_variant_fingerprint",
    "fingerprint",
}
_TREATMENT_FIELDS = {
    "schema_version", "treatment_id", "composition_fingerprint",
    "package_binding_fingerprint", "runtime_variant_fingerprint",
    "treatment_fingerprint", "fingerprint",
}


def _nonempty(value: object, field: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        raise ValueError(f"public experiment result {field} is invalid")
    return normalized


def _verify_fingerprint(record: Mapping[str, Any], field: str) -> None:
    material = dict(record)
    fingerprint = material.pop("fingerprint", None)
    if fingerprint != model_data_fingerprint(material):
        raise ValueError(f"public experiment result {field} fingerprint is invalid")


def _verify_action(value: object) -> None:
    if not isinstance(value, Mapping):
        raise ValueError("public experiment result action is invalid")
    action = thaw_model_data(value)
    if set(action) == {"kind", "content"} and action["kind"] == "text":
        _nonempty(action["content"], "text action")
        return
    if set(action) == {"kind", "tool_calls"} and action["kind"] == "tool":
        rows = action["tool_calls"]
        if not isinstance(rows, list) or not rows:
            raise ValueError("public experiment result tool action is invalid")
        for row in rows:
            if not isinstance(row, Mapping) or set(row) != {
                "id", "name", "arguments",
            } or not isinstance(row["arguments"], Mapping):
                raise ValueError("public experiment result tool action is invalid")
            _nonempty(row["id"], "tool call id")
            _nonempty(row["name"], "tool call name")
        return
    raise ValueError("public experiment result action is invalid")


def _verify_runtime_manifest(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _RUNTIME_FIELDS:
        raise ValueError("public experiment runtime manifest is invalid")
    record = thaw_model_data(value)
    if record["schema_version"] != "tide-tau3-runtime-manifest-v1":
        raise ValueError("public experiment runtime manifest is unsupported")
    for field in _RUNTIME_FIELDS - {"schema_version", "fingerprint"}:
        _nonempty(record[field], f"runtime manifest {field}")
    _verify_fingerprint(record, "runtime manifest")
    return record


def _verify_treatment_manifest(
    value: object, *, runtime: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != _TREATMENT_FIELDS:
        raise ValueError("public experiment treatment manifest is invalid")
    record = thaw_model_data(value)
    if record["schema_version"] != "framework-treatment-manifest-v1":
        raise ValueError("public experiment treatment manifest is unsupported")
    for field in _TREATMENT_FIELDS - {"schema_version", "fingerprint"}:
        _nonempty(record[field], f"treatment manifest {field}")
    links = (
        (record["treatment_id"], runtime["declared_runtime_identity"]),
        (record["composition_fingerprint"],
         runtime["session_binding_fingerprint"]),
        (record["package_binding_fingerprint"],
         runtime["model_binding_fingerprint"]),
        (record["runtime_variant_fingerprint"],
         runtime["runtime_variant_fingerprint"]),
    )
    if any(actual != expected for actual, expected in links):
        raise ValueError("public experiment treatment binding is invalid")
    identity_material = {
        "treatment_id": record["treatment_id"],
        "composition_fingerprint": record["composition_fingerprint"],
        "package_binding_fingerprint": record[
            "package_binding_fingerprint"],
        "runtime_variant_fingerprint": record[
            "runtime_variant_fingerprint"],
    }
    if record["treatment_fingerprint"] != model_data_fingerprint(
        identity_material
    ):
        raise ValueError("public experiment treatment identity is invalid")
    _verify_fingerprint(record, "treatment manifest")
    return record


def verify_public_experiment_result_record(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Verify the complete exported record without importing a model package."""
    if not isinstance(value, Mapping) or set(value) != _RESULT_FIELDS:
        raise ValueError("public experiment result fields are invalid")
    record = thaw_model_data(value)
    if record["schema_version"] != PUBLIC_EXPERIMENT_RESULT_VERSION:
        raise ValueError("public experiment result version is unsupported")
    status = record["status"]
    if status not in {"committed", "failed"}:
        raise ValueError("public experiment result status is invalid")
    _nonempty(record["session_id"], "session identity")
    _nonempty(record["event_id"], "event identity")
    next_ordinal = record["next_runtime_ordinal"]
    if isinstance(next_ordinal, bool) or not isinstance(next_ordinal, int) \
            or next_ordinal < 0:
        raise ValueError("public experiment result next ordinal is invalid")
    refs = record["receipt_refs"]
    if not isinstance(refs, dict) or not refs or any(
        not str(stage or "").strip() or not str(ref or "").strip()
        for stage, ref in refs.items()
    ):
        raise ValueError("public experiment result receipt refs are invalid")
    runtime = _verify_runtime_manifest(record["runtime_manifest"])
    receipt = verify_public_experiment_receipt_record(
        record["framework_receipt"])
    if receipt["runtime_variant_fingerprint"] != runtime[
        "runtime_variant_fingerprint"
    ] or receipt["session_binding_fingerprint"] != runtime[
        "session_binding_fingerprint"
    ]:
        raise ValueError("public experiment framework receipt binding is invalid")
    for stage, settlement in receipt["stage_settlements"].items():
        receipt_ref = settlement["receipt_ref"]
        if receipt_ref is not None and refs.get(stage) != receipt_ref:
            raise ValueError("public experiment stage receipt projection is invalid")

    if status == "committed":
        accepted = record["accepted_ordinal"]
        if isinstance(accepted, bool) or not isinstance(accepted, int) \
                or accepted < 0 or next_ordinal != accepted + 1:
            raise ValueError("public experiment committed ordinal is invalid")
        _verify_action(record["action"])
        if not isinstance(record["usage"], dict):
            raise ValueError("public experiment usage is invalid")
        treatment = _verify_treatment_manifest(
            record["treatment_manifest"], runtime=runtime)
        if receipt.get("treatment_fingerprint") != treatment[
            "treatment_fingerprint"
        ]:
            raise ValueError("public experiment receipt treatment is invalid")
        if refs.get("lifecycle") != receipt.get("lifecycle_receipt_ref"):
            raise ValueError("public experiment lifecycle receipt is invalid")
        if refs.get("action_commit") != receipt.get(
            "action_commit_receipt_ref"
        ):
            raise ValueError("public experiment action Commit receipt is invalid")
    else:
        if record["accepted_ordinal"] is not None or record["action"] is not None:
            raise ValueError("failed public experiment result cannot commit")
        if record["usage"] is not None or record["treatment_manifest"] is not None:
            raise ValueError("failed public experiment result has downstream output")
        if refs.get("lifecycle_failure") != receipt.get(
            "lifecycle_failure_ref"
        ):
            raise ValueError("public experiment failure receipt is invalid")

    _verify_fingerprint(record, "outer")
    return record


__all__ = (
    "PUBLIC_EXPERIMENT_RESULT_VERSION",
    "verify_public_experiment_result_record",
)
