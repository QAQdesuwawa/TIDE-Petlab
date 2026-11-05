"""One human ingress may prepare while an earlier private fact is measured.

Transport overlaps; the Actor alone accepts facts and performs calibration.
The original provider payload stays immutable, including its earlier context.
"""
from contextlib import contextmanager
from threading import get_ident
from framework.model_api import model_data_fingerprint as fp,thaw_model_data

KEY='desktop-pet-measurement-prefetch-v1'
OUTPUT_KEY='desktop-pet-measurement-prefetch-v2-output'
REBASE='desktop-pet-measurement-rebase-v1'


def reply_scope(method):
    from functools import wraps
    @wraps(method)
    def call(product,event_id,*args,**kwargs):
        app=product.application;previous=getattr(app,'_prefetch_reply_source',None)
        app._prefetch_reply_source=event_id
        try:return method(product,event_id,*args,**kwargs)
        finally:app._prefetch_reply_source=previous
    return call


def _tail(app,row):
    tail=row.get('tail')
    if tail is None:return None
    from .deferred_intention import marker
    saved=marker(app,tail['source_ref'])
    if saved is None or saved['memory_id']!=tail['event_id'] or saved['fingerprint']!=tail['marker']:
        raise ValueError('prefetched output tail differs')
    if row['version']!=OUTPUT_KEY:raise ValueError('private prefetch cannot acquire an output tail')
    if tail['source_ref'] in app._product()['sources']:
        from .product import PetProduct
        from types import SimpleNamespace
        origin=app.runtime.memory_instance(tail['source_ref']).fact
        output=app._product()['sources'][row['parent_id']]
        if PetProduct._reply_source_key(SimpleNamespace(application=app),output)!=(origin.accepted_runtime_ordinal,origin.temporal_position_fingerprint):
            raise ValueError('prefetched tail belongs to another reaction')
    return tail


@contextmanager
def tail_admission(app,source_ref,identity):
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from model_packages.desktop_pet_v1.events import PetEvent
    journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    children=[s for k,s in app._product()['sources'].items() if k not in journal and 'prefetch' in s]
    previous=getattr(app,'_measurement_tail_admission',None)
    if children:
        if len(children)!=1:raise ValueError('multiple pending prefetched inputs')
        event=PetEvent.from_record(children[0]['event']);row=provenance(app,event);tail=_tail(app,row)
        if tail is None or tail['source_ref']!=source_ref or tail['event_id']!=identity or not after_parent(app,event):
            raise ValueError('unrelated settlement cannot pass prefetched input')
        app._measurement_tail_admission=(event.event_id,identity,get_ident())
    try:yield
    finally:app._measurement_tail_admission=previous


def stash(app,event_id,payload):
    product=app._product(source_id=event_id);source=product['sources'][event_id]
    if 'prefetched_payload' in source:
        if source['prefetched_payload_fingerprint']!=fp(payload):raise ValueError('prefetched payload changed')
        return
    source['prefetched_payload']=thaw_model_data(payload)
    source['prefetched_payload_fingerprint']=fp(payload)
    app._publish_product(product)


@contextmanager
def admission(app,parent):
    if getattr(app,'_measurement_admission',None) is not None:raise RuntimeError('nested measurement admission')
    app._measurement_admission=(parent,get_ident())
    try:yield
    finally:app._measurement_admission=None


def permits(app,pending):
    tail=getattr(app,'_measurement_tail_admission',None)
    if tail is not None and tail[2]==get_ident() and pending==[tail[0]]:return True
    active=getattr(app,'_measurement_admission',None)
    return (active is not None and active[1]==get_ident() and pending==[active[0]]
        and app._product()['sources'][active[0]]['origin'] in {'cx67_private_intention','execution'})


def bind(app,event,product):
    active=getattr(app,'_measurement_admission',None)
    if active is None:return
    if active[1]!=get_ident():raise RuntimeError('measurement admission requires owner')
    parent=product['sources'][active[0]]
    from model_packages.desktop_pet_v1.events import PetEvent
    parent_event=PetEvent.from_record(parent['event'])
    row={'version':KEY,'parent_id':parent_event.event_id,'parent_source':parent_event.source_binding_ref,
         'event_source':event.source_binding_ref,'position':app.runtime.session.position.to_record()}
    if parent['origin']=='execution':
        row['version']=OUTPUT_KEY
        source_ref=getattr(app,'_prefetch_reply_source',None)
        if source_ref is not None:
            from .deferred_intention import marker
            from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
            tail=marker(app,source_ref)
            if tail is not None and tail['memory_id'] not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
                row['tail']={'source_ref':source_ref,'event_id':tail['memory_id'],'marker':tail['fingerprint']}
    row['fingerprint']=fp(row)
    product['sources'][event.event_id]['prefetch']=row


def provenance(app,event):
    source=app._product()['sources'][event.event_id];row=source.get('prefetch')
    if row is None:return None
    row=thaw_model_data(row)
    if row.get('version') not in {KEY,OUTPUT_KEY} or row['fingerprint']!=fp({k:v for k,v in row.items() if k!='fingerprint'}):
        raise ValueError('measurement prefetch provenance differs')
    from model_packages.desktop_pet_v1.events import PetEvent
    parent=app._product()['sources'][row['parent_id']]
    parent_event=PetEvent.from_record(parent['event']);app._verify_source(parent_event)
    origin='execution' if row['version']==OUTPUT_KEY else 'cx67_private_intention'
    if parent['origin']!=origin or row['parent_source']!=parent_event.source_binding_ref or row['event_source']!=event.source_binding_ref:
        raise ValueError('measurement prefetch source differs')
    _tail(app,row)
    return row


def after_parent(app,event,prepared_position=None):
    row=provenance(app,event)
    if row is None:return False
    if prepared_position is not None and row['position']!=prepared_position:return False
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    if row['parent_id'] not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):return False
    fact=app.runtime.memory_instance(row['parent_id']).fact
    if fact.accepted_runtime_ordinal!=row['position']['runtime_ordinal']+1:return False
    tail=_tail(app,row)
    if tail is not None and tail['event_id'] in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
        next_fact=app.runtime.memory_instance(tail['event_id']).fact
        if next_fact.accepted_runtime_ordinal!=fact.accepted_runtime_ordinal+1:return False
        fact=next_fact
    return (fact.accepted_runtime_ordinal==app.runtime.session.position.runtime_ordinal
        and fact.temporal_position_fingerprint==app.runtime.session.position.position.fingerprint)


def rebase(service,event,payload):
    app=service.application
    if provenance(app,event) is None:return payload
    if not after_parent(app,event):raise ValueError('prefetched measurement requires its immediate settled parent')
    tail=_tail(app,provenance(app,event))
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    if tail is not None and tail['event_id'] not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
        raise ValueError('prefetched input must follow the recorded output tail')
    from product_adapters.desktop_pet_v1.calibration import KEY as CAL,reference
    original=thaw_model_data(payload);value=thaw_model_data(payload)
    if CAL in value:
        value[CAL]=reference(app.runtime,event,calibration_version=value[CAL]['calibration_version'])
    row={'version':REBASE,'original_fingerprint':fp(original),'measurement_fingerprint':fp(value),
         'parent_fact':app.runtime.memory_instance(provenance(app,event)['parent_id']).fact.fingerprint}
    if tail is not None:row['tail_fact']=app.runtime.memory_instance(tail['event_id']).fact.fingerprint
    row['fingerprint']=fp(row)
    product=app._product(source_id=event.event_id)
    product['sources'][event.event_id]['measurement_rebase']=row
    app._publish_product(product)
    return value


def verify_payload(app,event,original,cached):
    row=app._product()['sources'][event.event_id].get('measurement_rebase')
    if row is None:return False
    if row.get('version')!=REBASE or row['fingerprint']!=fp({k:v for k,v in row.items() if k!='fingerprint'}):return False
    binding=provenance(app,event)
    if binding is None or row['parent_fact']!=app.runtime.memory_instance(binding['parent_id']).fact.fingerprint:return False
    tail=_tail(app,binding)
    if tail is not None and row.get('tail_fact')!=app.runtime.memory_instance(tail['event_id']).fact.fingerprint:return False
    from product_adapters.desktop_pet_v1.calibration import KEY as CAL
    a=thaw_model_data(original);b=thaw_model_data(cached)
    if row['original_fingerprint']!=fp(a) or row['measurement_fingerprint']!=fp(b):return False
    a.pop(CAL,None);b.pop(CAL,None)
    return fp(a)==fp(b)


def pending(app):
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    return any(k not in journal and 'prefetch' in v for k,v in app._product()['sources'].items())


def measure(service,event,work):
    """Prepare at most one sealed queued input; do not dequeue or reply here."""
    from concurrent.futures import ThreadPoolExecutor,wait
    app=service.application;supplier=getattr(service,'waiting_input',None)
    child_waiting=pending(app)
    if supplier is None or child_waiting or app._product()['sources'][event.event_id]['origin'] not in {'cx67_private_intention','execution'}:
        work.transport();value=work.finish()
        if child_waiting and app._product()['sources'][event.event_id]['origin']=='cx67_private_intention':
            stash(app,event.event_id,value)
        return value
    child=None;completed=False;child_saved=False
    pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='pet-measurement')
    try:
        parent=pool.submit(work.transport)
        try:
            while not parent.done():
                if child is None:
                    previous=getattr(app,'_measurement_preparing_source',None)
                    source=app._product()['sources'][event.event_id]
                    app._measurement_preparing_source=(source['evidence']['source_ref']
                        if source['origin']=='cx67_private_intention'
                        else getattr(app,'_prefetch_reply_source',None))
                    try:args=supplier()
                    finally:app._measurement_preparing_source=previous
                    if args is not None:
                        with admission(app,event.event_id):new=app.accept_input(**args)
                        next_work=service.prepare(new)
                        future=pool.submit(next_work.transport)
                        child=(next_work,future,new.event_id)
                        if not hasattr(service,'_prefetched'):service._prefetched={}
                        service._prefetched[new.event_id]=(next_work,future)
                if child is not None and not child_saved and child[1].done():
                    child[1].result();stash(app,child[2],child[0].finish())
                    child_saved=True
                wait((parent,),timeout=.01)
            parent.result()
            result=work.finish()
            if child is not None:stash(app,event.event_id,result)
            completed=True
            return result
        finally:
            # A failed parent must still preserve a received child response;
            # never automatically resend it during explicit recovery.
            if child is not None and not completed and not child_saved:
                child[1].result()
                stash(app,child[2],child[0].finish())
    finally:
        # Parent acceptance may run while the child's transport is still active.
        # Its owner-thread parse is retained for the ordinary input consumer.
        pool.shutdown(wait=not completed)


def finish_pending(service):
    """On orderly close preserve received responses, without accepting history."""
    for event_id,(work,future) in tuple(getattr(service,'_prefetched',{}).items()):
        future.result()
        stash(service.application,event_id,work.finish())
        del service._prefetched[event_id]


def recover(product,sources):
    children=[s for s in sources if 'prefetch' in s]
    if len(children)!=1:return None
    from model_packages.desktop_pet_v1.events import PetEvent
    child=children[0];event=PetEvent.from_record(child['event']);app=product.application
    row=provenance(app,event)
    tail=_tail(app,row)
    allowed={event.event_id,row['parent_id']}|({tail['event_id']} if tail is not None else set())
    if any(s['event']['event_id'] not in allowed for s in sources):return None
    parent=next((s for s in sources if s['event']['event_id']==row['parent_id']),None)
    if parent is not None:
        parent_event=PetEvent.from_record(parent['event'])
        if parent['origin']=='execution':product._recover_output(parent_event)
        else:app._settle(parent_event,product.semantic.retry_measure)
    if tail is not None:
        from .intention_source import ingest
        with tail_admission(app,tail['source_ref'],tail['event_id']):
            ingest(app,source_ref=tail['source_ref'],coordinate=app.runtime.session.position.position.coordinate,measure=product.semantic.retry_measure)
    return [child]


def settle_peer_before_reply(product,source_ref):
    """Consume the already-dispatched peer following this formation.

    Same owner, same settlement/qualification path; never another HTTP retry.
    Return a newly qualified delivery for the existing takeover mechanism.
    """
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from .peer_reaction import is_peer,assess,react
    from model_packages.desktop_pet_v1.events import PetEvent
    app=product.application;data=app._product()
    journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    children=[(k,s) for k,s in data['sources'].items() if k not in journal and 'prefetch' in s]
    if not children:return None
    if len(children)!=1:raise ValueError('multiple pending prefetched inputs')
    identity,child=children[0];parent=data['sources'][child['prefetch']['parent_id']]
    if parent['origin']!='cx67_private_intention' or parent['evidence']['source_ref']!=source_ref:return None
    if not is_peer(app,identity):return None
    event=PetEvent.from_record(child['event'])
    app._settle(event,product.semantic.measure)
    grant=assess(app,identity)
    if grant['eligible']:
        import json
        receipt=child['evidence']['input_source']
        artifact=app.store.path.with_suffix('.resources')/receipt['artifact']
        # _settle verified this source/visibility/artifact before acceptance.
        return json.loads(artifact.read_text('utf8'))['delivery']['delivery_id']
    from .retained_intention import retain
    retain(app,source_ref,identity)
    react(product,identity,executor=None)
    return None
