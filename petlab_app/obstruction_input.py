"""A blocked movement segment observed through the existing eligible input path.

The host records a blocked destination, not a collision or somebody's intention.
Only the observing pet receives this fact. No automatic replay of the old plan.
"""
import json
from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from model_packages.desktop_pet_v1.obstruction import VERSION, content
from ops.storage import atomic_write_json
from .external_input import archive_root



def _index(app):
    path=archive_root(app)/'obstruction-pending.json'
    return path, json.loads(path.read_text('utf8')) if path.exists() else []


def pending(app):
    return _index(app)[1]




def enqueue(product,observation):
    app=product.application;o=thaw_model_data(observation)
    fields={'owner_ref','blocker_ref','action_id','content_id','target_ref','position','destination','blocker_bounds','occurred_at_ms'}
    if set(o)!=fields or o['owner_ref']!=app.owner_ref or o['blocker_ref']==app.owner_ref:
        raise ValueError('obstruction owner or fields differ')
    # Confirm the blocked segment. Earlier completed segments retain their
    # own actual-effect receipt, including across sealed-outcome recovery.
    from product_adapters.desktop_pet_v1.composite_journal import ParentJournal
    journal=ParentJournal(product.gateway.trace_dir/'parents',o['action_id'])
    state=journal.state()
    result=state.get('children',{}).get(o['content_id'],{}).get('result',{})
    actual=state.get('outcome',{}).get('details',{}).get('obstruction')
    if actual is None:
        actual=json.loads((journal.directory/'observations'/'obstruction.json').read_text('utf8'))
    if (not state.get('closed') or result.get('status')!='cancelled'
        or result.get('reason')!='destination_occupied' or result.get('cause_ref')!=o['blocker_ref'] or actual!=o):
        raise ValueError('obstruction lacks confirmed blocked-child receipt')
    root_ref=state['source']['source_event_id']
    if root_ref.startswith('obstruction:'):
        prior=archive_root(app)/(root_ref.replace(':','-')+'.json')
        root_ref=json.loads(prior.read_text('utf8'))['root_source_ref']
    situation={k:o[k] for k in ('owner_ref','blocker_ref','target_ref','position','destination','blocker_bounds')}
    situation['root_source_ref']=root_ref
    identity='obstruction:'+fp(situation)
    root=archive_root(app);root.mkdir(parents=True,exist_ok=True)
    path=root/(identity.replace(':','-')+'.json')
    index,queue=_index(app)
    if path.exists():
        row=json.loads(path.read_text('utf8'))
        # Replanning this same intention against unchanged geometry is one
        # observation. A later independent intention remains a new experience.
        from .peer_reaction import KEY
        if identity in app.runtime.session.profile.get(KEY,{}).get('outcomes',{}):return identity
    else:
        row={'version':VERSION,'event_id':identity,'reader_ref':app.owner_ref,
            'actor_ref':o['blocker_ref'],'session_binding_fingerprint':app.runtime.session.session_binding.fingerprint,
            'observation':o,'artifact':path.name,'root_source_ref':root_ref}
        row['fingerprint']=fp(row)
        atomic_write_json(str(path),row,durable=True)
    if identity not in queue:
        atomic_write_json(str(index),[*queue,identity],durable=True)
    return identity


def validate(app,receipt,contents,*,event_id=None):
    row=thaw_model_data(receipt)
    if (row.get('version')!=VERSION or row.get('reader_ref')!=app.owner_ref
        or row.get('session_binding_fingerprint')!=app.runtime.session.session_binding.fingerprint
        or row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'})
        or event_id is not None and row.get('event_id')!=event_id):raise ValueError('obstruction source differs')
    name=row['event_id'].replace(':','-')+'.json'
    if row.get('artifact')!=name or '/' in name or '\\' in name:raise ValueError('obstruction artifact differs')
    if json.loads((archive_root(app)/name).read_text('utf8'))!=row or tuple(contents)!=(content(row),):
        raise ValueError('obstruction observation changed')
    if row['actor_ref']!=row['observation']['blocker_ref']:raise ValueError('obstruction counterparty differs')
    return row


def pump(product,*,executor,is_cancelled=lambda:False,on_reply_ready=lambda:None):
    app=product.application;index,queue=_index(app)
    if not queue:return False
    identity=queue[0]
    row=json.loads((archive_root(app)/(identity.replace(':','-')+'.json')).read_text('utf8'))
    saved=app._product(source_id=identity)['sources'].get(identity)
    from .product import wall_clock_ms
    at=saved['event']['position']['coordinate'] if saved else max(wall_clock_ms(),app.runtime.session.position.position.coordinate)
    product.interact(event_id=identity,contents=(content(row),),coordinate=at,
        input_source=row,response_policy='eligible',executor=executor,is_cancelled=is_cancelled)
    from .peer_reaction import react
    react(product,identity,executor=executor,is_cancelled=is_cancelled,on_reply_ready=on_reply_ready)
    # A reaction may itself enqueue a *different* situation. Preserve it.
    fresh=_index(app)[1]
    atomic_write_json(str(index),[key for key in fresh if key!=identity],durable=True)
    return True
