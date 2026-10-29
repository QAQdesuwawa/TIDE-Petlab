"""Public own disclosure is routed by actual visibility, not copied root state.

The six-axis primary transition remains the reference result. Other recipients
of the identical public contents receive only that event's own-disclosure
stimulus through the original G operator, against their own relation state.
"""
from framework.model_api import InteractionModelManifest,model_data_fingerprint as fp,thaw_model_data
from .relation_package import MultiPetPackage,META,SETTLEMENT,signed,verified,view,pack
from . import DesktopPetPackage
VERSION='desktop-pet-public-disclosure-scope-v1'


class DisclosureAwareMultiPetPackage(MultiPetPackage):
 own_disclosure_axis = 'L_self'  # Frozen v0.2 convention; holder v0.3 overrides.
 def __init__(self,*,parameter_package,owner_ref,human_ref):
  super().__init__(parameter_package=parameter_package,owner_ref=owner_ref,human_ref=human_ref)
  base=self.manifest
  fields={k:getattr(base,k) for k in ('api_version','capability_profile','capabilities','constants_schema','parameters_schema','state_schema','measurement_schema','shared_projection_schema','progress_delta_schema')}
  versions=dict(base.operator_versions)
  for stage in ('derive_event_contribution','settle_state'):versions[stage]=VERSION+'+'+versions[stage]
  self.manifest=InteractionModelManifest.create(package_id=base.package_id,package_version='0.2.0-public-disclosure',
   **fields,parameter_package={**thaw_model_data(base.parameter_package),'public_disclosure_scope_version':VERSION},operator_versions=versions)

 def derive_event_contribution(self,*,payload,context):
  result=super().derive_event_contribution(payload=payload,context=context)
  scope=context.metadata.get('desktop_pet_public_disclosure')
  if scope is None:return result
  scope=thaw_model_data(scope)
  if (scope.get('version')!=VERSION or scope.get('fingerprint')!=fp({k:v for k,v in scope.items() if k!='fingerprint'})
      or scope.get('owner_ref')!=self.owner_ref or context.metadata.get('committed_event_kind')!='npc_output'
      or scope.get('event_fingerprint')!=context.metadata.get('desktop_pet_event_fingerprint')
      or scope.get('source_binding_ref')!=context.metadata.get('source_receipt_fingerprint')):
   raise ValueError('public disclosure lacks verified own execution source')
  state=payload['state'];peer,_=self._selected(state,context)
  if scope['primary']!=peer or not scope['content_ids'] or len(scope['peers'])!=len(set(scope['peers'])) or peer in scope['peers'] or self.owner_ref in scope['peers']:
   raise ValueError('public disclosure recipients differ from primary')
  progressed=self._progress(payload['progress_contribution'],state,peer)['relations_after']
  # This is the same measured public episode, not the primary relation's level
  # or actual delta, and never its other five psychological effects.
  measurement=thaw_model_data(payload['measurement'])
  stimulus={axis:0.0 for axis in self.manifest.parameter_package['initial_state_default']}
  stimulus[self.own_disclosure_axis]=measurement['variable_deltas'][self.own_disclosure_axis]
  children=[]
  for other in scope['peers']:
   roots=progressed.get(other,self.manifest.parameter_package['initial_state_default'])
   evidence={'variable_deltas':stimulus,'public_disclosure_scope':scope['fingerprint'],
    'source_measurement_fingerprint':fp(measurement),'recipient_ref':other}
   child=DesktopPetPackage.derive_event_contribution(self,payload={'parameters':payload['parameters'],'state':roots,'measurement':evidence},context=context)['event_contribution']
   children.append({'peer_ref':other,'new_relation':other not in progressed,'event_contribution':child})
  primary=thaw_model_data(result['event_contribution']);primary.pop('fingerprint')
  return {'event_contribution':signed({**primary,'directed_disclosures':{'version':VERSION,'scope':scope,'children':children}})}

 def settle_state(self,*,payload,context):
  result=super().settle_state(payload=payload,context=context)
  disclosures=(payload.get('event_contribution') or {}).get('directed_disclosures')
  if disclosures is None:return result
  outer=thaw_model_data(result['settlement']);relations=outer['state_candidate'][META]['roots_by_peer'];primary=outer['state_candidate'][META]['active_peer'];rows=[]
  if disclosures['version']!=VERSION:raise ValueError('unknown disclosure contribution')
  for row in disclosures['children']:
   peer=row['peer_ref'];child=thaw_model_data(row['event_contribution'])
   if peer==primary or peer==self.owner_ref:raise ValueError('disclosure duplicates primary')
   expected=relations.get(peer,self.manifest.parameter_package['initial_state_default'])
   if fp(expected)!=fp(child['x_before']):raise ValueError('disclosure does not continue its own relation')
   reference=DesktopPetPackage.settle_state(self,payload={'state':expected,'progress_contribution':None,'event_contribution':child},context=context)['settlement']
   relations[peer]=reference['state_candidate'];rows.append({'peer_ref':peer,'settlement':reference,'source_scope_fingerprint':disclosures['scope']['fingerprint']})
  outer['state_candidate']=pack(self.owner_ref,primary,relations);outer['public_disclosure_settlements']=rows;outer.pop('fingerprint',None)
  return {'settlement':signed(outer)}
