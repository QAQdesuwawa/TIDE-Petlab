"""Explicit local codec recovery over a verified, already received response."""
import json
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from framework.provider_host import ProviderRequest,ProviderAttempt,ProviderResponse,ProviderExecutionResult
from framework.provider_codec import ProviderCodecParseReceipt,execute_provider_codec_parse
from framework.provider_lifecycle import ProviderLifecycleResult
from framework.receipt_adapters import adapt_provider_codec_parse
from framework.receipt_dag import LifecycleArtifactEvidence,LifecycleFailureEvidence

REVISION='petlab-source-bound-local-reparse-v1'
def reparse_failed(gateway,*,session_binding,current_position,operation_kind,source_fact_ref,codec_contract,messages,temperature,require_json,parse,source_receipt_refs=()):
 body=gateway.transport_for(operation_kind).payload(messages=messages,temperature=temperature,require_json=require_json)
 rows=[]
 for path in gateway.trace_dir.glob('*.json'):
  record=json.loads(path.read_text(encoding='utf8'))
  if (record.get('version')=='petlab-provider-trace-v1' and record.get('source_fact_ref')==source_fact_ref
      and record.get('operation_kind')==operation_kind and record.get('status')=='failed'
      and (record.get('codec_parse') or {}).get('failure_kind')=='invalid_memory_query_nodes'):
   rows.append((path,record))
 if not rows:return None
 # Oldest received attempt is authoritative for recovery, not a lucky later draw.
 path,record=min(rows,key=lambda item:item[1]['host_timing']['response_received_at_unix_ms'])
 raw=record['execution'];request=ProviderRequest(**raw['request'])
 request.verify_integrity(session_binding=session_binding,current_position=current_position)
 from model_packages.desktop_pet_v1.query_tree import accepts_received_contract
 compatible=request.codec_contract==codec_contract or (operation_kind=='semantic_measurement' and accepts_received_contract(request.codec_contract,codec_contract))
 if not compatible or fp(request.request_payload)!=fp(body):raise ValueError('local recovery request differs from received request')
 response=ProviderResponse(**raw['response']);attempt=ProviderAttempt(**raw['attempt'])
 execution=ProviderExecutionResult(version=request.version,status=raw['receipt']['status'],request=request,attempt=attempt,response=response,failure=None,receipt=raw['receipt'])
 execution.verify_integrity()
 old=ProviderCodecParseReceipt(**record['codec_parse']);old.verify_integrity(response=response)
 if fp(record['wire_response'])!=fp(response.response_payload):raise ValueError('received response body differs')
 receipt=execute_provider_codec_parse(response=response,codec_contract=codec_contract+':'+REVISION,parse=parse)
 evidence={}
 prefix='desktop_pet_'+operation_kind
 for key,value in record['evidence'].items():
  if key==prefix+'_codec_parse':continue
  cls=LifecycleArtifactEvidence if 'artifact_kind' in value else LifecycleFailureEvidence
  evidence[key]=cls(**value)
 stage=prefix+'_codec_parse'
 evidence[stage]=adapt_provider_codec_parse(stage=stage,parse_receipt=receipt,response=response,session_binding=session_binding,current_position=current_position,parent_receipt_refs=(evidence[prefix+'_provider_response'].receipt_ref,))
 from .providers import _save_trace
 audit={'version':REVISION,'original_trace':str(path),'original_response_fingerprint':response.fingerprint,'original_failure_fingerprint':old.fingerprint,'codec_parse':receipt.to_record(),'network_requests':0}
 audit['fingerprint']=fp(audit)
 _save_trace(gateway.trace_dir/'local-reparses'/(audit['fingerprint']+'.json'),audit)
 gateway.last_failure=None
 return ProviderLifecycleResult(status='parsed' if receipt.status=='parsed' else 'failed',request=request,execution=execution,parse_receipt=receipt,parsed_payload=receipt.parsed_payload,evidence=evidence)
