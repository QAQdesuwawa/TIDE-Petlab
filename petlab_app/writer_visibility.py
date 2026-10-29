"""Recipient eligibility for Writer content; no recall or state authority."""
from .destinations import audience,_input
from model_packages.desktop_pet_v1.relation_package import META
from framework.model_api import model_data_fingerprint as fp

LEGACY_VERSION='petlab-writer-recipient-memory-v1'
VERSION='petlab-writer-recipient-memory-v2-public-observations'
VERSIONS={LEGACY_VERSION,VERSION}


def plan(app,event_id,*,version=VERSION):
 if version is None:return None
 if version not in VERSIONS:raise ValueError('unknown Writer memory visibility version')
 if META not in app.runtime.session.state.state:return None
 return {'version':version,'audience':list(audience(app,event_id)),'excluded':[]}


def allowed(app,memory,readers,*,version=VERSION):
 if version not in VERSIONS:raise ValueError("unknown Writer memory visibility version")
 event=memory.event;source=app._product(source_id=event.event_id)['sources'][event.event_id]
 contents=event.visible_contents(app.owner_ref)
 if not contents:return False
 # Old text delivery adapted the shared conversation to one reader. Its
 # frozen conversation still proves the visibility of that single text body.
 from .participant_input import VERSION as HUMAN,NPC_VERSION
 receipt=source['evidence'].get('input_source',{})
 shared=None
 if version==VERSION and public_observation(app,memory) is not None:return True
 if event.event_kind=='peer_input' and receipt.get('version') in {HUMAN,NPC_VERSION}:
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
 receipt=app._product(source_id=event.event_id)['sources'][event.event_id]['evidence'].get('input_source',{})
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
  if allowed(app,memory,visibility['audience'],version=visibility['version']):kept.append(memory)
  else:visibility['excluded'].append({'event_id':memory.event.event_id,'lane':lane,'reason':'outside_output_audience'})
 return tuple(kept)


def receipt(visibility):
 if visibility is None:return None
 return {**visibility,'fingerprint':fp(visibility)}
