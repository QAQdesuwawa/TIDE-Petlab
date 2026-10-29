"""Read committed recall edges; never infer entity links or alter History."""
from framework.model_api import thaw_model_data


def read_recalled_influence_edges(history, *, candidate_event_ids, recalled_event_ids,
                                  before_runtime_ordinal, enabled=True):
    if not enabled:
        return []
    candidates = set(candidate_event_ids)
    roots = set(recalled_event_ids)
    if not roots <= candidates:
        raise ValueError('recalled roots escape the candidate scope')
    facts = {fact.fact_id: fact for fact in history.facts()}
    incoming = {}
    for edge in history.influence_edges():
        if edge.child_fact_id not in candidates:
            continue
        if edge.parent_fact_id not in candidates:
            raise ValueError('recall influence parent escapes candidate scope')
        parent, child = facts[edge.parent_fact_id], facts[edge.child_fact_id]
        if not parent.accepted_runtime_ordinal < child.accepted_runtime_ordinal < before_runtime_ordinal:
            raise ValueError('recall influence must stay strictly in the past')
        incoming.setdefault(edge.child_fact_id, []).append(edge)
    pending, visited, result = list(roots), set(), {}
    while pending:
        child_id = pending.pop()
        if child_id in visited:
            continue
        visited.add(child_id)
        for edge in incoming.get(child_id, ()):
            result[edge.fingerprint] = thaw_model_data(edge.to_record())
            pending.append(edge.parent_fact_id)
    return [result[key] for key in sorted(result)]
