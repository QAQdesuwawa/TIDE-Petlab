"""Directed relationship views in one package-owned, Kernel-committed state.

Every numerical operation delegates to the unchanged reference package. The
extra state member is provenance/storage, never a psychological root or clock.
"""
from collections.abc import Mapping
from copy import deepcopy
from framework.model_api import InteractionModelManifest,model_data_fingerprint as fp,thaw_model_data
from model_packages.cx_six_seven_v1.v2_package import _root_vector
from . import DesktopPetPackage
from ops.relationship_runtime import DirectedRelationshipKey
META='__directed_relations__'
VERSION='desktop-pet-directed-relations-v1'
PROGRESS=VERSION+':progress'
SETTLEMENT=VERSION+':settlement'

def signed(row):return {**row,'fingerprint':fp(row)}
def verified(row,version):
 row=thaw_model_data(row)
 if row.get('version')!=version or row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'}):raise ValueError('directed relation product integrity differs')
 return row

def view(state):
 state=thaw_model_data(state)
 if META not in state:return _root_vector(state,'root state')
 if set(state)!={*('E_env','P_need','V_attr','V_rep','L_self','L_opponent'),META}:raise ValueError('directed state fields differ')
 meta=state[META]
 if set(meta)!={'version','owner_ref','active_peer','roots_by_peer'} or meta['version']!=VERSION:raise ValueError('directed state metadata differs')
 if not isinstance(meta['owner_ref'],str) or not meta['owner_ref'] or meta['owner_ref'] in meta['roots_by_peer']:raise ValueError('directed state owner invalid')
 roots=_root_vector({k:v for k,v in state.items() if k!=META},'active roots')
 saved={k:_root_vector(v,'relation roots') for k,v in meta['roots_by_peer'].items()}
 if meta['active_peer'] not in saved or roots!=saved[meta['active_peer']]:raise ValueError('active relation roots differ')
 return roots

def pack(owner,peer,relations):
 value={**relations[peer],META:{'version':VERSION,'owner_ref':owner,'active_peer':peer,'roots_by_peer':deepcopy(relations)}}
 view(value);return value

class MultiPetPackage(DesktopPetPackage):
 def __init__(self,*,parameter_package,owner_ref,human_ref):
  if not isinstance(owner_ref,str) or not owner_ref or not isinstance(human_ref,str) or not human_ref or owner_ref==human_ref:raise ValueError('directed package identity invalid')
  super().__init__(parameter_package=parameter_package)
  self.owner_ref=owner_ref;self.human_ref=human_ref
  base=self.manifest;operators=dict(base.operator_versions)
  for key in ('initialize_state','advance_progress','derive_event_contribution','settle_state','project_shared_semantics'):operators[key]=VERSION+'+'+operators[key]
  fields={k:getattr(base,k) for k in ('api_version','capability_profile','capabilities','constants_schema','parameters_schema','measurement_schema','shared_projection_schema','progress_delta_schema')}
  self.manifest=InteractionModelManifest.create(package_id='tide.desktop-pet.multi',package_version='0.1.0-directed-state',
   **fields,state_schema={'type':'object','active_axes':list(parameter_package['initial_state_default']),'relation_metadata':META},
   parameter_package={**thaw_model_data(base.parameter_package),'directed_relation_binding':{'version':VERSION,'owner_ref':owner_ref,'human_ref':human_ref}},operator_versions=operators)
 def initialize_parameters(self,*,payload,context):
  # Human preference correction is relation-specific, never broadcast to NPC peers.
  if payload['b0_binding']['enabled']:raise ValueError('multi pet requires identity B0 until per-relation bindings are adopted')
  return super().initialize_parameters(payload=payload,context=context)
 def initialize_state(self,*,payload,context):
  roots=super().initialize_state(payload=payload,context=context)['state']
  return {'state':pack(self.owner_ref,self.human_ref,{self.human_ref:roots})}
 def _selected(self,state,context):
  view(state);meta=state[META]
  if meta['owner_ref']!=self.owner_ref:raise ValueError('directed state owner differs')
  peer=context.metadata.get('desktop_pet_counterparty_ref',meta['active_peer'])
  if not isinstance(peer,str) or not peer or peer==self.owner_ref:raise ValueError('directed peer invalid')
  DirectedRelationshipKey(self.owner_ref,peer)
  if peer not in meta['roots_by_peer']:
   if (context.metadata.get('committed_event_kind')!='peer_input'
       or not context.metadata.get('desktop_pet_relation_source')):raise ValueError('new relation needs a received committed source')
   roots=thaw_model_data(self.manifest.parameter_package['initial_state_default'])
  else:roots=thaw_model_data(meta['roots_by_peer'][peer])
  return peer,roots
 def prepare_memory_inputs(self,*,payload,context):
  if 'query' in payload and 'shared_projection' in payload['query']:
   query,source=reference_tick_payload(payload['query'])
   result=super().prepare_memory_inputs(payload={**payload,'query':query},context=context)
   if source is not None:result={**result,'directed_query_source':source}
   return result
  return super().prepare_memory_inputs(payload=payload,context=context)
 def advance_progress(self,*,payload,context):
  state=payload['state'];peer,before=self._selected(state,context)
  relations={};records={}
  for other,roots in state[META]['roots_by_peer'].items():
   result=super().advance_progress(payload={**payload,'state':roots},context=context)['progress_contribution']
   relations[other]=result['x_after'];records[other]=result
  if peer not in records:
   # A new relation is born at this input, not aged from before it existed.
   delta=thaw_model_data(payload['progress_delta']);delta['payload']['elapsed_ms']=0;delta['advanced']=False
   delta['fingerprint']=fp({k:v for k,v in delta.items() if k!='fingerprint'})
   result=super().advance_progress(payload={**payload,'state':before,'progress_delta':delta},context=context)['progress_contribution']
   records[peer]=result;relations[peer]=result['x_after']
  return {'progress_contribution':signed({'version':PROGRESS,'owner_ref':self.owner_ref,'peer_ref':peer,
    'source_state_fingerprint':fp(state),'source_progress_fingerprint':payload['progress_delta']['fingerprint'],
    'reference_contribution':records[peer],'relations_after':relations})}
 def _progress(self,value,state,peer):
  row=verified(value,PROGRESS)
  if row['owner_ref']!=self.owner_ref or row['peer_ref']!=peer or row['source_state_fingerprint']!=fp(state):raise ValueError('progress belongs to another relation')
  return row
 def derive_event_contribution(self,*,payload,context):
  state=payload['state'];peer,roots=self._selected(state,context)
  plain={**payload,'state':roots}
  if payload.get('progress_contribution') is not None:plain['progress_contribution']=self._progress(payload['progress_contribution'],state,peer)['reference_contribution']
  return super().derive_event_contribution(payload=plain,context=context)
 def settle_state(self,*,payload,context):
  state=payload['state'];peer,roots=self._selected(state,context)
  plain={**payload,'state':roots};relations=thaw_model_data(state[META]['roots_by_peer'])
  if payload.get('progress_contribution') is not None:
   progress=self._progress(payload['progress_contribution'],state,peer)
   plain['progress_contribution']=progress['reference_contribution'];relations=progress['relations_after']
  reference=super().settle_state(payload=plain,context=context)['settlement']
  relations[peer]=reference['state_candidate']
  outer={**reference,'version':SETTLEMENT,'state_before':thaw_model_data(state),
    'state_candidate':pack(self.owner_ref,peer,relations),'reference_settlement':reference}
  outer.pop('fingerprint',None)
  return {'settlement':signed(outer)}
 def project_shared_semantics(self,*,payload,context):
  outer=verified(payload['settlement'],SETTLEMENT)
  view(outer['state_before']);view(outer['state_candidate'])
  reference=outer['reference_settlement']
  if view(outer['state_candidate'])!=reference['state_candidate']:raise ValueError('projection relation differs from reference settlement')
  result=super().project_shared_semantics(payload={**payload,'settlement':reference},context=context)['shared_projection']
  result={**result,'source_settlement_fingerprint':outer['fingerprint'],'source_state_candidate':outer['state_candidate']}
  result.pop('fingerprint',None)
  return {'shared_projection':signed(result)}


def numerical_pair(before,after,settlement):
 """Use this accepted relation's original roots, not another peer's old face."""
 if META not in after:return before,after
 outer=verified(settlement,SETTLEMENT)
 if fp(outer['state_before'])!=fp(before) or fp(outer['state_candidate'])!=fp(after):raise ValueError('presentation differs from directed acceptance')
 reference=outer['reference_settlement']
 if view(after)!=reference['state_candidate']:raise ValueError('presentation relation roots differ')
 return reference['state_before'],reference['state_candidate']


def reference_tick_payload(payload):
 """Verify the full accepted state before delegating its original root view."""
 if 'settlement' not in payload or payload['settlement'].get('version')!=SETTLEMENT:return payload,None
 outer=verified(payload['settlement'],SETTLEMENT);shared=thaw_model_data(payload['shared_projection'])
 if shared.get('fingerprint')!=fp({k:v for k,v in shared.items() if k!='fingerprint'}):raise ValueError('scoped recall shared integrity differs')
 if shared.get('source_settlement_fingerprint')!=outer['fingerprint'] or fp(shared.get('source_state_candidate'))!=fp(outer['state_candidate']):raise ValueError('scoped recall source differs')
 reference=outer['reference_settlement']
 if view(outer['state_candidate'])!=reference['state_candidate']:raise ValueError('scoped recall root view differs')
 original={**shared,'source_settlement_fingerprint':reference['fingerprint'],'source_state_candidate':reference['state_candidate']}
 original.pop('fingerprint',None);original=signed(original)
 source=signed({'version':VERSION+':tick-reference','settlement_fingerprint':outer['fingerprint'],
  'shared_projection_fingerprint':shared['fingerprint'],'reference_settlement_fingerprint':reference['fingerprint'],
  'reference_shared_projection_fingerprint':original['fingerprint']})
 return {**payload,'settlement':reference,'shared_projection':original},source
