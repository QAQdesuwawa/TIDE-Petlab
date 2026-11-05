"""One input-driven eligibility opportunity after a committed NPC delivery.

No synthetic Tick, required policy, additional measurement or state formula.
"""
from dataclasses import replace
from framework.action_protocol import ActionAuthorization
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from framework.model_api.action import read_action_assessment
from product_adapters.desktop_pet_v1.expression import PetExpressionService
from .owned_journals import publish_session
from .product import wall_clock_ms

KEY='desktop-pet-peer-reaction-v1'
ORIGIN='eligible_peer_reply_v1'
BOUNDS={'min_interval_ms':2000,'max_interval_ms':300000}
CARRY='desktop-pet-peer-intent-continuation-v1'

def _signed(row):return {**row,'fingerprint':fp(row)}
def _verify(row):
 if row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'}):raise ValueError('peer reaction receipt differs')
def _journal(app):
 old=app.runtime.session.profile.get(KEY,{})
 return {'version':KEY,'assessments':dict(old.get('assessments',{})),'outcomes':dict(old.get('outcomes',{})),
  **({'reply_destinations':dict(old['reply_destinations'])} if 'reply_destinations' in old else {})}
def _save(app,data):
 from .intent_history import capture,KEY as INTENT_KEY,capture_formations,FACTS_KEY
 profile={**app.runtime.session.profile,KEY:data}
 facts=capture_formations(app,data,previous_peer=_journal(app))
 if facts:profile[FACTS_KEY]=facts
 intentions=capture(profile,owner=app.owner_ref,peer=data,previous_peer=_journal(app))
 if intentions is not None:profile[INTENT_KEY]=intentions
 publish_session(app,replace(app.runtime.session,profile=profile))
def is_peer(app,event_id):
 from .obstruction_input import VERSION as OBSTRUCTION
 from .participant_input import NPC_VERSIONS,OBSERVED_VERSIONS
 source=app._product()['sources'].get(event_id,{})
 from .human_interaction import VERSION as HUMAN_VERSION
 version=source.get('evidence',{}).get('input_source',{}).get('version')
 contact=version==HUMAN_VERSION and any(c.get('semantic_key') in {'drag_contact','witnessed_pointer','scene_departure'} for c in source.get('event',{}).get('contents',()))
 return source.get('origin')=='input' and source.get('evidence',{}).get('response_policy')=='eligible' and (version in NPC_VERSIONS or version in {OBSTRUCTION,*OBSERVED_VERSIONS} or contact)

def has_pending_qualification(app):
 """Already accepted opening work precedes a new idle observation plan."""
 data=_journal(app)
 for identity,grant in data['assessments'].items():
  if identity in data['outcomes']:continue
  _verify(grant)
  if grant.get('eligible'):
   if grant['fact_fingerprint']!=app.runtime.memory_instance(identity).fact.fingerprint:
    raise ValueError('pending qualification differs from accepted source')
   return True
 return False


def newly_qualified_delivery(app,delivery_id):
 """Arrival alone is not a new accepted intention; carried grants are old ones."""
 from .multi_pet import _key
 event_id='participant:'+_key(delivery_id)
 grant=app.runtime.session.profile.get(KEY,{}).get('assessments',{}).get(event_id)
 if grant is None:return False
 _verify(grant)
 if grant.get('version')!=KEY or not grant.get('eligible'):return False
 fact=app.runtime.memory_instance(event_id).fact
 if grant.get('fact_fingerprint')!=fact.fingerprint:
  raise ValueError('interrupt qualification differs from accepted input')
 return True

def _assessment(app,event_id):
 if not is_peer(app,event_id):raise ValueError('peer opportunity requires committed eligible NPC input')
 life=PetExpressionService(application=app).project(event_id)
 expression=life.expression
 payload={'shared_projection':expression.shared_projection_execution.payload['shared_projection'],**BOUNDS}
 execution=app.runtime.model_runtime.invoke('assess_action',payload=payload,context=expression.context)
 assessment=read_action_assessment(execution,source_fact_ref=event_id,source_position=app.runtime.session.position.position,source_runtime_ordinal=app.runtime.session.position.runtime_ordinal)
 source=app.runtime.memory_instance(event_id)
 row=_signed({'version':KEY,'event_id':event_id,'fact_fingerprint':source.fact.fingerprint,
  'position_fingerprint':app.runtime.session.position.fingerprint,'lifecycle_fingerprint':life.receipt['fingerprint'],
  'bounds':BOUNDS,'assessment_stage':execution.receipt.to_record(),'assessment_output':thaw_model_data(execution.payload),'eligible':assessment.eligible})
 return row

def _verify_carried(app,row):
 """A current planning source consumes earlier grants, not a second gate."""
 data=_journal(app);current=row;seen=set()
 stack=[current]
 while stack:
  item=stack.pop();_verify(item);identity=item['event_id']
  if identity in seen:raise ValueError('peer intent lineage repeats')
  seen.add(identity)
  fact=app.runtime.memory_instance(identity).fact
  if not is_peer(app,identity) or not item['eligible'] or item['fact_fingerprint']!=fact.fingerprint:
   raise ValueError('peer intent differs from accepted source')
  if item.get('version')!=CARRY:
   if item.get('version')!=KEY:raise ValueError('unknown peer intent grant')
   continue
  if not item.get('carried_from'):raise ValueError('peer intent lacks original grant')
  for parent in item['carried_from']:
   outcome=data['outcomes'].get(parent)
   if outcome is None:raise ValueError('peer intent lacks handoff')
   _verify(outcome)
   if outcome['status']!='continued' or outcome.get('continued_by')!=identity:
    raise ValueError('peer intent handoff differs')
   from .multi_pet import _key
   if identity not in {'participant:'+_key(d) for d in outcome.get('superseded_by',())}:
    raise ValueError('peer intent belongs to another delivery')
   original=data['assessments'][parent]
   if app.runtime.memory_instance(parent).fact.accepted_runtime_ordinal>=fact.accepted_runtime_ordinal:
    raise ValueError('peer intent handoff reverses history')
   stack.append(original)

def _carry(app,event_id,data):
 from .multi_pet import _key
 parents=[]
 for identity,outcome in data['outcomes'].items():
  if outcome.get('status')!='pending':continue
  _verify(outcome)
  if event_id in {'participant:'+_key(d) for d in outcome.get('superseded_by',())}:
   grant=data['assessments'][identity];_verify(grant)
   if not grant['eligible']:raise ValueError('pending intent was never eligible')
   parents.append(identity)
 if not parents:return None
 fact=app.runtime.memory_instance(event_id).fact
 row=_signed({'version':CARRY,'event_id':event_id,'fact_fingerprint':fact.fingerprint,
  'position_fingerprint':app.runtime.session.position.fingerprint,'eligible':True,'carried_from':parents})
 for identity in parents:
  old=data['outcomes'][identity]
  data['outcomes'][identity]=_signed({**{k:v for k,v in old.items() if k!='fingerprint'},
   'status':'continued','continued_by':event_id})
 return row

def authorize(app,event_id):
 if event_id in _journal(app)['outcomes']:raise ValueError('peer opportunity is already closed')
 row=_journal(app)['assessments'].get(event_id)
 if row is None:raise ValueError('peer reply has no accepted assessment')
 row=thaw_model_data(row);_verify(row)
 if row.get('version')==CARRY:
  _verify_carried(app,row)
  if row['position_fingerprint']!=app.runtime.session.position.fingerprint:
   raise ValueError('continued peer planning state is stale')
 elif not row['eligible'] or _assessment(app,event_id)!=row:raise ValueError('peer reply assessment is ineligible or stale')
 source=app.runtime.memory_instance(event_id)
 from .destinations import primary
 return ActionAuthorization.create(session_binding=app.runtime.session.session_binding,current_position=app.runtime.session.position,
  action_kind='direct_reply',subject_ref=app.owner_ref,target_ref=primary(app,event_id),source_fact_ref=event_id,
  source_settlement_fingerprint=source.fact.settlement_ref['acceptance_fingerprint'],
  package_eligibility_evidence={'version':KEY,'eligible':True,'assessment_receipt_fingerprint':fp(row.get('assessment_stage',row))},
  host_authorization_evidence={'origin':ORIGIN,'peer_assessment_fingerprint':row['fingerprint']})

def assess(app,event_id,*,allow_carry=False):
 """Seal the original input-driven gate without dispatching any realization."""
 with app._lock:
  if not is_peer(app,event_id):raise ValueError('peer assessment requires accepted eligible input')
  data=_journal(app)
  existing=data['assessments'].get(event_id)
  if existing is not None:
   _verify(existing)
   if existing.get('fact_fingerprint')!=app.runtime.memory_instance(event_id).fact.fingerprint:
    raise ValueError('peer assessment differs from accepted source')
   if existing.get('version')==CARRY:_verify_carried(app,existing)
   return thaw_model_data(existing)
  if event_id in data['outcomes']:raise ValueError('peer opportunity is already closed')
  existing=(_carry(app,event_id,data) if allow_carry else None) or _assessment(app,event_id)
  data['assessments'][event_id]=existing
  if existing['eligible']:
   from .destinations import freeze_peer_destination
   frozen=freeze_peer_destination(app,event_id)
   if frozen is not None:data.setdefault('reply_destinations',{})[event_id]=frozen
  _save(app,data)
  return thaw_model_data(existing)


def react(product,event_id,*,executor,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False,on_reply_ready=lambda:None,newer_delivery=None):
 app=product.application
 with app._lock:
  if not is_peer(app,event_id):return None
  data=_journal(app);saved=data['outcomes'].get(event_id)
  if saved is not None:
   _verify(saved)
   from .deferred_intention import settle_finished
   settle_finished(product,event_id)
   return thaw_model_data(saved)
  fact=app.runtime.memory_instance(event_id).fact
  guard=None
  def finish(status,eligible):
   fresh=_journal(app);row=_signed({'event_id':event_id,'status':status,'eligible':eligible})
   if guard is not None and guard.superseded_by:row=_signed({**{k:v for k,v in row.items() if k!='fingerprint'},'superseded_by':list(guard.superseded_by)})
   if eligible:
    from .intention_progress import stopped
    stopped(app,event_id,status,superseded_by=() if guard is None else guard.superseded_by)
   fresh['outcomes'][event_id]=row;_save(app,fresh)
   from .deferred_intention import settle_finished
   settle_finished(product,event_id)
   return row
  existing=data['assessments'].get(event_id)
  if existing is not None:_verify(existing)
  # A committed actual output already consumes this opportunity, even if its
  # self-measurement needed recovery. It must never be displayed a second time.
  for source in app._product()['sources'].values():
   if source['origin']=='execution' and product._reply_source_key(source)==(fact.accepted_runtime_ordinal,fact.temporal_position_fingerprint):
    from .intention_progress import actual
    actual(app,event_id,source)
    return finish('executed',bool(existing and existing['eligible']))
  # Old archives can contain clock progress between an accepted grant and
  # its first formation settlement. No intervening event may be skipped.
  from .intention_source import enabled as intention_enabled,ingest as ingest_intention
  from .intent_history import FACTS_KEY
  chain=app.runtime.session.profile.get(FACTS_KEY,{}).get(event_id,())
  from .execution_inputs import follows_completed_execution
  from .deferred_intention import marker as deferred_marker, defer
  if (existing and existing.get('version')==KEY and existing['eligible'] and intention_enabled(app)
      and deferred_marker(app,event_id) is None
      and fact.accepted_runtime_ordinal!=app.runtime.session.position.runtime_ordinal
      and chain and chain[-1]['phase']=='formed'
      and 'intention:'+chain[0]['intention_id'] not in app._product()['sources']
      and app.runtime.events and (app.runtime.events[-1].event_id==event_id or follows_completed_execution(app,event_id))):
   ingest_intention(app,source_ref=event_id,coordinate=app.runtime.session.position.position.coordinate,measure=product.semantic.measure)
  from .measurement_pipeline import settle_peer_before_reply
  prepared_peer=settle_peer_before_reply(product,event_id)
  if prepared_peer is not None:
   from .delivery_guard import DeliveryReplyGuard
   guard=DeliveryReplyGuard(is_cancelled,lambda:(prepared_peer,))
   guard.superseded_by=(prepared_peer,)
   return finish('pending',True)
  if fact.accepted_runtime_ordinal!=app.runtime.session.position.runtime_ordinal or fact.temporal_position_fingerprint!=app.runtime.session.position.position.fingerprint:
   from .intention_continuation import current as current_intention
   if current_intention(app,event_id) is None:
    deliveries=tuple(newer_delivery() or ()) if newer_delivery is not None else ()
    if existing and existing['eligible'] and deliveries and all(newly_qualified_delivery(app,d) for d in deliveries):
     from .delivery_guard import DeliveryReplyGuard
     guard=DeliveryReplyGuard(is_cancelled,lambda:deliveries)
     guard.superseded_by=deliveries
     return finish('pending',True)
    return finish('superseded',False)
  if existing is None:
   existing=assess(app,event_id,allow_carry=True)
  if not existing['eligible']:return finish('ineligible',False)
  from .intention_source import enabled as intention_enabled,ingest as ingest_intention
  if intention_enabled(app) and not existing.get('carried_from'):
   if not defer(app,event_id):
    ingest_intention(app,source_ref=event_id,coordinate=app.runtime.session.position.position.coordinate,
                    measure=product.semantic.measure)
  prepared_peer=settle_peer_before_reply(product,event_id)
  if prepared_peer is not None:
   from .delivery_guard import DeliveryReplyGuard
   guard=DeliveryReplyGuard(is_cancelled,lambda:(prepared_peer,))
   guard.superseded_by=(prepared_peer,)
   return finish('pending',True)
  if is_cancelled():return finish('cancelled',True)
  if product.writer is None:
   from .reaction_action import ensure
   body=ensure(product,event_id)
   if not body or not body['action_keys']:return finish('no_language_realizer',True)
  if newer_delivery is not None:
   from .delivery_guard import DeliveryReplyGuard
   guard=DeliveryReplyGuard(is_cancelled,newer_delivery)
   if guard():return finish('pending' if guard.superseded_by else 'cancelled',True)
  from .intention_progress import planned,actual,stopped
  from .intention_wait import QualifiedTakeover
  try:
   result=product._reply(event_id,executor=executor,completion_coordinate=completion_coordinate,is_cancelled=is_cancelled if guard is None else guard,on_reply_ready=on_reply_ready,on_prepared=lambda prepared:planned(app,event_id,prepared,writer=product.writer),**({} if guard is None else {'continuation':guard}))
  except QualifiedTakeover as takeover:
   from .delivery_guard import DeliveryReplyGuard
   if guard is None:guard=DeliveryReplyGuard(is_cancelled,lambda:takeover.deliveries)
   guard.superseded_by=takeover.deliveries
   return finish('pending',True)
  except Exception as error:
   from .live_execution import PlaybackSettlementPending
   if isinstance(error,PlaybackSettlementPending):raise
   stopped(app,event_id,'technical_failure')
   raise
  if result is not None:actual(app,event_id,app._product()['sources'][result.memory.event.event_id])
  if result is None and guard is not None and guard.superseded_by:return finish('pending',True)
  return finish('executed' if result is not None else 'quiet' if product.last_quiet_realization is not None else 'cancelled',True)
