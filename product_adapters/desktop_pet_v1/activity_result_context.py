"""Read-only actual results of selected activity memories, never extra recall.

An action intention is not its result. Follow the already sealed source edge,
then authorize the actual observed child separately. No root values are read.
"""
import json
from framework.model_api import model_data_fingerprint as fp
VERSION='pet-selected-activity-results-v1'
LIMIT_CHARS=1600


def readable(row):
    from datetime import datetime,timezone
    coverage=row['coverage'];complete=coverage['excerpt_chars']==coverage['read_span'][1]-coverage['read_span'][0]
    return {'阅读时间':datetime.fromtimestamp(row['observed_at_ms']/1000,timezone.utc).isoformat(),
            '网页标题':row['title'],'来源网址':row['url'],'已读原文':row['excerpt'],
            '交付范围':'本次已读片段全文' if complete else '本次已读片段的前'+str(coverage['excerpt_chars'])+'字'}


def build(app,event_id,selected_ids,readers):
    selected=set(selected_ids)
    if not selected:return None
    from petlab_app.session import PRODUCT_KEY
    history=app.runtime._history();source=app.runtime.memory_instance(event_id)
    facts=history.facts();product=app.runtime.session.profile.get(PRODUCT_KEY,{})
    cache=getattr(app,'_activity_result_index',None)
    if cache is None or cache[0]>len(facts) or (cache[0] and facts[cache[0]-1].fingerprint!=cache[1]):
        cache=(0,None,{})
    indexed,_,links=cache
    for fact in facts[indexed:]:
        row=product.get('sources',{}).get(fact.fact_id,{})
        receipt=row.get('evidence',{}).get('input_source',{})
        if receipt.get('kind')!='web_page' or not receipt.get('activity_source'):continue
        activity=receipt['activity_source'];parent=activity.get('activity',activity).get('event_id')
        if parent:links.setdefault(parent,[]).append(fact.fact_id)
    app._activity_result_index=(len(facts),facts[-1].fingerprint if facts else None,links)
    ids={i for parent in selected for i in links.get(parent,()) if i not in selected}
    ordered=sorted(ids,key=lambda i:history.fact(i).accepted_runtime_ordinal,reverse=True)
    rows=[];refs=[];used=[];deferred=[]
    from petlab_app.writer_visibility import public_observation
    for identity in ordered:
        memory=app.runtime.memory_instance(identity);event=memory.event
        if (memory.fact.accepted_runtime_ordinal>=source.fact.accepted_runtime_ordinal
            or event.owner_ref!=app.owner_ref or event.scope_ref!=source.event.scope_ref):continue
        receipt=public_observation(app,memory)
        if receipt is None:continue
        activity=receipt['activity_source']
        parent=activity.get('activity',activity)['event_id']
        if parent not in selected:raise ValueError('observation parent differs from selected activity')
        text=event.contents[0].description.split('网页原文：\n',1)[1]
        row={'event_id':identity,'parent_event_id':parent,'source_receipt_fingerprint':receipt['fingerprint'],
             'observed_at_ms':event.position.coordinate,'title':receipt['origin']['title'][:180],
             'url':receipt['origin']['url'][:512],'excerpt':text[:1000],
             'coverage':{'read_span':list(receipt['read_span']),'source_chars':receipt['source_chars'],'excerpt_chars':min(1000,len(text))}}
        def size():return len(json.dumps([*(readable(r) for r in rows),readable(row)],ensure_ascii=False,separators=(',',':')))
        overflow=size()-LIMIT_CHARS
        if overflow>0:
            row['excerpt']=row['excerpt'][:max(0,len(row['excerpt'])-overflow)]
            row['coverage']['excerpt_chars']=len(row['excerpt'])
        if not row['excerpt'] or size()>LIMIT_CHARS:
            deferred.append(identity);continue
        rows.append(row);used.append(identity);refs.extend((memory.fact.fingerprint,history.fact(parent).fingerprint))
    if not rows:return None
    read=history.authorize_read(reader_ref=app.owner_ref,trigger_fact_id=event_id,
        before_runtime_ordinal=source.fact.accepted_runtime_ordinal,requested_fact_ids=tuple(used),
        authorization_ref={'version':VERSION,'selected_activity_ids':sorted(selected)})
    value={'version':VERSION,'source_event_id':event_id,'results':rows,'source_refs':refs,
        'read_receipt_fingerprint':read.fingerprint,'limit_chars':LIMIT_CHARS,'deferred_event_ids':deferred,
        'formal_recall':False,'state_commit_authority':False}
    return {**value,'fingerprint':fp(value)}
