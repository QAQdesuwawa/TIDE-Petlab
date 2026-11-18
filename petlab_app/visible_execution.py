"""Durable outward checkpoints from an already-authorized physical execution.

No state settlement, action choice or model call. The UI only enqueues signals;
a recorder verifies each actual phase and publishes the first heard prefix and
the remaining terminal evidence through the same recipient route.
"""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import queue,threading
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.events import Content
from .multi_pet import _immutable,_read,_key,_signed,_verify
from .visible_delivery import (
 KINDS,ENVELOPE_TEXT,FRAGMENT_VERSION,OBJECT_FRAGMENT_VERSION,OBJECT_REF,visible_parameters,
)

VERSION='petlab-visible-execution-checkpoint-v1'
EFFECT='petlab-visible-effect-v1'
SPEECH_PROGRESS='petlab-spoken-progress-v1'
SEALED_DELIVERY='sealed-output-v1'
SPEECH_FIRST_DELIVERY='speech-first-output-v2'
EPISODE_DELIVERIES={SEALED_DELIVERY,SPEECH_FIRST_DELIVERY}


def phase_key(item):return item['content']['content_id']+('::started' if item['phase']=='started' else '::ended')


def display_content(content,phase,reason):
 if content.kind!='action':return content
 if phase=='started':text='开始'+content.description+'，活动正在进行。'
 elif phase=='completed':text='此前的'+content.description+'已经结束。'
 else:
  explanation={'user_contact':'停止原因是用户接触。','technical_failure':'停止原因是播放执行故障。',
   'source_expired':'原行动的现场适用条件已失效。','window_closed':'停止原因是显示窗口关闭。'}.get(reason,'停止原因未确认。')
  text='此前的'+content.description+'已被打断并停止。'+explanation
 return replace(content,description=text,parameters={})


class VisibleExecutionPublisher:
 def __init__(self,link):
  from .filesystem_paths import native_directory
  self.link=link;self.workspace=link.workspace
  self.root=native_directory(link.local/'visible-executions','0'*64+'/batch-'+'0'*64+'.json')
  self._progress_lock=threading.Lock();self._progress={}
 def directory(self,parent):return self.root/_key(parent)
 def speech_progress_snapshot(self,parent,fingerprint=None):
  """Persist only a Writer-read snapshot, never one file or event per letter."""
  if fingerprint is not None:
   if len(fingerprint)!=64 or any(c not in '0123456789abcdef' for c in fingerprint):raise ValueError('invalid progress snapshot reference')
   value=_verify(_read(self.directory(parent)/('progress-'+fingerprint+'.json')))
   if value['fingerprint']!=fingerprint or value['source']['parent_action_id']!=parent:raise ValueError('progress snapshot source differs')
   return value
  with self._progress_lock:
   current=self._progress.get(parent)
   if not current or not current['items']:return None
   value=_signed({'version':SPEECH_PROGRESS,'source':current['source'],'items':list(current['items'].values())})
  _immutable(self.directory(parent)/('progress-'+value['fingerprint']+'.json'),value)
  return value
 def update_speech_progress(self,meta,item):
  with self._progress_lock:
   current=self._progress.setdefault(meta['parent_action_id'],{'source':meta,'items':{}})
   if current['source']!=meta:raise ValueError('progress parent changed')
   current['items'][item['content']['content_id']]=item
 def finish_speech_progress(self,parent,identity):
  with self._progress_lock:
   current=self._progress.get(parent)
   if current:current['items'].pop(identity,None)
 def records(self,parent):
  directory=self.directory(parent)
  rows=sorted((_verify(_read(p)) for p in directory.glob('batch-*.json')),key=lambda r:r['sequence'])
  if not rows:return rows
  source=_verify(_read(directory/'source.json'))
  if source['parent_action_id']!=parent:raise ValueError('visible checkpoint source differs')
  previous=None
  for index,row in enumerate(rows):
   if row['source']!=source:raise ValueError('visible checkpoint source differs')
   if row['sequence']!=index or row['previous']!=previous:raise ValueError('visible checkpoint lineage differs')
   previous=row['fingerprint']
  return rows
 def recover(self):
  for directory in sorted(self.root.glob('*')):
   if not directory.is_dir():continue
   source=_verify(_read(directory/'source.json'))
   if directory!=self.directory(source['parent_action_id']):raise ValueError('checkpoint directory differs')
   if source.get('participant_delivery') in EPISODE_DELIVERIES:
    if not self.seal_ready(source):self.publish_speech_prefix(source)
   else:
    for row in self.records(source['parent_action_id']):self.publish(row)
 def begin(self,prepared,parent,contents):
  app=self.link.product.application
  plan,expected=prepared.validate(binding=app.runtime.session.session_binding,owner_ref=app.owner_ref,user_ref=prepared.authorization.target_ref)
  if plan.action_id!=parent or tuple(expected)!=tuple(contents):raise ValueError('checkpoint dispatch differs from authorized parent')
  destination=thaw_model_data(prepared.request.readonly_facts.get('participant_destination'))
  if destination is None:return None
  from .destinations import is_npc_whisper
  direct=prepared.candidate.realization_evidence.get('speech_to')
  meta=_signed({'version':VERSION,'parent_action_id':parent,'owner_ref':app.owner_ref,
   'profile_fingerprint':app.definition.fingerprint,'session_binding':app.runtime.session.session_binding.fingerprint,
   'participant_delivery':SPEECH_FIRST_DELIVERY,'prepared_fingerprint':prepared.fingerprint,
   'authorization_ref':prepared.authorization.fingerprint,'request_ref':prepared.request.fingerprint,
   'target_ref':prepared.authorization.target_ref,'destination':destination,
   **({'selected_speech_audience':list(prepared.candidate.candidate_payload['audience']['speech'])} if prepared.request.control.get('audience_contract') else {}),
   **({'speech_to':list(direct)} if direct is not None else {}),
   **({'physical_observers':list(prepared.request.readonly_facts['audience_envelope']['physical_observers'])} if prepared.request.control.get('audience_contract') else {}),
   **({'public_physical_envelope':True} if is_npc_whisper(destination) else {})})
  path=self.directory(parent)/'source.json'
  if path.exists():
   saved=_verify(_read(path))
   if saved.get('participant_delivery') is None:
    legacy=_signed({key:value for key,value in meta.items()
                    if key not in {'participant_delivery','prepared_fingerprint','fingerprint'}})
    if legacy!=saved:raise ValueError('legacy visible parent source differs')
    meta=saved
   elif saved.get('participant_delivery')==SEALED_DELIVERY:
    previous={key:value for key,value in meta.items() if key!='fingerprint'}
    if _signed({**previous,'participant_delivery':SEALED_DELIVERY})!=saved:
     raise ValueError('visible episode parent source differs')
    meta=saved
  _immutable(path,meta)
  return Recorder(self,prepared,tuple(contents),meta,app.runtime.session.session_binding)
 def save(self,meta,items,*,evidence_kind='physical_execution_checkpoint',commit_ref=None):
  if not items:return None
  _verify(meta)
  parent=meta['parent_action_id']
  _immutable(self.directory(parent)/'source.json',meta)
  existing=self.records(parent);seen={phase_key(i):i for r in existing for i in r['items']}
  fresh=[]
  for item in items:
   key=phase_key(item)
   if key in seen:
    if fp(seen[key])!=fp(item):raise ValueError('actual visible phase changed after publication')
   else:
    start=seen.get(item['content']['content_id']+'::started')
    if start is not None and item['phase']!='started' and (item['started_at_ms']!=start['started_at_ms'] or item['at_ms']<start['at_ms']):
     raise ValueError('visible terminal phase differs from its actual start')
    fresh.append(item)
  if not fresh:return None
  row=_signed({'version':VERSION,'source':meta,'items':fresh,'evidence_kind':evidence_kind,'commit_ref':commit_ref,
   'sequence':len(existing),'previous':existing[-1]['fingerprint'] if existing else None})
  _immutable(self.directory(meta['parent_action_id'])/('batch-'+row['fingerprint']+'.json'),row)
  if meta.get('participant_delivery') not in EPISODE_DELIVERIES:
   self.publish(row)
  return row
 def publish_speech_prefix(self,meta):
  if meta.get('participant_delivery')!=SPEECH_FIRST_DELIVERY:return None
  directory=self.directory(meta['parent_action_id']);path=directory/'speech-prefix.json'
  if path.exists():
   prefix=_verify(_read(path))
   if prefix['source']!=meta:raise ValueError('speech prefix source differs')
  else:
   latest={}
   for batch in self.records(meta['parent_action_id']):
    speech_times=[item['at_ms'] for item in batch['items']
                  if item['content']['kind']=='speech' and item['phase'] in {'completed','interrupted'}]
    heard=min(speech_times) if speech_times else None
    for item in batch['items']:
     if heard is None or item['at_ms']<=heard:latest[item['content']['content_id']]=item
    if heard is not None:
     items=[item for item in latest.values() if item['at_ms']<=heard]
     prefix=_signed({'version':VERSION,'source':meta,'items':items,
       'evidence_kind':'physical_execution_checkpoint','commit_ref':batch['fingerprint'],
       'sequence':0,'previous':None,'closed_at_ms':heard})
     _immutable(path,prefix)
     break
   else:return None
  self.publish(prefix)
  return prefix
 def publish_episode(self,row):
  prefix=self.publish_speech_prefix(row['source'])
  if prefix is not None:
   final_speech={item['content']['content_id'] for item in row['items'] if item['content']['kind']=='speech'}
   if any(item['content']['kind']=='speech' and item['content']['content_id'] not in final_speech
          for item in prefix['items']):
    raise ValueError('played speech is missing from its parent outcome')
   sent={phase_key(item) for item in prefix['items']}
   remaining=[item for item in row['items'] if phase_key(item) not in sent]
   terminal={key:value for key,value in row.items() if key!='fingerprint'}
   row=_signed({**terminal,'items':remaining,'sequence':1,'previous':prefix['fingerprint']})
  if row['items']:self.publish(row)
 def seal_ready(self,meta):
  from product_adapters.desktop_pet_v1.composite_journal import ParentJournal
  parent=meta['parent_action_id']
  sealed=self.directory(parent)/'episode.json'
  if sealed.exists():
   row=_verify(_read(sealed))
   if row['source']!=meta:raise ValueError('sealed visible episode source differs')
   self.publish_episode(row)
   return True
  state=ParentJournal(self.link.product.gateway.trace_dir/'parents',parent).state()
  outcome=state.get('outcome')
  if outcome is None:return False
  if state['source']['prepared_fingerprint']!=meta['prepared_fingerprint']:
   raise ValueError('visible episode outcome belongs to another parent')
  visible_ids={key for key,child in state['plan'].children.items() if child['kind'] in KINDS}
  return self.seal(meta,outcome,expected=visible_ids)
 def seal(self,meta,outcome,*,expected=None):
  parent=meta['parent_action_id']
  batches=self.records(parent)
  # The latest real checkpoint carries the child's actual terminal result.
  # Stable insertion order preserves the order in which children were seen.
  by_id={}
  for batch in batches:
   for item in batch['items']:by_id[item['content']['content_id']]=item
  outcomes=outcome['details']['child_results']
  actual={key for key,row in outcomes.items() if row['started_at_ms'] is not None
          and row['status'] in {'completed','interrupted'}}
  required=actual & (set(by_id) if expected is None else expected)
  if any(key not in by_id or by_id[key]['phase']!=outcomes[key]['status'] for key in required):
   return False # Incomplete recorder: let authorized recovery reconcile it.
  items=[item for key,item in by_id.items() if key in required]
  at=outcome['details']['occurred_at_wall_ms']
  if any(item['at_ms']>at for item in items):raise ValueError('visible episode exceeds terminal time')
  row=_signed({'version':VERSION,'source':meta,'items':items,
       'evidence_kind':'physical_execution_episode','commit_ref':fp(outcome),
       'sequence':0,'previous':None,'closed_at_ms':at})
  _immutable(self.directory(parent)/'episode.json',row)
  self.publish_episode(row)
  return True
 def publish(self,row):
  _verify(row);meta=_verify(row['source']);w=self.workspace;owner=meta['owner_ref'];parent=meta['parent_action_id']
  if (meta['version']!=VERSION or row['version']!=VERSION or owner not in w.aliases
      or w.definitions[w.aliases[owner]].fingerprint!=meta['profile_fingerprint']):raise ValueError('visible checkpoint owner differs')
  destination=meta['destination'];_,descriptor=w.registered_destination(destination['conversation'])
  if 'physical_observers' in meta:
   observers=meta['physical_observers']
   if len(set(observers))!=len(observers) or set(observers)!=set(w.participants)-{owner}:
    raise ValueError('physical observer scope differs from registered scene')
   descriptor=w.conversations['public']
  elif meta.get('public_physical_envelope'):
   from .destinations import is_npc_whisper
   if not is_npc_whisper(destination):raise ValueError('public physical envelope requires private NPC destination')
   descriptor=w.conversations['public']
  from model_packages.cx_six_seven_v1.multi_participant.events import canonical_npc_output_event_id
  segment=parent+'#visible:'+row['fingerprint']
  canonical=canonical_npc_output_event_id(conversation_id=descriptor.conversation_id,sender_participant_id=owner,action_id=segment)
  old_path=w.root/'sources'/(_key(canonical)+'.json')
  if old_path.exists():
   old=_verify(_read(old_path));origin=old['origin']
   if origin['commit_fingerprint']!=row['fingerprint'] or origin['participant_id']!=owner or origin.get('parent_action_id')!=parent:
    raise ValueError('published checkpoint binding differs')
   w._route(old);return
  from .visible_delivery import expand_presentations
  expanded=[]
  for item in row['items']:
   for content in expand_presentations((Content.from_record(item['content']),)):
    expanded.append({**item,'content':content.to_record()})
  if len({i['content']['content_id'] for i in expanded})!=len(expanded):raise ValueError('visible projection identity collision')
  audience=set(descriptor.active_member_ids)-{owner};items=[];has_objects=False
  for item in expanded:
   c=Content.from_record(item['content'])
   if c.kind not in KINDS or c.private:raise ValueError('checkpoint contains a nonvisible child')
   recipients=tuple(meta.get('physical_observers',tuple(r for r in descriptor.active_member_ids if r!=owner))) if (meta.get('public_physical_envelope') or 'physical_observers' in meta) and c.kind in {'action','expression'} else tuple(r for r in c.recipient_refs if r in audience)
   if not recipients:continue
   params=visible_parameters(c,completed=item['phase']=='completed')
   has_objects=has_objects or OBJECT_REF in params
   c=replace(c,recipient_refs=recipients,parameters=params)
   body=c.to_record();digest=fp(body)
   _immutable(w.root/'visible-contents'/(digest+'.json'),body)
   items.append({'content_id':c.content_id,'kind':c.kind,'recipient_refs':list(recipients),'body_fingerprint':digest,
    **{k:item[k] for k in ('phase','started_at_ms','at_ms','reason','cause_ref')}})
  if not items:return
  segment=parent+'#visible:'+row['fingerprint']
  from product_adapters.desktop_pet_v1.audience_delivery import visible_targets
  targets=visible_targets([meta['target_ref']],meta.get('speech_to'),items)
  manifest={'version':OBJECT_FRAGMENT_VERSION if has_objects else FRAGMENT_VERSION,'parent_event_id':parent,'segment_id':segment,'actor_ref':owner,
   'target_refs':targets,'items':items,'sequence':row['sequence'],
   'previous_segment_id':None if row['previous'] is None else parent+'#visible:'+row['previous']}
  from model_packages.cx_six_seven_v1.multi_participant.events import canonical_npc_output_event_id,commit_message_event
  canonical=canonical_npc_output_event_id(conversation_id=descriptor.conversation_id,sender_participant_id=owner,action_id=segment)
  # Route the authorized target separately from each child's visible audience.
  event=commit_message_event(descriptor,event_id=canonical,sender_participant_id=owner,text=ENVELOPE_TEXT,
   committed_at_ms=row.get('closed_at_ms',max(i['at_ms'] for i in items)),sender_action_id=segment,addressed_participant_ids=manifest['target_refs'])
  origin={'kind':'npc','participant_id':owner,'action_id':segment,'event_fingerprint':fp(manifest),
   'commit_fingerprint':row['fingerprint'],'evidence_kind':row['evidence_kind'],'parent_action_id':parent,
   **({'participant_delivery':SPEECH_FIRST_DELIVERY}
      if meta.get('participant_delivery')==SPEECH_FIRST_DELIVERY else {}),
   **({'private_speech_target':meta['target_ref']} if meta.get('public_physical_envelope') else {})}
  selected=meta.get('selected_speech_audience')
  if selected is not None:
   origin['selected_speech_audience']=selected
   if len(selected)==1 and any(i['kind']=='speech' and i['recipient_refs']==selected for i in items):
    origin['private_speech_target']=selected[0]
  w._append(event,origin,manifest)
 def finish_committed(self,app,event,source):
  path=self.directory(event.event_id)/'source.json'
  if not path.exists():return False
  app._verify_source(event);meta=_verify(_read(path))
  if meta['owner_ref']!=app.owner_ref or meta['session_binding']!=app.runtime.session.session_binding.fingerprint:
   raise ValueError('final checkpoint parent owner differs')
  sealed=meta.get('participant_delivery') in EPISODE_DELIVERIES
  if sealed and self.seal_ready(meta):return True
  child_results={}
  if sealed:
   receipt=source['evidence']['fact']['model_payload']['execution_receipt']
   child_results=receipt['outcome']['details'].get('child_results',{})
  seen={phase_key(i):i for r in self.records(event.event_id) for i in r['items']};remaining=[]
  for c in event.contents:
   if c.kind not in KINDS or c.private:continue
   if c.content_id+'::ended' in seen:continue
   if sealed:
    child=child_results.get(c.content_id,{})
    if child.get('started_at_ms') is None or child['status'] not in {'completed','interrupted'}:continue
    status=child['status'];start=child['started_at_ms'];end=child['ended_at_ms']
    reason=child.get('reason');cause=child.get('cause_ref')
   else:
    start=seen.get(c.content_id+'::started',{}).get('started_at_ms',event.position.coordinate)
    status=c.parameters.get('execution_status','completed') if c.kind=='action' else 'completed'
    end=event.position.coordinate;reason=c.parameters.get('interruption_reason');cause=c.parameters.get('cause_ref')
   params=visible_parameters(c,completed=status=='completed')
   remaining.append({'content':replace(c,parameters=params).to_record(),
    'phase':status,'started_at_ms':start,'at_ms':end,'reason':reason,'cause_ref':cause})
  self.save(meta,remaining,evidence_kind='committed_execution',commit_ref=source['evidence']['commit_receipt']['fingerprint'])
  if not sealed:return True
  if self.seal_ready(meta):return True
  if meta.get('participant_delivery')==SPEECH_FIRST_DELIVERY:
   raise ValueError('speech-first output is missing its terminal evidence')
  return False


class Recorder:
 def __init__(self,publisher,prepared,contents,meta,binding):
  self.publisher=publisher;self.prepared=prepared;self.expected={c.content_id:c for c in contents};self.meta=meta;self.binding=binding
  self.queue=queue.Queue();self.errors=[];self.route_error=None;self.closed=False
  self.speech_progress={};self.speech_terminal=set()
  self.worker=threading.Thread(target=self.run,name='pet-visible-receipts');self.worker.start()
 def submit(self,value):
  if self.closed:raise ValueError('visible execution observer closed')
  self.queue.put(thaw_model_data(value))
 def close(self):
  self.closed=True;self.queue.put(None);self.worker.join()
  if self.errors:raise self.errors[0]
  if self.route_error is not None:raise self.route_error
  if self.meta.get('participant_delivery') in EPISODE_DELIVERIES:self.publisher.seal_ready(self.meta)
  with self.publisher._progress_lock:
   self.publisher._progress.pop(self.meta['parent_action_id'],None)
 def run(self):
  while True:
   value=self.queue.get()
   if value is None:return
   if self.errors:continue
   try:self.accept(value)
   except BaseException as error:self.errors.append(error)
 def accept(self,value):
  if value.get('version')==SPEECH_PROGRESS:
   self.accept_speech_progress(value);return
  if (set(value)!={'version','action_id','items'} or value['version'] not in {EFFECT,'petlab-visible-effect-v2-spatial','petlab-visible-effect-v3-object'} or value['action_id']!=self.meta['parent_action_id']
      or not value['items'] or len(value['items'])>len(self.prepared.composite_plan().children)):
   raise ValueError('visible effect batch differs')
  from product_adapters.desktop_pet_v1.composite_journal import ParentJournal
  state=ParentJournal(self.publisher.link.product.gateway.trace_dir/'parents',value['action_id']).state()
  if state['source']['prepared_fingerprint']!=self.prepared.fingerprint:raise ValueError('visible effect has no sealed parent')
  actual=[];keys=set()
  for row in value['items']:
   extras={'gaze_result','movement_result','meal_result','contact_result','prop_move_result','meal_button_result'} if value['version']=='petlab-visible-effect-v3-object' else {'gaze_result','movement_result'} if value['version']=='petlab-visible-effect-v2-spatial' else set()
   extras=extras|{'spoken_prefix'}
   if set(row)!=({'content','phase','started_at_ms','at_ms','reason','cause_ref'}|(set(row)&extras)):raise ValueError('visible effect fields differ')
   c=Content.from_record(row['content']);identity=c.content_id;child=state['children'].get(identity,{})
   if c.private or c.kind not in KINDS:raise ValueError('private or invisible child cannot be published')
   if child.get('phase') not in {'dispatched','finished'}:raise ValueError('visible effect was not dispatched')
   self.resolve_expected(identity,child)
   if self.expected.get(identity)!=c:raise ValueError('visible content differs from sealed candidate')
   if c.kind=='speech' and row['phase']=='interrupted':
    from model_packages.desktop_pet_v1.behavior import realized_speech_prefix
    c=realized_speech_prefix(c,row.get('spoken_prefix'))
   elif 'spoken_prefix' in row:raise ValueError('speech prefix has no interrupted speech')
   if c.kind=='speech':
    prior=self.speech_progress.get(identity)
    if prior is not None and (not c.description.startswith(prior['prefix']) or row['at_ms']<prior['at_ms'] or row['started_at_ms']!=prior['started_at_ms']):
     raise ValueError('terminal speech contradicts displayed prefix')
    self.speech_terminal.add(identity)
    self.publisher.finish_speech_progress(self.meta['parent_action_id'],identity)
   if (row['phase'] not in ({'started','completed','interrupted'} if c.kind=='action' or c.kind=='expression' and 'hold_ms' in c.parameters else {'completed','interrupted'} if c.kind=='speech' else {'completed'})
       or type(row['at_ms']) is not int or type(row['started_at_ms']) is not int
       or row['started_at_ms']>row['at_ms'] or row['at_ms']<self.prepared.position.position.coordinate
       or row['phase']=='started' and row['started_at_ms']!=row['at_ms']):raise ValueError('visible effect phase/time invalid')
   key=phase_key(row)
   if key in keys:raise ValueError('batch repeats a visible effect')
   keys.add(key)
   from model_packages.desktop_pet_v1 import spatial
   if c.parameters.get('presentation',{}).get('version')==spatial.PRESENTATION:
    c=spatial.realized_content(c,row.get('gaze_result'))
   elif 'gaze_result' in row:raise ValueError('gaze result has no planned expression')
   if c.kind=='action' and c.semantic_key in ('move_near','stroll','move_away','relocate'):
    from model_packages.desktop_pet_v1.movement import realized
    visible=realized(c,row.get('movement_result'),status=row['phase'],reason=row['reason'],cause_ref=row.get('cause_ref'))
   elif 'movement_result' in row:raise ValueError('movement result has no selected movement')
   else:visible=display_content(c,row['phase'],row['reason'])
   if 'meal' in c.parameters and row['phase']!='started':
    from model_packages.desktop_pet_v1.shared_meal import result_content
    visible=replace(result_content(c,row.get('meal_result'),status=row['phase']),parameters={})
   elif 'meal_result' in row:raise ValueError('meal result has no completed body action')
   if 'social_contact' in c.parameters and row['phase']!='started':
    from model_packages.desktop_pet_v1.social_contact import realized
    visible=replace(realized(c,row.get('contact_result'),row['phase']),parameters={})
   elif 'contact_result' in row:raise ValueError('contact result has no completed body action')
   if 'prop_move' in c.parameters:
    from model_packages.desktop_pet_v1.prop_move import realized
    visible=realized(c,row.get('prop_move_result'),row['phase'])
   elif 'prop_move_result' in row:raise ValueError('prop result has no selected action')
   if 'meal_button' in c.parameters:
    from model_packages.desktop_pet_v1.meal_button import realized
    visible=realized(c,row.get('meal_button_result'),row['phase'])
   elif 'meal_button_result' in row:raise ValueError('button result has no selected action')
   from .visible_delivery import action_description
   params=visible_parameters(visible,completed=row['phase']=='completed')
   visible=replace(visible,description=action_description(c,visible.description),parameters=params)
   plan=self.prepared.composite_plan()
   if c.kind=='action' and plan.version in {'desktop-pet-composite-v4-sequence','desktop-pet-composite-v5-dependencies'}:
    from model_packages.desktop_pet_v1.action_sequence import describe_stage,body_order
    bodies=body_order(plan.children)
    visible=describe_stage(visible,bodies.index(c.content_id))
   actual.append({**{k:v for k,v in row.items() if k not in {'gaze_result','movement_result','meal_result','contact_result','prop_move_result','meal_button_result','spoken_prefix'}},'content':visible.to_record()})
  batch=self.publisher.save(self.meta,actual)
  if (batch and self.meta.get('participant_delivery')==SPEECH_FIRST_DELIVERY
      and any(i['content']['kind']=='speech' for i in batch['items'])
      and not (self.publisher.directory(self.meta['parent_action_id'])/'speech-prefix.json').exists()):
   try:self.publisher.publish_speech_prefix(self.meta)
   except OSError as error:
    # Finish recording physical phases even if recipient routing is interrupted.
    self.route_error=error


 def resolve_expected(self,identity,child):
  if identity not in self.expected:
   evidence=child['prepared'];late=self.prepared.candidate.candidate_payload['late_speech']
   if self.prepared.request.control.get('staged_version')=='desktop-pet-staged-v2-private-thought':
    from product_adapters.desktop_pet_v1.composite_writer import verify_expression_children
    children,_=verify_expression_children(parent=self.prepared,evidence=evidence,binding=self.binding,
     owner_ref=self.meta['owner_ref'],user_ref=self.meta['target_ref'],child_ids={'speech':late['content_id'],'thought':late['thought_content_id']})
   else:
    from product_adapters.desktop_pet_v1.composite_writer import verify_speech_child
    speech,_=verify_speech_child(parent=self.prepared,evidence=evidence,binding=self.binding,
     owner_ref=self.meta['owner_ref'],user_ref=self.meta['target_ref'],child_id=late['content_id'])
    children=(speech,)
   self.expected.update({x.content_id:x for x in children})

 def accept_speech_progress(self,value):
  from product_adapters.desktop_pet_v1.composite_journal import ParentJournal
  if set(value)!={'version','action_id','content','prefix','started_at_ms','at_ms'} or value['action_id']!=self.meta['parent_action_id']:
   raise ValueError('speech progress fields differ')
  c=Content.from_record(value['content']);identity=c.content_id
  state=ParentJournal(self.publisher.link.product.gateway.trace_dir/'parents',value['action_id']).state()
  child=state['children'].get(identity,{})
  if state['source']['prepared_fingerprint']!=self.prepared.fingerprint or child.get('phase')!='dispatched':
   raise ValueError('speech progress lacks dispatched child')
  self.resolve_expected(identity,child)
  prefix=value['prefix'];old=self.speech_progress.get(identity)
  if (state['source']['prepared_fingerprint']!=self.prepared.fingerprint or child.get('phase')!='dispatched'
      or c!=self.expected.get(identity) or c.kind!='speech' or c.private or identity in self.speech_terminal
      or not isinstance(prefix,str) or not prefix or not c.description.startswith(prefix)
      or type(value['at_ms']) is not int or type(value['started_at_ms']) is not int
      or not self.prepared.position.position.coordinate<=value['started_at_ms']<=value['at_ms']
      or old is not None and (not prefix.startswith(old['prefix']) or value['started_at_ms']!=old['started_at_ms'] or value['at_ms']<old['at_ms'])):
   raise ValueError('speech progress differs from actual monotone prefix')
  self.speech_progress[identity]=dict(value)
  self.publisher.update_speech_progress(self.meta,{'content':replace(c,description=prefix).to_record(),
      'phase':'progress','started_at_ms':value['started_at_ms'],'at_ms':value['at_ms'],'reason':None,'cause_ref':None})


@contextmanager
def observe(product,prepared,parent,contents,executor):
 publisher=getattr(product,'visible_execution_publisher',None)
 if publisher is None or not callable(getattr(executor,'execution_observer',None)) or prepared.composite_plan() is None:
  yield None
  return
 recorder=publisher.begin(prepared,parent,contents)
 try:yield None if recorder is None else recorder.submit
 finally:
  if recorder is not None:recorder.close()
