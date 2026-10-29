"""Read the already-selected calibration predecessor; no new recall or state."""
import json
from framework.model_api import model_data_fingerprint

VERSION='desktop-pet-self-measurement-continuity-v1'

def build(runtime,event,reference,*,limit_chars=2048):
    if event.event_kind!='npc_output' or not reference or not reference.get('previous_event_id'):return None
    prior=runtime.memory_instance(reference['previous_event_id'])
    if (prior.fact.fingerprint!=reference['previous_fact_fingerprint'] or prior.event.actor_ref!=event.actor_ref
            or prior.event.event_kind!=event.event_kind or prior.event.position.coordinate>event.position.coordinate
            or {c.kind for c in prior.event.contents}!={c.kind for c in event.contents}):
        raise ValueError('measurement continuity differs from actual calibration predecessor')
    rows=[]
    for content in prior.event.visible_contents(event.owner_ref):
        row={'类型':content.kind,'正文':content.description,'可见性':'私有' if content.private else '公开'}
        if 'execution_status' in content.parameters:row['执行状态']=content.parameters['execution_status']
        rows.append(row)
    text=json.dumps({'实际说话或行动者':'current_npc','内容':rows},ensure_ascii=False)
    record={'version':VERSION,'event_fingerprint':event.fingerprint,
        'previous_event_id':prior.event.event_id,'previous_event_fingerprint':prior.event.fingerprint,
        'previous_fact_fingerprint':prior.fact.fingerprint,'reference_fingerprint':reference['fingerprint'],
        'limit_chars':limit_chars,'source_chars':len(text),'status':'delivered' if len(text)<=limit_chars else 'deferred_budget',
        'text':text if len(text)<=limit_chars else ''}
    record['fingerprint']=model_data_fingerprint(record)
    return record
