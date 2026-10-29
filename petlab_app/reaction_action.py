"""Package body selection at an existing input opportunity (no extra trigger)."""
from dataclasses import replace
from framework.model_api import thaw_model_data,model_data_fingerprint as fp
from model_packages.desktop_pet_v1 import shared_meal,action_candidates
KEY='petlab-input-body-selection-v1'

def selection(app,event_id):
    from .idle_opening import is_idle_tick,source
    if is_idle_tick(app,event_id):return thaw_model_data(source(app,event_id,current=True)[0].row.get('behavior_selection'))
    row=app.runtime.session.profile.get(KEY,{}).get(event_id)
    if row is None:return None
    row=thaw_model_data(row)
    if row['fingerprint']!=fp({k:v for k,v in row.items() if k!='fingerprint'}):raise ValueError('body selection receipt changed')
    return action_candidates.validate(row['selection'])

def authorization(app,event_id):
    from .idle_opening import is_idle_tick,authorize as idle_authorize
    if is_idle_tick(app,event_id):return idle_authorize(app,event_id)
    from product_adapters.desktop_pet_v1.expression import PetExpressionService
    life,required=PetExpressionService(application=app).project_for_reply(event_id)
    if life.evidence.response_policy=='eligible':
        from .peer_reaction import is_peer,authorize
        if is_peer(app,event_id):return authorize(app,event_id)
        from .interaction_observer import authorize_interaction_tick
        return authorize_interaction_tick(app,event_id)
    return required

def ensure(product,event_id):
    app=product.application
    if event_id in app.runtime.session.profile.get(KEY,{}):return selection(app,event_id)
    supplier=getattr(app,'meal_snapshot',None)
    scene=supplier() if callable(supplier) else None
    if scene is None:return None
    from product_adapters.desktop_pet_v1 import deferred_reply
    if deferred_reply.needed(app,event_id):return None
    auth=authorization(app,event_id)
    if auth is None:return None
    if app.owner_ref not in scene['participants']:return None
    if auth.target_ref not in scene['participants'] and not shared_meal.available(scene,app.owner_ref,auth.target_ref):return None
    from product_adapters.desktop_pet_v1.settled_presentation import snapshot
    settled=snapshot(app)
    if settled is None:return None
    plan=settled['reaction']['expression_plan']
    from model_packages.desktop_pet_v1 import movement
    spatial_supplier=getattr(app,'scene_snapshot',None)
    spatial_scene=spatial_supplier() if callable(spatial_supplier) else None
    base=movement.catalog(scene=spatial_scene,target_ref=auth.target_ref)
    catalog=shared_meal.catalog(base,scene,app.owner_ref,auth.target_ref)
    chosen=action_candidates.choose(eligible=True,tokens=action_candidates.compact_tokens(plan),source=auth.source_settlement_fingerprint,catalog=catalog,allow_speech=product.writer is not None)
    from model_packages.desktop_pet_v1.action_sequence import offer
    offered=offer(chosen,catalog,owner_ref=app.owner_ref,target_ref=auth.target_ref)
    row=shared_meal.signed({'event_id':event_id,'selection':chosen,'authorization_ref':auth.fingerprint,
        **({'action_sequence_context':offered} if offered is not None else {})})
    from .owned_journals import publish_session
    data=dict(app.runtime.session.profile.get(KEY,{}));data[event_id]=row
    publish_session(app,replace(app.runtime.session,profile={**app.runtime.session.profile,KEY:data}))
    return chosen

def context(app,event_id):
    # Common non-meal events need no repeated idle source/proof traversal.
    from .idle_opening import KEY as IDLE_KEY
    raw=app.runtime.session.profile.get(IDLE_KEY,{}).get('ticks',{}).get(event_id,{}).get('behavior_selection')
    if raw is None:raw=app.runtime.session.profile.get(KEY,{}).get(event_id,{}).get('selection')
    if not raw or 'meal' not in (raw.get('definition') or {}):return None
    chosen=selection(app,event_id)
    if not chosen or not chosen['action_keys'] or 'meal' not in (chosen.get('definition') or {}):return None
    definition=chosen['definition'];bound=shared_meal.verify_binding(definition['meal'])
    return {'version':shared_meal.VERSION,'selection_ref':chosen['fingerprint'],'scene':bound['scene'],
        'actor_ref':bound['actor_ref'],'target_ref':bound['target_ref'],'key':bound['key'],'description':definition['description']}
