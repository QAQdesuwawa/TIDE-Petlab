"""Product workspace for canonical participants and durable visible deliveries.

This is transport, not a second memory/state authority. An inbox is an accepted
source projection awaiting its owner Actor, never a measured psychological fact.
"""
from dataclasses import replace
from pathlib import Path
import hashlib,json,threading,os
from contextlib import contextmanager
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.cx_six_seven_v1.participants import make_human_participant,make_npc_profile
from model_packages.cx_six_seven_v1.multi_participant.conversations import ConversationDescriptor
from model_packages.cx_six_seven_v1.multi_participant.events import CommittedConversationEvent,commit_message_event,canonical_npc_output_event_id
from model_packages.cx_six_seven_v1.multi_participant.router import route_committed_event
from model_packages.cx_six_seven_v1.multi_participant.local_projection import project_delivery
from product_adapters.cx_six_seven_v1.multi_participant.sessions import ProductConversationSession,ProductConversationSessionRegistry
from ops.storage import atomic_write_json

VERSION='petlab-multi-workspace-v1'
OPEN_INVITATION_POLICY='open-invitation-v1'
DEFAULT_NAMES={'DS1':'大肥鱼','DS2':'小肥鱼'}

def _instance_text(text,name):
 # The preset's authored nickname is an identity field. Update that exact
 # field for the new named pair; leave the persona prose and legacy names intact.
 if name:
  text=text.replace('【作者补充身份资料】\n昵称与自称：大肥鱼。','【作者补充身份资料】\n昵称与自称：'+name+'。')
 return text+'\n\n【当前实例身份】\n本实例的名字是'+name+'，这是当前说话和行动的角色身份。'

def multi_definitions(base,*,names):
 if not isinstance(names,dict) or len(names)<2 or any(not isinstance(k,str) or not k or not isinstance(n,str) or not n.strip() for k,n in names.items()):raise ValueError('multi display names invalid')
 if len(set(names.values()))!=len(names):raise ValueError('multi display names must be distinct')
 return {label:replace(base,session_id=base.session_id+':'+label,owner_ref='npc:'+label,
    scope_ref='petlab:private:'+label,name=name,
    background=_instance_text(base.background,name)) for label,name in names.items()}


def dual_definitions(base,*,names=None):
 names=DEFAULT_NAMES if names is None else names
 if set(names)!=set(DEFAULT_NAMES):raise ValueError('dual display names invalid')
 return multi_definitions(base,names=names)


def multi_configurations(configuration,*,workspace_root=None,count=None):
 """One composition path for the existing pair and an explicit larger cast."""
 from model_packages.desktop_pet_v1.spatial import MAX_PARTICIPANTS
 if count is not None and (type(count)is not int or not 2<=count<=MAX_PARTICIPANTS):raise ValueError('pet count exceeds supported scene bounds')
 saved=None;path=None if workspace_root is None else Path(workspace_root)/'workspace.json'
 if path is not None and path.exists():
  saved=_read(path)
  if set(saved)!={'version','definitions'} or saved['version']!=VERSION or len(saved['definitions'])<2:raise ValueError('multi workspace definition differs')
  names={k:v['name'] for k,v in saved['definitions'].items()}
  imported=path.parent/'imported-pair.json'
  if imported.is_file():
   from .paired_history_import import VERSION as IMPORT_VERSION
   from .composition import PetDefinition
   from .setup_dialog import read_configuration_record
   from .desktop_app import configuration_from_record
   records={k:PetDefinition.from_record(v) for k,v in saved['definitions'].items()}
   if (_read(imported)!={'version':IMPORT_VERSION,'definitions':{k:d.fingerprint for k,d in records.items()}}
       or (count is not None and count!=len(records))):
    raise ValueError('imported pair workspace identity differs')
   result={}
   for label,d in records.items():
    config_path=path.parent/'actors'/_key(d.owner_ref)[:16]/'config.json'
    record=read_configuration_record(config_path)
    if fp(record['definition'])!=d.fingerprint:raise ValueError('imported participant configuration differs')
    local=configuration_from_record(record,require_api_key=False)
    from .import_expression_refresh import load_profile_refresh
    profile=load_profile_refresh(path.parent,label,d,local.expression_profile)
    result[label]=replace(configuration,definition=d,expression_profile=profile,
                          memory_recall_version=local.memory_recall_version)
   return result
  if count is not None and count!=len(names):raise ValueError('saved workspace pet count differs; choose a new workspace')
 else:
  size=2 if count is None else count
  labels=['大肥鱼','小肥鱼','三肥鱼','四肥鱼']
  names={'DS'+str(i+1):labels[i] if i<len(labels) else '肥鱼'+str(i+1) for i in range(size)}
 definitions=multi_definitions(configuration.definition,names=names)
 if saved is not None and fp(saved['definitions'])!=fp({k:v.to_record() for k,v in definitions.items()}):
  raise ValueError('saved multi definition differs from configuration; keep the original configuration')
 result={}
 for label,definition in definitions.items():
  profile=configuration.expression_profile
  if profile is not None:
   profile=thaw_model_data(profile)
   profile={**profile,'description':_instance_text(profile['description'],definition.name),
      'source_ref':profile['source_ref']+':instance:'+label}
  result[label]=replace(configuration,definition=definition,expression_profile=profile)
 return result


def dual_configurations(configuration,*,workspace_root=None):
 return multi_configurations(configuration,workspace_root=workspace_root,count=2)


def _read(path):return json.loads(path.read_text(encoding='utf8'))
def _key(identity):return hashlib.sha256(identity.encode('utf8')).hexdigest()
def _signed(row):return {**row,'fingerprint':fp(row)}
def _verify(row):
 if row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'}):raise ValueError('multi source integrity differs')
 return row

def _immutable(path,row):
 if path.exists():
  if _read(path)!=row:raise ValueError('multi immutable identity conflict')
 else:
  path.parent.mkdir(parents=True,exist_ok=True);atomic_write_json(str(path),row,durable=True)

@contextmanager
def routing_lock(root):
 """Only coordinates immutable source publication; never controls Actor time."""
 path=Path(root)/'.routing.lock'
 with path.open('a+b') as handle:
  if handle.tell()==0:handle.write(b'0');handle.flush()
  handle.seek(0)
  if os.name=='nt':
   import msvcrt
   msvcrt.locking(handle.fileno(),msvcrt.LK_LOCK,1)
  else:
   import fcntl
   fcntl.flock(handle.fileno(),fcntl.LOCK_EX)
  try:yield
  finally:
   handle.seek(0)
   if os.name=='nt':msvcrt.locking(handle.fileno(),msvcrt.LK_UNLCK,1)
   else:fcntl.flock(handle.fileno(),fcntl.LOCK_UN)

class MultiPetWorkspace:
 def __init__(self,root,definitions):
  self.root=Path(root).resolve();self.definitions=dict(definitions);self.lock=threading.RLock()
  if len(self.definitions)<2:raise ValueError('multi workspace needs independent pets')
  if any(not isinstance(k,str) or not k for k in self.definitions):raise ValueError('pet alias invalid')
  owners=[d.owner_ref for d in self.definitions.values()];users={d.user_ref for d in self.definitions.values()}
  if len(set(owners))!=len(owners) or len(users)!=1:raise ValueError('multi identities differ')
  self.human=next(iter(users));self.aliases={d.owner_ref:k for k,d in self.definitions.items()}
  self.root.mkdir(parents=True,exist_ok=True)
  manifest={'version':VERSION,'definitions':{k:d.to_record() for k,d in self.definitions.items()}}
  _immutable(self.root/'workspace.json',manifest)
  self.registry=ProductConversationSessionRegistry(base_dir=str(self.root/'conversations'),durable_writes=True)
  participants={self.human:make_human_participant(self.human)}
  for d in self.definitions.values():
   participants[d.owner_ref]=make_npc_profile(participant_id=d.owner_ref,profile_id=d.fingerprint,c_base_ref=d.base_constants_version).participant
  self.participants=participants
  descriptors={'public':ConversationDescriptor(conversation_id='petlab:public',kind='group',active_member_ids=tuple(participants),membership_version=1)}
  for name,d in self.definitions.items():
   descriptors['private:'+name]=ConversationDescriptor(conversation_id=d.scope_ref,kind='direct',active_member_ids=(self.human,d.owner_ref),membership_version=1)
  descriptors['peers']=ConversationDescriptor(conversation_id='petlab:peers',kind='group',active_member_ids=tuple(owners),membership_version=1)
  from itertools import combinations
  for a,b in combinations(sorted(owners),2):
   key=self.whisper_key(a,b)
   descriptors[key]=ConversationDescriptor(conversation_id='petlab:'+key,kind='direct',active_member_ids=(a,b),membership_version=1)
  self.conversations=descriptors
  for descriptor in descriptors.values():
   self.registry.register_session(ProductConversationSession(conversation=descriptor,participants=tuple(participants[x] for x in descriptor.active_member_ids)))
  for path in sorted((self.root/'audience-scopes').glob('*.json')):
   descriptor=ConversationDescriptor.from_record(_read(path))
   self.audience_scope(descriptor.active_member_ids,expected=descriptor,
                       local=descriptor.conversation_id.startswith('petlab:audience-local:'))
 def audience_scope(self,members,*,expected=None,local=False):
  """Register a deterministic frozen recipient scope, not a new social group."""
  members=tuple(sorted(members))
  if len(members)<2 or len(set(members))!=len(members) or not set(members)<=set(self.participants):
   raise ValueError('invalid audience scope members')
  prefix='audience-local:' if local else 'audience:'
  key=prefix+_key('\0'.join(members))
  descriptor=ConversationDescriptor(conversation_id='petlab:'+key,kind='group' if local or len(members)>2 else 'direct',active_member_ids=members,membership_version=1)
  if expected is not None and expected!=descriptor:raise ValueError('audience scope identity differs')
  with self.lock:
   if key not in self.conversations:
    name=key.replace(':','-') if local else key.split(':',1)[1]
    _immutable(self.root/'audience-scopes'/(name+'.json'),descriptor.as_record())
    self.registry.register_session(ProductConversationSession(conversation=descriptor,participants=tuple(self.participants[x] for x in members)))
    self.conversations[key]=descriptor
  return descriptor
 def registered_destination(self,record):
  record=thaw_model_data(record)
  identity=record['conversation_id']
  for key,descriptor in self.conversations.items():
   if descriptor.conversation_id==identity:
    if descriptor.as_record()!=record:raise ValueError('executed destination differs from registration')
    return key,descriptor
  prefixes=('petlab:audience:','petlab:audience-local:')
  if not identity.startswith(prefixes):raise ValueError('executed destination is not registered')
  saved=self.registry.load_session(identity)
  if saved is None or saved.conversation.as_record()!=record:
   raise ValueError('executed audience scope differs from durable registration')
  descriptor=self.audience_scope(saved.conversation.active_member_ids,expected=saved.conversation,
                                 local=identity.startswith(prefixes[1]))
  return identity.removeprefix('petlab:'),descriptor
 @staticmethod
 def whisper_key(a,b):return 'whisper:'+':'.join(sorted((a,b)))
 def set_speech_mode(self,owner,peer,mode):
  if owner not in self.aliases or peer not in self.aliases or owner==peer or mode not in {'public','private'}:raise ValueError('invalid peer speech mode')
  path=self.actor_path(self.aliases[owner])/'speech-modes.json'
  with self.lock,routing_lock(self.root):
   data=_read(path) if path.exists() else {}
   data[peer]=mode;path.parent.mkdir(parents=True,exist_ok=True)
   atomic_write_json(str(path),data,durable=True)
 def speech_mode(self,owner,peer):
  path=self.actor_path(self.aliases[owner])/'speech-modes.json'
  return _read(path).get(peer) if path.exists() else None
 def actor_path(self,alias):
  if alias not in self.definitions:raise ValueError('unknown pet owner')
  return self.root/'actors'/_key(self.definitions[alias].owner_ref)[:16]
 def inbox_path(self,alias,delivery_id):return self.actor_path(alias)/'inbox'/(_key(delivery_id)[:24]+'.json')
 def _descriptor(self,alias):
  if alias not in self.conversations:raise ValueError('unknown conversation')
  return self.conversations[alias]
 def _append(self,event,origin,visible_manifest=None,human_content=None):
  row=_signed({'version':VERSION,'event':event.as_record(),'origin':origin,
   **({'human_content':human_content} if human_content is not None else {}),
   **({'visible_manifest':visible_manifest} if visible_manifest is not None else {})})
  if human_content is not None:
   from .human_interaction import content
   content(row)
  with self.lock,routing_lock(self.root):
   _immutable(self.root/'sources'/(_key(event.event_id)+'.json'),row)
   self._route(row)
  return event
 def submit_human(self,*,event_id,conversation,text,at,addressed,input_content=None,open_invitation=False):
  if not isinstance(text,str) or not text.strip():raise ValueError('submitted message missing')
  event=commit_message_event(self._descriptor(conversation),event_id=event_id,sender_participant_id=self.human,text=text,committed_at_ms=at,addressed_participant_ids=addressed)
  origin={'kind':'human','participant_id':self.human}
  if open_invitation:
   if (conversation!='public' or input_content is not None or
       set(event.addressed_participant_ids)!=set(event.visible_recipient_ids)):
    raise ValueError('open invitation requires an untyped public message to the room')
   origin['opening_policy']=OPEN_INVITATION_POLICY
  return self._append(event,origin,human_content=None if input_content is None else input_content.to_record())
 def publish_output(self,app,*,event_id,conversation,addressed):
  if app is None or app.owner_ref not in self.aliases:raise ValueError('NPC output needs exact registered owner')
  definition=self.definitions[self.aliases[app.owner_ref]]
  if app.definition.fingerprint!=definition.fingerprint:raise ValueError('NPC profile differs from registered owner')
  # Read the committed execution fact, not the later measured memory instance.
  from model_packages.desktop_pet_v1.events import PetEvent
  source=app._product()["sources"].get(event_id)
  if source is None or source.get("origin")!="execution":raise ValueError("NPC source lacks committed execution")
  event=PetEvent.from_record(source["event"]);app._verify_source(event)
  if event.event_kind!='npc_output' or event.actor_ref!=app.owner_ref:raise ValueError('NPC source is not own committed output')
  descriptor=self._descriptor(conversation)
  audience=set(descriptor.active_member_ids)-{app.owner_ref}
  source=app._product()['sources'][event_id]
  frozen=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')
  if frozen is not None and (frozen['conversation']['conversation_id']!=descriptor.conversation_id or set(frozen['addressed'])!=set(addressed)):raise ValueError('publication destination differs from executed request')
  from .destinations import is_npc_whisper
  physical=is_npc_whisper(frozen)
  realization=source['evidence']['fact']['model_payload']['execution_receipt']['realization_evidence']
  envelope=realization.get('audience_envelope')
  if envelope is not None:
   from model_packages.desktop_pet_v1.audience import validate as validate_audience,select as select_audience
   envelope=validate_audience(envelope)
   from .writer_visibility import speech_audience
   if envelope['owner']!=app.owner_ref or envelope['allowed_speech']!=speech_audience(frozen,realization.get('memory_visibility')) or set(envelope['physical_observers'])!=set(self.participants)-{app.owner_ref}:
    raise ValueError('output physical observer scope differs')
   select_audience({'speech':realization['selected_speech_audience']},envelope)
   physical=True
  if physical:
   descriptor=self.conversations['public'];audience=set(descriptor.active_member_ids)-{app.owner_ref}
  origin={'kind':'npc','participant_id':app.owner_ref,'action_id':event_id,'event_fingerprint':event.fingerprint,
    'commit_fingerprint':source['evidence']['commit_receipt']['fingerprint'],
    **({'private_speech_target':frozen['primary']} if physical else {})}
  if envelope is not None:
   origin.pop('private_speech_target',None)
   origin['selected_speech_audience']=list(realization['selected_speech_audience'])
  canonical=canonical_npc_output_event_id(conversation_id=descriptor.conversation_id,sender_participant_id=app.owner_ref,action_id=event_id)
  actual_targets=tuple(event.target_refs) or tuple(addressed)
  if 'speech_to' in realization:
   from product_adapters.desktop_pet_v1.audience_delivery import visible_targets
   actual_targets=tuple(visible_targets(actual_targets,realization['speech_to'],
       [{'kind':c.kind,'recipient_refs':c.recipient_refs} for c in event.contents if not c.private]))
  # A previously published source is immutable, including legacy speech-only
  # projections of mixed actions. Recovery never reinterprets old history.
  old_path=self.root/'sources'/(_key(canonical)+'.json')
  if old_path.exists():
   old=_verify(_read(old_path));old_event=CommittedConversationEvent.from_record(old['event'])
   if (old['origin']!=origin
       or set(old_event.addressed_participant_ids) not in (set(addressed),set(actual_targets))):
    raise ValueError('published execution binding differs')
   self._route(old);return old_event
  from .visible_delivery import outward,create,ENVELOPE_TEXT
  contents=outward(event,audience)
  if not contents:raise ValueError('NPC execution has no visible audience')
  manifest,bodies=create(event,audience,public_physical=physical)
  if 'speech_to' in realization:
   manifest['target_refs']=list(actual_targets)
  for digest,body in bodies.items():_immutable(self.root/'visible-contents'/(digest+'.json'),body)
  committed=commit_message_event(descriptor,event_id=canonical,sender_participant_id=app.owner_ref,text=ENVELOPE_TEXT,committed_at_ms=event.position.coordinate,sender_action_id=event_id,addressed_participant_ids=actual_targets)
  return self._append(committed,origin,manifest)
 def _route(self,row):
  _verify(row);event=CommittedConversationEvent.from_record(row['event'])
  origin=row['origin'];sender=self.participants.get(event.sender_participant_id)
  if sender is None or origin.get('participant_id')!=event.sender_participant_id or origin.get('kind')!=sender.kind:raise ValueError('source sender identity differs')
  if sender.kind=='npc' and (not event.sender_action_id or origin.get('action_id')!=event.sender_action_id):raise ValueError('NPC source lacks committed action')
  requires_reply=(sender.kind=='human' and origin.get('opening_policy')!=OPEN_INVITATION_POLICY
    and row.get('human_content',{}).get('semantic_key') not in {'drag_contact','scene_departure','witnessed_pointer'})
  policy='required' if requires_reply else 'eligible'
  for delivery in route_committed_event(event):
   alias=self.aliases.get(delivery.recipient_participant_id)
   if alias is None:continue # human has no model store/Actor
   from .visible_delivery import project as visible_project
   visible=visible_project(self.root,row,delivery.recipient_participant_id)
   if 'visible_manifest' in row and visible is None:continue
   projection=project_delivery(event,delivery,recipient_participant_id=self.definitions[alias].owner_ref)
   receipt=_signed({'version':VERSION,'source_fingerprint':row['fingerprint'],
      'delivery':delivery.as_record(),'projection':projection.as_record(),
      'response_policy':policy if delivery.delivery_scope=='addressed' or visible is not None else None,
      'sender_kind':sender.kind,'preference_evidence':sender.kind=='human',
      **({'visible':visible} if visible is not None else {})})
   _immutable(self.inbox_path(alias,delivery.delivery_id),receipt)
 def source_event(self,event_id):
  row=_verify(_read(self.root/'sources'/(_key(event_id)+'.json')))
  if row['event']['event_id']!=event_id:raise ValueError('source identity differs from archive path')
  return row
 def source_events(self):
  return tuple(sorted((_verify(_read(p)) for p in (self.root/'sources').glob('*.json')),key=lambda r:(r['event']['committed_at_ms'],r['event']['event_id'])))
 def recover_routes(self):
  with self.lock,routing_lock(self.root):
   for row in self.source_events():self._route(row)
 def read_delivery(self,alias,path,*,include_source=False):
  self.actor_path(alias)
  row=_verify(_read(path));projection=row['projection']
  if projection['recipient_participant_id']!=self.definitions[alias].owner_ref:raise ValueError('inbox owner differs')
  source=self.source_event(projection['source_event_id']);event=CommittedConversationEvent.from_record(source['event'])
  expected=next((x for x in route_committed_event(event) if x.recipient_participant_id==projection['recipient_participant_id']),None)
  if expected is None or row['source_fingerprint']!=source['fingerprint'] or row['delivery']!=expected.as_record() or projection!=project_delivery(event,expected).as_record():raise ValueError('inbox projection differs from committed source')
  from .visible_delivery import project as visible_project
  visible=visible_project(self.root,source,projection['recipient_participant_id'])
  if ('visible_manifest' in source and visible is None) or row.get('visible')!=visible:raise ValueError('inbox visible contents differ')
  if path!=self.inbox_path(alias,expected.delivery_id):raise ValueError('inbox path differs from delivery')
  return (row,source) if include_source else row
 def inbox(self,alias):
  rows=[self.read_delivery(alias,path) for path in (self.actor_path(alias)/'inbox').glob('*.json')]
  from .visible_delivery import delivery_order
  return tuple(sorted(rows,key=delivery_order))
