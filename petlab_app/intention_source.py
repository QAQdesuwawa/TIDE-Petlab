"""Adapt six-seven private formation evidence to the existing self-event path.

Explicit entry until permission rebinding is integrated. No playback or peer send.
"""
from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from model_packages.cx_six_seven_v1.intention_facts import validate, VERSION as FACTS_KEY
from model_packages.cx_six_seven_v1.intention_semantics import formation_text
from model_packages.desktop_pet_v1.events import PetEvent, Thought

ORIGIN = 'cx67_private_intention'
VERSION = 'cx67-intention-formation-source-v1'


def enabled(app):
    from model_packages.cx_six_seven_v1.intention_facts import ACTIVATION
    return app.runtime.session.profile.get(ACTIVATION)=={'version':VERSION}


def enable(app):
    """Explicit opt-in, preserving already-bound histories and original math."""
    from dataclasses import replace
    from model_packages.cx_six_seven_v1.intention_facts import ACTIVATION
    from .owned_journals import publish_session
    with app._lock:
        profile=dict(app.runtime.session.profile)
        if ACTIVATION in profile and not enabled(app):
            raise ValueError('unknown intention memory activation version')
        if enabled(app):return
        profile[ACTIVATION]={'version':VERSION}
        publish_session(app,replace(app.runtime.session,profile=profile))


def build(app, *, source_ref, coordinate):
    facts = app.runtime.session.profile.get(FACTS_KEY, {}).get(source_ref, ())
    validate(facts)
    if not facts or facts[0]['owner'] != app.owner_ref:
        raise ValueError('no owned accepted intention')
    first = facts[0]
    from .idle_opening import is_idle_tick,source as idle_source
    if is_idle_tick(app,source_ref):
        tick,accepted=idle_source(app,source_ref)
        if accepted.authorization is None or first['evidence_ref']!=tick.row['fingerprint']:
            raise ValueError('intention formation lacks original Tick qualification')
        grant_ref=tick.row['fingerprint'];parent_ref=tick.row['source_acceptance_fingerprint']
        from .destinations import primary
        target=primary(app,source_ref);scope=app.definition.scope_ref
    elif source_ref in app.runtime.session.profile.get('desktop_pet_interaction_observations_v1',{}).get('ticks',{}):
        from .interaction_observer import KEY,_verify
        grant=thaw_model_data(app.runtime.session.profile[KEY]['ticks'][source_ref]);_verify(grant)
        parent=app.runtime.memory_instance(source_ref)
        if (not grant['eligible'] or grant['fingerprint']!=first['evidence_ref']
                or grant.get('intention_parent_fact')!=parent.fact.fingerprint):
            raise ValueError('intention formation lacks accepted interaction qualification')
        grant_ref=grant['fingerprint'];parent_ref=parent.fact.fingerprint
        target=parent.event.actor_ref;scope=parent.event.scope_ref
    else:
        from .peer_reaction import KEY
        grant = app.runtime.session.profile.get(KEY, {}).get('assessments', {}).get(source_ref)
        if (grant is None or not grant['eligible'] or grant.get('carried_from')
                or grant['fingerprint'] != first['evidence_ref']
                or fp({k:v for k,v in grant.items() if k!='fingerprint'}) != grant['fingerprint']):
            raise ValueError('intention formation lacks original qualification')
        parent = app.runtime.memory_instance(source_ref)
        if grant['fact_fingerprint'] != parent.fact.fingerprint:
            raise ValueError('intention parent differs from qualification')
        grant_ref=grant['fingerprint'];parent_ref=parent.fact.fingerprint
        target=parent.event.actor_ref;scope=parent.event.scope_ref
    position = app.runtime.temporal_runtime.normalize_position(coordinate)
    if coordinate < first['at_ms']:
        raise ValueError('intention settlement precedes formation')
    identity = 'intention:' + first['intention_id']
    evidence = {'version':VERSION, 'formation_ref':first['fingerprint'],
                'source_ref':source_ref, 'grant_ref':grant_ref,
                'parent_fact_ref':parent_ref,
                'occurred_at_ms':first['at_ms'], 'settlement_position':position.to_record()}
    event = PetEvent(event_id=identity, source_ref='intention:'+first['fingerprint'],
        source_receipt_ref=fp(evidence), owner_ref=app.owner_ref, actor_ref=app.owner_ref,
        target_refs=(target,), scope_ref=scope,
        position=position, event_kind='npc_output',
        contents=(Thought(content_id=identity+':formation', description=formation_text(facts),
                          recipient_refs=(app.owner_ref,)),))
    return event, evidence


def verify(app, event, source):
    proof = source.get('evidence', {})
    if proof.get('version') != VERSION or source.get('origin') != ORIGIN:
        raise ValueError('intention source version differs')
    expected, evidence = build(app, source_ref=proof['source_ref'], coordinate=event.position.coordinate)
    if (fp(evidence) != fp(proof) or expected.to_record() != event.to_record()
            or fp(source.get('event')) != fp(event.to_record())):
        raise ValueError('intention source binding differs')


def ingest(app, *, source_ref, coordinate, measure):
    """Persist once, then use original measurement/settlement/History transaction."""
    with app._lock:
        facts = app.runtime.session.profile.get(FACTS_KEY, {}).get(source_ref, ())
        if not facts:
            raise ValueError('no accepted intention')
        identity = 'intention:' + facts[0]['intention_id']
        product = app._product(source_id=identity)
        old = product['sources'].get(identity)
        if old is not None:
            event = PetEvent.from_record(old['event'])
            verify(app, event, old)
        else:
            app._require_ready()
            event, evidence = build(app, source_ref=source_ref, coordinate=coordinate)
            product['sources'][identity] = {'origin':ORIGIN, 'event':event.to_record(), 'evidence':evidence}
            app._publish_product(product)
        return app._settle(event, measure)
