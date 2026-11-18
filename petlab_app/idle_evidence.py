"""Collect already accepted owner-local evidence for idle planning."""
from statistics import median

from model_packages.cx_six_seven_v1.continuous_root_projection import (
    CONTINUOUS_ROOT_PROJECTION_B_V1, project_continuous_roots,
)
from model_packages.cx_six_seven_v1.interaction_geometry import relationship_transition
from model_packages.cx_six_seven_v1.preference_parameters import CONSTANT_ORDER
from model_packages.desktop_pet_v1.autonomous_opening import VERSION, weighted_changes
from model_packages.desktop_pet_v1.web_observation import is_observation
from model_packages.desktop_pet_v1.relation_package import META
from model_packages.desktop_pet_v1.peer_opening import KEY as SPEECH_USE, matches
from product_adapters.desktop_pet_v1.event_timeline import occurrence
from product_adapters.desktop_pet_v1.settlement_audit import verify

from .session import PRODUCT_KEY

SOURCE_CLOCK = 'committed-occurrence-v1'


def _continuation(app, fact, event, source):
    from .peer_reaction import KEY, _verify

    journal = app.runtime.session.profile.get(KEY, {})
    assessment = journal.get('assessments', {}).get(fact.fact_id)
    if assessment is None or 'speech_use_assessment' not in assessment:
        return None
    _verify(assessment)
    proof = assessment['speech_use_assessment']
    use = source.get('measurement', {}).get(SPEECH_USE)
    if (assessment['fact_fingerprint'] != fact.fingerprint
            or tuple(proof['edge']) != (app.owner_ref, event.actor_ref)
            or not matches(use, event) or use['kind'] != proof['kind']):
        raise ValueError('idle continuation differs from accepted same-edge speech')
    outcome = journal.get('outcomes', {}).get(fact.fact_id)
    if outcome is not None:
        _verify(outcome)
    # An explicit quiet decision or an actual reply closes this opportunity.
    # Only an ineligible input may be rechecked by a later, formal Tick.
    exchange = {name: {'reference_level': row['reference_level'],
                       'weighted_change': row['weighted_change']}
                for name, row in proof['exchange_evidence'].items()
                } if outcome is not None and outcome['status'] == 'ineligible' else {}
    return {'kind': proof['kind'], 'assessment': assessment['fingerprint'],
            'reference': proof['reference'], 'exchange_evidence': exchange,
            'closed': outcome is not None and outcome['status'] in ('executed', 'quiet')}


def collect(app, *, now, horizon, ticks):
    """Build per-edge evidence from accepted facts before the current plan."""
    sources = app.runtime.session.profile[PRODUCT_KEY]['sources']
    binding = app.runtime.session.parameters.parameters['constant_binding']
    events = {}
    for fact in reversed(app.runtime._history().facts()):
        source = sources.get(fact.fact_id, {})
        received_at = source.get('event', {}).get('position', {}).get('coordinate')
        if received_at is None or received_at > now:
            continue
        # Owner history is ordered by settlement, while a late ingress may
        # carry an older, independently archived source time.
        if received_at < now - horizon:
            break
        if source.get('origin') not in ('input', 'execution'):
            continue
        node = fact.model_payload.get('runtime_full_memory_node', {}).get('node_projection', {})
        if not node or node['constant_binding'] != binding:
            continue
        memory = app.runtime.memory_instance(fact.fact_id)
        event = memory.event
        if source['origin'] == 'input':
            # The input resolver binds an ordinary peer input to its actor.
            # A web observation uses the active edge at that time instead.
            if event.event_kind != 'peer_input' or is_observation(event):
                continue
            peer = event.actor_ref
            at,_=occurrence(app,event,world_observations=True)
            if at < now - horizon:
                continue
        else:
            at=received_at
            audit = source.get('settlement_audit')
            if audit is None:
                continue
            verify(audit, event=event, fact=fact)
            meta = audit['state_after'].get(META, {})
            if meta.get('owner_ref') != app.owner_ref:
                continue
            peer = meta['active_peer']
        if peer not in app.multi_workspace.participants or peer == app.owner_ref:
            continue
        result = project_continuous_roots(
            transition=relationship_transition(node['x_before'], node['x_after']),
            constant_binding=node['constant_binding'], params=CONTINUOUS_ROOT_PROJECTION_B_V1)
        item = {'id':fact.fact_id, 'fact_fingerprint':fact.fingerprint,
                'at_ms':at, 'changes':weighted_changes(result.receipt)}
        if source['origin'] == 'input' and (proof := _continuation(app, fact, event, source)) is not None:
            item['continuation'] = proof
        events.setdefault(peer, []).append(item)
    quiet = {}
    consumed = set()
    for row in ticks.values():
        grant = row['grant']
        decision = grant['assessment_output']['action_assessment']
        proof = decision['constraints'].get('desktop_idle_opening')
        at = row['source_snapshot']['position']['position']['coordinate']
        if proof and proof['eligible'] and at <= now:
            consumed.add((tuple(proof['edge']), proof['anchor_event']))
        if decision['eligible'] or (proof and not proof['checks'].get('target_available', True)):
            continue
        projection = grant['shared_projection_output']['shared_projection']
        meta = projection['source_state_candidate'].get(META, {})
        if meta.get('owner_ref') != app.owner_ref:
            continue
        at = row['source_snapshot']['position']['position']['coordinate']
        run_ms = at - row['plan']['origin_coordinate']
        peer = meta['active_peer']
        if run_ms > 0 and now-horizon <= at <= now:
            rate = abs(weighted_changes(projection['receipt'])['b1']) / run_ms
            if rate > quiet.get(peer, {}).get('pressure_per_ms', 0):
                quiet[peer] = {'tick_id':row['tick_id'], 'tick_fingerprint':row['fingerprint'],
                               'pressure_per_ms':rate}

    def for_peer(peer):
        recent = sorted(events.get(peer, ()), key=lambda item: item['at_ms'], reverse=True)
        edge = (app.owner_ref, peer)
        return {'version': VERSION, 'clock_basis':SOURCE_CLOCK,
                'edge':list(edge), 'planned_at_ms':now, 'horizon_ms':horizon,
                'recent_events':recent, 'quiet_reference':quiet.get(peer),
                'anchor_consumed':bool(recent and ((edge, recent[0]['id']) in consumed
                                       or recent[0].get('continuation', {}).get('closed'))),
                'change_scales':{name:median(nonzero) if (nonzero := [
                    abs(event['changes'][name]) for event in recent if event['changes'][name]]) else 0.0
                    for name in CONSTANT_ORDER}}

    return for_peer
