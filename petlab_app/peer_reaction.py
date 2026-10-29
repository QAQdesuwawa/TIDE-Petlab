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

def _signed(row):return {**row,'fingerprint':fp(row)}
def _verify(row):
 if row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'}):raise ValueError('peer reaction receipt differs')
def _journal(app):
 old=app.runtime.session.profile.get(KEY,{})
 return {'version':KEY,'assessments':dict(old.get('assessments',{})),'outcomes':dict(old.get('outcomes',{}))}
def _save(app,data):
 publish_session(app,replace(app.runtime.session,profile={**app.runtime.session.profile,KEY:data}))
def is_peer(app,event_id):
 from .participant_input import NPC_VERSIONS
 source=app._product()['sources'].get(event_id,{})
 return source.get('origin')=='input' and source.get('evidence',{}).get('response_policy')=='eligible' and source.get('evidence',{}).get('input_source',{}).get('version') in NPC_VERSIONS

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

def authorize(app,event_id):
 if event_id in _journal(app)['outcomes']:raise ValueError('peer opportunity is already closed')
 row=_journal(app)['assessments'].get(event_id)
 if row is None:raise ValueError('peer reply has no accepted assessment')
 row=thaw_model_data(row);_verify(row)
 if not row['eligible'] or _assessment(app,event_id)!=row:raise ValueError('peer reply assessment is ineligible or stale')
 source=app.runtime.memory_instance(event_id)
 from .destinations import primary
 return ActionAuthorization.create(session_binding=app.runtime.session.session_binding,current_position=app.runtime.session.position,
  action_kind='direct_reply',subject_ref=app.owner_ref,target_ref=primary(app,event_id),source_fact_ref=event_id,
  source_settlement_fingerprint=source.fact.settlement_ref['acceptance_fingerprint'],
  package_eligibility_evidence={'version':KEY,'eligible':True,'assessment_receipt_fingerprint':fp(row['assessment_stage'])},
  host_authorization_evidence={'origin':ORIGIN,'peer_assessment_fingerprint':row['fingerprint']})

def react(product,event_id,*,executor,completion_coordinate=wall_clock_ms,is_cancelled=lambda:False,on_reply_ready=lambda:None,newer_delivery=None):
 app=product.application
 with app._lock:
  if not is_peer(app,event_id):return None
  data=_journal(app);saved=data['outcomes'].get(event_id)
  if saved is not None:_verify(saved);return thaw_model_data(saved)
  fact=app.runtime.memory_instance(event_id).fact
  guard=None
  def finish(status,eligible):
   fresh=_journal(app);row=_signed({'event_id':event_id,'status':status,'eligible':eligible})
   if guard is not None and guard.superseded_by:row=_signed({**{k:v for k,v in row.items() if k!='fingerprint'},'superseded_by':list(guard.superseded_by)})
   fresh['outcomes'][event_id]=row;_save(app,fresh);return row
  existing=data['assessments'].get(event_id)
  if existing is not None:_verify(existing)
  # A committed actual output already consumes this opportunity, even if its
  # self-measurement needed recovery. It must never be displayed a second time.
  for source in app._product()['sources'].values():
   if source['origin']=='execution' and product._reply_source_key(source)==(fact.accepted_runtime_ordinal,fact.temporal_position_fingerprint):
    return finish('executed',bool(existing and existing['eligible']))
  if fact.accepted_runtime_ordinal!=app.runtime.session.position.runtime_ordinal or fact.temporal_position_fingerprint!=app.runtime.session.position.position.fingerprint:
   return finish('superseded',False)
  if existing is None:
   existing=_assessment(app,event_id);data['assessments'][event_id]=existing;_save(app,data)
  if not existing['eligible']:return finish('ineligible',False)
  if is_cancelled():return finish('cancelled',True)
  if product.writer is None:
   from .reaction_action import ensure
   body=ensure(product,event_id)
   if not body or not body['action_keys']:return finish('no_language_realizer',True)
  if newer_delivery is not None:
   from .delivery_guard import DeliveryReplyGuard
   guard=DeliveryReplyGuard(is_cancelled,newer_delivery)
   if guard():return finish('superseded',True)
  result=product._reply(event_id,executor=executor,completion_coordinate=completion_coordinate,is_cancelled=is_cancelled if guard is None else guard,on_reply_ready=on_reply_ready,**({} if guard is None else {'continuation':guard}))
  if result is None and guard is not None and guard.superseded_by:return finish('superseded',True)
  return finish('executed' if result is not None else 'quiet' if product.last_quiet_realization is not None else 'cancelled',True)
