"""Optional Full Memory adapter for the generic required-reply host.

The generic host only knows that a memory lane may yield Writer cards.  This
adapter owns the concrete Full Memory claims, occupation and hydration chain
used by the reference product composition.
"""
from __future__ import annotations

from product_adapters.cx_six_seven_v1.content_hydration import (
    CommittedContentRead,
    CommittedContentRecord,
    committed_content_writer_cards,
    numbered_committed_content_writer_card,
    load_occupied_committed_content,
)
from interaction_extensions.content_attention import (
    ContentOccupationPolicy,
    settle_memory_content_occupation,
)
from framework.model_api import model_data_fingerprint
from framework.model_api.identity import context_fact_identity
from framework.history import FRAMEWORK_HISTORY_PROFILE_KEY, HistoryAuthority
from model_packages.cx_six_seven_v1.memory_link_delivery import with_memory_links
from ops.memory_link_history_source import history_memory_link_subcard
from product_adapters.cx_six_seven_v1.memory_content_claims import (
    read_memory_content_claims,
)
from ops.product_conversation_journal import ProductConversationSourceJournal
from interaction_extensions.multi_participant.sessions import (
    ProductConversationSessionRegistry,
)
from ops.product_default_direct_provision import ProductDefaultDirectProvision
from ops.runtime_full_memory_node_product import RuntimeFullMemoryNodeProduct


class ProductFullMemoryCardsProvider:
    """Hydrate committed Full Memory nodes into auditable Writer cards."""

    def __init__(
        self,
        *,
        provision: ProductDefaultDirectProvision,
        conversation_registry: ProductConversationSessionRegistry,
        temporal_runtime,
        content_occupation_policy: ContentOccupationPolicy,
        memory_links_enabled: bool = False,
    ) -> None:
        if not isinstance(provision, ProductDefaultDirectProvision):
            raise TypeError("Full Memory cards require direct provision")
        if not isinstance(
            conversation_registry, ProductConversationSessionRegistry
        ):
            raise TypeError("Full Memory cards require conversation registry")
        if not isinstance(content_occupation_policy, ContentOccupationPolicy):
            raise TypeError("content occupation policy must be explicit")
        self.provision = provision
        self.registry = conversation_registry
        self.temporal_runtime = temporal_runtime
        self.content_occupation_policy = content_occupation_policy
        if not isinstance(memory_links_enabled, bool):
            raise ValueError("memory links require an explicit boolean switch")
        self.memory_links_enabled = memory_links_enabled

    def _source_rows(self):
        return ProductConversationSourceJournal(
            registry=self.registry,
            conversation_id=self.provision.conversation_id,
        ).read_source_receipts()

    def __call__(self, lifecycle):
        transaction = lifecycle.transaction
        event_transaction = transaction.event_transaction
        history = HistoryAuthority.from_record(
            transaction.session.profile[FRAMEWORK_HISTORY_PROFILE_KEY])
        return self._hydrate_recall(
            recall_execution=event_transaction.recall_execution,
            context=event_transaction.context,
            recalled_event_ids=event_transaction.recall.recalled_event_ids,
            history=history, history_read=event_transaction.history_read,
            session_binding_fingerprint=transaction.session.session_binding.fingerprint,
        )

    def from_autonomous_selection(self, selection, *, session_binding_fingerprint):
        """Hydrate an already-produced tick selection; never recall here."""
        from ops.runtime_autonomous_memory_selection import RuntimeAutonomousMemorySelection
        if not isinstance(selection, RuntimeAutonomousMemorySelection):
            raise TypeError("autonomous memory cards require a formal selection")
        return self._hydrate_recall(
            recall_execution=selection.recall_execution, context=selection.context,
            recalled_event_ids=selection.recall.recalled_event_ids,
            history=HistoryAuthority.from_record(selection.history_snapshot),
            history_read=selection.history_read,
            session_binding_fingerprint=session_binding_fingerprint,
        )

    def _hydrate_recall(self, *, recall_execution, context, recalled_event_ids,
                        history, history_read, session_binding_fingerprint):
        if context_fact_identity(context).subject_ref != self.provision.npc.participant_id:
            raise ValueError("memory content owner differs from provision")
        claims = read_memory_content_claims(
            recall_execution,
            expected_context=context,
            recalled_event_ids=recalled_event_ids,
        )
        occupation = settle_memory_content_occupation(
            claims=claims, policy=self.content_occupation_policy)
        if history.session_binding_fingerprint != session_binding_fingerprint:
            raise ValueError("memory content history binding differs from session")
        read = history_read
        if read is None or read.to_record() not in history.to_record()["reads"]:
            raise ValueError("memory content requires the transaction's authorized history read")
        nodes = tuple(RuntimeFullMemoryNodeProduct.from_record(
            fact.model_payload["runtime_full_memory_node"]) for fact in read.facts)
        for fact, node in zip(read.facts, nodes):
            if (node.event_id != fact.fact_id or
                    node.source_conversation_id != fact.scope_ref or
                    node.session_binding_fingerprint != history.session_binding_fingerprint or
                    node.accepted_runtime_ordinal != fact.accepted_runtime_ordinal or
                    node.temporal_position_fingerprint != fact.temporal_position_fingerprint):
                raise ValueError("memory content node differs from its authorized fact")
        node_by_id = {node.event_id: node for node in nodes}
        evaluation = context.temporal_position
        source_rows_by_conversation = {}

        def load(event_id):
            node = node_by_id.get(event_id)
            if node is not None and node.source_conversation_id not in source_rows_by_conversation:
                source_rows_by_conversation[node.source_conversation_id] = {
                    row["event_id"]: row for row in ProductConversationSourceJournal(
                        registry=self.registry, conversation_id=node.source_conversation_id,
                    ).read_source_receipts()
                }
            row = (None if node is None else
                   source_rows_by_conversation[node.source_conversation_id].get(event_id))
            if row is None or node is None:
                raise ValueError(
                    "occupied memory content lacks canonical source or node")
            event = row["source_event"]
            if event["conversation_id"] != node.source_conversation_id:
                raise ValueError("memory source conversation differs from committed node")
            position = self.temporal_runtime.normalize_position(
                event["committed_at_ms"])
            if position.fingerprint != node.temporal_position_fingerprint:
                raise ValueError(
                    "memory source time differs from committed node")
            record = CommittedContentRecord(
                event_id=event_id,
                conversation_id=event["conversation_id"],
                member_snapshot_ids=tuple(event["member_snapshot_ids"]),
                sender_participant_id=event["sender_participant_id"],
                visible_recipient_ids=tuple(event["visible_recipient_ids"]),
                addressed_participant_ids=tuple(
                    event["addressed_participant_ids"]),
                text=event["text"],
                temporal_position=position,
                runtime_ordinal=node.accepted_runtime_ordinal,
                source_receipt_refs=(
                    "conversation-source:"
                    f"{event['conversation_id']}#{event_id}",
                    "source-receipt:"
                    f"{model_data_fingerprint(row)}",
                ),
            )
            return CommittedContentRead(
                record=record,
                order_proof=self.temporal_runtime.prove_order(
                    position, evaluation),
            )

        batch = load_occupied_committed_content(
            canonical_event_ids=recalled_event_ids,
            occupation=occupation,
            evaluation_position=evaluation,
            evaluation_runtime_ordinal=context.runtime_ordinal,
            owner_participant_id=self.provision.npc.participant_id,
            content_loader=load,
        )
        if not self.memory_links_enabled:
            return occupation, committed_content_writer_cards(batch)
        if not batch.records:
            return occupation, ()
        parent = numbered_history_content_card(
            batch=batch, history=history, history_read=history_read,
        )
        return occupation, (parent,)


def numbered_history_content_card(*, batch, history, history_read):
    """Chosen product composition seam; no selection or settlement authority."""
    parent = numbered_committed_content_writer_card(batch)
    if parent is None:
        return None
    for record in batch.records:
        fact = history.fact(record.event_id)
        if (fact.accepted_runtime_ordinal != record.runtime_ordinal or
                fact.temporal_position_fingerprint != record.temporal_position.fingerprint):
            raise ValueError("numbered memory source position differs from History")
    links = history_memory_link_subcard(
        history=history, history_read=history_read, parent_card_id=parent.card_id,
        selected_memory_ids=parent.provenance["selected_memory_ids"], enabled=True,
    )
    return with_memory_links(parent, links)


__all__ = ("ProductFullMemoryCardsProvider", "numbered_history_content_card")
