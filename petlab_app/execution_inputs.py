"""Bind an input's existing qualification to the effect active when it arrived."""
from dataclasses import replace
from .peer_reaction import _signed,_verify,KEY as PEER
from .owned_journals import publish_session

KEY='cx67-live-execution-inputs-v1'


def remember(app,action_id,event_id):
    from .live_execution import accepts_input,recovery_projection
    entry=app._product()['executions'].get(action_id,{})
    if not entry or not (accepts_input(app,action_id,entry) or recovery_projection(app,event_id,action_id)):
        raise ValueError('input was not assessed during this live execution')
    fact=app.runtime.memory_instance(event_id).fact
    grant=app.runtime.session.profile.get(PEER,{}).get('assessments',{}).get(event_id)
    if grant is None:raise ValueError('live input lacks formal assessment')
    _verify(grant)
    if grant['version']!=PEER or grant['fact_fingerprint']!=fact.fingerprint:
        raise ValueError('live input qualification differs')
    if not grant['eligible']:return
    row=_signed({'version':KEY,'event_id':event_id,'action_id':action_id,
        'input_fact':fact.fingerprint,'grant':grant['fingerprint'],
        'prepared_fingerprint':entry['prepared_fingerprint']})
    rows=app.runtime.session.profile.get(KEY,{})
    if event_id in rows:
        if rows[event_id]!=row:raise ValueError('live input binding changed')
        return
    publish_session(app,replace(app.runtime.session,profile={**app.runtime.session.profile,KEY:{**rows,event_id:row}}))


def follows_completed_execution(app,event_id):
    row=app.runtime.session.profile.get(KEY,{}).get(event_id)
    if row is None:return False
    _verify(row)
    from .intent_history import FACTS_KEY
    chain=app.runtime.session.profile.get(FACTS_KEY,{}).get(event_id,())
    grant=app.runtime.session.profile.get(PEER,{}).get('assessments',{}).get(event_id)
    if grant is None:return False
    _verify(grant)
    product=app._product();entry=product['executions'].get(row['action_id'],{})
    fact=app.runtime.memory_instance(event_id).fact
    if (row['version']!=KEY or row['event_id']!=event_id or row['input_fact']!=fact.fingerprint
        or row['grant']!=grant['fingerprint'] or grant['version']!=PEER or not grant['eligible']
        or row['prepared_fingerprint']!=entry.get('prepared_fingerprint')):
        raise ValueError('live input source binding differs')
    if (not chain or chain[-1]['phase']!='formed' or entry.get('status')!='executed'
        or 'intention:'+chain[0]['intention_id'] in product['sources']
        or event_id in app.runtime.session.profile.get(PEER,{}).get('outcomes',{})):
        return False
    effect=app.runtime.memory_instance(row['action_id'])
    app._verify_source(effect.event)
    if effect.fact.accepted_runtime_ordinal!=fact.accepted_runtime_ordinal+1:return False
    position=app.runtime.session.position
    if effect.fact.accepted_runtime_ordinal==position.runtime_ordinal:
        return effect.fact.temporal_position_fingerprint==position.position.fingerprint
    # The previous response may settle its own deferred formation after its
    # effect. That exact tail is not an external event cancelling this grant.
    if position.runtime_ordinal!=effect.fact.accepted_runtime_ordinal+1:return False
    if not app.runtime.events:return False
    identity=app.runtime.events[-1].event_id
    source=product['sources'].get(identity,{})
    if source.get('origin')!='cx67_private_intention':return False
    parent=source['evidence']['source_ref']
    from .deferred_intention import marker
    saved=marker(app,parent)
    if saved is None or saved['memory_id']!=identity:return False
    from .product import PetProduct
    from types import SimpleNamespace
    original=app.runtime.memory_instance(parent).fact
    if PetProduct._reply_source_key(SimpleNamespace(application=app),product['sources'][row['action_id']])!=(original.accepted_runtime_ordinal,original.temporal_position_fingerprint):
        return False
    tail=app.runtime.memory_instance(identity)
    app._verify_source(tail.event)
    return (tail.fact.accepted_runtime_ordinal==position.runtime_ordinal
        and tail.fact.temporal_position_fingerprint==position.position.fingerprint)
