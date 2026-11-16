"""Read-only competition of scored, audience-authorized memory events."""

from types import SimpleNamespace

from framework.model_api import model_data_fingerprint
from interaction_extensions.content_attention import settle_memory_content_occupation
from model_packages.cx_six_seven_v1.memory_content_claims import MemoryContentClaim
from ops.product_default_required_reply import product_reference_content_occupation_policy

VERSION = 'desktop-pet-relative-memory-content-v1'


def select_documents(app, context, memories, numeric_ids, visibility, *, groups=None):
    from petlab_app.writer_visibility import filter_memories

    result = context['result']['content_scores']
    method, scores = result['method'], result['scores']
    candidates = context['content_score_sources']
    numeric = set(numeric_ids)
    ids = dict.fromkeys(i for doc in candidates for i in doc['member_event_ids'] if i not in numeric)
    allowed = {m.event.event_id for m in filter_memories(
        app, (memories[i] for i in ids), visibility, lane='rag')}
    strongest = {}
    for doc in candidates:
        score = scores[doc['event_id']]
        for identity in doc['member_event_ids']:
            if identity in allowed:
                strongest[identity] = max(score, strongest.get(identity, score))

    grouped = {}
    for identity, score in strongest.items():
        group = (groups or {}).get(identity, identity)
        grouped.setdefault(group, {})[identity] = score
    partial = result.get('partial_rerank', {}) if groups is not None else {}
    representatives = {group: max(members, key=members.get) for group, members in grouped.items()}
    strengths = {group: strongest[identity] for group, identity in representatives.items()}
    occupation = _occupy(context, method, strengths)
    selected_groups = set(occupation.selected_for_hydration_ids)

    # Recompare the covered groups on the rerank scale. Untested groups keep
    # their vector decision. They do not receive a fabricated rank score.
    if partial:
        ranked_members = {}
        for doc in candidates:
            if doc['event_id'] not in partial:
                continue
            for identity in doc['member_event_ids']:
                if identity in strongest:
                    score = partial[doc['event_id']]
                    ranked_members[identity] = max(score, ranked_members.get(identity, score))
        ranked_groups = {}
        for group in grouped:
            if group not in selected_groups:
                continue
            members = {i: ranked_members[i] for i in grouped[group] if i in ranked_members}
            if members:
                winner = max(members, key=members.get)
                ranked_groups[group] = members[winner]
                # The group can win because its input was relevant while its
                # representative preserves the actual reply and cognition.
        if ranked_groups:
            reranked = _occupy(context, 'partial_reranker', ranked_groups)
            selected_groups.difference_update(ranked_groups)
            selected_groups.update(reranked.selected_for_hydration_ids)
            receipt = {'vector': occupation.receipt, 'rerank': reranked.receipt,
                       'selected_groups': sorted(selected_groups)}
            receipt['fingerprint'] = model_data_fingerprint(receipt)
        else:
            receipt = occupation.receipt
    else:
        receipt = occupation.receipt

    selected = {representatives[group] for group in selected_groups}
    if groups is not None:
        # Bind the group decision to the exact facts actually hydrated.
        receipt = {'version': 'desktop-pet-exchange-content-occupation-v1',
                   'competition': receipt, 'selected_event_ids': sorted(selected)}
        receipt['fingerprint'] = model_data_fingerprint(receipt)
    documents = []
    for row in candidates:
        members = [i for i in row['member_event_ids'] if i in selected]
        if members:
            documents.append({**row, 'member_event_ids': members})
    # Keep the original common-scale scores for subsequent display ranking.
    return documents, scores, receipt


def _occupy(context, method, strongest):
    # Rescale within the current authorized pool so even uniformly weak
    # scores retain their order. The common offset supports negative cosine.
    low = min(0.0, min(strongest.values(), default=0.0))
    high = max(strongest.values(), default=0.0)
    offset = -low if low < 0 else 0.0
    span = high + offset
    if span > 0:
        strengths = {i: (score + offset) / span for i, score in strongest.items()}
    else:
        strengths = dict.fromkeys(strongest, 1.0)
    claims = tuple(MemoryContentClaim(
        event_id=i, strength=strength, claim_version=VERSION,
        source_evidence_fingerprint=model_data_fingerprint({
            'source': context['fingerprint'], 'event': i, 'method': method, 'score': strongest[i],
        })) for i, strength in strengths.items())
    occupation = settle_memory_content_occupation(
        claims=SimpleNamespace(version=VERSION, claims=claims),
        policy=product_reference_content_occupation_policy())
    return occupation
