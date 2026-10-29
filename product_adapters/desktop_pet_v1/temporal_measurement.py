"""Bind actual observation intervals to one durable semantic provider lifecycle.

No roots, eligibility, mouse capture or provider SDK live in this adapter.
The journal is host provenance, not another History or a psychological input.
"""
from petlab_app.owned_journals import publish_session
from dataclasses import replace
import json
import uuid

from framework.model_api import model_data_fingerprint as fingerprint, thaw_model_data
from framework.provider_host import ProviderRequest,ProviderAttempt,ProviderResponse,ProviderFailure,ProviderExecutionResult
from framework.provider_codec import ProviderCodecParseReceipt
from framework.runtime_kernel import RuntimePositionState
from model_packages.desktop_pet_v1.events import PetEvent
from model_packages.desktop_pet_v1.interaction_delivery import project_parameters
from model_packages.desktop_pet_v1.three_time import ObservationTime
from petlab_app.providers import read_host_timing
from .execution import ExecutionUncertain

KEY='desktop_pet_temporal_measurements_v1'


def signed(value):return {**value,'fingerprint':fingerprint(value)}


def verify(value):
    if value.get('fingerprint')!=fingerprint({k:v for k,v in value.items() if k!='fingerprint'}):
        raise ValueError('temporal measurement journal integrity differs')


def observations(event):
    return tuple(c for c in event.contents if c.kind=='interaction' and
        c.parameters.get('capture_version') in {'petlab-mouse-observation-v3','petlab-mouse-contact-v4','petlab-mouse-batch-v5'})


def publish(app,event_id,row):
    profile=dict(app.runtime.session.profile)
    journal=dict(profile.get(KEY,{}));profile[KEY]=journal
    journal[event_id]=signed(row)
    updated=replace(app.runtime.session,profile=profile)
    publish_session(app,updated)


def capture_position(app,event):
    """Physical occurrence remains distinct from a delayed formal admission."""
    from petlab_app.session import PRODUCT_KEY,ADMISSION_VERSIONS
    evidence=app.runtime.session.profile[PRODUCT_KEY]['sources'][event.event_id]['evidence']
    if evidence.get('version') in ADMISSION_VERSIONS:
        position=app.runtime.temporal_runtime.normalize_position(evidence['capture_position']['coordinate'])
        if position.to_record()!=evidence['capture_position']:raise ValueError('physical capture position differs')
        app.runtime.temporal_runtime.prove_order(position,event.position)
        return position
    return event.position


def verify_capture_chain(app,event):
    """Check actual source intervals, including captures made before this journal."""
    physical_end=capture_position(app,event).coordinate
    for content in observations(event):
        p=content.parameters;project_parameters(p)
        first,last=p['trajectory'][0],p['trajectory'][-1]
        if p.get('capture_version')=='petlab-mouse-batch-v5':
            if last[2]!=physical_end:raise ValueError('batch end differs from physical capture position')
            continue
        sequence=p['sequence'];total=p['total_duration_ms']
        if (isinstance(sequence,bool) or not isinstance(sequence,int) or sequence<1
                or isinstance(total,bool) or not isinstance(total,int) or total<0
                or last[2]!=physical_end or p['interval_duration_ms']!=last[2]-first[2]):
            raise ValueError('capture interval or identity differs')
        previous=[]
        from petlab_app.session import PRODUCT_KEY
        # Read-only scan under the application's serial transaction; matching
        # records below still undergo their original source validation.
        for other_id,source in app.runtime.session.profile.get(PRODUCT_KEY,{}).get('sources',{}).items():
            if other_id==event.event_id:continue
            # Sources were validated when received/restored. This chain is
            # scoped to the same physical gesture; decode and reverify matching
            # candidates, not every unrelated event in the archive.
            raw_event=source['event']
            if not any(c.get('kind')=='interaction' and c.get('parameters',{}).get('gesture_id')==p['gesture_id']
                    for c in raw_event['contents']):continue
            other=PetEvent.from_record(raw_event)
            for c in other.contents:
                q=c.parameters
                if c.kind=='interaction' and q.get('gesture_id')==p['gesture_id']:
                    if q.get('sequence')==sequence:raise ValueError('observation sequence already belongs to another event')
                    if q.get('sequence')==sequence-1:previous.append((other,q))
        if sequence==1:
            if first[2]!=last[2]-total:raise ValueError('first observation does not start at interaction origin')
        else:
            if len(previous)!=1:raise ValueError('observation lacks one previous captured interval')
            other,q=previous[0]
            if (q['phase'] not in {'ongoing','started'} or tuple(q['trajectory'][-1])!=tuple(first)
                    or other.position.binding!=event.position.binding
                    or q['trajectory'][-1][2]-q['total_duration_ms']!=last[2]-total):
                raise ValueError('observation interval has a gap, overlap or changed origin')


def coordinates(event,*,started,completed,completion_ref,physical_end=None):
    if event.position.binding.domain_id!='tide.wall-clock-ms':
        raise ValueError('provider UTC coordinates require the wall-clock domain')
    domain=fingerprint(event.position.binding.to_record())
    physical_end=event.position.coordinate if physical_end is None else physical_end
    result=[]
    for content in observations(event):
        p=content.parameters
        project_parameters(p)  # exact feature/source check, never an extra model
        first,last=p['trajectory'][0],p['trajectory'][-1]
        if last[2]!=physical_end or p['interval_duration_ms']!=last[2]-first[2]:
            raise ValueError('observed interval differs from formal event position')
        origin=last[2]-p['total_duration_ms']
        value=ObservationTime(interaction_id=p['gesture_id'],observation_id=content.content_id,
            sequence=p['sequence'],time_domain=domain,origin_ms=origin,
            world_start_ms=first[2],world_end_ms=last[2],local_start_ms=first[2]-origin,
            local_end_ms=last[2]-origin,query_started_ms=started,
            source_ref=fingerprint(content.to_record()))
        result.append(value.complete(completed,receipt_ref=completion_ref).to_record())
    return result


def load(app,gateway,event,row,*,allow_failed=False):
    row=thaw_model_data(row)
    verify(row)
    if row['event_fingerprint']!=event.fingerprint or row['source_receipt_ref']!=event.source_receipt_ref:
        raise ValueError('temporal query source differs from actual event')
    nonce=row['invocation_id']
    if not isinstance(nonce,str) or len(nonce)!=32 or any(c not in '0123456789abcdef' for c in nonce):
        raise ValueError('temporal invocation id is invalid')
    path=gateway.trace_dir/(nonce+'.json')
    if not path.exists():raise ExecutionUncertain('semantic query has no complete provider receipt; reconciliation required')
    text=path.read_text(encoding='utf-8')
    record=json.loads(text)
    if record.get('status') not in {'parsed','failed'}:
        raise ExecutionUncertain('semantic query is not parsed; no automatic redispatch')
    native=(gateway._current_native_trace(path,text,record) if hasattr(gateway,'_current_native_trace') else None)
    execution=record['execution']
    request=ProviderRequest(**execution['request']) if native is None else native.request
    snap=row['runtime_position']
    position=RuntimePositionState.create(session_binding=app.runtime.session.session_binding,
        position=app.runtime.temporal_runtime.normalize_position(snap['position']['coordinate']),
        runtime_ordinal=snap['runtime_ordinal'])
    if position.to_record()!=snap:raise ValueError('temporal query runtime position differs')
    request.verify_integrity(session_binding=app.runtime.session.session_binding,current_position=position)
    if native is None:
        attempt=ProviderAttempt(**execution['attempt'])
        response=None if execution['response'] is None else ProviderResponse(**execution['response'])
        failure=None if execution['failure'] is None else ProviderFailure(**execution['failure'])
        ProviderExecutionResult(version=request.version,status=execution['receipt']['status'],request=request,
            attempt=attempt,response=response,failure=failure,receipt=execution['receipt']).verify_integrity()
        parsed=None if record['codec_parse'] is None else ProviderCodecParseReceipt(**record['codec_parse'])
        if parsed is not None:
            if response is None:raise ValueError('codec receipt lacks provider response')
            parsed.verify_integrity(response=response)
    else:
        attempt=native.execution.attempt;response=native.execution.response
        failure=native.execution.failure;parsed=native.parse_receipt
    if (record['status']=='parsed' and (parsed is None or parsed.status!='parsed')) or (
            record['status']=='failed' and failure is None and (parsed is None or parsed.status!='failed')):
        raise ValueError('provider terminal status differs from actual receipts')
    if (record['invocation_id']!=nonce or request.source_fact_ref!=event.event_id
            or request.operation_kind!='semantic_measurement'
            or request.codec_contract!=row['codec_contract'] or (parsed is not None and parsed.codec_contract!=row['codec_contract'])
            or fingerprint(request.request_payload)!=row['request_payload_fingerprint']
            or attempt.invocation_evidence.get('invocation_id')!=nonce
            or attempt.invocation_evidence.get('transport_binding_fingerprint')!=row['transport_binding_fingerprint']
            or fingerprint(record['transport_binding'])!=row['transport_binding_fingerprint']
            or (response is not None and fingerprint(response.response_payload)!=fingerprint(record['wire_response']))):
        raise ValueError('temporal provider lifecycle differs from reserved source')
    timing=read_host_timing(record,expected_request_fingerprint=request.fingerprint)
    processing=record.get('host_processing')
    if timing is None or processing is None:raise ValueError('temporal query lacks actual processing timestamps')
    verify(processing)
    if (processing['version']!='petlab-provider-processing-time-v1' or processing['clock']!='unix-ms'
            or processing['status']!=record['status'] or processing['invocation_id']!=nonce
            or processing['provider_request_fingerprint']!=request.fingerprint
            or processing['codec_parse_fingerprint']!=(None if parsed is None else parsed.fingerprint)
            or processing['execution_receipt_fingerprint']!=execution['receipt']['fingerprint']):
        raise ValueError('processing completion is not bound to its codec receipt')
    end=processing['completed_at_unix_ms']
    if (isinstance(end,bool) or not isinstance(end,int)
            or timing['transport_finished_at_unix_ms'] is None or end<timing['transport_finished_at_unix_ms']):
        raise ValueError('processing completion clock order differs')
    if record['status']=='failed':
        if not allow_failed:raise ExecutionUncertain('semantic query has a confirmed failure; use explicit resume')
        complete={**{k:v for k,v in row.items() if k!='fingerprint'},'status':'failed',
            'provider_trace_fingerprint':fingerprint(record),
            'failure_receipt_ref':failure.fingerprint if failure is not None else parsed.fingerprint}
        if row['status']=='failed' and signed(complete)!=thaw_model_data(row):
            raise ValueError('failed attempt differs from archived failure')
        return complete,None
    if timing['response_received_at_unix_ms'] is None:raise ValueError('parsed query lacks response arrival')
    times=coordinates(event,started=timing['started_at_unix_ms'],completed=end,completion_ref=parsed.fingerprint,physical_end=capture_position(app,event).coordinate)
    complete={**{k:v for k,v in row.items() if k!='fingerprint'},'status':'parsed',
        'provider_trace_fingerprint':fingerprint(record),'observations':times,
        'measurement_fingerprint':fingerprint(parsed.parsed_payload)}
    if row['status']=='parsed' and signed(complete)!=thaw_model_data(row):
        raise ValueError('saved temporal association differs from provider/source evidence')
    return complete,thaw_model_data(parsed.parsed_payload)


def measure(app,gateway,event,*,codec_contract,messages,temperature,require_json,parse,retry_failed=False):
    verify_capture_chain(app,event)
    journal=app.runtime.session.profile.get(KEY,{})
    row=journal.get(event.event_id)
    failures=[]
    if row is not None and retry_failed and row['status']=='reserved':
        completed,payload=load(app,gateway,event,row,allow_failed=True)
        if payload is None:
            failures=[*thaw_model_data(row.get('failed_attempts',())),
                signed({k:v for k,v in completed.items() if k!='failed_attempts'})]
            row=None
    if row is None:
        row=signed({'version':KEY,'status':'reserved','event_fingerprint':event.fingerprint,
            'source_receipt_ref':event.source_receipt_ref,'invocation_id':uuid.uuid4().hex,
            'runtime_position':app.runtime.session.position.to_record(),'codec_contract':codec_contract,
            'failed_attempts':failures,
            'transport_binding_fingerprint':fingerprint(gateway.transport.binding),
            'request_payload_fingerprint':fingerprint(gateway.transport.payload(messages=messages,
                temperature=temperature,require_json=require_json))})
        # Durable identity precedes any dispatch; a crash can never silently
        # turn this logical query into a second paid invocation.
        publish(app,event.event_id,{k:v for k,v in row.items() if k!='fingerprint'})
        gateway.run(session_binding=app.runtime.session.session_binding,current_position=app.runtime.session.position,
            operation_kind='semantic_measurement',source_fact_ref=event.event_id,codec_contract=codec_contract,
            messages=messages,temperature=temperature,require_json=require_json,parse=parse,
            source_receipt_refs=(event.source_receipt_ref,),invocation_id=row['invocation_id'])
    complete,payload=load(app,gateway,event,row)
    if row['status']!='parsed':publish(app,event.event_id,complete)
    return payload


def verify_saved(app,gateway):
    from petlab_app.session import PRODUCT_KEY
    sources=app.runtime.session.profile.get(PRODUCT_KEY,{}).get('sources',{})
    for event_id,row in app.runtime.session.profile.get(KEY,{}).items():
        verify(row)
        if row.get('version')!=KEY or row.get('status') not in {'reserved','parsed'} or event_id not in sources:
            raise ValueError('temporal measurement journal source/status is invalid')
        event=PetEvent.from_record(sources[event_id]['event'])
        if not observations(event) or row['event_fingerprint']!=event.fingerprint:
            raise ValueError('temporal query does not belong to its observation')
        verify_capture_chain(app,event)
        for failed in row.get('failed_attempts',()):
            if failed.get('status')!='failed':raise ValueError('attempt history lacks failure receipt')
            _old,payload=load(app,gateway,event,failed,allow_failed=True)
            if payload is not None:raise ValueError('successful attempt cannot be redispatched')
        if row['status']=='parsed':
            _complete,payload=load(app,gateway,event,row)
            cached=sources[event_id].get('measurement')
            if cached is not None and fingerprint(cached)!=fingerprint(payload):
                raise ValueError('settled measurement differs from temporal query')
