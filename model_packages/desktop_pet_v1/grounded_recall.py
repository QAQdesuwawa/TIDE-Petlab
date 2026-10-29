"""Pet-only grounded content/retention recall. Original CX67 stays frozen."""
import math,json
from collections.abc import Mapping
from dataclasses import replace
from framework.model_api import model_data_fingerprint,thaw_model_data
from model_packages.cx_six_seven_v1.root_dynamics import ROOT_ORDER,RECOVERABLE_AXES,flow_root_time
from model_packages.cx_six_seven_v1.continuous_root_projection import PROJECTION_AXES
from .events import PetEvent,MemoryInstance

VERSION='desktop-pet-grounded-retention-recall-v2'
KEY='pet_memory_recall'
_REGISTRY={}


def signatures(event):
    out=set()
    def walk(content):
        p=content.parameters
        if p.get('capture_version')=='petlab-mouse-batch-v5':
            from .events import Content
            for part in p['parts']:walk(Content.from_record(part))
        elif content.kind=='interaction':
            target=p.get('body_target') or {}
            action=(p.get('gesture_definition') or {}).get('semantic_key',content.semantic_key)
            out.add((action,target.get('region_id')))
    for c in event.contents:walk(c)
    return [list(x) for x in sorted(out,key=str)]


def make_cue(event,parsed):
    from .semantic import evidence_source
    text=evidence_source(event);phrases=[]
    for c in parsed.get('cues',()):
        a,b=c['span'];term=c['text']
        if (type(a)is not int or type(b)is not int or not 0<=a<b<=len(text)
                or term not in text[a:b] or text.count(text[a:b])!=1):
            raise ValueError('recall cue lacks unique actual source evidence')
        if term not in phrases:phrases.append(term)
    from .reference_window import VERSION as WINDOW_VERSION
    if parsed.get('reference_version')==WINDOW_VERSION:
        from .reference_window import referenced_phrases
        from .reference_scope import retained_phrases
        resolved=referenced_phrases(event,parsed,_REGISTRY)
        phrases=retained_phrases(event,parsed)
        for term in resolved:
            if term not in phrases:phrases.append(term)
    elif 'reference_version' in parsed:
        from .reference_cues import referenced_phrases,VERSION as REFERENCE_VERSION,SLOT_VERSION
        resolved=referenced_phrases(event,parsed,_REGISTRY)
        if parsed['reference_version']==REFERENCE_VERSION:
            from .reference_scope import retained_phrases
            phrases=retained_phrases(event,parsed)
        elif parsed['reference_version']==SLOT_VERSION:
            # Substitute the resolved source slot, retaining independent clauses.
            slots=[c['span'] for c in parsed['reference_cues']]
            phrases=list(dict.fromkeys(c['text'] for c in parsed.get('cues',())
                if not any(a<=c['span'][0] and c['span'][1]<=b for a,b in slots)))
        for term in resolved:
            if term not in phrases:phrases.append(term)
    return {'version':VERSION,'event_id':event.event_id,'event_fingerprint':event.fingerprint,
            'source_ref':event.source_binding_ref,'phrases':phrases,'signatures':signatures(event)}


def parse_cues(raw,event):
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    from .semantic import evidence_source
    data=json.loads(_strip_fences(raw));items=data.get('memory_cues',[]);text=evidence_source(event)
    if not isinstance(items,list) or len(items)>16:raise ValueError('memory cue output exceeds contract')
    cues=[];rejected=0
    for item in items:
        if not isinstance(item,dict):rejected+=1;continue
        term=item.get('text');quote=item.get('evidence_text')
        if (not isinstance(term,str) or not term.strip() or len(term)>48
                or not isinstance(quote,str) or not quote or len(quote)>256
                or term not in quote or text.count(quote)!=1):rejected+=1;continue
        at=text.index(quote);entry={'text':term,'span':[at,at+len(quote)]}
        if entry not in cues:cues.append(entry)
    return {'version':VERSION,'cues':cues,'rejected_cue_count':rejected}


def register_event(event,fact):
    key=(fact.session_binding_fingerprint,fact.fingerprint)
    if key in _REGISTRY:return
    MemoryInstance(event,fact)
    from .semantic import evidence_source
    _REGISTRY[key]={'event_id':event.event_id,'event_fingerprint':event.fingerprint,
        'source_ref':event.source_binding_ref,'text':evidence_source(event),'signatures':signatures(event),
        'actor_ref':event.actor_ref,'owner_ref':event.owner_ref,'scope_ref':event.scope_ref,'event_kind':event.event_kind,
        'coordinate':event.position.coordinate,'content_kinds':sorted({c.kind for c in event.contents})}


def register_profile(profile,history=None):
    from ops.runtime_full_memory_profile_commit import FRAMEWORK_HISTORY_PROFILE_KEY
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from framework.history import HistoryAuthority
    raw=profile.get(FRAMEWORK_HISTORY_PROFILE_KEY)
    if raw is None:return
    history=history or HistoryAuthority.from_record(raw)
    for identity,entry in profile.get(EVENT_JOURNAL_KEY,{}).items():
        fact=history.fact(identity)
        if (fact.session_binding_fingerprint,fact.fingerprint) not in _REGISTRY:
            register_event(PetEvent.from_record(entry['event']),fact)


def metadata(runtime,event,parsed):
    from interaction_extensions.local_memory_source import register
    if parsed.get('version')!=VERSION:raise ValueError('unknown pet recall model')
    cue=make_cue(event,parsed)
    view=register(runtime._history(),session_id=runtime.session.session_binding.session_id,owner_ref=runtime.owner_ref)
    return {KEY:{'version':VERSION,'cue':cue,'source_view':thaw_model_data(view.reference)}}


def enabled(context):return context.metadata.get(KEY,{}).get('version')==VERSION


def view_for(context):
    from interaction_extensions.local_memory_source import resolve
    return resolve(context.metadata[KEY]['source_view'],context)


def source_for(view,identity):
    fact=view.index.facts[view.index.by_id[identity]];view.node(identity)
    source=_REGISTRY.get((fact.session_binding_fingerprint,fact.fingerprint))
    if source is None:raise ValueError('grounded memory source unavailable; restore original event journal')
    if source['event_id']!=identity or source['source_ref']!=fact.source_ref:raise ValueError('grounded source differs from formal fact')
    return source


def association(cue,source):
    if cue['signatures']:
        hits=[list(x) for x in cue['signatures'] if tuple(x) in {tuple(y) for y in source['signatures']}]
        return len(hits)/len(cue['signatures']),{'kind':'physical-signatures','hits':hits}
    phrases=cue['phrases'];hits=[x for x in phrases if x in source['text']]
    return (len(hits)/len(phrases) if phrases else 0.),{'kind':'grounded-phrases','hits':hits}


def current_projection(package,payload,context):
    from .memory_query_bound import projection_for_query
    return projection_for_query(package,{k:v for k,v in payload.items() if k not in ('candidate_event_ids','historical_live')},context)['vector']


def evaluate(package,*,identity,row,current,context):
    view=view_for(context);source=source_for(view,identity);cue=context.metadata[KEY]['cue']
    sem,match=association(cue,source)
    historical=row['recall_input']['projection']['vector']
    h=math.hypot(*historical.values());q=math.hypot(*current.values())
    signed=max(-1.,min(1.,sum(current[k]*historical[k] for k in PROJECTION_AXES)/h/q)) if h and q else None
    relation=sem*abs(signed or 0.)
    node=view.node(identity);projection=node['projection'];birth=projection['delta_x_event']
    live=row['influence_input']['live_vector'];radius=float(package._package_params('memory_forward_readout')['bound_radius'])
    birth_norm=min(math.hypot(*birth.values()),radius)
    if birth_norm:
        retention=min(1.,math.hypot(*live.values())/birth_norm)
        time_kind='original-live-versus-birth'
    else:
        c=projection['constant_binding'];age=context.temporal_position.coordinate-projection['event']['committed_at_ms']
        flow=flow_root_time(c['effective_constants'],projection['x_before'],age,effective_constants_version=c['effective_constants_version'],params=node['evolution_binding']['root_time_parameters'])
        rates=flow.receipt['effective_rate_per_hour']
        retention=sum(math.exp(-rates[k]*age/3600000.) for k in RECOVERABLE_AXES)/len(RECOVERABLE_AXES)
        time_kind='zero-delta-original-phi-retention'
    impulse=retention*relation;threshold=((1-retention)+(1-relation))/2
    margin=impulse-threshold;recalled=bool(sem and h and q and margin>=0)
    return {'impulse':impulse,'threshold':threshold,'margin':margin,'recalled':recalled,
        'formula_version':VERSION,'receipt':{'version':VERSION,'semantic_coverage':sem,'match':match,
        'signed_projection_cosine':signed,'combined_relation':relation,'retention':retention,'retention_source':time_kind,
        'fact_fingerprint':view.index.facts[view.index.by_id[identity]].fingerprint,
        'source_event_fingerprint':source['event_fingerprint'],'evaluation_time_ms':context.temporal_position.coordinate,
        'time_effect_application_count':1}}


def recall(package,*,payload,context,rows=None,current=None):
    from .memory_direct_readout import DirectReader
    from time import perf_counter
    if not enabled(context):raise ValueError('grounded recall requires explicit version')
    view=view_for(context);current=current or current_projection(package,payload,context)
    ids=list(payload['candidate_event_ids']);by_id={} if rows is None else {r['event_id']:r for r in rows}
    reader=DirectReader(package,view,context);evidence={}
    for identity in ids:
        row=by_id.get(identity)
        if row is None:
            if not reader.ensure(identity,float('inf'),perf_counter):raise ValueError('memory source could not be validated')
            row=reader.get(identity)
        provided=next(x for x in payload['historical_live'] if x['event_id']==identity)
        expected=row['recall_input']
        if (provided['evaluation_time_ms']!=expected['evaluation_time_ms']
                or provided['live_readout_ref']!=expected['live_readout_ref']
                or provided['projection']['vector']!=expected['projection']['vector']):
            raise ValueError('grounded recall input differs from original source readout')
        evidence[identity]=evaluate(package,identity=identity,row=row,current=current,context=context)
    recalled=[i for i in ids if evidence[i]['recalled']]
    return {'recall_decision':{'version':VERSION,'candidate_event_ids':ids,'recalled_event_ids':recalled,'candidate_evidence':evidence},
        'memory_content_claims':{'version':'desktop-pet-grounded-content-claim-v2','claims':[
            {'event_id':i,'strength':evidence[i]['impulse'],'claim_version':'desktop-pet-grounded-content-claim-v2',
             'source_evidence_fingerprint':model_data_fingerprint(evidence[i])} for i in recalled]}}
