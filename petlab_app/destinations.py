"""Freeze recipient identity from committed Conversation provenance, not text."""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.relation_package import META
from model_packages.cx_six_seven_v1.participants import Participant
from model_packages.cx_six_seven_v1.multi_participant.conversations import ConversationDescriptor
VERSION='petlab-participant-destination-v1'
_INPUT_SNAPSHOT=ContextVar('petlab_destination_input_snapshot',default=None)


@contextmanager
def input_snapshot():
 """One synchronous read-only assembly; never retain trust across turns."""
 token=_INPUT_SNAPSHOT.set({})
 try:yield
 finally:_INPUT_SNAPSHOT.reset(token)


def with_input_snapshot(method):
 @wraps(method)
 def scoped(*args,**kwargs):
  with input_snapshot():return method(*args,**kwargs)
 return scoped

def is_npc_whisper(row):
 return bool(row and row['conversation']['kind']=='direct' and
             all(p['kind']=='npc' for p in row['participants']))

def validate(app,row):
 row=thaw_model_data(row)
 if row.get('version')!=VERSION or row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'}):raise ValueError('destination integrity differs')
 if row['owner_ref']!=app.owner_ref or row['session_binding']!=app.runtime.session.session_binding.fingerprint:raise ValueError('destination owner differs')
 conversation=ConversationDescriptor.from_record(row['conversation'])
 people={p.participant_id:p for p in (Participant.from_record(v) for v in row['participants'])}
 audience=tuple(x for x in conversation.active_member_ids if x!=app.owner_ref)
 if (app.owner_ref not in people or set(people)!=set(conversation.active_member_ids)
  or row['audience']!=list(audience) or not row['addressed'] or not set(row['addressed']).issubset(audience)
  or row['primary'] not in row['addressed']):raise ValueError('destination audience differs')
 return row

def _input(app,event_id):
 cache=_INPUT_SNAPSHOT.get()
 if cache is None:return _read_input(app,event_id)
 key=(id(app),event_id);session=app.runtime.session
 cached=cache.get(key)
 if cached is None or cached[0] is not session:
  cache[key]=(session,_read_input(app,event_id))
 return deepcopy(cache[key][1])


def _read_input(app,event_id):
 from .session import PRODUCT_KEY
 source=app.runtime.session.profile[PRODUCT_KEY]['sources'][event_id];event=app.runtime.memory_instance(event_id).event;app._verify_source(event)
 from .participant_input import VERSIONS,validate as validate_input
 receipt=source['evidence'].get('input_source',{})
 from .obstruction_input import VERSION as OBSTRUCTION
 from .scene_encounter import DESKTOP_VERSION
 from .screen_perception import VERSIONS as SCENE_FACTS
 if receipt.get('version') in {DESKTOP_VERSION,*SCENE_FACTS} and hasattr(app,'multi_workspace'):
  w=app.multi_workspace
  if receipt['version'] in SCENE_FACTS:
   readers=sorted((set(receipt['material']['viewers']) & set(w.participants))-{app.owner_ref})
   if not readers:readers=[app.user_ref]
  else:readers=[receipt['actor_ref']]
  conversation=w.audience_scope({app.owner_ref,*readers},local=True)
  audience=[ref for ref in conversation.active_member_ids if ref!=app.owner_ref]
  row={'version':VERSION,'owner_ref':app.owner_ref,'session_binding':app.runtime.session.session_binding.fingerprint,
       'source_event_id':event_id,'source_binding_ref':event.source_binding_ref,
       'conversation':conversation.as_record(),'participants':[w.participants[x].as_record() for x in conversation.active_member_ids],
       'primary':audience[0],'audience':audience,'addressed':audience}
  return validate(app,{**row,'fingerprint':fp(row)})
 if receipt.get('version')==OBSTRUCTION and hasattr(app,'multi_workspace'):
  from .idle_targets import make_destination
  row=make_destination(app,receipt['actor_ref'])
  row={k:v for k,v in row.items() if k!='fingerprint'}
  row.update(source_event_id=event_id,source_binding_ref=event.source_binding_ref)
  return validate(app,{**row,'fingerprint':fp(row)})
 if receipt.get('version') not in VERSIONS:return None
 _,artifact=validate_input(app,receipt,event.contents,event_id=event_id,return_artifact=True)
 if 'participants' not in artifact:raise ValueError('multi destination lacks frozen participant snapshot')
 origin=artifact['source']['event'];conversation=artifact['conversation'];people=artifact['participants']
 # A public physical envelope can carry a speech child visible to this recipient
 # alone. Its own exact recipient scope, not its wording, preserves the reply boundary.
 visible=artifact.get('visible',{})
 speech=[c for c in visible.get('contents',()) if c['kind']=='speech']
 selected=artifact['source']['origin'].get('selected_speech_audience')
 if (selected and app.owner_ref in selected and speech
     and all(set(c['recipient_refs'])==set(selected) for c in speech)
     and hasattr(app,'multi_workspace')):
  w=app.multi_workspace
  if not set(selected)<=set(conversation['active_member_ids']):raise ValueError('selected speech scope exceeds source')
  members=set(selected)|{origin['sender_participant_id']}
  if members!=set(conversation['active_member_ids']):
   conversation=w.audience_scope(members).as_record()
   people=[p for p in people if p['participant_id'] in members]
 elif (artifact['source']['origin'].get('private_speech_target')==app.owner_ref
     and speech and all(c['recipient_refs']==[app.owner_ref] for c in speech)
     and hasattr(app,'multi_workspace') and origin['sender_participant_id'] in app.multi_workspace.aliases):
  w=app.multi_workspace
  conversation=w.conversations[w.whisper_key(app.owner_ref,origin['sender_participant_id'])].as_record()
  people=[p for p in people if p['participant_id'] in conversation['active_member_ids']]
 members=conversation['active_member_ids']
 audience=[x for x in members if x!=app.owner_ref]
 addressed=[x for x in members if x!=app.owner_ref and (x==origin['sender_participant_id'] or x in origin['addressed_participant_ids'])]
 row={'version':VERSION,'owner_ref':app.owner_ref,'session_binding':app.runtime.session.session_binding.fingerprint,
  'source_event_id':event_id,'source_binding_ref':event.source_binding_ref,
  'conversation':conversation,'participants':people,
  'primary':origin['sender_participant_id'],'audience':audience,'addressed':addressed}
 return validate(app,{**row,'fingerprint':fp(row)})

def autonomous_destination(row):
 """Use the original product destination policy after the Actor's due grant."""
 if row is None:return None
 from copy import deepcopy
 origin=deepcopy(row)
 row={k:v for k,v in origin.items() if k!='fingerprint'}
 row['addressed']=list(row['audience'])
 row['addressing_policy']='original-autonomous-destination-v1'
 row['origin_destination_fingerprint']=origin['fingerprint']
 return {**row,'fingerprint':fp(row)}

def freeze_peer_destination(app,event_id):
 """Bind an explicit host speech mode once, without rewriting the input scope."""
 origin=_input(app,event_id)
 if origin is None or not hasattr(app,'multi_workspace'):return None
 peer=origin['primary'];w=app.multi_workspace
 if peer not in w.aliases:return None
 mode=w.speech_mode(app.owner_ref,peer)
 if mode is None:return None
 from .idle_targets import make_destination
 target=make_destination(app,peer,visibility=mode)
 target={k:v for k,v in target.items() if k!='fingerprint'}
 target.update(source_event_id=event_id,source_binding_ref=origin['source_binding_ref'],addressing_policy='petlab-peer-reply-destination-v1')
 target=validate(app,{**target,'fingerprint':fp(target)})
 body={'version':'petlab-peer-reply-destination-v1','source_destination_fingerprint':origin['fingerprint'],
       'source_fact_fingerprint':app.runtime.memory_instance(event_id).fact.fingerprint,'destination':target}
 return {**body,'fingerprint':fp(body)}


def destination(app,event_id=None):
 if META not in app.runtime.session.state.state:return None
 if event_id is not None:
  tick=app.runtime.session.profile.get('desktop_pet_idle_opening_v1',{}).get('ticks',{}).get(event_id)
  if tick is not None:
   row=tick.get('participant_destination')
   if row is None:return None
   row=validate(app,row)
   from .idle_targets import SUPPORTED_VERSIONS
   if row.get('addressing_policy') in SUPPORTED_VERSIONS:
    selection=tick['plan']['target_selection']
    if selection.get('fingerprint')!=fp({k:v for k,v in selection.items() if k!='fingerprint'}) or fp(row)!=fp(selection['destination']) or row['primary']!=selection['peer']:raise ValueError('idle relationship destination differs')
    return row
   origin=_input(app,row['source_event_id'])
   expected=autonomous_destination(origin) if row.get('addressing_policy')=='original-autonomous-destination-v1' else origin
   if expected!=row:raise ValueError('idle destination source differs')
   return row
  origin=_input(app,event_id)
  frozen=app.runtime.session.profile.get('desktop-pet-peer-reaction-v1',{}).get('reply_destinations',{}).get(event_id)
  if frozen is None:return origin
  frozen=thaw_model_data(frozen)
  if (frozen.get('version')!='petlab-peer-reply-destination-v1'
      or frozen.get('fingerprint')!=fp({k:v for k,v in frozen.items() if k!='fingerprint'})
      or origin is None or frozen['source_destination_fingerprint']!=origin['fingerprint']
      or frozen['source_fact_fingerprint']!=app.runtime.memory_instance(event_id).fact.fingerprint):
   raise ValueError('peer output destination source differs')
  target=validate(app,frozen['destination'])
  if target['primary']!=origin['primary']:raise ValueError('peer output destination changed the selected person')
  return target
 peer=app.runtime.session.state.state[META]['active_peer']
 for event in reversed(app.runtime.events):
  if event.event_kind=='peer_input' and event.actor_ref==peer:
   # A newer private/local ingress is a boundary, not a reason to reuse an old group.
   return _input(app,event.event_id)
 return None

def primary(app,event_id=None):
 row=destination(app,event_id)
 return app.user_ref if row is None else row['primary']
def audience(app,event_id):
 row=destination(app,event_id)
 return (app.user_ref,) if row is None else tuple(row['audience'])

