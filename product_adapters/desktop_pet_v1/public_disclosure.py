"""Actual public own-disclosure scope, after normal execution-source validation."""
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1.relation_package import META
VERSION='desktop-pet-public-disclosure-scope-v1'
FLAG='public_disclosure_scope_version'


def enabled(runtime):
 return runtime.model_runtime.manifest.parameter_package.get(FLAG)==VERSION


def destination(runtime,event):
 source=runtime.session.profile['desktop_pet_product_v1']['sources'][event.event_id]
 if source['origin']!='execution':return None
 fact=source['evidence']['fact']
 return fact['model_payload']['execution_receipt']['realization_evidence'].get('participant_destination')


def primary(runtime,event,fallback):
 if not enabled(runtime) or event.event_kind!='npc_output':return fallback
 dest=destination(runtime,event)
 return fallback if dest is None else dest['primary']


def context(runtime,event,measurement_payload,peer):
 if not enabled(runtime) or event.event_kind!='npc_output':return {}
 dest=destination(runtime,event)
 if dest is None:return {}
 source=runtime.session.profile['desktop_pet_product_v1']['sources'][event.event_id]
 public=tuple(c for c in event.contents if not c.private and c.kind in ('speech','expression','action'))
 primary_ids=[c.content_id for c in public if peer in c.recipient_refs]
 if not primary_ids:return {}
 peers=[];deferred=[]
 for other in dest['audience']:
  if other==peer:continue
  ids=[c.content_id for c in public if other in c.recipient_refs]
  if ids==primary_ids:peers.append(other)
  elif ids:deferred.append({'peer_ref':other,'reason':'different_visible_content_requires_own_measurement','content_ids':ids})
 if not peers and not deferred:return {}
 row={'version':VERSION,'owner_ref':event.owner_ref,'event_fingerprint':event.fingerprint,
  'source_binding_ref':event.source_binding_ref,'source_fact_fingerprint':source['evidence']['fact']['fingerprint'],
  'primary':peer,'peers':peers,'content_ids':primary_ids,'deferred':deferred,
  'measurement_payload_fingerprint':fp(measurement_payload)}
 return {'desktop_pet_public_disclosure':{**row,'fingerprint':fp(row)}}


def counterparty(runtime,event):
 """The existing directed target, shared by measurement and settlement."""
 from model_packages.desktop_pet_v1.web_observation import is_observation
 roots=runtime.session.state.state
 peer=(roots[META]['active_peer'] if is_observation(event) else
       event.actor_ref if event.event_kind=='peer_input' else
       event.target_refs[0] if len(event.target_refs)==1 else roots[META]['active_peer'])
 return primary(runtime,event,peer)
