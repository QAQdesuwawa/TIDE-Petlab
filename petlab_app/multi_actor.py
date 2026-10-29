"""Owner-local handoff between durable Conversation deliveries and PetProduct."""
from pathlib import Path
import json
from .composition import PetDefinition
from .multi_pet import MultiPetWorkspace,_immutable,_key,_read
from .participant_input import prepare
from .product import wall_clock_ms
from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY

class MultiPetActorLink:
 def __init__(self,product,root,alias):
  self.product=product;self.alias=alias
  manifest=_read(Path(root)/'workspace.json')
  self.workspace=MultiPetWorkspace(root,{k:PetDefinition.from_record(v) for k,v in manifest['definitions'].items()})
  if product.application.definition.fingerprint!=self.workspace.definitions[alias].fingerprint:raise ValueError('multi Actor identity differs')
  product.application.multi_workspace=self.workspace
  self.local=self.workspace.actor_path(alias);self._directory_stamp=None;self._pending=[];self._seen=set();self._publication_cursor=0;self.last_receipt=None
  from .visible_execution import VisibleExecutionPublisher
  self.visible=VisibleExecutionPublisher(self);product.visible_execution_publisher=self.visible
  self.visible.recover()
  product.application.on_execution_committed=lambda event:self.flush_outputs()
  # Recover durable public effects even if their self-measurement is pending.
  self.flush_outputs()
 def flush_outputs(self):
  app=self.product.application
  from itertools import islice
  from model_packages.desktop_pet_v1.events import PetEvent
  sources=app._product()["sources"]
  for identity in tuple(islice(sources,self._publication_cursor,None)):
   event=PetEvent.from_record(sources[identity]["event"])
   if event.event_kind!='npc_output':self._publication_cursor+=1;continue
   marker=self.local/'published'/(_key(event.event_id)[:24]+'.json')
   if marker.exists():self._publication_cursor+=1;continue
   if not any(c.kind in {'speech','expression','action'} and not c.private and any(r!=app.owner_ref for r in c.recipient_refs) for c in event.contents):
    _immutable(marker,{'action_id':event.event_id,'status':'local_action_only'})
    self._publication_cursor+=1;continue
   source=app._product()['sources'][event.event_id]
   destination=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')
   if destination is None:self._publication_cursor+=1;continue # private pointer work has no multi publication
   conversation=next((k for k,d in self.workspace.conversations.items() if d.conversation_id==destination['conversation']['conversation_id']),None)
   if conversation is None:raise ValueError('executed destination is not registered')
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
  for path in sorted(directory.glob('*.json')) if directory.exists() else ():
   if path.name in self._seen:continue
   self._seen.add(path.name)
   if (self.local/'accepted'/path.name).exists():continue
   self._pending.append(self.workspace.read_delivery(self.alias,path))
  from .visible_delivery import delivery_order
  self._pending.sort(key=delivery_order)
  self._directory_stamp=stamp
 def pending_count(self):
  self._refresh();return len(self._pending)
 def _newer_human_waiting(self,row):
  self._refresh()
  return any(x["sender_kind"]=="human" and x["projection"]["committed_at_ms"]>=row["projection"]["committed_at_ms"] for x in self._pending)
 def _newer_peer_waiting(self,row):
  self._refresh()
  if row.get('sender_kind')!='npc' or not row.get('visible'):return ()
  p=row['projection'];scope=('conversation_id','membership_version','sender_participant_id','recipient_participant_id')
  return tuple(x['delivery']['delivery_id'] for x in self._pending
   if x.get('sender_kind')=='npc' and x.get('visible') and x['delivery']['delivery_id']!=row['delivery']['delivery_id']
   and all(x['projection'].get(k)==p.get(k) for k in scope)
   and x['projection']['committed_at_ms']>p['committed_at_ms'])
 def pump(self,*,executor,is_cancelled=lambda:False,on_reply_ready=lambda:None):
  self.flush_outputs();self._refresh()
  if not self._pending:return False
  row=self._pending[0];identity=row['delivery']['delivery_id']
  failed=self.local/'failed'/(_key(identity)[:24]+'.json')
  if failed.exists():raise RuntimeError('multi delivery requires explicit recovery')
  try:
   if row['delivery']['delivery_scope']=='observed' and 'visible' not in row:
    _immutable(self.local/'observations'/(_key(identity)[:24]+'.json'),row)
    outcome='observed'
   else:
    args=prepare(self.product.application,row,self.workspace)
    # Source/visibility validation above precedes this UI-only acknowledgement.
    # It is not a selected Action and must never run again during recovery.
    acknowledge=getattr(executor,'acknowledge_input',None)
    if acknowledge is not None and args['event_id'] not in self.product.application._product()['sources'] and not is_cancelled():
     app=self.product.application
     acknowledge({'version':'petlab-input-feedback-v1','source_ref':identity,
      'target_ref':args['input_source']['actor_ref'],'session_binding':app.runtime.session.session_binding.fingerprint})
    self.product.interact(**args,executor=executor,is_cancelled=is_cancelled,on_reply_ready=on_reply_ready,
     on_input_settled=getattr(self.product,'on_settled_presentation',lambda:None))
    if row["sender_kind"]=="npc":
     from .peer_reaction import react
     react(self.product,args["event_id"],executor=executor,is_cancelled=lambda:is_cancelled() or self._newer_human_waiting(row),on_reply_ready=on_reply_ready,newer_delivery=lambda:self._newer_peer_waiting(row))
    outcome='settled'
   self.flush_outputs()
   self.last_receipt={'delivery_id':identity,'status':outcome,'sender_kind':row['sender_kind']}
   _immutable(self.local/'accepted'/(_key(identity)[:24]+'.json'),self.last_receipt)
   self._pending=[x for x in self._pending if x["delivery"]["delivery_id"]!=identity];return True
  except Exception as error:
   import traceback
   _immutable(failed,{'delivery_id':identity,'error_type':type(error).__name__,'frames':[{'file':Path(frame.filename).name,'line':frame.lineno,'function':frame.name} for frame in traceback.extract_tb(error.__traceback__)]})
   raise

 def requires_recovery(self):
  self._refresh()
  return any((self.local/'failed'/(_key(r['delivery']['delivery_id'])[:24]+'.json')).exists() for r in self._pending)
 def resume(self,*,executor,is_cancelled=lambda:False):
  """Explicit host recovery only. Existing effects are reconciled, never replayed."""
  self._refresh()
  if not self._pending:return None
  row=self._pending[0];identity=row['delivery']['delivery_id'];key=_key(identity)[:24]
  if not (self.local/'failed'/(key+'.json')).exists():return None
  event_id='participant:'+_key(identity)
  app=self.product.application
  if event_id in app._product()['sources']:
   self.product.resume(executor=executor,is_cancelled=is_cancelled)
  else:
   args=prepare(app,row,self.workspace)
   self.product.interact(**args,executor=executor,is_cancelled=is_cancelled,retry_failed=True)
  if row["sender_kind"]=="npc":
   from .peer_reaction import react
   react(self.product,event_id,executor=executor,is_cancelled=lambda:is_cancelled() or self._newer_human_waiting(row),newer_delivery=lambda:self._newer_peer_waiting(row))
  if event_id not in app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}) or event_id in self.product.pending_reply_ids():
   raise RuntimeError('multi recovery remains unfinished')
  self.flush_outputs()
  receipt={'delivery_id':identity,'status':'settled','sender_kind':row['sender_kind']}
  _immutable(self.local/'accepted'/(key+'.json'),receipt)
  _immutable(self.local/'resolved'/(key+'.json'),{'delivery_id':identity,'status':'reconciled'})
  self._pending=[x for x in self._pending if x["delivery"]["delivery_id"]!=identity];self.last_receipt=receipt
  return receipt
