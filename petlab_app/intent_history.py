"""Pet adapter: collect accepted host receipts for the six-seven package."""
from model_packages.cx_six_seven_v1.intention_history import KEY,extend
from model_packages.cx_six_seven_v1 import intention_facts

FACTS_KEY=intention_facts.VERSION


def capture_interaction(app,event_id):
    """Consume an already accepted ongoing Tick, before advancing its position."""
    from .intention_source import enabled
    if not enabled(app):return
    from .interaction_observer import authorize_interaction_tick,KEY
    from .owned_journals import publish_session
    from dataclasses import replace
    if event_id in app.runtime.session.profile.get(FACTS_KEY,{}):return
    authorize_interaction_tick(app,event_id)
    row=app.runtime.session.profile[KEY]['ticks'][event_id]
    records=dict(app.runtime.session.profile.get(FACTS_KEY,{}))
    records[event_id]=intention_facts.form(owner=app.owner_ref,source_ref=event_id,
        grant_ref=row['fingerprint'],at_ms=app.runtime.session.position.position.coordinate,eligible=True)
    publish_session(app,replace(app.runtime.session,profile={**app.runtime.session.profile,FACTS_KEY:records}))

def capture_formations(app,peer,*,previous_peer):
    """Persist new accepted grants; old archives are not backfilled with dates."""
    old=app.runtime.session.profile.get(FACTS_KEY,{})
    records=dict(old)
    for source,row in peer.get('assessments',{}).items():
        if source in previous_peer.get('assessments',{}):continue
        if not row['eligible'] or row.get('carried_from'):continue
        from framework.model_api import model_data_fingerprint
        if row['fingerprint']!=model_data_fingerprint({k:v for k,v in row.items() if k!='fingerprint'}):
            raise ValueError('intention qualification receipt changed')
        fact=app.runtime.memory_instance(source).fact
        if row['fact_fingerprint']!=fact.fingerprint or row['position_fingerprint']!=app.runtime.session.position.fingerprint:
            raise ValueError('intention qualification source differs')
        records[source]=intention_facts.form(owner=app.owner_ref,source_ref=source,
            grant_ref=row['fingerprint'],at_ms=app.runtime.session.position.position.coordinate,eligible=True)
    return records

def capture(profile,*,owner,peer=None,previous_peer=None):
    """Join already accepted qualification and outcomes without emitting input."""
    result=profile.get(KEY)
    for source,row in (peer or {}).get('assessments',{}).items():
        if previous_peer is not None:
            old=previous_peer.get('assessments',{}).get(source)
            if (old==row and previous_peer.get('outcomes',{}).get(source)==(peer or {}).get('outcomes',{}).get(source)):
                continue
        result=extend(result,owner=owner,source=source,grant=row['fingerprint'],
            eligible=row['eligible'],status=(peer or {}).get('outcomes',{}).get(source,{}).get('status'))
    return result
