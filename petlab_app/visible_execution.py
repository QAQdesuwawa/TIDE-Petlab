"""Durable outward checkpoints from an already-authorized physical execution.

No state settlement, action choice or model call. The UI only enqueues signals;
a recorder verifies sealed child provenance and publishes each actual phase once.
"""
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
import queue,threading
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.events import Content
from .multi_pet import _immutable,_read,_key,_signed,_verify
from .visible_delivery import KINDS,ENVELOPE_TEXT

VERSION='petlab-visible-execution-checkpoint-v1'
MANIFEST='petlab-visible-children-v2-fragments'
EFFECT='petlab-visible-effect-v1'


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
  self.link=link;self.workspace=link.workspace;self.root=link.local/'visible-executions'
 def directory(self,parent):return self.root/_key(parent)
 def records(self,parent):
  directory=self.directory(parent)
  rows=sorted((_verify(_read(p)) for p in directory.glob('batch-*.json')),key=lambda r:r['sequence'])
  previous=None
  for index,row in enumerate(rows):
   if row['sequence']!=index or row['previous']!=previous or row['source']['parent_action_id']!=parent:raise ValueError('visible checkpoint lineage differs')
   previous=row['fingerprint']
  return rows
 def recover(self):
  for directory in sorted(self.root.glob('*')):
   if not directory.is_dir():continue
   source=_verify(_read(directory/'source.json'))
   if directory!=self.directory(source['parent_action_id']):raise ValueError('checkpoint directory differs')
   for row in self.records(source['parent_action_id']):self.publish(row)
 def begin(self,prepared,parent,contents):
  app=self.link.product.application
  plan,expected=prepared.validate(binding=app.runtime.session.session_binding,owner_ref=app.owner_ref,user_ref=prepared.authorization.target_ref)
  if plan.action_id!=parent or tuple(expected)!=tuple(contents):raise ValueError('checkpoint dispatch differs from authorized parent')
  destination=thaw_model_data(prepared.request.readonly_facts.get('participant_destination'))
  if destination is None:return None
  meta=_signed({'version':VERSION,'parent_action_id':parent,'owner_ref':app.owner_ref,
   'profile_fingerprint':app.definition.fingerprint,'session_binding':app.runtime.session.session_binding.fingerprint,
   'authorization_ref':prepared.authorization.fingerprint,'request_ref':prepared.request.fingerprint,
   'target_ref':prepared.authorization.target_ref,'destination':destination})
  _immutable(self.directory(parent)/'source.json',meta)
  return Recorder(self,prepared,tuple(contents),meta,app.runtime.session.session_binding)
 def save(self,meta,items,*,evidence_kind='physical_execution_checkpoint',commit_ref=None):
  if not items:return None
  existing=self.records(meta['parent_action_id']);seen={phase_key(i):i for r in existing for i in r['items']}
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
  self.publish(row)
  return row
 def publish(self,row):
  _verify(row);meta=_verify(row['source']);w=self.workspace;owner=meta['owner_ref'];parent=meta['parent_action_id']
  if (meta['version']!=VERSION or row['version']!=VERSION or owner not in w.aliases
      or w.definitions[w.aliases[owner]].fingerprint!=meta['profile_fingerprint']):raise ValueError('visible checkpoint owner differs')
  destination=meta['destination'];descriptor=next((d for d in w.conversations.values() if d.as_record()==destination['conversation']),None)
  if descriptor is None:raise ValueError('visible checkpoint destination differs')
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
  audience=set(descriptor.active_member_ids)-{owner};items=[]
  for item in expanded:
   c=Content.from_record(item['content'])
   if c.kind not in KINDS or c.private:raise ValueError('checkpoint contains a nonvisible child')
   recipients=tuple(r for r in c.recipient_refs if r in audience)
   if not recipients:continue
   c=replace(c,recipient_refs=recipients,parameters={} if c.kind=='action' else c.parameters)
   body=c.to_record();digest=fp(body)
   _immutable(w.root/'visible-contents'/(digest+'.json'),body)
   items.append({'content_id':c.content_id,'kind':c.kind,'recipient_refs':list(recipients),'body_fingerprint':digest,
    **{k:item[k] for k in ('phase','started_at_ms','at_ms','reason','cause_ref')}})
  if not items:return
  segment=parent+'#visible:'+row['fingerprint']
  manifest={'version':MANIFEST,'parent_event_id':parent,'segment_id':segment,'actor_ref':owner,
   'target_refs':[meta['target_ref']],'items':items,'sequence':row['sequence'],
   'previous_segment_id':None if row['previous'] is None else parent+'#visible:'+row['previous']}
  from model_packages.cx_six_seven_v1.multi_participant.events import canonical_npc_output_event_id,commit_message_event
  canonical=canonical_npc_output_event_id(conversation_id=descriptor.conversation_id,sender_participant_id=owner,action_id=segment)
  event=commit_message_event(descriptor,event_id=canonical,sender_participant_id=owner,text=ENVELOPE_TEXT,
   committed_at_ms=max(i['at_ms'] for i in items),sender_action_id=segment,addressed_participant_ids=destination['addressed'])
  origin={'kind':'npc','participant_id':owner,'action_id':segment,'event_fingerprint':fp(manifest),
   'commit_fingerprint':row['fingerprint'],'evidence_kind':row['evidence_kind'],'parent_action_id':parent}
  w._append(event,origin,manifest)
 def finish_committed(self,app,event,source):
  path=self.directory(event.event_id)/'source.json'
  if not path.exists():return False
  app._verify_source(event);meta=_verify(_read(path))
  if meta['owner_ref']!=app.owner_ref or meta['session_binding']!=app.runtime.session.session_binding.fingerprint:
   raise ValueError('final checkpoint parent owner differs')
  seen={phase_key(i):i for r in self.records(event.event_id) for i in r['items']};remaining=[]
  for c in event.contents:
   if c.kind not in KINDS or c.private:continue
   if c.content_id+'::ended' in seen:continue
   start=seen.get(c.content_id+'::started',{}).get('started_at_ms',event.position.coordinate)
   status=c.parameters.get('execution_status','completed') if c.kind=='action' else 'completed'
   remaining.append({'content':replace(c,parameters={} if c.kind=='action' else c.parameters).to_record(),
    'phase':status,'started_at_ms':start,'at_ms':event.position.coordinate,
    'reason':c.parameters.get('interruption_reason'),'cause_ref':c.parameters.get('cause_ref')})
  self.save(meta,remaining,evidence_kind='committed_execution',commit_ref=source['evidence']['commit_receipt']['fingerprint'])
  return True


class Recorder:
 def __init__(self,publisher,prepared,contents,meta,binding):
  self.publisher=publisher;self.prepared=prepared;self.expected={c.content_id:c for c in contents};self.meta=meta;self.binding=binding
  self.queue=queue.Queue();self.errors=[];self.closed=False
  self.worker=threading.Thread(target=self.run,name='pet-visible-receipts');self.worker.start()
 def submit(self,value):
  if self.closed:raise ValueError('visible execution observer closed')
  self.queue.put(thaw_model_data(value))
 def close(self):
  self.closed=True;self.queue.put(None);self.worker.join()
  if self.errors:raise self.errors[0]
 def run(self):
  while True:
   value=self.queue.get()
   if value is None:return
   if self.errors:continue
   try:self.accept(value)
   except BaseException as error:self.errors.append(error)
 def accept(self,value):
  if (set(value)!={'version','action_id','items'} or value['version'] not in {EFFECT,'petlab-visible-effect-v2-spatial','petlab-visible-effect-v3-object'} or value['action_id']!=self.meta['parent_action_id']
      or not value['items'] or len(value['items'])>len(self.prepared.composite_plan().children)):
   raise ValueError('visible effect batch differs')
  from product_adapters.desktop_pet_v1.composite_journal import ParentJournal
  state=ParentJournal(self.publisher.link.product.gateway.trace_dir/'parents',value['action_id']).state()
  if state['source']['prepared_fingerprint']!=self.prepared.fingerprint:raise ValueError('visible effect has no sealed parent')
  actual=[];keys=set()
  for row in value['items']:
   extras={'gaze_result','movement_result','meal_result'} if value['version']=='petlab-visible-effect-v3-object' else {'gaze_result','movement_result'} if value['version']=='petlab-visible-effect-v2-spatial' else set()
   if set(row)!=({'content','phase','started_at_ms','at_ms','reason','cause_ref'}|(set(row)&extras)):raise ValueError('visible effect fields differ')
   c=Content.from_record(row['content']);identity=c.content_id;child=state['children'].get(identity,{})
   if c.private or c.kind not in KINDS:raise ValueError('private or invisible child cannot be published')
   if child.get('phase') not in {'dispatched','finished'}:raise ValueError('visible effect was not dispatched')
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
   if self.expected.get(identity)!=c:raise ValueError('visible content differs from sealed candidate')
   if (row['phase'] not in ({'started','completed','interrupted'} if c.kind=='action' else {'completed'})
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
   if c.kind=='action' and c.semantic_key=='move_near':
    from model_packages.desktop_pet_v1.movement import realized
    visible=realized(c,row.get('movement_result'),status=row['phase'],reason=row['reason'])
    visible=replace(visible,parameters={})
   elif 'movement_result' in row:raise ValueError('movement result has no selected movement')
   else:visible=display_content(c,row['phase'],row['reason'])
   if 'meal' in c.parameters and row['phase']!='started':
    from model_packages.desktop_pet_v1.shared_meal import result_content
    visible=replace(result_content(c,row.get('meal_result'),status=row['phase']),parameters={})
   elif 'meal_result' in row:raise ValueError('meal result has no completed body action')
   plan=self.prepared.composite_plan()
   if c.kind=='action' and plan.version=='desktop-pet-composite-v4-sequence':
    from model_packages.desktop_pet_v1.action_sequence import describe_stage
    bodies=[key for key,r in plan.children.items() if r['kind']=='action']
    visible=describe_stage(visible,bodies.index(c.content_id))
   actual.append({**{k:v for k,v in row.items() if k not in {'gaze_result','movement_result','meal_result'}},'content':visible.to_record()})
  self.publisher.save(self.meta,actual)


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
