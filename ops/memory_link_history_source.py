"""Read-only source adapter for the optional memory-link formatter.

This projection witnesses accepted recall influence, not domain causality.
It neither discovers semantic edges nor interprets contribution payloads.
It is deliberately separate from numbering/rendering and model packages.
"""
from __future__ import annotations

from collections.abc import Sequence
from collections import OrderedDict
from threading import Lock
from framework.model_api import model_data_fingerprint

from framework.history import HistoryAuthority, HistoryRead, ActualRecall, InfluenceEdge
from interaction_extensions.memory_links import MemoryLinkSubcard, build_memory_link_subcard

_EDGE_PARSES=OrderedDict()
_EDGE_PARSE_LOCK=Lock()


def _checked_edge(edge):
    record={'version':edge.version,'edge_id':edge.edge_id,'recall_id':edge.recall_id,
        'parent_fact_id':edge.parent_fact_id,'child_fact_id':edge.child_fact_id,
        'contribution_payload':edge.contribution_payload,'evidence':edge.evidence}
    fingerprint=model_data_fingerprint(record)
    if fingerprint!=edge.fingerprint:raise ValueError('influence edge fingerprint is invalid')
    with _EDGE_PARSE_LOCK:
        checked=_EDGE_PARSES.get(fingerprint)
        if checked is not None:
            _EDGE_PARSES.move_to_end(fingerprint)
            return checked
    checked=InfluenceEdge.from_record({**record,'fingerprint':fingerprint})
    with _EDGE_PARSE_LOCK:
        _EDGE_PARSES[fingerprint]=checked
        while len(_EDGE_PARSES)>4096:_EDGE_PARSES.popitem(last=False)
    return checked


def history_memory_link_subcard(
    *, history: HistoryAuthority, history_read: HistoryRead, parent_card_id: str,
    selected_memory_ids: Sequence[str], enabled: bool = False,
) -> MemoryLinkSubcard | None:
    """Project only direct accepted edges whose endpoints are both selected.

    The caller owns the parent's exact numbered body. IDs must already be in
    occurrence order; this function never sorts/reselects memories or fills a
    missing intermediate node. Direct edges remain two-node chains, avoiding
    transitive path enumeration. Source verification uses a scoped authority,
    never a reconstruction of the complete historical snapshot.
    """
    if not isinstance(enabled, bool):
        raise ValueError("memory link source requires an explicit boolean switch")
    if not enabled:
        return None
    if not isinstance(history, HistoryAuthority) or not isinstance(history_read, HistoryRead):
        raise ValueError("memory link source requires HistoryAuthority and HistoryRead")
    if history_read.session_binding_fingerprint != history.session_binding_fingerprint:
        raise ValueError("memory link history read binding differs from source")
    accepted = history._reads.get(history_read.fingerprint)
    if accepted is None or accepted.to_record() != history_read.to_record():
        raise ValueError("memory link source requires an accepted history read")
    # The incoming authority was already restored by its owner. Verify only
    # this accepted read and the source dependency closure of selected edges;
    # do not serialize every unrelated fact/read/recall on the display path.
    verified = HistoryAuthority(session_binding_fingerprint=history.session_binding_fingerprint)
    if isinstance(selected_memory_ids, (str, bytes)) or not isinstance(selected_memory_ids, Sequence):
        raise ValueError("selected memory IDs must be a sequence")
    selected = tuple(selected_memory_ids)
    if any(not isinstance(value, str) or not value.strip() for value in selected):
        raise ValueError("selected memory IDs must be non-empty strings")
    if len(selected) != len(set(selected)):
        raise ValueError("selected memory IDs must be unique")
    if not set(selected).issubset(history_read.fact_ids):
        raise ValueError("selected memory is outside the accepted history read")
    positions = {value: index for index, value in enumerate(selected)}
    edges = [edge for edge in history.influence_edges()
             if edge.parent_fact_id in positions and edge.child_fact_id in positions]
    reads = {history_read.fingerprint: history_read}
    recalls = {}
    for edge in edges:
        # One accepted recall can contribute several distinct edges. Its
        # source record is identical for all of them; verify it once in this
        # projection, while every edge still receives its own check below.
        if edge.recall_id in recalls:
            continue
        recall = ActualRecall.from_record(history.actual_recall(edge.recall_id).to_record())
        original = history._reads.get(recall.history_read_fingerprint)
        if original is None:
            raise ValueError("memory link recall requires an accepted history read")
        reads[original.fingerprint] = original
        recalls[recall.recall_id] = recall
    fact_ids = {identity for read in reads.values() for identity in read.fact_ids}
    fact_ids.update(edge.child_fact_id for edge in edges)
    facts = [history.fact(identity) for identity in fact_ids]
    for fact in sorted(facts,key=lambda value:value.accepted_runtime_ordinal):
        # append_fact performs the integrity and binding checks itself.
        verified.append_fact(fact)
    for read in reads.values():
        checked = HistoryRead.from_record(read.to_record(),facts_by_id=verified._facts)
        replayed = verified.authorize_read(reader_ref=checked.reader_ref,trigger_fact_id=checked.trigger_fact_id,
            before_runtime_ordinal=checked.before_runtime_ordinal,requested_fact_ids=checked.fact_ids,
            authorization_ref=checked.authorization_ref)
        if replayed.fingerprint != read.fingerprint:
            raise ValueError("memory link source read fingerprint differs")
    for recall in recalls.values():
        verified.append_actual_recall(recall,history_read=verified._reads[recall.history_read_fingerprint])
    # from_record already normalizes/freezes every payload and checks its hash.
    # Supplying the original fields avoids to_record's additional deep copy;
    # this is still the original parser, not an unchecked edge insertion.
    edges = [verified.append_influence_edge(_checked_edge(edge)) for edge in edges]
    ordinals = tuple(verified.fact(value).accepted_runtime_ordinal for value in selected)
    if any(left >= right for left, right in zip(ordinals, ordinals[1:])):
        raise ValueError("selected memory IDs must already follow occurrence order")
    edges.sort(key=lambda edge: (positions[edge.parent_fact_id],
                                 positions[edge.child_fact_id], edge.fingerprint))
    return build_memory_link_subcard(
        enabled=True, parent_card_id=parent_card_id, selected_memory_ids=selected,
        related_chains=tuple((edge.parent_fact_id, edge.child_fact_id) for edge in edges),
        source_receipt_refs=("history-read:" + history_read.fingerprint,
                             *("history-influence-edge:" + edge.fingerprint for edge in edges)),
    )


__all__ = ("history_memory_link_subcard",)
