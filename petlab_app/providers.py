"""One explicitly bound Chat Completions transport and generic provider host."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass,field,fields
import json
import logging
import math
import os
from pathlib import Path
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

from compatibility.windows_proxy import provider_proxy_handler
from framework.model_api import model_data_fingerprint,thaw_model_data
from framework.provider_host import ProviderInvocationFailure
from .provider_lifecycle import execute_provider_lifecycle
from framework.provider_codec import ProviderCodecParseFailureSignal


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None


@dataclass(frozen=True,kw_only=True)
class ChatTransport:
    endpoint:str
    model:str
    api_key:str|None=field(default=None,repr=False)
    timeout_s:float=60.0
    json_mode:bool=True
    send_temperature:bool=True
    thinking_mode:str|None=None

    def __post_init__(self):
        if self.thinking_mode is not None and (not isinstance(self.thinking_mode,str) or self.thinking_mode not in {"enabled","disabled"}):
            raise ValueError("provider thinking mode must be enabled, disabled or None")
        url=urllib.parse.urlsplit(self.endpoint)
        if url.scheme not in {"http","https"} or not url.hostname or url.username or url.password or url.fragment:
            raise ValueError("provider endpoint must be an explicit HTTP URL without embedded credentials")
        if not isinstance(self.model,str) or not self.model.strip():raise ValueError("provider model is missing")
        if self.api_key is not None and (not isinstance(self.api_key,str) or not self.api_key.strip()):
            raise ValueError("provider key must be a configured non-empty value or None")
        if isinstance(self.timeout_s,bool) or not math.isfinite(self.timeout_s) or self.timeout_s<=0:
            raise ValueError("provider timeout is invalid")
        if not isinstance(self.json_mode,bool) or not isinstance(self.send_temperature,bool):
            raise ValueError("provider wire options must be explicit booleans")

    @property
    def binding(self):
        binding={"version":"petlab-chat-completions-v1","endpoint":self.endpoint,"model":self.model,
            "timeout_s":self.timeout_s,"json_mode":self.json_mode,"send_temperature":self.send_temperature}
        # Omission preserves archived legacy request/binding fingerprints.
        if self.thinking_mode is not None:binding["thinking_mode"]=self.thinking_mode
        return binding

    def payload(self,*,messages,temperature,require_json):
        if not isinstance(messages,(list,tuple)) or not messages:raise ValueError("provider messages are missing")
        body={"model":self.model,"messages":thaw_model_data(messages),"stream":False}
        if self.thinking_mode is not None:body["thinking"]={"type":self.thinking_mode}
        if self.send_temperature:body["temperature"]=temperature
        if require_json and self.json_mode:body["response_format"]={"type":"json_object"}
        return body

    def __call__(self,request,payload):
        from .transport_timing import mark
        from .pooled_transport import post
        import httpx
        mark('transport_started')
        if payload.get("model")!=self.model:raise ValueError("provider payload changed the selected model")
        headers={"Content-Type":"application/json"}
        if self.api_key is not None:headers["Authorization"]="Bearer "+self.api_key
        data=json.dumps(thaw_model_data(payload),ensure_ascii=False,allow_nan=False).encode("utf-8")
        mark('payload_ready')
        try:
            # No automatic retry or cross-endpoint redirect changes the explicit provider binding.
            status,raw=post(self.endpoint,data,headers,self.timeout_s)
        except (httpx.HTTPError,TimeoutError,OSError):
            raise ProviderInvocationFailure(failure_kind="transport_unavailable",failure_source_ref=request.request_ref) from None
        if not 200<=status<300:
            raise ProviderInvocationFailure(failure_kind="http_"+str(status),failure_source_ref=request.request_ref)
        try:
            wire=json.loads(raw.decode('utf-8'),parse_constant=lambda value:(_ for _ in ()).throw(ValueError(value)))
        except (ValueError,UnicodeError):
            raise ProviderInvocationFailure(failure_kind="invalid_wire_json",failure_source_ref=request.request_ref) from None
        if not isinstance(wire,dict):
            raise ProviderInvocationFailure(failure_kind="invalid_wire_shape",failure_source_ref=request.request_ref)
        mark('decoded')
        return wire


def completion_text(wire):
    try:
        choices=wire["choices"]
        if not isinstance(choices,(list,tuple)) or len(choices)!=1:raise ValueError("completion choice count differs")
        choice=choices[0]
        message=choice["message"]
        text=message["content"]
        if (choice.get("finish_reason")!="stop" or not isinstance(text,str) or not text.strip()
                or message.get("tool_calls") or message.get("refusal")):
            raise ValueError("completion is not a complete text response")
        return text
    except (KeyError,TypeError,ValueError):
        raise ProviderCodecParseFailureSignal(failure_kind="incomplete_text_completion",
            failure_source_ref=model_data_fingerprint(wire)) from None


def _save_trace(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,temporary=tempfile.mkstemp(prefix=path.name+".",suffix=".tmp",dir=path.parent)
    serialized=False
    try:
        with os.fdopen(fd,"w",encoding="utf-8") as output:
            encoded=json.dumps(value,ensure_ascii=False,allow_nan=False)
            output.write(encoded)
            output.flush();os.fsync(output.fileno())
        serialized=True
        from .atomic_replace import replace
        replace(temporary,path)
    except OSError as error:
        if serialized:error.add_note('Complete trace retained for reconciliation: '+temporary)
        raise
    finally:
        # An unpublished complete response may be the only copy of a paid call.
        # Keep it for reconciliation; never treat it as a published receipt.
        if not serialized and os.path.exists(temporary):os.unlink(temporary)
    return encoded


def read_host_timing(record, *, expected_request_fingerprint):
    """Validate persisted local timing against its exact provider request/wire.

    Old traces legitimately lack this evidence. Never infer their start time
    from duration or provider-created timestamps.
    """
    row=record.get('host_timing')
    if row is None:return None
    raw={key:value for key,value in row.items() if key!='fingerprint'}
    request=record.get('provider_request',{})
    if (row.get('fingerprint')!=model_data_fingerprint(raw)
            or row.get('version')!='petlab-provider-host-time-v1' or row.get('clock')!='unix-ms'
            or row.get('invocation_id')!=record.get('invocation_id')
            or row.get('provider_request_fingerprint')!=expected_request_fingerprint
            or request.get('fingerprint')!=expected_request_fingerprint
            or model_data_fingerprint({k:v for k,v in request.items() if k!='fingerprint'})!=expected_request_fingerprint):
        raise ValueError('provider timing source integrity differs')
    start=row.get('started_at_unix_ms');end=row.get('transport_finished_at_unix_ms');received=row.get('response_received_at_unix_ms')
    for value in (start,end,received):
        if value is not None and (isinstance(value,bool) or not isinstance(value,int)):
            raise ValueError('provider timing requires integer coordinates')
    if start is None or (end is not None and end<start) or (received is not None and (end is None or not start<=received<=end)):
        raise ValueError('provider clock order differs; no timestamp clamping allowed')
    if received is not None and row.get('wire_fingerprint')!=model_data_fingerprint(record.get('wire_response')):
        raise ValueError('provider timing response differs')
    return dict(row)


def _native_fields(result):
    execution=result.execution
    return {"execution":{"request":result.request.to_record(),
        "attempt":execution.attempt.to_record(),
        "response":None if execution.response is None else execution.response.to_record(),
        "failure":None if execution.failure is None else execution.failure.to_record(),
        "receipt":thaw_model_data(execution.receipt)},
        "codec_parse":None if result.parse_receipt is None else result.parse_receipt.to_record()}


class ProviderGateway:
    def __init__(self,transport,*,trace_dir,usage_sink=None,operation_transports=None):
        if not callable(transport) or not callable(getattr(transport,"payload",None)):
            raise TypeError("provider gateway requires an explicitly bound transport")
        if usage_sink is not None and not callable(usage_sink):
            raise TypeError("usage sink must be callable or None")
        self.transport=transport
        self._operation_transports=dict(operation_transports or {})
        if any(not isinstance(kind,str) or not kind.strip() or not callable(bound)
               or not callable(getattr(bound,'payload',None)) for kind,bound in self._operation_transports.items()):
            raise TypeError('operation transports require named, explicitly bound transports')
        self.trace_dir=Path(trace_dir).resolve()
        self.usage_sink=usage_sink
        self._readonly_views=None
        self._native_trace=None
        self.last_failure=None  # Operational display only; never used for dispatch.
        self.diagnostic_job=None;self.diagnostic_invocations=[]

    def readonly_views_scope(self):
        from .provider_views import scope
        return scope(self)

    def archive_readonly(self,path,record):
        from .provider_views import archive
        return archive(self,path,record)

    def close(self):
        self._native_trace=None
        if self._readonly_views is not None:
            self._readonly_views.close();self._readonly_views=None

    def _current_native_trace(self,path,text,record):
        """Reuse only our last exact native return and its actual durable bytes."""
        from framework.provider_host import ProviderExecutionResult
        from framework.provider_codec import ProviderCodecParseReceipt
        cached=self._native_trace
        if (cached is None or cached[0]!=Path(path) or not isinstance(cached[1],str)
                or cached[3] is not ProviderExecutionResult.verify_integrity
                or cached[4] is not ProviderCodecParseReceipt.verify_integrity):return None
        if text!=cached[1]:raise ValueError('current provider trace actual bytes changed')
        result=cached[2]
        # The archive bytes are already exact; use the existing type-sensitive
        # model-data comparator rather than hashing the same two trees again.
        from .checkpoint_journal import _same
        if (result.status!=record['status'] or result.execution.version!=result.request.version
                or result.execution.status!=record['execution']['receipt']['status']
                or result.execution.request is not result.request
                or not _same(_native_fields(result),
                    {'execution':record['execution'],'codec_parse':record['codec_parse']})):
            raise ValueError('current native provider result differs from durable trace')
        return result

    def transport_for(self,operation_kind):
        return self._operation_transports.get(operation_kind,self.transport)

    def _record_usage(self,wire,*,record,path,operation_kind,completed_at,persist=True,deliver=True):
        """Observe the actual response once; missing usage is never estimated."""
        usage=wire.get("usage") if isinstance(wire,Mapping) else None
        actual=usage if isinstance(usage,Mapping) else {}
        row={"version":"petlab-provider-usage-v1","call_id":record["invocation_id"],
            "operation_kind":operation_kind,"model":record['request_payload'].get('model'),
            "served_model":wire.get("model") if isinstance(wire,Mapping) else None,
            "completed_at_unix_ms":completed_at,"usage_status":"observed" if actual else "missing",
            "prompt_tokens":actual.get("prompt_tokens"),"completion_tokens":actual.get("completion_tokens"),
            "total_tokens":actual.get("total_tokens"),
            "prompt_cache_hit_tokens":actual.get("prompt_cache_hit_tokens"),
            "prompt_cache_miss_tokens":actual.get("prompt_cache_miss_tokens"),
            "cost_status":"unpriced","cost_reason":"no_frozen_price_card"}
        record["usage_record"]=row
        record["usage_delivery"]={"status":"not_configured" if self.usage_sink is None else "pending"}
        # The received-wire write may publish usage in the same durable record.
        if persist:_save_trace(path,record)
        if deliver:self._deliver_usage(record=record,path=path)

    def _deliver_usage(self,*,record,path):
        if self.usage_sink is not None:
            row=record['usage_record']
            try:
                self.usage_sink(thaw_model_data(row))
            except Exception as exc:
                record["usage_delivery"]={"status":"failed","error_type":type(exc).__name__}
                logging.getLogger(__name__).error("usage_receipt_delivery_failed error_type=%s",type(exc).__name__)
            else:
                record["usage_delivery"]={"status":"delivered"}
            _save_trace(path,record)

    def run(self,*,session_binding,current_position,operation_kind,source_fact_ref,codec_contract,
            messages,temperature,require_json,parse,source_receipt_refs=(),invocation_id=None):
        self.last_failure=None
        self._native_trace=None
        from .ordered_checkpoint import flush_active_checkpoint
        flush_active_checkpoint()
        nonce=uuid.uuid4().hex if invocation_id is None else invocation_id
        if not isinstance(nonce,str) or len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):
            raise ValueError('provider invocation id must be exact lowercase UUID hex')
        path=self.trace_dir/(nonce+".json")
        if path.exists():raise ValueError('provider invocation already exists; restore its receipt')
        transport=self.transport_for(operation_kind)
        body=transport.payload(messages=messages,temperature=temperature,require_json=require_json)
        binding=dict(transport.binding)
        record={"version":"petlab-provider-trace-v1","status":"prepared","invocation_id":nonce,
            "transport_binding":binding,"request_payload":thaw_model_data(body),"source_fact_ref":source_fact_ref,
            "operation_kind":operation_kind}
        if self.diagnostic_job is not None:
            record['diagnostic_job']=dict(self.diagnostic_job)
            self.diagnostic_invocations.append({'invocation_id':nonce,'source_fact_ref':source_fact_ref,'operation_kind':operation_kind})
        from .provider_views import take,publish,VERSION as READONLY_VIEWS_VERSION
        readonly_views=take(self)
        if readonly_views:
            record['readonly_views_version']=READONLY_VIEWS_VERSION
            record['readonly_views']=thaw_model_data(readonly_views)
        # No external effect exists yet: prepared material is included in the
        # first durable started record, before the transport is invoked.
        def invoke(request,payload):
            record["status"]="started"
            record["provider_request"]=request.to_record()
            timing={"version":"petlab-provider-host-time-v1","clock":"unix-ms",
                "invocation_id":nonce,"provider_request_fingerprint":request.fingerprint,
                "started_at_unix_ms":time.time_ns()//1_000_000,
                "transport_finished_at_unix_ms":None,"response_received_at_unix_ms":None,
                "wire_fingerprint":None}
            def stamp_timing():
                record["host_timing"]={**timing,"fingerprint":model_data_fingerprint(timing)}
            stamp_timing()
            _save_trace(path,record)
            # The complete view sources are now durable in this trace. Derived
            # independent files may fail without invalidating the native call.
            try:publish(self,readonly_views)
            except Exception as error:
                logging.getLogger(__name__).error('readonly_view_queue_failed error_type=%s',type(error).__name__)
            started=time.perf_counter()
            from .transport_timing import capture
            phases=None
            try:
                with capture() as phases:
                    wire=transport(request,payload)
                timing["response_received_at_unix_ms"]=time.time_ns()//1_000_000
                timing["wire_fingerprint"]=model_data_fingerprint(wire)
                # The first post-transport publication must already contain the
                # body: even its timing write can hit a persistent reader lock.
                record["wire_response"]=thaw_model_data(wire)
                record["status"]="received"
                self._record_usage(wire,record=record,path=path,operation_kind=operation_kind,
                    completed_at=time.time_ns()//1_000_000,persist=False,deliver=False)
            finally:
                timing["transport_finished_at_unix_ms"]=time.time_ns()//1_000_000
                stamp_timing()
                # Diagnostics only: never a temporal coordinate or model input.
                record["diagnostics"]={"transport_seconds":time.perf_counter()-started}
                if phases is not None and phases.events:
                    record['diagnostics']['http_phases']=phases.record()
                _save_trace(path,record)
            self._deliver_usage(record=record,path=path)
            return wire
        result=execute_provider_lifecycle(stage_prefix="desktop_pet_"+operation_kind,
            session_binding=session_binding,current_position=current_position,operation_kind=operation_kind,
            source_fact_ref=source_fact_ref,codec_contract=codec_contract,request_payload=body,
            transport_payload=body,implementation_ref=binding["version"],
            invocation_evidence={"invocation_id":nonce,"transport_binding_fingerprint":model_data_fingerprint(binding)},
            provider_invoke=invoke,parse=parse,parent_receipt_refs=source_receipt_refs)
        execution=result.execution
        record.update({"status":result.status,**_native_fields(result),
            "evidence":{key:thaw_model_data({item.name:getattr(value,item.name) for item in fields(value)})
                        for key,value in result.evidence.items()}})
        processing={"version":"petlab-provider-processing-time-v1","clock":"unix-ms",
            "invocation_id":nonce,"provider_request_fingerprint":result.request.fingerprint,
            "status":result.status,"completed_at_unix_ms":time.time_ns()//1_000_000,
            "execution_receipt_fingerprint":record['execution']['receipt']['fingerprint'],
            "codec_parse_fingerprint":None if result.parse_receipt is None else result.parse_receipt.fingerprint}
        record['host_processing']={**processing,'fingerprint':model_data_fingerprint(processing)}
        encoded=_save_trace(path,record)
        from framework.provider_host import ProviderExecutionResult
        from framework.provider_codec import ProviderCodecParseReceipt
        self._native_trace=(path,encoded,result,ProviderExecutionResult.verify_integrity,ProviderCodecParseReceipt.verify_integrity)
        from .provider_diagnostics import failure_summary
        self.last_failure=failure_summary(record)
        return result
