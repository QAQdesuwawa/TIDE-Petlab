"""Compose native provider operations; audit projection never restores their inputs.

This host accepts no prevalidated external artifacts. Request, execution and parse
are created and checked by the original framework in this call. Cold trace replay
continues through the original adapters. Transport can run on a caller-owned
worker; parsing returns to the preparing thread. No Commit authority is added.
"""
from framework.model_api import model_data_fingerprint, thaw_model_data
from framework.provider_host import ProviderRequest, execute_provider
from framework.provider_codec import execute_provider_codec_parse
from framework.provider_lifecycle import ProviderLifecycleResult
from framework.receipt_dag import LifecycleArtifactEvidence, LifecycleFailureEvidence


def _context_value(binding, position):
    # Compare actual values across untrusted callbacks, not caller-supplied hashes.
    # Nested records thaw their immutable payloads; this performs no Kernel restore.
    return (binding.session_id, binding.model_binding.to_record(),
        binding.temporal_binding.to_record(), binding.runtime_variant.to_record(),
        binding.fingerprint, position.to_record())


def _project_evidence(prefix, request, execution, parsed, parents):
    """Read-only proof formatting from fresh native results, never a validation gate."""
    context=dict(session_binding_fingerprint=request.session_binding_fingerprint,
        temporal_position_fingerprint=request.temporal_position_fingerprint,
        runtime_ordinal=request.runtime_ordinal)
    evidence={}
    def artifact(suffix,kind,ref,input_payload,output_payload,parent_refs):
        stage=prefix+suffix
        node=LifecycleArtifactEvidence.create(stage=stage,artifact_kind=kind,receipt_ref=ref,
            input_payload=input_payload,output_payload=output_payload,
            parent_receipt_refs=parent_refs,**context)
        evidence[stage]=node
        return node
    def failure(suffix,kind,ref,parent_refs):
        stage=prefix+suffix
        evidence[stage]=LifecycleFailureEvidence.create(stage=stage,failure_kind=kind,
            failure_ref=ref,parent_receipt_refs=parent_refs,**context)
    req=artifact('_provider_request','provider-request',request.fingerprint,
        {'operation_kind':request.operation_kind,'source_fact_ref':request.source_fact_ref,
         'codec_contract':request.codec_contract,'request_payload':thaw_model_data(request.request_payload),
         'transport_options':thaw_model_data(request.transport_options)},request.to_record(),parents)
    attempt=execution.attempt
    att=artifact('_provider_attempt','provider-attempt',attempt.fingerprint,
        {'request_fingerprint':request.fingerprint,'implementation_ref':attempt.implementation_ref,
         'invocation_evidence':thaw_model_data(attempt.invocation_evidence)},
        attempt.to_record(),(req.receipt_ref,))
    response_parents=(req.receipt_ref,att.receipt_ref)
    if execution.status=='failed':
        failure('_provider_response',execution.failure.failure_kind,
            execution.failure.failure_ref,response_parents)
        return evidence
    response=execution.response
    res=artifact('_provider_response','provider-response',response.fingerprint,
        {'request_fingerprint':request.fingerprint,'attempt_fingerprint':attempt.fingerprint},
        response.to_record(),response_parents)
    if parsed.status=='failed':
        failure('_codec_parse',parsed.failure_kind,parsed.parse_ref,(res.receipt_ref,))
    else:
        artifact('_codec_parse','provider-codec-parse',parsed.fingerprint,
            {'provider_response_fingerprint':response.fingerprint,'codec_contract':parsed.codec_contract},
            parsed.to_record(),(res.receipt_ref,))
    return evidence


class PreparedProviderCall:
    """Host-owned phase handle. Only transport may run outside its owner thread."""
    def __init__(self,*,stage_prefix,session_binding,current_position,operation_kind,
            source_fact_ref,codec_contract,request_payload,transport_payload,implementation_ref,
            invocation_evidence,provider_invoke,parse,parent_receipt_refs=()):
        from threading import Lock,get_ident
        from framework.model_api import freeze_model_payload
        self._owner=get_ident();self._lock=Lock();self._dispatched=False
        self._done=False;self._result=None;self._error=None;self._finish_error=None
        self.prefix=str(stage_prefix or '').strip()
        if not self.prefix:raise ValueError('provider lifecycle stage prefix must be non-empty')
        if not callable(parse):raise TypeError('provider lifecycle requires a parse callable')
        if not callable(provider_invoke):raise TypeError('provider transport requires an invoke callable')
        self.request=ProviderRequest.create(session_binding=session_binding,current_position=current_position,
            operation_kind=operation_kind,source_fact_ref=source_fact_ref,codec_contract=codec_contract,
            request_payload=request_payload,transport_options={'response_format':'structured'})
        self._binding=session_binding;self._position=current_position
        self._context=_context_value(session_binding,current_position)
        self._request_value=self.request.to_record()
        self._payload=freeze_model_payload(transport_payload)
        self._evidence=freeze_model_payload({**dict(invocation_evidence),
            'transport_request_fingerprint':model_data_fingerprint(self._payload)})
        self._implementation=implementation_ref;self._invoke=provider_invoke
        self._parse=parse;self._codec=codec_contract;self._parents=tuple(parent_receipt_refs)

    def _unchanged(self):
        if (_context_value(self._binding,self._position)!=self._context
                or self.request.to_record()!=self._request_value):
            raise ValueError('provider callback changed validated runtime context or request')

    def transport(self):
        with self._lock:
            if self._dispatched:raise RuntimeError('provider call already dispatched')
            self._dispatched=True
        try:
            self._unchanged()
            def invoke(actual):
                try:return self._invoke(actual,self._payload)
                finally:self._unchanged()
            self._execution=execute_provider(request=self.request,implementation_ref=self._implementation,
                invocation_evidence=self._evidence,invoke=invoke)
        except BaseException as error:
            self._error=error
            raise
        finally:
            with self._lock:self._done=True

    def finish(self):
        from threading import get_ident
        if get_ident()!=self._owner:raise RuntimeError('provider parse requires owner thread')
        with self._lock:
            if not self._done:raise RuntimeError('provider transport not completed')
        if self._error is not None:raise self._error
        if self._finish_error is not None:raise self._finish_error
        if self._result is not None:return self._result
        try:
            self._unchanged()
            execution=self._execution;parsed=None
            if execution.status!='failed':
                response_value=execution.response.to_record()
                def decode(wire):
                    try:return self._parse(wire)
                    finally:
                        self._unchanged()
                        if execution.response.to_record()!=response_value:
                            raise ValueError('provider parser changed validated response')
                parsed=execute_provider_codec_parse(response=execution.response,
                    codec_contract=self._codec,parse=decode)
            evidence=_project_evidence(self.prefix,self.request,execution,parsed,self._parents)
            self._result=ProviderLifecycleResult(
                status='parsed' if parsed is not None and parsed.status=='parsed' else 'failed',
                request=self.request,execution=execution,parse_receipt=parsed,
                parsed_payload=parsed.parsed_payload if parsed is not None and parsed.status=='parsed' else None,
                evidence=evidence)
            return self._result
        except BaseException as error:
            self._finish_error=error
            raise


def prepare_provider_call(**kwargs):
    return PreparedProviderCall(**kwargs)


def execute_provider_lifecycle(**kwargs):
    """Synchronous compatibility entry uses the same split native phases."""
    call=prepare_provider_call(**kwargs)
    call.transport()
    return call.finish()
