"""P5 deterministic, zero-network package qualification checks."""
from __future__ import annotations

import ast
import json
from pathlib import Path
import subprocess
import sys

import pytest

from framework.model_api import model_data_fingerprint
from framework.model_api.invocation_v2 import ModelInvocationContextV2
from framework.temporal_domain import (
    REQUIRED_TEMPORAL_OPERATORS,
    TemporalDomainBinding,
    TemporalDomainManifest,
    TemporalPosition,
)
from model_packages.policy_transaction_v1 import (
    CAPABILITIES,
    PolicyTransactionV1Package,
)
from model_packages.policy_transaction_v1.state_space import (
    DEFAULT_STATE_SPACE_DEFINITION,
    empty_state,
    validate_state,
)


PACKAGE_DIR = Path(__file__).resolve().parent
EXECUTABLE_MODULES = (
    "__init__.py", "state_space.py", "text_codec.py", "policy_gate.py", "memory.py",
)
BANNED_IMPORT_PARTS = {
    "benchmark", "adapter", "evaluator", "runtime", "registry", "provider",
    "requests", "httpx", "urllib", "socket", "openai", "anthropic",
}


def _context():
    manifest = TemporalDomainManifest.create(
        domain_id="fixture.logical-step",
        domain_version="1.0.0",
        position_schema={"type": "integer"},
        progress_schema={"type": "object"},
        observation_schema={"type": "object"},
        parameter_package={"version": "fixture-logical-step-v1"},
        operator_versions={
            name: f"fixture-{name}-v1" for name in REQUIRED_TEMPORAL_OPERATORS
        },
    )
    return ModelInvocationContextV2(
        session_id="session:qualification",
        scope_ref="task:qualification",
        subject_ref="agent:one",
        event_ref="event:qualification",
        action_ref=None,
        temporal_position=TemporalPosition.create(
            binding=TemporalDomainBinding.from_manifest(manifest), coordinate=1),
        runtime_ordinal=1,
        metadata={},
    )


def test_manifest_and_operator_identity_are_stable_in_clean_processes():
    script = (
        "import json; "
        "from model_packages.policy_transaction_v1 import PolicyTransactionV1Package; "
        "p=PolicyTransactionV1Package(profile_id='qualification'); "
        "print(json.dumps(p.manifest.to_record(),sort_keys=True,separators=(',',':')))"
    )
    first = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True,
    ).stdout.strip()
    second = subprocess.run(
        [sys.executable, "-c", script], check=True, capture_output=True, text=True,
    ).stdout.strip()
    assert first == second
    record = json.loads(first)
    assert set(record["operator_versions"]) == set(CAPABILITIES)
    assert len(set(record["operator_versions"].values())) == len(CAPABILITIES)
    assert record["operator_versions"]["measure_event"].endswith("-v2")
    assert record["operator_versions"]["recall_history"].endswith("-v4")
    assert record["operator_versions"]["advance_progress"].endswith("-v1")
    PolicyTransactionV1Package(profile_id="qualification").manifest.verify_integrity()


def test_model_data_round_trip_and_tampered_contribution_fail_closed():
    package = PolicyTransactionV1Package(profile_id="qualification")
    context = _context()
    state = empty_state(DEFAULT_STATE_SPACE_DEFINITION)
    round_tripped = json.loads(json.dumps(state, sort_keys=True))
    assert validate_state(
        round_tripped, definition=DEFAULT_STATE_SPACE_DEFINITION) == state
    parameters = package.initialize_parameters(
        payload={
            "profile_id": package.profile_id,
            "policy_contract_digest": "a" * 64,
            "tool_contract_digest": "b" * 64,
            "exact_tool_ids": ["lookup_entity"],
        },
        context=context,
    )["parameters"]
    progress = package.advance_progress(
        payload={"parameters": parameters, "state": state, "progress_delta": {}},
        context=context,
    )["progress_contribution"]
    tampered = dict(progress)
    tampered["invented_field"] = True
    with pytest.raises(ValueError, match="fingerprint is invalid"):
        package.settle_state(
            payload={
                "state": state,
                "progress_contribution": tampered,
                "event_contribution": None,
            },
            context=context,
        )

    wrong_parameter_material = {
        key: value for key, value in parameters.items() if key != "fingerprint"}
    wrong_parameter_material["version"] = "policy-transaction-session-parameters-v999"
    wrong_parameters = {
        **wrong_parameter_material,
        "fingerprint": model_data_fingerprint(wrong_parameter_material),
    }
    with pytest.raises(ValueError, match="parameters version"):
        package.advance_progress(
            payload={
                "parameters": wrong_parameters,
                "state": state,
                "progress_delta": {},
            },
            context=context,
        )

    wrong_progress_material = {
        key: value for key, value in progress.items() if key != "fingerprint"}
    wrong_progress_material["version"] = "policy-transaction-progress-contribution-v999"
    wrong_progress = {
        **wrong_progress_material,
        "fingerprint": model_data_fingerprint(wrong_progress_material),
    }
    with pytest.raises(ValueError, match="progress contribution version"):
        package.settle_state(
            payload={
                "state": state,
                "progress_contribution": wrong_progress,
                "event_contribution": None,
            },
            context=context,
        )


def test_executable_package_imports_only_stdlib_local_and_model_contracts():
    for filename in EXECUTABLE_MODULES:
        tree = ast.parse((PACKAGE_DIR / filename).read_text(encoding="utf-8"))
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append(node.module)
        for imported in imports:
            lowered = imported.lower()
            assert not any(part in lowered for part in BANNED_IMPORT_PARTS), (
                filename, imported)
            if imported.startswith("framework"):
                assert imported in {
                    "framework.model_api", "framework.model_api.invocation_v2",
                }


def test_package_source_contains_no_old_bucket_or_tau3_contract_names():
    source = "\n".join(
        (PACKAGE_DIR / filename).read_text(encoding="utf-8")
        for filename in EXECUTABLE_MODULES
    ).lower()
    for obsolete in (
        "state_buckets", "update_kinds", "semantic_keys", "reference_keys",
        "tau3benchmarkturnrequest", "domain_ids",
    ):
        assert obsolete not in source

