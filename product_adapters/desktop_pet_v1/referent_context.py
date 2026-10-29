"""Bounded literal references, with committed provenance and no state authority."""
import json
from dataclasses import replace
from framework.model_api import model_data_fingerprint,thaw_model_data
from model_packages.desktop_pet_v1.semantic import evidence_source
VERSION='desktop-pet-measurement-referents-v1'

def _record(event,kind,sources,text,limit_chars):
 row={'version':VERSION,'event_fingerprint':event.fingerprint,'kind':kind,'sources':sources,
      'limit_chars':limit_chars,'source_chars':len(text),'status':'delivered' if len(text)<=limit_chars else 'deferred_budget',
      'text':text if len(text)<=limit_chars else ''}
 row['fingerprint']=model_data_fingerprint(row)
 return row

def build(application,event,*,limit_chars=2048):
 if type(limit_chars)is not int or limit_chars<=0:raise ValueError('referent budget must be positive')
 application._verify_source(event)
 runtime=application.runtime
 if event.event_kind=='peer_input':
  from .input_reference_window import build as window
  prior=window(runtime,event,limit_chars=limit_chars)
  if prior is None:return None
  row=_record(event,'prior_inputs',[x['source'] for x in prior['sources']],prior['text'],limit_chars)
  row['window_fingerprint']=prior['fingerprint'];row['status']=prior['status']
  row['source_chars']=prior['source_chars'];row['fingerprint']=model_data_fingerprint({k:v for k,v in row.items() if k!='fingerprint'})
  return row
 if event.event_kind!='npc_output':return None
 source=runtime.session.profile['desktop_pet_product_v1']['sources'][event.event_id]
 actual=source['evidence']['fact']['model_payload']['execution_receipt']
 delivery=thaw_model_data(actual['realization_evidence'].get('expression_delivery'))
 if delivery is None:return None
 from .emission import _verify_signed,DELIVERY_VERSION,COMMIT_VERSION,VISUAL_COMMIT_VERSION
 _verify_signed(delivery,DELIVERY_VERSION)
 commit=thaw_model_data(source.get('expression_commit'))
 _verify_signed(commit,VISUAL_COMMIT_VERSION if isinstance(commit,dict) and commit.get('version')==VISUAL_COMMIT_VERSION else COMMIT_VERSION)
 if (commit['delivery']!=delivery or commit['action_id']!=event.event_id
     or commit['execution_receipt_fingerprint']!=actual['fingerprint']
     or commit['action_commit_fingerprint']!=event.source_receipt_ref):
  raise ValueError('measurement referent differs from actual expression commit')
 if delivery.get('source_kind')=='idle_tick':return None
 parent=runtime.memory_instance(delivery['event_id']);application._verify_source(parent.event)
 if (parent.event.source_binding_ref!=delivery['source_binding_ref']
     or parent.fact.settlement_ref['acceptance_fingerprint']!=delivery['source_acceptance_fingerprint']
     or parent.event.owner_ref!=event.owner_ref or parent.event.scope_ref!=event.scope_ref
     or parent.event.position.coordinate>event.position.coordinate):
  raise ValueError('measurement referent parent differs from committed source')
 visible=parent.event.visible_contents(event.owner_ref)
 if not visible:return None
 from model_packages.desktop_pet_v1.relation_package import META
 speaker=(parent.event.actor_ref if META in runtime.session.state.state else 'current_npc' if parent.event.actor_ref==event.owner_ref else 'user')
 text=json.dumps({'实际说话或行动者':speaker,
    '原文':evidence_source(replace(parent.event,contents=visible))},ensure_ascii=False,separators=(',',':'))
 ref={'event_id':parent.event.event_id,'event_fingerprint':parent.event.fingerprint,
      'fact_fingerprint':parent.fact.fingerprint,'source_binding_ref':parent.event.source_binding_ref,
      'delivery_fingerprint':delivery['fingerprint']}
 return _record(event,'response_parent',[ref],text,limit_chars)
