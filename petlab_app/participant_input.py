"""Source-bound human Conversation delivery into the existing pet ingress.

NPC relation-state adoption and observed-only memory are deliberately separate:
this first adapter must not silently settle an NPC input into the human relation.
"""
import json,hashlib,time
from pathlib import Path
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.cx_six_seven_v1.multi_participant.events import CommittedConversationEvent
from model_packages.cx_six_seven_v1.multi_participant.router import route_committed_event
from model_packages.cx_six_seven_v1.multi_participant.local_projection import project_delivery
from model_packages.desktop_pet_v1.events import Speech
from .multi_pet import _immutable,_key,_verify
VERSION='pet-participant-delivery-input-v1'
from .human_interaction import VERSION as HUMAN_INTERACTION_VERSION,content as human_content
NPC_VERSION='pet-participant-delivery-input-v2'
VISIBLE_VERSION='pet-participant-delivery-input-v3-visible'
NPC_VERSIONS={NPC_VERSION,VISIBLE_VERSION}
VERSIONS={VERSION,HUMAN_INTERACTION_VERSION,*NPC_VERSIONS}

def prepare(app,row,workspace,*,coordinate=None):
 alias=workspace.aliases.get(app.owner_ref)
 if alias is None or app.definition.fingerprint!=workspace.definitions[alias].fingerprint:raise ValueError('recipient owner differs from workspace')
 try:known=workspace.read_delivery(alias,workspace.inbox_path(alias,row['delivery']['delivery_id']))
 except FileNotFoundError:raise ValueError('delivery not in recipient inbox') from None
 if known!=row:raise ValueError('delivery not in recipient inbox')
 from model_packages.desktop_pet_v1.relation_package import META
 if row['sender_kind']=='npc' and META not in app.runtime.session.state.state:raise ValueError('NPC input awaits directed-relation adapter')
 if row['sender_kind'] not in {'human','npc'}:raise ValueError('unsupported participant kind')
 if row['delivery']['delivery_scope']!='addressed' and 'visible' not in row:raise ValueError('observed message is not a relation input')
 source=workspace.source_event(row['delivery']['source_event_id'])
 event_id='participant:'+_key(row['delivery']['delivery_id'])
 saved=app._product()['sources'].get(event_id)
 at=saved['event']['position']['coordinate'] if saved is not None else time.time_ns()//1000000 if coordinate is None else coordinate
 if type(at)is not int or at<source['event']['committed_at_ms']:raise ValueError('delivery precedes source')
 relative='participants/'+_key(row['delivery']['delivery_id'])[:24]+'.json'
 artifact={'source':source,'delivery':row['delivery'],
  'conversation':workspace.registry.load_session(source['event']['conversation_id']).conversation.as_record(),
  'participants':[workspace.participants[x].as_record() for x in source['event']['member_snapshot_ids']],
  **({'visible':row['visible']} if 'visible' in row else {})}
 path=app.store.path.with_suffix('.resources')/relative
 _immutable(path,artifact);digest=hashlib.sha256(path.read_bytes()).hexdigest()
 receipt={'version':VISIBLE_VERSION if 'visible' in row else NPC_VERSION if row['sender_kind']=='npc' else HUMAN_INTERACTION_VERSION if 'human_content' in source else VERSION,'event_id':event_id,'actor_ref':row['projection']['sender_participant_id'],'reader_ref':app.owner_ref,
  'session_binding_fingerprint':app.runtime.session.session_binding.fingerprint,
  'artifact':relative,'artifact_sha256':digest,'received_at_ms':at}
 receipt['fingerprint']=fp(receipt)
 if 'visible' in row:
  from .visible_delivery import validate_projection
  contents=validate_projection(source,app.owner_ref,row['visible'])
 elif 'human_content' in source:contents=(human_content(source),)
 else:contents=(Speech(content_id='speech',description=row['projection']['text'],recipient_refs=(app.owner_ref,)),)
 validate(app,receipt,contents,event_id=event_id)
 return {'event_id':event_id,'contents':contents,'coordinate':at,'response_policy':row['response_policy'],'input_source':receipt}

def validate(app,receipt,contents,*,event_id=None):
 row=thaw_model_data(receipt)
 if set(row)!={'version','event_id','actor_ref','reader_ref','session_binding_fingerprint','artifact','artifact_sha256','received_at_ms','fingerprint'}:raise ValueError('participant input fields differ')
 if (row['version'] not in VERSIONS or row['fingerprint']!=fp({k:v for k,v in row.items() if k!='fingerprint'})
  or row['reader_ref']!=app.owner_ref or row['actor_ref']==app.owner_ref
  or row['session_binding_fingerprint']!=app.runtime.session.session_binding.fingerprint
  or event_id is not None and row['event_id']!=event_id):raise ValueError('participant input binding differs')
 root=app.store.path.with_suffix('.resources').resolve();path=(root/row['artifact']).resolve()
 if not path.is_relative_to(root/'participants') or path.suffix!='.json':raise ValueError('participant artifact escapes archive')
 raw=path.read_bytes()
 if len(raw)>1024*1024 or hashlib.sha256(raw).hexdigest()!=row['artifact_sha256']:raise ValueError('participant artifact differs')
 artifact=json.loads(raw);source=_verify(artifact['source']);event=CommittedConversationEvent.from_record(source['event'])
 if 'participants' in artifact:
  from model_packages.cx_six_seven_v1.participants import Participant,make_human_participant,make_npc_profile
  from model_packages.cx_six_seven_v1.multi_participant.conversations import ConversationDescriptor
  conversation=ConversationDescriptor.from_record(artifact['conversation'])
  people={x.participant_id:x for x in (Participant.from_record(v) for v in artifact['participants'])}
  from .composition import PetDefinition,DEFINITION_KEY
  definition=PetDefinition.from_record(app.runtime.session.profile[DEFINITION_KEY])
  own=make_npc_profile(participant_id=app.owner_ref,profile_id=definition.fingerprint,c_base_ref=definition.base_constants_version).participant
  if (set(people)!=set(event.member_snapshot_ids) or people.get(app.owner_ref)!=own
   or conversation.conversation_id!=event.conversation_id or conversation.membership_version!=event.membership_version
   or conversation.active_member_ids!=event.member_snapshot_ids):raise ValueError('participant snapshot differs')
  if app.user_ref in people and people[app.user_ref]!=make_human_participant(app.user_ref):raise ValueError('human participant identity differs')
 if event.sender_participant_id!=row['actor_ref']:raise ValueError('participant sender differs')
 if row['version'] in {VERSION,HUMAN_INTERACTION_VERSION}:
  if source['origin']!={'kind':'human','participant_id':app.user_ref} or event.sender_participant_id!=app.user_ref or event.sender_action_id is not None:raise ValueError('participant human origin differs')
 else:
  from model_packages.desktop_pet_v1.relation_package import META
  from model_packages.cx_six_seven_v1.multi_participant.events import canonical_npc_output_event_id
  origin=source['origin']
  if (META not in app.runtime.session.state.state or origin.get('kind')!='npc'
      or origin.get('participant_id')!=row['actor_ref'] or row['actor_ref']==app.user_ref
      or not origin.get('commit_fingerprint') or not origin.get('event_fingerprint')
      or not event.sender_action_id or origin.get('action_id')!=event.sender_action_id
      or event.event_id!=canonical_npc_output_event_id(conversation_id=event.conversation_id,sender_participant_id=event.sender_participant_id,action_id=event.sender_action_id)):
   raise ValueError('participant NPC origin differs')
 deliveries=[x for x in route_committed_event(event) if x.recipient_participant_id==app.owner_ref]
 if len(deliveries)!=1 or artifact['delivery']!=deliveries[0].as_record() or deliveries[0].delivery_scope!='addressed' and row['version']!=VISIBLE_VERSION:raise ValueError('participant delivery is not addressed to owner')
 projection=project_delivery(event,deliveries[0],recipient_participant_id=app.owner_ref)
 if (row['event_id']!='participant:'+_key(deliveries[0].delivery_id) or type(row['received_at_ms'])is not int
  or row['received_at_ms']<event.committed_at_ms):raise ValueError('participant receipt identity or time differs')
 if row['version']==VISIBLE_VERSION:
  from .visible_delivery import validate_projection
  expected=validate_projection(source,app.owner_ref,artifact['visible'])
 elif row['version']==HUMAN_INTERACTION_VERSION:
  if 'visible_manifest' in source or 'visible' in artifact:raise ValueError('human interaction cannot inherit NPC visibility')
  expected=(human_content(source),)
 else:
  if 'visible_manifest' in source or 'visible' in artifact or 'human_content' in source:raise ValueError('typed source requires its versioned input')
  expected=(Speech(content_id='speech',description=projection.text,recipient_refs=(app.owner_ref,)),)
 if tuple(contents)!=expected:raise ValueError('participant input content differs from source')
 return row
