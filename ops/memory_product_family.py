"""Alpha 0.4 Memory product-family identity and append-only session anchor.

This module contains no memory mathematics. A family names the already frozen
C1--C5 and product-transaction contracts that one session is allowed to commit.
Old profiles without an anchor remain unbound.
"""
from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping

from ops.memory_actual_join import ACTUAL_RECALL_EVIDENCE_JOIN_VERSION
from ops.memory_actual_state import ACTUAL_RECALL_STATE_IMPACT_VERSION
from ops.memory_card_hydration import MEMORY_CARD_HYDRATION_VERSION
from ops.memory_content_attention import CONTENT_MEMORY_CARD_ATTENTION_VERSION
from ops.memory_influence_commit import MEMORY_INFLUENCE_COMMIT_VERSION
from ops.memory_join import MEMORY_EVIDENCE_JOIN_VERSION
from ops.memory_product_transaction import (
    LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION,
    MEMORY_PRODUCT_TRANSACTION_VERSION,
    MemoryProductTransaction,
)
from ops.memory_root_transition import ACTUAL_RECALL_ROOT_TRANSITION_VERSION
from ops.memory_state_impact import RECALL_STATE_IMPACT_VERSION


MEMORY_PRODUCT_FAMILY_VERSION = "alpha04-memory-product-family-v2"
LEGACY_MEMORY_PRODUCT_FAMILY_VERSION = "alpha04-memory-product-family-v1"
MEMORY_PRODUCT_FAMILY_ANCHOR_SCHEMA = 1


def _anchor_version(identity: Mapping[str, object], ordinal: int) -> str:
    digest = hashlib.sha256(json.dumps(
        identity, ensure_ascii=False, sort_keys=True,
        separators=(",", ":")).encode("utf-8")).hexdigest()[:12]
    return "memory-product-family-anchor-v%d-%04d-%s" % (
        MEMORY_PRODUCT_FAMILY_ANCHOR_SCHEMA, ordinal, digest)


def _validated_family_anchor_records(
        profile: Mapping[str, object]) -> tuple[dict[str, object], ...]:
    raw = (profile or {}).get("memory_product_family_anchors") or ()
    if not isinstance(raw, (tuple, list)):
        raise ValueError("Alpha 0.4 memory product family anchor journal is invalid")
    expected_fields = {
        "schema_version", "ordinal", "family",
        "effective_constants_version", "b0_version", "source",
        "parent_version", "created_at_ms", "version",
    }
    validated = []
    previous = None
    seen = set()
    for ordinal, item in enumerate(raw, start=1):
        if not isinstance(item, Mapping) or set(item) != expected_fields:
            raise ValueError(
                "Alpha 0.4 memory product family anchor fields are invalid")
        record = dict(item)
        if (
            record.get("schema_version")
            != MEMORY_PRODUCT_FAMILY_ANCHOR_SCHEMA
            or isinstance(record.get("ordinal"), bool)
            or record.get("ordinal") != ordinal
        ):
            raise ValueError(
                "Alpha 0.4 memory product family anchor ordinal is invalid")
        family = normalize_memory_product_family(record.get("family") or {})
        effective_ref = str(
            record.get("effective_constants_version") or "").strip()
        b0_ref = str(record.get("b0_version") or "").strip()
        created_at = record.get("created_at_ms")
        source = str(record.get("source") or "").strip()
        parent = record.get("parent_version")
        version = str(record.get("version") or "").strip()
        if (
            not effective_ref
            or not b0_ref
            or isinstance(created_at, bool)
            or not isinstance(created_at, int)
            or created_at < 0
            or not version
            or version in seen
        ):
            raise ValueError(
                "Alpha 0.4 memory product family anchor identity is invalid")
        if previous is None:
            if source != "initial_new_session" or parent is not None:
                raise ValueError(
                    "Alpha 0.4 memory product family anchor root is invalid")
        elif (
            source != "explicit_upgrade"
            or parent != previous["version"]
            or created_at < previous["created_at_ms"]
        ):
            raise ValueError(
                "Alpha 0.4 memory product family anchor chain is invalid")
        identity = {
            "schema_version": MEMORY_PRODUCT_FAMILY_ANCHOR_SCHEMA,
            "ordinal": ordinal,
            "family": family,
            "effective_constants_version": effective_ref,
            "b0_version": b0_ref,
            "source": source,
            "parent_version": parent,
            "created_at_ms": created_at,
        }
        if version != _anchor_version(identity, ordinal):
            raise ValueError(
                "Alpha 0.4 memory product family anchor fingerprint is invalid")
        canonical = dict(identity, version=version)
        validated.append(canonical)
        previous = canonical
        seen.add(version)
    return tuple(validated)


def _memory_product_family_v1() -> dict[str, str]:
    return {
        "canonical_join_contract": MEMORY_EVIDENCE_JOIN_VERSION,
        "content_attention_contract": CONTENT_MEMORY_CARD_ATTENTION_VERSION,
        "hydration_contract": MEMORY_CARD_HYDRATION_VERSION,
        "influence_commit_contract": MEMORY_INFLUENCE_COMMIT_VERSION,
        "product_transaction_contract": LEGACY_MEMORY_PRODUCT_TRANSACTION_VERSION,
        "state_impact_contract": RECALL_STATE_IMPACT_VERSION,
        "version": LEGACY_MEMORY_PRODUCT_FAMILY_VERSION,
    }


def default_memory_product_family() -> dict[str, str]:
    """Return the current exact contract identity; no runtime switches."""
    return {
        "canonical_join_contract": ACTUAL_RECALL_EVIDENCE_JOIN_VERSION,
        "content_attention_contract": CONTENT_MEMORY_CARD_ATTENTION_VERSION,
        "hydration_contract": MEMORY_CARD_HYDRATION_VERSION,
        "influence_commit_contract": MEMORY_INFLUENCE_COMMIT_VERSION,
        "product_transaction_contract": MEMORY_PRODUCT_TRANSACTION_VERSION,
        "root_transition_contract": ACTUAL_RECALL_ROOT_TRANSITION_VERSION,
        "state_impact_contract": ACTUAL_RECALL_STATE_IMPACT_VERSION,
        "version": MEMORY_PRODUCT_FAMILY_VERSION,
    }


def normalize_memory_product_family(family: Mapping[str, object]) -> dict[str, str]:
    """Validate one family identity without consulting state, time, text or UI."""
    if not isinstance(family, Mapping):
        raise ValueError("Alpha 0.4 memory product family must be a mapping")
    version = str(family.get("version") or "").strip()
    registered = {
        MEMORY_PRODUCT_FAMILY_VERSION: default_memory_product_family(),
        LEGACY_MEMORY_PRODUCT_FAMILY_VERSION: _memory_product_family_v1(),
    }
    expected = registered.get(version)
    if expected is None:
        raise ValueError("Alpha 0.4 memory product family version is unsupported")
    if set(family) != set(expected):
        missing = sorted(set(expected) - set(family))
        extra = sorted(set(family) - set(expected))
        details = []
        if missing:
            details.append("missing " + ", ".join(missing))
        if extra:
            details.append("unknown " + ", ".join(extra))
        raise ValueError(
            "Alpha 0.4 memory product family fields are invalid: "
            + "; ".join(details))
    normalized = {key: str(family.get(key) or "").strip() for key in expected}
    if normalized != expected:
        raise ValueError("Alpha 0.4 memory product family contracts are unsupported")
    return {key: normalized[key] for key in sorted(normalized)}


def validate_memory_product_family_transaction(
        family: Mapping[str, object],
        transaction: MemoryProductTransaction) -> None:
    """Require every D1 source contract to match the bound family exactly."""
    normalized = normalize_memory_product_family(family)
    if not isinstance(transaction, MemoryProductTransaction):
        raise ValueError("transaction must be a MemoryProductTransaction")
    if (transaction.formula_version
            != normalized["product_transaction_contract"]):
        raise ValueError("memory product transaction contract does not match family")
    expected = {
        "canonical_join": normalized["canonical_join_contract"],
        "state_impact": (
            normalized["state_impact_contract"]
            if transaction.state_impact_event_ids else None),
        "influence_commit": (
            normalized["influence_commit_contract"]
            if transaction.state_impact_event_ids else None),
        "attention": (
            normalized["content_attention_contract"]
            if transaction.canonical_event_ids else None),
        "hydration": (
            normalized["hydration_contract"]
            if transaction.canonical_event_ids else None),
    }
    if "root_transition_contract" in normalized:
        expected["root_transition"] = (
            normalized["root_transition_contract"]
            if transaction.state_impact_event_ids else None)
    actual = dict(transaction.source_versions)
    if set(actual) != set(expected) or actual != expected:
        raise ValueError("memory product source contracts do not match family")


def append_memory_product_family_anchor(
        profile: Mapping[str, object], family: Mapping[str, object], *,
        effective_constants_version: str, b0_version: str,
        at_ms: int, source: str) -> dict[str, object]:
    """Append the initial new-session binding; never rewrite an older anchor."""
    if source != "initial_new_session":
        raise ValueError(
            "Alpha 0.4 memory product family anchor requires a new-session source")
    if isinstance(at_ms, bool) or not isinstance(at_ms, int) or at_ms < 0:
        raise ValueError("Alpha 0.4 memory product family anchor needs absolute time")
    effective_ref = str(effective_constants_version or "").strip()
    b0_ref = str(b0_version or "").strip()
    if not effective_ref or not b0_ref:
        raise ValueError(
            "Alpha 0.4 memory product family anchor requires C_effective and B0")
    normalized = normalize_memory_product_family(family)
    copied = dict(profile or {})
    existing = [dict(item) for item in (
        copied.get("memory_product_family_anchors") or ())]
    if existing:
        raise ValueError(
            "Alpha 0.4 memory product family initial anchor requires an empty journal")
    if copied.get("session_memory_product_family_anchor_version"):
        raise ValueError(
            "Alpha 0.4 memory product family is already bound; migration must append explicitly")
    identity = {
        "schema_version": MEMORY_PRODUCT_FAMILY_ANCHOR_SCHEMA,
        "ordinal": len(existing) + 1,
        "family": normalized,
        "effective_constants_version": effective_ref,
        "b0_version": b0_ref,
        "source": source,
        "parent_version": None,
        "created_at_ms": at_ms,
    }
    version = _anchor_version(identity, len(existing) + 1)
    existing.append(dict(identity, version=version))
    copied["memory_product_family_anchors"] = existing
    copied["session_memory_product_family_anchor_version"] = version
    return copied


def append_explicit_memory_product_family_version(
        profile: Mapping[str, object], family: Mapping[str, object], *,
        effective_constants_version: str, b0_version: str,
        at_ms: int, expected_parent_version: str) -> dict[str, object]:
    """Append one explicit family upgrade without reinterpreting old facts."""
    if isinstance(at_ms, bool) or not isinstance(at_ms, int) or at_ms < 0:
        raise ValueError(
            "Alpha 0.4 memory product family migration needs absolute time")
    parent_ref = str(expected_parent_version or "").strip()
    if not parent_ref:
        raise ValueError(
            "Alpha 0.4 memory product family migration needs exact parent")
    current = current_memory_product_family_anchor(profile)
    if current is None:
        raise ValueError(
            "unbound legacy session cannot be upgraded implicitly")
    if current["version"] != parent_ref:
        raise ValueError(
            "Alpha 0.4 memory product family migration parent is stale")
    if at_ms < current["created_at_ms"]:
        raise ValueError(
            "Alpha 0.4 memory product family migration time moved backwards")
    effective_ref = str(effective_constants_version or "").strip()
    b0_ref = str(b0_version or "").strip()
    if not effective_ref or not b0_ref:
        raise ValueError(
            "Alpha 0.4 memory product family migration requires C_effective and B0")
    normalized = normalize_memory_product_family(family)
    if (
        normalized == current["family"]
        and effective_ref == current["effective_constants_version"]
        and b0_ref == current["b0_version"]
    ):
        raise ValueError(
            "Alpha 0.4 memory product family migration has no version change")

    copied = dict(profile or {})
    existing = [dict(item) for item in (
        copied.get("memory_product_family_anchors") or ())]
    if not existing or existing[-1].get("version") != parent_ref:
        raise ValueError(
            "Alpha 0.4 memory product family migration chain is not active")
    ordinal = len(existing) + 1
    identity = {
        "schema_version": MEMORY_PRODUCT_FAMILY_ANCHOR_SCHEMA,
        "ordinal": ordinal,
        "family": normalized,
        "effective_constants_version": effective_ref,
        "b0_version": b0_ref,
        "source": "explicit_upgrade",
        "parent_version": parent_ref,
        "created_at_ms": at_ms,
    }
    version = _anchor_version(identity, ordinal)
    existing.append(dict(identity, version=version))
    copied["memory_product_family_anchors"] = existing
    copied["session_memory_product_family_anchor_version"] = version
    return copied


def current_memory_product_family_anchor(
        profile: Mapping[str, object]) -> Mapping[str, object] | None:
    """Resolve the active exact anchor; no anchor means family-off legacy mode."""
    active = str((profile or {}).get(
        "session_memory_product_family_anchor_version") or "").strip()
    if not active:
        return None
    records = _validated_family_anchor_records(profile)
    if not records or records[-1]["version"] != active:
        raise ValueError(
            "Alpha 0.4 memory product family active anchor is not journal tip")
    matches = [record for record in records if record.get("version") == active]
    if len(matches) != 1:
        raise ValueError("Alpha 0.4 memory product family anchor cannot be resolved")
    record = matches[0]
    family = normalize_memory_product_family(record.get("family") or {})
    effective_ref = str(record.get("effective_constants_version") or "").strip()
    b0_ref = str(record.get("b0_version") or "").strip()
    if not effective_ref or not b0_ref:
        raise ValueError("Alpha 0.4 memory product family anchor lacks constants")
    source = str(record.get("source") or "").strip()
    if (
        record.get("schema_version") != MEMORY_PRODUCT_FAMILY_ANCHOR_SCHEMA
        or source not in {"initial_new_session", "explicit_upgrade"}
    ):
        raise ValueError("Alpha 0.4 memory product family anchor schema is unsupported")
    return {
        "version": active,
        "family": family,
        "effective_constants_version": effective_ref,
        "b0_version": b0_ref,
        "source": source,
        "created_at_ms": int(record["created_at_ms"]),
    }
