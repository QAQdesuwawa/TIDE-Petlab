"""Recipient eligibility for Writer content; no recall or state authority."""
from .destinations import audience,_input
from model_packages.desktop_pet_v1.relation_package import META
from framework.model_api import model_data_fingerprint as fp

LEGACY_VERSION='petlab-writer-recipient-memory-v1'
VERSION='petlab-writer-recipient-memory-v2-public-observations'
VERSIONS={LEGACY_VERSION,VERSION}
SCOPED_VERSION='petlab-writer-recipient-memory-v3-scoped-speech'
VERSIONS.add(SCOPED_VERSION)
CHOICE_VERSION='petlab-writer-recipient-memory-v4-stable-choice'
VERSIONS.add(CHOICE_VERSION)


def plan(app,event_id,*,version=VERSION):
 if version is None:return None
 if version not in VERSIONS:raise ValueError('unknown Writer memory visibility version')
 if META not in app.runtime.session.state.state:return None
 row={'version':version,'audience':list(audience(app,event_id)),'excluded':[]}
 from .intention_source import enabled
 if enabled(app):row['private_intention_context']='cx67-owner-cognition-v1'
 if version==SCOPED_VERSION:
  from .destinations import primary
  row['required_reader']=primary(app,event_id)
 return row


def allowed(app,memory,readers,*,version=VERSION):
 if version not in VERSIONS:raise ValueError("unknown Writer memory visibility version")
 event=memory.event;source=app._product()['sources'][event.event_id]
 contents=event.visible_contents(app.owner_ref)
 if not contents:return False
 # Old text delivery adapted the shared conversation to one reader. Its
 # frozen conversation still proves the visibility of that single text body.
 from .participant_input import VERSION as HUMAN,NPC_VERSION,OBSERVED_VERSIONS,SPEECH_VERSIONS
 receipt=source['evidence'].get('input_source',{})
 shared=None
 if version in {VERSION,SCOPED_VERSION,CHOICE_VERSION} and public_observation(app,memory) is not None:return True
 if event.event_kind=='peer_input' and receipt.get('version') in {HUMAN,NPC_VERSION,*OBSERVED_VERSIONS,*SPEECH_VERSIONS}:
  destination=_input(app,event.event_id)
  if destination is not None:shared=set(destination['audience'])|{app.owner_ref,event.actor_ref}
 for c in contents:
  if c.private or c.kind=='thought':
   # The user-facing thought channel is still the owner's own cognition.
   # It never grants another NPC access to the same private body.
   if event.actor_ref==app.owner_ref and set(readers)<={app.owner_ref,app.user_ref}:continue
   return False
  visible=shared if shared is not None else set(c.recipient_refs)|{app.owner_ref,event.actor_ref}
  if not set(readers)<=visible:return False
 return True


def public_observation(app,memory):
 """One current source check shared by recipient and result-link delivery."""
 event=memory.event
 receipt=app._product()['sources'][event.event_id]['evidence'].get('input_source',{})
 from model_packages.desktop_pet_v1.web_observation import is_observation
 if (receipt.get('kind')!='web_page' or event.owner_ref!=app.owner_ref
     or not is_observation(event) or any(c.private for c in event.contents)):return None
 app._verify_source(event)
 return receipt


def filter_memories(app,memories,visibility,*,lane):
 if visibility is None:return tuple(memories)
 if visibility['version'] not in VERSIONS:raise ValueError('unknown visibility plan')
 kept=[]
 for memory in memories:
  if visibility.get('private_intention_context')=='cx67-owner-cognition-v1':
   from .intention_source import ORIGIN
   event=memory.event
   source=app.runtime.session.profile['desktop_pet_product_v1']['sources'].get(event.event_id,{})
   if source.get('origin')==ORIGIN and event.owner_ref==app.owner_ref and event.actor_ref==app.owner_ref:
    # Writer is this owner's private cognition. Output audience does not
    # receive this memory body; public delivery still uses actual contents.
    app._verify_source(event)
    kept.append(memory);continue
  if visibility['version']==SCOPED_VERSION:
   readers=[r for r in visibility['audience'] if allowed(app,memory,[r],version=SCOPED_VERSION)]
   if visibility['required_reader'] in readers:
    visibility['audience']=readers;kept.append(memory);continue
   visibility['excluded'].append({'event_id':memory.event.event_id,'lane':lane,'reason':'outside_output_audience'})
   continue
  if allowed(app,memory,visibility['audience'],version=visibility['version']):kept.append(memory)
  else:visibility['excluded'].append({'event_id':memory.event.event_id,'lane':lane,'reason':'outside_output_audience'})
 return tuple(kept)


def receipt(visibility):
 if visibility is None:return None
 return {**visibility,'fingerprint':fp(visibility)}


def speech_audience(destination,visibility):
 """One sealed cap used by assembly, candidate checks and publication."""
 original=list(destination['audience'])
 if visibility is None or visibility.get('version') not in {SCOPED_VERSION,CHOICE_VERSION}:return original
 row={k:v for k,v in visibility.items() if k!='fingerprint'}
 if visibility.get('fingerprint')!=fp(row):raise ValueError('memory visibility integrity differs')
 readers=row['audience']
 if visibility['version']==CHOICE_VERSION:
  if list(readers)!=original:raise ValueError('stable speech choice differs from destination')
  return list(readers)
 if (not readers or len(set(readers))!=len(readers) or not set(readers)<=set(original)
     or row['required_reader']!=destination['primary'] or row['required_reader'] not in readers):
  raise ValueError('memory speech audience differs')
 return list(readers)
