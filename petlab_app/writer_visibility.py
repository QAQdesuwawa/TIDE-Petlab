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
OWNER_VERSION='petlab-writer-recipient-memory-v5-owner-cognition'
VERSIONS.add(OWNER_VERSION)
EXPERIENCE_VERSION='petlab-writer-recipient-memory-v6-shareable-experience'
VERSIONS.add(EXPERIENCE_VERSION)
TOWN_VERSION='petlab-writer-recipient-memory-v7-public-scene-activity'
VERSIONS.add(TOWN_VERSION)
SHARED_WORLD_VERSION='petlab-writer-recipient-memory-v8-shared-world'
VERSIONS.add(SHARED_WORLD_VERSION)
EXPERIENCE_VERSIONS={EXPERIENCE_VERSION,SHARED_WORLD_VERSION}


def output_readers(app,event_id):
 """New owner-authored contents may address registered scene participants.

 This does not change the readers of any input or recalled source.
 """
 if not hasattr(app,'multi_workspace'):return list(audience(app,event_id))
 return [ref for ref in app.multi_workspace.participants if ref!=app.owner_ref]


def plan(app,event_id,*,version=VERSION):
 if version is None:return None
 if version not in VERSIONS:raise ValueError('unknown Writer memory visibility version')
 if META not in app.runtime.session.state.state:return None
 row={'version':version,'audience':list(audience(app,event_id)),'excluded':[]}
 if version==OWNER_VERSION:
  row['input_audience']=list(row['audience'])
  row['owner']=app.owner_ref
  row['audience']=output_readers(app,event_id)
 from .intention_source import enabled
 if version not in EXPERIENCE_VERSIONS and enabled(app):row['private_intention_context']='cx67-owner-cognition-v1'
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
 if version in {VERSION,SCOPED_VERSION,CHOICE_VERSION,TOWN_VERSION,*EXPERIENCE_VERSIONS} and public_observation(app,memory) is not None:return True
 if version==TOWN_VERSION and public_scene_activity(app,event,receipt):return True
 if version==SHARED_WORLD_VERSION:
  from .screen_perception import WORLD_VERSION
  if receipt.get('version')==WORLD_VERSION and source.get('origin')=='input':
   # The content is a per-owner projection. The archived world observation
   # carries the witnesses; foreground windows and private speech don't use it.
   app._verify_source(event)
   return set(readers)<=set(receipt['material']['viewers'])
 if event.event_kind=='peer_input' and receipt.get('version') in {HUMAN,NPC_VERSION,*OBSERVED_VERSIONS,*SPEECH_VERSIONS}:
  destination=_input(app,event.event_id)
  if destination is not None:shared=set(destination['audience'])|{app.owner_ref,event.actor_ref}
 for c in contents:
  if c.private or c.kind=='thought':
   # The user-facing thought channel is still the owner's own cognition.
   # It never grants another NPC access to the same private body.
   if version not in EXPERIENCE_VERSIONS and event.actor_ref==app.owner_ref and set(readers)<={app.owner_ref,app.user_ref}:continue
   return False
  visible=shared if shared is not None else set(c.recipient_refs)|{app.owner_ref,event.actor_ref}
  if not set(readers)<=visible:return False
 return True


def public_scene_activity(app,event,receipt):
 """A town host can prove a witnessed physical activity was public."""
 from . import scene_encounter
 verify=getattr(app,'public_scene_activity',None)
 if (not callable(verify) or receipt.get('version')!=scene_encounter.ACTIVITY_VERSION
     or event.owner_ref!=app.owner_ref or receipt['reader_ref']!=app.owner_ref
     or receipt['actor_ref']!=event.actor_ref or receipt['event_id']!=event.event_id
     or not verify(receipt) or tuple(event.contents)!=(scene_encounter.content(receipt),)):
  return False
 app._verify_source(event)
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
  if visibility['version']==OWNER_VERSION:
   event=memory.event
   if event.owner_ref==app.owner_ref and event.visible_contents(app.owner_ref):
    kept.append(memory)
   else:visibility['excluded'].append({'event_id':event.event_id,'lane':lane,'reason':'outside_owner_read_scope'})
   continue
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
 if visibility is None or visibility.get('version') not in {SCOPED_VERSION,CHOICE_VERSION,OWNER_VERSION,*EXPERIENCE_VERSIONS}:return original
 row={k:v for k,v in visibility.items() if k!='fingerprint'}
 if visibility.get('fingerprint')!=fp(row):raise ValueError('memory visibility integrity differs')
 readers=row['audience']
 if visibility['version']==OWNER_VERSION:
  if (list(row['input_audience'])!=original or not row['owner'] or row['owner'] in readers
      or len(set(readers))!=len(readers) or not set(original)<=set(readers)):
   raise ValueError('owner output audience differs from source/scene')
  return list(readers)
 if visibility['version'] in {CHOICE_VERSION,*EXPERIENCE_VERSIONS}:
  if list(readers)!=original:raise ValueError('stable speech choice differs from destination')
  return list(readers)
 if (not readers or len(set(readers))!=len(readers) or not set(readers)<=set(original)
     or row['required_reader']!=destination['primary'] or row['required_reader'] not in readers):
  raise ValueError('memory speech audience differs')
 return list(readers)
