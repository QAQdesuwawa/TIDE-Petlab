"""Presentation ranking for already qualified, authorized memory events."""

from math import isfinite

VERSION = 'desktop-pet-memory-effect-rerank-v1'
EFFECT_BONUS = 0.03
UNIFIED_VERSION = 'desktop-pet-three-route-writer-ranking-v1'
STRONGEST_VERSION = 'desktop-pet-three-route-writer-ranking-v2-strongest'
PARTIAL_VERSION = "desktop-pet-three-route-writer-ranking-v3-partial-rerank"
ROUTE_WEIGHTS = {'property': .35, 'rag': .40, 'numeric': .25}


def rank_routes(candidates, *, rag_status, numeric_status, rag_scale='reranker', version=UNIFIED_VERSION):
    """Rank exact source IDs for Writer hydration only."""
    if version not in (UNIFIED_VERSION, STRONGEST_VERSION, PARTIAL_VERSION):
        raise ValueError('unknown Writer route ranking version')
    strongest = version in (STRONGEST_VERSION, PARTIAL_VERSION)
    ranked = []
    for identity, scores in candidates.items():
        scale = scores.get("rag_scale", rag_scale) if version == PARTIAL_VERSION else rag_scale
        routes = {}
        for lane, weight in ROUTE_WEIGHTS.items():
            raw = scores.get(lane)
            if raw is not None and not isfinite(raw):
                raise ValueError('Writer retrieval score must be finite')
            status = 'scored' if raw is not None else {
                'property': 'no_match', 'rag': rag_status, 'numeric': numeric_status,
            }[lane]
            # Cosine has a signed range. The other routes already publish
            # nonnegative strengths on the unit interval.
            value = (raw + 1.) / 2. if raw is not None and lane == 'rag' and scale == 'embedding' else raw
            calibrated = None if value is None else max(0., min(1., value))
            routes[lane] = {'status': status, 'raw': raw, 'calibrated': calibrated}
            if not strongest:
                routes[lane]['weight'] = weight
            if lane == 'rag':
                routes[lane]['scale'] = scale
        if strongest:
            available = {lane: row['calibrated'] for lane, row in routes.items()
                         if row['calibrated'] is not None}
            winner = max(available, key=available.get, default=None)
            ranked.append({'event_id': identity, 'routes': routes,
                           'score': available.get(winner, 0.), 'winning_route': winner,
                           'ranking_version': version})
        else:
            total = sum(row['weight'] * (row['calibrated'] or 0.) for row in routes.values())
            ranked.append({'event_id': identity, 'routes': routes, 'score': total})
    return sorted(ranked, key=lambda row: (-row['score'], row['event_id']))


def execution_credit(source):
    if source['origin'] != 'execution':
        return 0.0, None
    receipt = source['evidence']['fact']['model_payload']['execution_receipt']
    actual = {content['content_id'] for content in receipt['completed_contents']
              if content['kind'] in ('speech', 'action', 'expression')}
    details = receipt['outcome']['details']
    # Ignore omitted channels. An attempted but cancelled child still counts
    # toward the planned work. A private thought never counts as playback.
    children = {identity: row for identity, row in details.get('child_results', {}).items()
                if row.get('reason') != 'no_content_selected'
                and identity not in {c['content_id'] for c in receipt['completed_contents']
                                     if c['kind'] == 'thought'}}
    if children:
        credit = sum(1.0 if children.get(i, {}).get('status') == 'completed'
                     else 0.5 if children.get(i, {}).get('status') == 'interrupted' else 0.0
                     for i in actual) / len(children)
    else:
        credit = (0.5 if details.get('interrupted') else 1.0) if actual else 0.0
    return credit, receipt.get('fingerprint')


def rank(layer_scores, sources):
    rows = []
    for identity, scores in layer_scores.items():
        relevance = max(scores.values())
        credit, receipt = execution_credit(sources[identity])
        rows.append({'event_id': identity, 'layer_scores': dict(scores),
                     'relevance': relevance, 'effect_credit': credit,
                     'execution_receipt': receipt, 'score': relevance + EFFECT_BONUS * credit})
    return sorted(rows, key=lambda row: -row['score'])
