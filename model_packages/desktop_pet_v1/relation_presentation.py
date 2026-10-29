"""Latest formal feedback by directed target; identity metadata, no new math."""
from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from .reaction import validate as validate_reaction
from .relation_package import META, view

KEY='desktop_pet_relation_presentations_v1'
VERSION='desktop-pet-settled-presentation-v3-relations'
ENTRY='desktop-pet-relation-presentation-v1'


def signed(value):return {**value,'fingerprint':fp(value)}


def target(state):
 if META not in state:return None
 view(state)
 return state[META]['active_peer']


def entry(row,*,session_binding,parameters):
 state=row['acceptance']['state']['state']
 peer=target(state)
 if peer is None:raise ValueError('presentation has no directed relation')
 value={'version':ENTRY,'session_binding':session_binding,'parameters':parameters,
  'owner_ref':state[META]['owner_ref'],'target_ref':peer,'source_kind':row['source_kind'],
  'source_ref':row['source_ref'],'reaction':thaw_model_data(row['reaction'])}
 return signed(value)


def validate_entry(value,*,session_binding,parameters=None,owner_ref=None,target_ref=None):
 if (value.get('version')!=ENTRY or value.get('fingerprint')!=fp({k:v for k,v in value.items() if k!='fingerprint'})
     or value.get('session_binding')!=session_binding or not value.get('owner_ref') or not value.get('target_ref')
     or value['owner_ref']==value['target_ref'] or value.get('source_kind') not in {'event','progress'} or not value.get('source_ref')
     or parameters is not None and value.get('parameters')!=parameters
     or owner_ref is not None and value['owner_ref']!=owner_ref
     or target_ref is not None and value['target_ref']!=target_ref):
  raise ValueError('relation presentation identity differs')
 validate_reaction(value['reaction'])
 return value


def read(profile,*,session_binding,parameters,owner_ref):
 value=profile.get(KEY)
 if value is None:return {}
 if (value.get('version')!=KEY or value.get('fingerprint')!=fp({k:v for k,v in value.items() if k!='fingerprint'})
     or value.get('display_target_ref') not in value.get('entries',{})):
  raise ValueError('relation presentation cache differs')
 result=thaw_model_data(value['entries'])
 for peer,item in result.items():validate_entry(item,session_binding=session_binding,parameters=parameters,owner_ref=owner_ref,target_ref=peer)
 return result


def stored(entries,display_target):return signed({'version':KEY,'display_target_ref':display_target,'entries':entries})
