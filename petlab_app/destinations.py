"""Freeze recipient identity from committed Conversation provenance, not text."""
import json
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.relation_package import META
from model_packages.cx_six_seven_v1.participants import Participant
from model_packages.cx_six_seven_v1.multi_participant.conversations import ConversationDescriptor
VERSION='petlab-participant-destination-v1'

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
 source=app._product()['sources'][event_id];event=app.runtime.memory_instance(event_id).event;app._verify_source(event)
 from .participant_input import VERSIONS,validate as validate_input
 receipt=source['evidence'].get('input_source',{})
 if receipt.get('version') not in VERSIONS:return None
 validate_input(app,receipt,event.contents,event_id=event_id)
 artifact=json.loads((app.store.path.with_suffix('.resources')/receipt['artifact']).read_text(encoding='utf8'))
 if 'participants' not in artifact:raise ValueError('multi destination lacks frozen participant snapshot')
 origin=artifact['source']['event'];conversation=artifact['conversation'];members=conversation['active_member_ids']
 audience=[x for x in members if x!=app.owner_ref]
 addressed=[x for x in members if x!=app.owner_ref and (x==origin['sender_participant_id'] or x in origin['addressed_participant_ids'])]
 row={'version':VERSION,'owner_ref':app.owner_ref,'session_binding':app.runtime.session.session_binding.fingerprint,
  'source_event_id':event_id,'source_binding_ref':event.source_binding_ref,
  'conversation':conversation,'participants':artifact['participants'],
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


def destination(app,event_id=None):
 if META not in app.runtime.session.state.state:return None
 if event_id is not None:
  tick=app.runtime.session.profile.get('desktop_pet_idle_opening_v1',{}).get('ticks',{}).get(event_id)
  if tick is not None:
   row=tick.get('participant_destination')
   if row is None:return None
   row=validate(app,row)
   from .idle_targets import VERSION as TARGET_VERSION
   if row.get('addressing_policy')==TARGET_VERSION:
    selection=tick['plan']['target_selection']
    if selection.get('fingerprint')!=fp({k:v for k,v in selection.items() if k!='fingerprint'}) or fp(row)!=fp(selection['destination']) or row['primary']!=selection['peer']:raise ValueError('idle relationship destination differs')
    return row
   origin=_input(app,row['source_event_id'])
   expected=autonomous_destination(origin) if row.get('addressing_policy')=='original-autonomous-destination-v1' else origin
   if expected!=row:raise ValueError('idle destination source differs')
   return row
  return _input(app,event_id)
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

