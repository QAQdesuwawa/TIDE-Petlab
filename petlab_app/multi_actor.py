"""Owner-local handoff between durable Conversation deliveries and PetProduct."""
from pathlib import Path
import json
from .composition import PetDefinition
from .multi_pet import MultiPetWorkspace,_immutable,_key,_read
from .participant_input import prepare,is_human_observation
from .product import wall_clock_ms
from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY

class MultiPetActorLink:
 def __init__(self,product,root,alias,*,host_clock=None):
  self.product=product;self.alias=alias;self._host_clock=host_clock
  manifest=_read(Path(root)/'workspace.json')
  self.workspace=MultiPetWorkspace(root,{k:PetDefinition.from_record(v) for k,v in manifest['definitions'].items()})
  if product.application.definition.fingerprint!=self.workspace.definitions[alias].fingerprint:raise ValueError('multi Actor identity differs')
  product.application.multi_workspace=self.workspace
  from .intention_wait import prepare as prepare_waiting
  product.prepare_waiting_writer=lambda event_id,**kw:prepare_waiting(self,event_id,**kw)
  self.local=self.workspace.actor_path(alias);self._directory_stamp=None;self._pending=[];self._seen=set();self._publication_cursor=0;self._visible_siblings={};self.last_receipt=None;self._human_frontier=None
  product.application.pending_visible_inputs=self.pending_visible_inputs
  product.semantic.waiting_input=self.prepare_waiting_input
  from .visible_execution import VisibleExecutionPublisher
  self.visible=VisibleExecutionPublisher(self);product.visible_execution_publisher=self.visible
  self.visible.recover()
  product.application.on_execution_committed=lambda event:self.flush_outputs()
  from .playback_intake import continue_after_playback
  product.continue_after_playback=lambda effect,**kwargs:continue_after_playback(self,effect,**kwargs)
  from .pointer_witness import publish
  product.application.on_input_committed=lambda event:publish(product.application,self.workspace,event)
  # Recover durable public effects even if their self-measurement is pending.
  self.flush_outputs()
 def flush_outputs(self):
  app=self.product.application
  from itertools import islice
  from model_packages.desktop_pet_v1.events import PetEvent
  sources=app._product()["sources"]
  for identity in tuple(islice(sources,self._publication_cursor,None)):
   event=PetEvent.from_record(sources[identity]["event"])
   if event.event_kind!='npc_output' or sources[identity]['origin']=='transcript':
    self._publication_cursor+=1;continue
   marker=self.local/'published'/(_key(event.event_id)[:24]+'.json')
   if marker.exists():self._publication_cursor+=1;continue
   if not any(c.kind in {'speech','expression','action'} and not c.private and any(r!=app.owner_ref for r in c.recipient_refs) for c in event.contents):
    _immutable(marker,{'action_id':event.event_id,'status':'local_action_only'})
    self._publication_cursor+=1;continue
   source=app._product()['sources'][event.event_id]
   destination=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')
   if destination is None:self._publication_cursor+=1;continue # private pointer work has no multi publication
   conversation,_=self.workspace.registered_destination(destination['conversation'])
   if self.visible.finish_committed(app,event,source):
    _immutable(marker,{'action_id':event.event_id,'status':'visible_checkpoints'})
    self._publication_cursor+=1;continue
   canonical=self.workspace.publish_output(app,event_id=event.event_id,conversation=conversation,addressed=destination['addressed'])
   _immutable(marker,{'action_id':event.event_id,'source_event_id':canonical.event_id})
   self._publication_cursor+=1
 def _refresh(self):
  directory=self.local/'inbox'
  stamp=directory.stat().st_mtime_ns if directory.exists() else None
  if stamp==self._directory_stamp:return
  arrived=[]
  for path in sorted(directory.glob('*.json')) if directory.exists() else ():
   if path.name in self._seen:continue
   self._seen.add(path.name)
   row=self.workspace.read_delivery(self.alias,path)
   if row.get("visible"):
    self._visible_siblings.setdefault(row["visible"]["parent_event_id"],[]).append(row)
   from .delivery_admission import human_frontier
   frontier=human_frontier((row,))
   if frontier is not None and (self._human_frontier is None or frontier>self._human_frontier):self._human_frontier=frontier
   if (self.local/'accepted'/path.name).exists():continue
   self._pending.append(row)
   arrived.append(row)
  # Startup must not replay the old unstarted backlog before the latest scene.
  # This is intake priority only: original occurrence times remain unchanged.
  # Preserve ordering within this arrival batch and never preempt a started
  # transaction here; psychological eligibility remains in peer_reaction.
  if arrived:
   from .visible_delivery import delivery_order
   boundary=(min if getattr(self,'_intake_initialized',False) else max)(map(delivery_order,arrived))
   previous=getattr(self,'_arrival_frontier',None)
   self._arrival_frontier=boundary if previous is None else max(previous,boundary)
  self._intake_initialized=True
  self._sort_intake()
  self._directory_stamp=stamp
 def _sort_intake(self):
  from .delivery_admission import ordered
  app=self.product.application;sources=app._product()['sources']
  settled=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
  from .peer_reaction import KEY as PEER_KEY
  peer=app.runtime.session.profile.get(PEER_KEY,{})
  qualified={identity for identity,grant in peer.get('assessments',{}).items()
   if grant.get('eligible') and identity not in peer.get('outcomes',{})}
  started=[r['delivery']['delivery_id'] for r in self._pending
   if ('participant:'+_key(r['delivery']['delivery_id']) in sources and 'participant:'+_key(r['delivery']['delivery_id']) not in settled)
   or 'participant:'+_key(r['delivery']['delivery_id']) in qualified
   or (self.local/'failed'/(_key(r['delivery']['delivery_id'])[:24]+'.json')).exists()]
  frontiers=[f for f in (self._human_frontier,getattr(self,'_arrival_frontier',None)) if f is not None]
  self._pending=ordered(self._pending,max(frontiers) if frontiers else None,started,human_anchor=self._human_frontier)
 def pending_count(self):
  from .obstruction_input import pending
  from .desktop_routines import pending as sightings
  self._refresh();return len(self._pending)+len(pending(self.product.application))+len(sightings(self))
 def pending_visible_inputs(self):
  # Only this recipient's validated inbox; no other Actor's memory.
  self._refresh()
  from .visible_delivery import delivery_order
  return tuple(sorted(self._pending,key=delivery_order)[-16:])
 def notice_waiting_speech(self):
  """Send presentation-only attention from verified pending deliveries."""
  acknowledge=getattr(self,'_acknowledge_peer_input',None)
  if acknowledge is None:return
  from .delivery_admission import can_interrupt_peer_reply,current_input
  from .visible_delivery import delivery_order
  app=self.product.application
  sources=app._product()['sources']
  fresh=[row for row in self._pending if can_interrupt_peer_reply(row)
   and current_input(row,None,self._human_frontier)
   and 'participant:'+_key(row['delivery']['delivery_id']) not in sources]
  if not fresh:return
  row=max(fresh,key=delivery_order)
  order=delivery_order(row)
  previous=getattr(self,'_last_peer_notice',None)
  if previous is not None and order<=previous:return
  self._last_peer_notice=order
  acknowledge({'version':'petlab-input-feedback-v1',
   'source_ref':row['delivery']['delivery_id'],
   'target_ref':row['projection']['sender_participant_id'],
   'session_binding':app.runtime.session.session_binding.fingerprint,
   'visual_only':False})
 def prepare_waiting_input(self):
  """Peek one real inbox source; normal pump retains consumption and reply."""
  self._refresh()
  if not self._pending:return None
  MultiPetActorLink.notice_waiting_speech(self)
  self._sort_intake()
  app=self.product.application
  from .peer_reaction import KEY as PEER_KEY
  peer=app.runtime.session.profile.get(PEER_KEY,{})
  active=getattr(app,'_measurement_preparing_source',None) or getattr(app,'_prefetch_reply_source',None)
  # One response may prepare its next input. Once that input has qualified,
  # don't leap past its unfinished response to accept a third historical one.
  if any(identity!=active and grant.get('eligible') and identity not in peer.get('outcomes',{})
         for identity,grant in peer.get('assessments',{}).items()):return None
  from .delivery_admission import can_interrupt_peer_reply,current_input,historical_action_end,is_peer_speech
  origin=getattr(app,'_measurement_preparing_origin',None)
  from .delivery_admission import source_delivery
  try:active_delivery=source_delivery(self,active)
  except FileNotFoundError:return None
  for row in self._pending:
   if active_delivery is not None and not current_input(row,active_delivery,None):continue
   identity=row['delivery']['delivery_id'];event_id='participant:'+_key(identity)
   if event_id in app._product()['sources']:continue
   if (self.local/'failed'/(_key(identity)[:24]+'.json')).exists():return None
   if self._covered_start(row):continue
   if row['sender_kind']=='npc':
    if origin=='cx67_private_intention' and not can_interrupt_peer_reply(row):continue
    # After playback the existing prefetch slot can serve overheard speech.
    # Keep historical and visual-only inputs in ordinary inbox order.
    if origin=='execution' and not (is_peer_speech(row) and current_input(row,None,self._human_frontier)):continue
   if historical_action_end(row,self._human_frontier,self.workspace):continue
   if row['delivery']['delivery_scope']=='observed' and 'visible' not in row and not is_human_observation(row,self.workspace):continue
   admit=getattr(self,'admit_waiting_input',None)
   if admit is not None and not admit(row):return None
   args=prepare(app,row,self.workspace)
   return {**args,'scope_ref':app.definition.scope_ref,'actor_ref':args['input_source']['actor_ref']}
  return None
 def _newer_human_waiting(self,row):
  self._refresh()
  from .peer_reaction import newly_qualified_delivery
  return any(x["sender_kind"]=="human" and x["delivery"]["delivery_id"]!=row["delivery"]["delivery_id"] and x["projection"]["committed_at_ms"]>=row["projection"]["committed_at_ms"]
   and (x.get('response_policy')=='required' or newly_qualified_delivery(self.product.application,x['delivery']['delivery_id'])) for x in self._pending)
 def _newer_peer_waiting(self,row):
  self._refresh()
  if row.get('sender_kind')!='npc' or not row.get('visible'):return ()
  p=row['projection'];scope=('conversation_id','membership_version','recipient_participant_id')
  from .peer_reaction import newly_qualified_delivery
  from .delivery_admission import can_interrupt_peer_reply
  return tuple(x['delivery']['delivery_id'] for x in self._pending
   if x.get('visible') and can_interrupt_peer_reply(x) and x['delivery']['delivery_id']!=row['delivery']['delivery_id']
   and all(x['projection'].get(k)==p.get(k) for k in scope)
   and x['projection']['committed_at_ms']>=p['committed_at_ms']
   and newly_qualified_delivery(self.product.application,x['delivery']['delivery_id']))
 def _covered_start(self,row):
  """An unseen start can be observed with its already-arrived actual ending.

  Keep both canonical deliveries. Never remove speech, a running interval,
  or an input whose independent settlement has already begun.
  """
  if 'participant:'+_key(row['delivery']['delivery_id']) in self.product.application._product()['sources']:return ()
  visible=row.get('visible')
  if row['sender_kind']!='npc' or not visible:return ()
  source=self.workspace.source_event(row['projection']['source_event_id'])
  reader=row['projection']['recipient_participant_id']
  items=[i for i in source.get('visible_manifest',{}).get('items',[]) if reader in i['recipient_refs']]
  if not items or any(i['kind']!='action' or i.get('phase')!='started' for i in items):return ()
  needed={(i['content_id'],i['started_at_ms']) for i in items};covered=set();deliveries=[]
  siblings=getattr(self,'_visible_siblings',{}).get(visible.get('parent_event_id'),self._pending)
  for later in siblings:
   v=later.get('visible',{})
   if (v.get('parent_event_id')!=visible.get('parent_event_id') or later['delivery']['delivery_id']==row['delivery']['delivery_id']
       or later['projection']['sender_participant_id']!=row['projection']['sender_participant_id']
       or later['projection']['recipient_participant_id']!=reader):continue
   last=self.workspace.source_event(later['projection']['source_event_id'])
   matched={(i['content_id'],i['started_at_ms']) for i in last.get('visible_manifest',{}).get('items',[])
    if reader in i['recipient_refs'] and i['kind']=='action' and i.get('phase') in {'completed','interrupted'}
    and i['at_ms']>=row['projection']['committed_at_ms']}&needed
   if matched:covered.update(matched);deliveries.append(later['delivery']['delivery_id'])
  return tuple(deliveries) if covered==needed else ()
 def _record_covered_start(self,row,covered):
  identity=row['delivery']['delivery_id'];key=_key(identity)[:24]+'.json'
  _immutable(self.local/'observations'/key,row)
  self.last_receipt={'delivery_id':identity,'status':'observed_with_terminal','sender_kind':row['sender_kind'],
    'covered_by_delivery_ids':list(covered),'measurement_performed':False}
  _immutable(self.local/'accepted'/key,self.last_receipt)
  self._pending.remove(row)
  return True
 def pump(self,*,executor,is_cancelled=lambda:False,on_reply_ready=lambda:None):
  from .playback_intake import finish_pending
  if not finish_pending(self):return False
  self.flush_outputs();self._refresh()
  self.notice_waiting_speech()
  from .obstruction_input import pump as pump_obstruction
  if pump_obstruction(self.product,executor=executor,is_cancelled=is_cancelled,on_reply_ready=on_reply_ready):return True
  if not self._pending:
   from .desktop_routines import pending as sightings,pump as pump_sighting
   observed=sightings(self)
   if observed:return pump_sighting(self,observed[0],executor=executor,
      is_cancelled=is_cancelled,on_reply_ready=on_reply_ready)
  if not self._pending:return False
  self._sort_intake()
  row=self._pending[0];identity=row['delivery']['delivery_id']
  failed=self.local/'failed'/(_key(identity)[:24]+'.json')
  if failed.exists():raise RuntimeError('multi delivery requires explicit recovery')
  try:
   reply=None
   self._defer_playback_settlement=True
   covered=self._covered_start(row)
   if covered:return self._record_covered_start(row,covered)
   if row['delivery']['delivery_scope']=='observed' and 'visible' not in row and not is_human_observation(row,self.workspace):
    _immutable(self.local/'observations'/(_key(identity)[:24]+'.json'),row)
    outcome='observed'
   else:
    clock=getattr(self,'_host_clock',None)
    args=prepare(self.product.application,row,self.workspace,**({} if clock is None else {'coordinate':clock()}))
    from .delivery_admission import historical_action_end
    historical=historical_action_end(row,self._human_frontier,self.workspace)
    completion={} if clock is None else {'completion_coordinate':clock}
    # Source/visibility validation above precedes this UI-only acknowledgement.
    # It is not a selected Action and must never run again during recovery.
    acknowledge=getattr(executor,'acknowledge_input',None)
    from .delivery_admission import can_interrupt_peer_reply
    peer_notice=can_interrupt_peer_reply(row) and getattr(self,'_acknowledge_peer_input',None) is not None
    if (acknowledge is not None and not peer_notice and not historical
        and args['event_id'] not in self.product.application._product()['sources'] and not is_cancelled()):
     app=self.product.application
     look_target=next((c.parameters['moved_ref'] for c in args['contents'] if c.semantic_key=='witnessed_pointer'),args['input_source']['actor_ref'])
     acknowledge({'version':'petlab-input-feedback-v1','source_ref':identity,
      'target_ref':look_target,'session_binding':app.runtime.session.session_binding.fingerprint,
      'visual_only':all(c.kind!='speech' for c in args['contents'])})
    from product_adapters.desktop_pet_v1.realization import WriterRealizationFailed
    try:
     self.product.interact(**args,executor=executor,is_cancelled=is_cancelled,on_reply_ready=on_reply_ready,
      on_input_settled=getattr(self.product,'on_settled_presentation',lambda:None),**completion)
    except WriterRealizationFailed:
     if args['event_id'] not in self.product.application.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):raise
     reply={'status':'technical_failure'}
    else:
     if row["sender_kind"]=="npc" or row.get("response_policy")=="eligible" or is_human_observation(row,self.workspace):
      from .peer_reaction import react
      reply=react(self.product,args["event_id"],executor=executor,is_cancelled=lambda:is_cancelled() or self._newer_human_waiting(row),on_reply_ready=on_reply_ready,newer_delivery=lambda:self._newer_peer_waiting(row),historical_only=historical,**completion)
    outcome='settled'
   self.flush_outputs()
   self.last_receipt={'delivery_id':identity,'status':outcome,'sender_kind':row['sender_kind']}
   if outcome=='settled' and reply and reply['status']=='technical_failure':
    self.last_receipt['reply_status']='technical_failure'
   _immutable(self.local/'accepted'/(_key(identity)[:24]+'.json'),self.last_receipt)
   self._pending=[x for x in self._pending if x["delivery"]["delivery_id"]!=identity];return True
  except Exception as error:
   from .live_execution import PlaybackSettlementPending
   if isinstance(error,PlaybackSettlementPending):
    self._suspended_delivery=identity
    return False
   import traceback
   _immutable(failed,{'delivery_id':identity,'error_type':type(error).__name__,'frames':[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name} for frame in traceback.extract_tb(error.__traceback__)]})
   raise
  finally:self._defer_playback_settlement=False

 def finish_suspended_playback(self,action_id):
  """Close the original delivery from its committed effect, never play again."""
  identity=getattr(self,'_suspended_delivery',None)
  if identity is None:return
  app=self.product.application;event_id='participant:'+_key(identity)
  fact=app.runtime.memory_instance(event_id).fact
  source=app._product()['sources'].get(action_id)
  if (source is None or source['origin']!='execution' or
      self.product._reply_source_key(source)!=(fact.accepted_runtime_ordinal,fact.temporal_position_fingerprint)):
   raise ValueError('suspended delivery does not match committed playback')
  policy=app._product()['sources'][event_id]['evidence']['response_policy']
  if policy=='required':
   # The source-bound committed output above is the required reply's outcome.
   # Required user inputs have no peer-reaction journal entry.
   if action_id not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
    raise ValueError('suspended required reply is not settled')
  else:
   from .peer_reaction import react
   result=react(self.product,event_id,executor=None)
   if result is None or result['status']!='executed':raise ValueError('suspended delivery is not completed')
  self.flush_outputs()
  row=next(r for r in self._pending if r['delivery']['delivery_id']==identity)
  self.last_receipt={'delivery_id':identity,'status':'settled','sender_kind':row['sender_kind']}
  _immutable(self.local/'accepted'/(_key(identity)[:24]+'.json'),self.last_receipt)
  if getattr(self,'_suspended_recovery',False):
   _immutable(self.local/'resolved'/(_key(identity)[:24]+'.json'),{'delivery_id':identity,'status':'reconciled'})
   self._suspended_recovery=False
  self._pending=[r for r in self._pending if r['delivery']['delivery_id']!=identity]

 def _failed_delivery(self):
  self._refresh()
  return next((row for row in self._pending
   if (self.local/'failed'/(_key(row['delivery']['delivery_id'])[:24]+'.json')).exists()),None)
 def requires_recovery(self):
  return self._failed_delivery() is not None
 def reconcile_writer_failures(self):
  """Recover older settled NPC deliveries with a terminal pre-playback failure."""
  app=self.product.application
  writer=self.product.writer
  if writer is None:return
  from .peer_reaction import KEY,record_writer_failure
  self._refresh()
  for row in tuple(self._pending):
   if row['sender_kind']!='npc':continue
   identity=row['delivery']['delivery_id'];digest=_key(identity);event_id='participant:'+digest
   key=digest[:24];failed=self.local/'failed'/(key+'.json')
   profile=app.runtime.session.profile
   if (not failed.exists() or event_id not in profile.get(EVENT_JOURNAL_KEY,{})
       or not profile.get(KEY,{}).get('assessments',{}).get(event_id,{}).get('eligible')):continue
   frames=_read(failed).get('frames',())
   if not frames or frames[-1]['file']!='realization.py' or frames[-1]['function']!='_prepare_steps':continue
   receipts=writer.attempt_journal.read_parent_receipts(event_id)
   allocated={r['writer_request_id'] for r in receipts if r['stage']=='allocated'}
   failed_attempts=[r for r in receipts if r['stage']=='terminal' and r.get('terminal_status')=='failed']
   if not failed_attempts or allocated!={r['writer_request_id'] for r in failed_attempts}:continue
   attempt=writer.attempt_journal.read_attempt(failed_attempts[-1]['writer_request_id'])
   trace=attempt.get('provider',{}).get('provider_result',{}).get('realizer_trace',{})
   if trace.get('status')!='failed' or trace.get('candidate') is not None or not trace.get('failure'):continue
   reply=record_writer_failure(app,event_id)
   if reply['status']!='technical_failure':continue
   _immutable(self.local/'accepted'/(key+'.json'),{'delivery_id':identity,'status':'settled',
    'sender_kind':row['sender_kind'],'reply_status':'technical_failure'})
   self._pending=[pending for pending in self._pending if pending['delivery']['delivery_id']!=identity]
 def resume(self,*,executor,is_cancelled=lambda:False):
  try:return self._resume(executor=executor,is_cancelled=is_cancelled)
  except Exception as error:
   from .live_execution import PlaybackSettlementPending
   if isinstance(error,PlaybackSettlementPending) and getattr(self,'_pending_playback_intake',None) is not None:
    self._suspended_delivery=self._resuming_delivery
    self._suspended_recovery=True
   raise

 def _resume(self,*,executor,is_cancelled=lambda:False):
  """Explicit host recovery only. Existing effects are reconciled, never replayed."""
  row=self._failed_delivery()
  if row is None:return None
  identity=row['delivery']['delivery_id'];key=_key(identity)[:24]
  self._resuming_delivery=identity
  event_id='participant:'+_key(identity)
  app=self.product.application
  recovered_takeover=()
  if event_id in app._product()['sources']:
   # Recovery may finish the newer input accepted during this Writer's wait.
   # Remember its identity before resume removes it from the pending sources.
   journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
   pending={k for k in app._product()['sources'] if k not in journal}
   from .peer_reaction import is_peer
   newer=next((r for r in self._pending if 'participant:'+_key(r['delivery']['delivery_id']) in pending
    and r['delivery']['delivery_id']!=identity and is_peer(app,'participant:'+_key(r['delivery']['delivery_id']))
    and r['projection']['conversation_id']==row['projection']['conversation_id']),None)
   if is_peer(app,event_id):
    # Reconcile this delivery's facts before its own reaction gate below.
    # Generic product resume can otherwise pick an unrelated old required reply.
    recovered=self.product.resume(executor=None,is_cancelled=lambda:True)
   else:
    recovered=self.product.resume(executor=executor,is_cancelled=is_cancelled)
   old_fact=app.runtime.memory_instance(event_id).fact
   recovered_source=(app._product()['sources'].get(recovered.memory.event.event_id,{})
    if recovered is not None and hasattr(recovered,'memory') else {})
   old_executed=(recovered_source.get('origin')=='execution' and
    self.product._reply_source_key(recovered_source)==(old_fact.accepted_runtime_ordinal,old_fact.temporal_position_fingerprint))
   if newer is not None:
    from .peer_reaction import assess,react
    next_id=newer['delivery']['delivery_id'];next_event='participant:'+_key(next_id)
    grant=assess(app,next_event)
    if grant['eligible']:recovered_takeover=(next_id,)
    if not grant['eligible']:
     from .retained_intention import retain
     if not old_executed:retain(app,event_id,next_event)
     react(self.product,next_event,executor=None)
     next_key=_key(next_id)[:24]
     _immutable(self.local/'accepted'/(next_key+'.json'),{'delivery_id':next_id,'status':'settled','sender_kind':newer['sender_kind']})
     _immutable(self.local/'resolved'/(next_key+'.json'),{'delivery_id':next_id,'status':'reconciled'})
     self._pending=[r for r in self._pending if r['delivery']['delivery_id']!=next_id]
  else:
   args=prepare(app,row,self.workspace)
   self.product.interact(**args,executor=executor,is_cancelled=is_cancelled,retry_failed=True)
  failure=_read(self.local/'failed'/(key+'.json'))
  waiting=failure.get('waiting_intention')
  if waiting:
   from .idle_opening import is_idle_tick
   if is_idle_tick(app,waiting):
    from .intention_idle import recover_waiting_input
    recover_waiting_input(app,waiting,event_id,identity)
  if row["sender_kind"]=="npc" or row.get("response_policy")=="eligible" or is_human_observation(row,self.workspace):
   from .peer_reaction import react
   react(self.product,event_id,executor=executor,is_cancelled=lambda:is_cancelled() or self._newer_human_waiting(row),newer_delivery=lambda:tuple(dict.fromkeys((*recovered_takeover,*self._newer_peer_waiting(row)))))
  if event_id not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}) or event_id in self.product.pending_reply_ids():
   raise RuntimeError('multi recovery remains unfinished')
  self.flush_outputs()
  receipt={'delivery_id':identity,'status':'settled','sender_kind':row['sender_kind']}
  _immutable(self.local/'accepted'/(key+'.json'),receipt)
  _immutable(self.local/'resolved'/(key+'.json'),{'delivery_id':identity,'status':'reconciled'})
  self._pending=[x for x in self._pending if x["delivery"]["delivery_id"]!=identity];self.last_receipt=receipt
  return receipt
