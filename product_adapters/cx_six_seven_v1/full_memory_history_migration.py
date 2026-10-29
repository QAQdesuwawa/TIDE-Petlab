"""Explicit legacy Full Memory journals -> framework History migration.

The paper runtime never calls this adapter implicitly.  A product migration
host may invoke it once, persist the returned profile atomically, and archive
the receipt.  Legacy node/lineage rows remain append-only evidence but cease
to be the formal read authority after the History snapshot is installed.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from framework.history import (
    FRAMEWORK_HISTORY_PROFILE_KEY,
    ActualRecall,
    CommittedFact,
    HistoryAuthority,
    InfluenceEdge,
)
from framework.model_api import freeze_model_payload, model_data_fingerprint
from ops.runtime_full_memory_profile_commit import (
    read_runtime_full_memory_lineage_journal,
    read_runtime_full_memory_node_journal,
)


LEGACY_FULL_MEMORY_HISTORY_MIGRATION_VERSION = (
    "cx67-legacy-full-memory-history-migration-v1")


@dataclass(frozen=True)
class LegacyFullMemoryHistoryMigration:
    profile: Mapping[str, Any]
    receipt: Mapping[str, Any]
    changed: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "profile", freeze_model_payload(self.profile))
        object.__setattr__(self, "receipt", freeze_model_payload(self.receipt))


def _fact(node) -> CommittedFact:
    return CommittedFact.create(
        fact_id=node.event_id,
        session_binding_fingerprint=node.session_binding_fingerprint,
        source_ref=node.owner_participant_id,
        scope_ref=node.source_conversation_id,
        visible_to_refs=(node.owner_participant_id,),
        temporal_position_fingerprint=node.temporal_position_fingerprint,
        accepted_runtime_ordinal=node.accepted_runtime_ordinal,
        content_ref={"event_id": node.event_id},
        model_payload={"runtime_full_memory_node": node.to_record()},
        settlement_ref={
            "acceptance_fingerprint": node.acceptance_fingerprint,
            "materialization_stage_receipt_fingerprint": (
                node.materialization_stage_receipt_fingerprint),
        },
    )


def migrate_legacy_full_memory_history(
    profile: Mapping[str, Any], *, session_binding_fingerprint: str,
) -> LegacyFullMemoryHistoryMigration:
    """Return a complete migrated profile; perform no storage write."""
    if not isinstance(profile, Mapping):
        raise ValueError("legacy history migration requires a profile mapping")
    binding = str(session_binding_fingerprint or "").strip()
    if not binding:
        raise ValueError("legacy history migration requires a session binding")

    existing = profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
    if existing is not None:
        history = HistoryAuthority.from_record(existing)
        if history.session_binding_fingerprint != binding:
            raise ValueError("framework history binding is invalid")
        material = {
            "version": LEGACY_FULL_MEMORY_HISTORY_MIGRATION_VERSION,
            "session_binding_fingerprint": binding,
            "changed": False,
            "history_fingerprint": history.to_record()["fingerprint"],
            "legacy_node_fingerprints": [],
            "legacy_lineage_fingerprints": [],
            "authority": {
                "profile_write": False,
                "state_commit": False,
                "history_append": False,
                "provider_call": False,
            },
        }
        receipt = dict(material, fingerprint=model_data_fingerprint(material))
        return LegacyFullMemoryHistoryMigration(
            profile=dict(profile), receipt=receipt, changed=False)

    nodes = read_runtime_full_memory_node_journal(
        profile, session_binding_fingerprint=binding)
    lineages = read_runtime_full_memory_lineage_journal(
        profile, session_binding_fingerprint=binding)
    if not nodes and not lineages:
        raise ValueError("legacy history migration found no legacy journals")

    history = HistoryAuthority(session_binding_fingerprint=binding)
    nodes_by_id = {}
    for node in nodes:
        history.append_fact(_fact(node))
        nodes_by_id[node.event_id] = node

    for lineage in lineages:
        child = nodes_by_id.get(lineage.child_event_id)
        if child is None:
            raise ValueError("legacy lineage child has no committed node")
        view = history.authorize_read(
            reader_ref=child.owner_participant_id,
            trigger_fact_id=child.event_id,
            before_runtime_ordinal=child.accepted_runtime_ordinal,
            requested_fact_ids=lineage.parent_event_ids,
            authorization_ref={
                "migration_version": (
                    LEGACY_FULL_MEMORY_HISTORY_MIGRATION_VERSION),
                "lineage_fingerprint": lineage.fingerprint,
            },
        )
        recall = ActualRecall.create(
            recall_id=f"legacy-recall:{lineage.fingerprint}",
            trigger_fact_id=child.event_id,
            history_read_fingerprint=view.fingerprint,
            recalled_fact_ids=lineage.parent_event_ids,
            model_stage_receipt_fingerprint=(
                lineage.recall_stage_output_fingerprint),
            model_output_fingerprint=lineage.recall_stage_output_fingerprint,
        )
        history.append_actual_recall(recall, history_read=view)
        for edge in lineage.edges:
            history.append_influence_edge(InfluenceEdge.create(
                edge_id=(
                    f"legacy-influence:{lineage.fingerprint}:"
                    f"{edge.parent_event_id}"
                ),
                recall_id=recall.recall_id,
                parent_fact_id=edge.parent_event_id,
                child_fact_id=edge.child_event_id,
                contribution_payload=edge.projected_contribution,
                evidence={
                    "lineage_fingerprint": lineage.fingerprint,
                    "acceptance_fingerprint": (
                        lineage.acceptance_fingerprint),
                    "aggregate_stage_output_fingerprint": (
                        lineage.aggregate_stage_output_fingerprint),
                    "edge_evidence": edge.edge_evidence,
                    "migration_version": (
                        LEGACY_FULL_MEMORY_HISTORY_MIGRATION_VERSION),
                },
            ))

    history_record = history.to_record()
    migrated = dict(profile)
    migrated[FRAMEWORK_HISTORY_PROFILE_KEY] = history_record
    material = {
        "version": LEGACY_FULL_MEMORY_HISTORY_MIGRATION_VERSION,
        "session_binding_fingerprint": binding,
        "changed": True,
        "history_fingerprint": history_record["fingerprint"],
        "legacy_node_fingerprints": [node.fingerprint for node in nodes],
        "legacy_lineage_fingerprints": [
            lineage.fingerprint for lineage in lineages
        ],
        "authority": {
            "profile_write": False,
            "state_commit": False,
            "history_append": False,
            "provider_call": False,
        },
    }
    receipt = dict(material, fingerprint=model_data_fingerprint(material))
    return LegacyFullMemoryHistoryMigration(
        profile=migrated,
        receipt=receipt,
        changed=True,
    )
