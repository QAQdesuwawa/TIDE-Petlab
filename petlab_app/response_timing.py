"""Read-only operational lineage; never scheduling, simulation or model input."""
from pathlib import Path
import hashlib,json,re


def difference(end,start):
    return end-start if type(end) is int and type(start) is int and end>=start else None


def observe_job(application,gateway,job,effects):
    from .session import PRODUCT_KEY
    sources=application.runtime.session.profile[PRODUCT_KEY]['sources']
    calls=[dict(row) for row in gateway.diagnostic_invocations]
    confirmed=[];refs={row['source_fact_ref'] for row in calls}
    for effect in effects:
        source=sources.get(effect['action_id'],{})
        receipt=source.get('evidence',{}).get('fact',{}).get('model_payload',{}).get('execution_receipt',{})
        origin=receipt.get('realization_evidence',{}).get('expression_delivery',{}).get('event_id')
        confirmed.append({**effect,'source_fact_ref':origin})
        if origin is not None:refs.add(origin)
    windows=[]
    for ref in sorted(refs):
        source=sources.get(ref,{})
        event=source.get('event',{})
        if event.get('event_kind')!='peer_input':continue
        for content in event.get('contents',()):
            p=content.get('parameters',{});points=p.get('trajectory')
            if content.get('kind')!='interaction' or not points:continue
            start,end=points[0][2],points[-1][2]
            origin=end-p.get('total_duration_ms',p.get('duration_ms',end-start))
            if p.get('capture_version')=='petlab-mouse-batch-v5':
                origin=min(part['parameters']['trajectory'][-1][2]-part['parameters']['total_duration_ms'] for part in p['parts'])
            windows.append({'source_fact_ref':ref,'content_id':content['content_id'],'contact_origin_ms':origin,
                'interval_start_ms':start,'interval_end_ms':end,'sealed_at_ms':p.get('sealed_at_ms',end),
                'admitted_at_ms':source.get('evidence',{}).get('position',{}).get('coordinate'),
                'phase':p.get('phase','ended'),'capture_version':p.get('capture_version')})
    return {'diagnostic_job':dict(gateway.diagnostic_job),'provider_invocations':calls,
        'capture_windows':windows,'confirmed_effects':confirmed}


def _load(path):
    try:
        if path.stat().st_size>16*1024*1024:return None
        value=json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else None
    except (OSError,ValueError):return None


def load_response_timing(provider_dir,playback_dir,job):
    result={'providers':[],'responses':[]}
    context=job.get('diagnostic_job')
    if not isinstance(context,dict):return result
    for call in job.get('provider_invocations',()):
        identity=call.get('invocation_id')
        if not isinstance(identity,str) or not re.fullmatch('[0-9a-f]{32}',identity):continue
        row=_load(Path(provider_dir)/(identity+'.json'))
        if row is None or row.get('version')!='petlab-provider-trace-v1' or row.get('invocation_id')!=identity or row.get('diagnostic_job')!=context:continue
        timing=row.get('host_timing') or {}
        result['providers'].append({'invocation_id':identity,'source_fact_ref':row.get('source_fact_ref'),
            'operation_kind':row.get('operation_kind'),'status':row.get('status'),
            'started_at_unix_ms':timing.get('started_at_unix_ms'),'finished_at_unix_ms':timing.get('transport_finished_at_unix_ms'),
            'network_ms':difference(timing.get('transport_finished_at_unix_ms'),timing.get('started_at_unix_ms'))})
    for effect in job.get('confirmed_effects',()):
        action=effect.get('action_id')
        if not isinstance(action,str):continue
        playback=None if playback_dir is None else _load(Path(playback_dir)/(hashlib.sha256(action.encode('utf-8')).hexdigest()+'.json'))
        stamps=[]
        if playback and playback.get('version')=='petlab-desktop-playback-v1' and playback.get('action_id')==action:
            stamps=[row['at_unix_ms'] for row in playback.get('speech_rendered',()) if type(row.get('at_unix_ms')) is int and row.get('content_id') in playback.get('completed_content_ids', ())]
        rendered=min(stamps) if stamps else None
        ref=effect.get('source_fact_ref')
        windows=[w for w in job.get('capture_windows',()) if ref is not None and w['source_fact_ref']==ref]
        origin=min(w['contact_origin_ms'] for w in windows) if windows else None
        end=max(w['interval_end_ms'] for w in windows) if windows else None
        sealed=max(w['sealed_at_ms'] for w in windows) if windows and all(type(w.get('sealed_at_ms')) is int for w in windows) else None
        result['responses'].append({'action_id':action,'source_fact_ref':ref,'rendered_at_unix_ms':rendered,
            'contact_origin_ms':origin,'interval_end_ms':end,'sealed_at_ms':sealed,
            'from_contact_ms':difference(rendered,origin),'after_interval_ms':difference(rendered,end),
            'capture_to_seal_ms':difference(sealed,end),'seal_to_host_receive_ms':difference(job.get('received_at_unix_ms'),sealed),
            'host_queue_ms':difference(job.get('started_at_unix_ms'),job.get('received_at_unix_ms'))})
    return result
