"""Owner-thread evidence that a started effect is live, not awaiting recovery."""
from contextlib import contextmanager
from threading import get_ident


class PlaybackSettlementPending(Exception):
    """Actual playback is sealed; its already-dispatched input is still pending."""
    pass


@contextmanager
def running(app,action_id):
    previous=getattr(app,'_live_execution',None)
    if previous is not None:raise RuntimeError('nested external execution is not supported')
    app._live_execution=(action_id,get_ident())
    inputs=[];app._live_execution_inputs=inputs
    try:yield inputs
    finally:
        app._live_execution=None
        app._live_execution_inputs=None


def accepts_input(app,action_id,entry):
    return (entry['status']=='started'
        and getattr(app,'_live_execution',None)==(action_id,get_ident()))


def record_input(app,event,product):
    active=getattr(app,'_live_execution',None)
    if active is not None and active[1]==get_ident():
        ref={'event_id':event.event_id,'source_binding':event.source_binding_ref}
        app._live_execution_inputs.append(ref)
        # Published atomically with the accepted source, not only after UI return.
        from framework.model_api import thaw_model_data
        entry=dict(thaw_model_data(product['executions'][active[0]]))
        entry['live_input_sources']=[*entry.get('live_input_sources',()),ref]
        product['executions'][active[0]]=entry


def admitted_position(app,entry):
    from framework.runtime_kernel import RuntimePositionState
    raw=entry['admitted_position']
    position=RuntimePositionState.create(session_binding=app.runtime.session.session_binding,
        position=app.runtime.temporal_runtime.normalize_position(raw['position']['coordinate']),
        runtime_ordinal=raw['runtime_ordinal'])
    if position.to_record()!=raw:raise ValueError('execution admission position differs')
    return position


def verify_recovery(app,prepared,entry,*,allow_pending=False):
    """An admitted effect may finish after its bound inputs; never dispatch it again."""
    if admitted_position(app,entry)!=prepared.position or entry['prepared_fingerprint']!=prepared.fingerprint:
        raise ValueError('recovery differs from admitted candidate')
    from model_packages.desktop_pet_v1.events import PetEvent
    refs=entry.get('live_input_sources',())
    ids=[]
    for ref in refs:
        source=app._product()['sources'][ref['event_id']]
        event=PetEvent.from_record(source['event']);app._verify_source(event)
        if source['origin']!='input' or event.source_binding_ref!=ref['source_binding']:
            raise ValueError('recovery input binding differs')
        ids.append(event.event_id)
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    accepted=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    if len(ids)!=len(set(ids)) or (not allow_pending and any(k not in accepted for k in ids)):
        raise ValueError('recovery inputs are not settled')
    settled=[key for key in ids if key in accepted]
    if settled!=ids[:len(settled)]:raise ValueError('recovery input order differs')
    later=[event.event_id for event in app.runtime.events
        if app.runtime.memory_instance(event.event_id).fact.accepted_runtime_ordinal>prepared.position.runtime_ordinal]
    if later!=settled:raise ValueError('recovery crosses unrelated formal history')
    expected=(app.runtime.memory_instance(settled[-1]).fact.temporal_position_fingerprint
        if settled else prepared.position.position.fingerprint)
    if app.runtime.session.position.position.fingerprint!=expected:
        raise ValueError('recovery crosses an unrelated clock advance')
    return list(refs)


def recover_inputs(product,pending):
    """Only the sealed live-execution linkage permits two pending sources."""
    from model_packages.desktop_pet_v1.events import PetEvent
    app=product.application
    inputs=[s for s in pending if s['origin']=='input']
    outputs=[s for s in pending if s['origin']=='execution']
    if len(inputs)!=1 or len(outputs)!=1 or len(pending)!=2:
        raise ValueError('multiple pending sources lack a single live execution linkage')
    event=PetEvent.from_record(inputs[0]['event']);effect=PetEvent.from_record(outputs[0]['event'])
    app._verify_source(event);app._verify_source(effect)
    receipt=outputs[0]['evidence']['fact']['model_payload']['execution_receipt']
    refs=receipt['realization_evidence'].get('live_input_sources',())
    if not any(r['event_id']==event.event_id and r['source_binding']==event.source_binding_ref for r in refs):
        raise ValueError('pending input is not bound to the actual execution')
    if event.position.coordinate>effect.position.coordinate:
        raise ValueError('pending input occurs after the actual execution')
    settle_recovered_input(product,event.event_id,effect.event_id)
    return outputs


def recovery_projection(app,event_id,action_id=None):
    active=getattr(app,'_recovery_projection',None)
    if active is None or active[1:]!=(event_id,get_ident()):return False
    if action_id is not None and active[0]!=action_id:return False
    source=app._product()['sources'].get(event_id)
    entry=app._product()['executions'].get(active[0],{})
    if not source:return False
    memory=app.runtime.memory_instance(event_id)
    if not any(r['event_id']==event_id and r['source_binding']==memory.event.source_binding_ref
            for r in entry.get('live_input_sources',())):return False
    fact=memory.fact
    return (fact.accepted_runtime_ordinal==app.runtime.session.position.runtime_ordinal
        and fact.temporal_position_fingerprint==app.runtime.session.position.position.fingerprint)


def settle_recovered_input(product,event_id,action_id):
    """Called after the enclosing sealed execution's source linkage is verified."""
    app=product.application
    app.settle_received(event_id,measure=product.semantic.retry_measure)
    from .peer_reaction import is_peer,assess
    if not is_peer(app,event_id):return
    previous=getattr(app,'_recovery_projection',None)
    if previous is not None:raise RuntimeError('nested recovery projection')
    app._recovery_projection=(action_id,event_id,get_ident())
    try:
        if not recovery_projection(app,event_id,action_id):raise ValueError('recovery projection source differs')
        grant=assess(app,event_id)
        if grant['eligible']:
            from .execution_inputs import remember
            remember(app,action_id,event_id)
        else:
            from .peer_reaction import react
            react(product,event_id,executor=None)
    finally:app._recovery_projection=previous


def finish_recovered_inputs(product,action_id,refs):
    """Complete interrupted recovery before the old actual effect advances state."""
    app=product.application
    from .peer_reaction import is_peer,KEY,_verify
    from .execution_inputs import KEY as LIVE
    for ref in refs:
        memory=app.runtime.memory_instance(ref['event_id'])
        app._verify_source(memory.event)
        if memory.event.source_binding_ref!=ref['source_binding']:
            raise ValueError('recovery input binding differs')
        if not is_peer(app,memory.event.event_id):continue
        data=app.runtime.session.profile.get(KEY,{})
        grant=data.get('assessments',{}).get(memory.event.event_id)
        if grant is not None:
            _verify(grant)
            if grant['fact_fingerprint']!=memory.fact.fingerprint:raise ValueError('recovery grant source differs')
            binding=app.runtime.session.profile.get(LIVE,{}).get(memory.event.event_id)
            if grant['eligible'] and binding is not None:
                _verify(binding)
                if binding['action_id']!=action_id or binding['grant']!=grant['fingerprint']:
                    raise ValueError('recovery grant execution differs')
                continue
            if not grant['eligible'] and memory.event.event_id in data.get('outcomes',{}):continue
        settle_recovered_input(product,memory.event.event_id,action_id)
