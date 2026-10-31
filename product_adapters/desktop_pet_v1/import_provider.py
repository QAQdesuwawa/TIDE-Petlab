"""Checkpoint an import estimate using the existing provider lifecycle/receipts."""
import json
from framework.model_api import model_data_fingerprint as fingerprint,thaw_model_data
from framework.provider_host import (ProviderRequest,ProviderAttempt,ProviderResponse,
    ProviderFailure,ProviderExecutionResult)
from framework.provider_codec import ProviderCodecParseReceipt,ProviderCodecParseFailureSignal
from product_adapters.desktop_pet_v1.execution import ExecutionUncertain
from petlab_app.providers import completion_text


def estimate(workspace,gateway,*,operation_key,session_binding,current_position,
             source_ref,codec_contract,messages,parse,source_receipt_refs=()):
    """One reserved logical request. Recovering a successful return makes zero calls."""
    operation_kind='import_estimate'
    transport=gateway.transport_for(operation_kind)
    payload=transport.payload(messages=messages,temperature=0.0,require_json=True)
    binding={'version':'desktop-pet-import-provider-v1',
        'session_binding':session_binding.fingerprint,'position':current_position.to_record(),
        'source_ref':source_ref,'codec_contract':codec_contract,
        'request_payload_fingerprint':fingerprint(payload),
        'transport_fingerprint':fingerprint(transport.binding),
        'source_receipt_refs':list(source_receipt_refs)}
    row,is_new=workspace.reserve_call(operation_key,binding)
    nonce=row['invocation_id']
    if is_new:
        def parse_wire(wire):
            try:
                result=parse(completion_text(wire))
                if result is None:raise ValueError('empty estimate')
                return result
            except (ValueError,TypeError,KeyError):
                raise ProviderCodecParseFailureSignal(failure_kind='invalid_import_estimate',
                    failure_source_ref=fingerprint(wire)) from None
        gateway.run(session_binding=session_binding,current_position=current_position,
            operation_kind=operation_kind,source_fact_ref=source_ref,codec_contract=codec_contract,
            messages=messages,temperature=0.0,require_json=True,parse=parse_wire,
            source_receipt_refs=source_receipt_refs,invocation_id=nonce)
    path=gateway.trace_dir/(nonce+'.json')
    if not path.is_file():raise ExecutionUncertain('import call reserved without a terminal receipt; reconcile before continuing')
    record=json.loads(path.read_text('utf8'))
    if record.get('status') not in ('parsed','failed'):
        raise ExecutionUncertain('import provider is incomplete; no automatic redispatch')
    raw=record['execution']
    request=ProviderRequest(**raw['request']);attempt=ProviderAttempt(**raw['attempt'])
    response=None if raw['response'] is None else ProviderResponse(**raw['response'])
    failure=None if raw['failure'] is None else ProviderFailure(**raw['failure'])
    execution=ProviderExecutionResult(version=request.version,status=raw['receipt']['status'],
        request=request,attempt=attempt,response=response,failure=failure,receipt=raw['receipt'])
    request.verify_integrity(session_binding=session_binding,current_position=current_position)
    execution.verify_integrity()
    codec=None if record['codec_parse'] is None else ProviderCodecParseReceipt(**record['codec_parse'])
    if codec is not None:
        if response is None:raise ValueError('import parse receipt lacks response')
        codec.verify_integrity(response=response)
    if (record['invocation_id']!=nonce or request.source_fact_ref!=source_ref
            or request.operation_kind!=operation_kind or request.codec_contract!=codec_contract
            or fingerprint(request.request_payload)!=binding['request_payload_fingerprint']
            or fingerprint(record['transport_binding'])!=binding['transport_fingerprint']
            or attempt.invocation_evidence.get('invocation_id')!=nonce
            or attempt.invocation_evidence.get('transport_binding_fingerprint')!=binding['transport_fingerprint']
            or (response is not None and fingerprint(response.response_payload)!=fingerprint(record['wire_response']))):
        raise ValueError('import provider receipt differs from reserved request')
    if record['status']!='parsed':
        raise ExecutionUncertain('import call has a recorded failure; fix or reconcile it without automatic retry')
    if codec is None or codec.status!='parsed' or codec.codec_contract!=codec_contract:
        raise ValueError('import provider parse binding differs')
    return {'payload':thaw_model_data(codec.parsed_payload),'request_ref':request.fingerprint,
        'parse_ref':codec.fingerprint,'reservation_ref':row['fingerprint'],
        'trace_ref':nonce,'codec_contract':codec_contract,
        'actual_model':response.response_payload.get('model'),
        'wire_fingerprint':fingerprint(response.response_payload)}
