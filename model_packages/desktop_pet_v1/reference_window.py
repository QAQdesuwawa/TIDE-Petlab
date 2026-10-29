"""Versioned multi-source reference resolution, exact quote evidence only."""
import json
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint
from .semantic import evidence_source
from .reference_scope import classify,permits

VERSION='desktop-pet-input-reference-cues-v4-source-window'
USED_SCOPE='used-only-v1'
FULL_SCOPE='all-delivered-v1'


class WindowSemanticCodec:
    def __init__(self,base,*,source_scope=USED_SCOPE):
        if source_scope not in (USED_SCOPE,FULL_SCOPE):raise ValueError('unknown reference source scope')
        self.base=base;self.reference_version=VERSION;self.reference_source_scope=source_scope
        self.request_version=base.request_version+':input-reference-v4-source-window'+(':used-sources-v1' if source_scope==USED_SCOPE else '')
    def __getattr__(self,name):return getattr(self.base,name)
    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint='')
        from pathlib import Path
        system+='\n'+Path(__file__).with_name('resources').joinpath('semantic/reference-window-v1.md').read_text(encoding='utf8').strip()
        user+='\n\n【近期用户话语：指代参照】\n'+recall_hint
        return system,user


def parse_response(raw,event,context,*,available=16,source_scope=USED_SCOPE):
    if source_scope not in (USED_SCOPE,FULL_SCOPE):raise ValueError('unknown reference source scope')
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    from .grounded_recall import parse_cues
    if (context.get('version')!=VERSION or context.get('status')!='delivered'
        or context.get('event_fingerprint')!=event.fingerprint
        or context.get('fingerprint')!=model_data_fingerprint({k:v for k,v in context.items() if k!='fingerprint'})):
        raise ValueError('reference window context differs from current event')
    data=json.loads(_strip_fences(raw));items=data.get('reference_cues',[])
    if not isinstance(items,list) or len(items)>available:raise ValueError('reference cue output exceeds contract')
    literal=parse_cues(raw,event)['cues'];kinds,deferred=classify(literal,data,event)
    scope={'cues':literal,'cue_kinds':kinds};current=evidence_source(event);cues=[];rejected=0
    for item in items:
        if not isinstance(item,dict):rejected+=1;continue
        term=item.get('text');quote=item.get('evidence_text');prior=item.get('reference_text');alias=item.get('reference_id')
        if (not isinstance(term,str) or not term.strip() or len(term)>48
            or not isinstance(quote,str) or not 0<len(quote)<=256 or current.count(quote)!=1
            or not isinstance(prior,str) or not 0<len(prior)<=256 or term not in prior):
            rejected+=1;continue
        candidates=[x for x in context['sources'] if (alias is None or x['reference_id']==str(alias)) and x['text'].count(prior)==1]
        if len(candidates)!=1:rejected+=1;continue
        source=candidates[0];a=current.index(quote);b=source['text'].index(prior)
        row={'text':term,'span':[a,a+len(quote)],'reference_span':[b,b+len(prior)],
             'reference_id':source['reference_id'],'reference_source':dict(source['source'])}
        if not permits(event,scope,row):rejected+=1;continue
        if row not in cues:cues.append(row)
    used={x['reference_id'] for x in cues}
    return {**({'reference_source_scope':USED_SCOPE} if source_scope==USED_SCOPE else {}),
        'reference_version':VERSION,'reference_source':dict(context['source']),
        'reference_context_fingerprint':context['fingerprint'],
        'reference_sources':[{'reference_id':x['reference_id'],'source':dict(x['source'])} for x in context['sources'] if source_scope==FULL_SCOPE or x['reference_id'] in used],
        'reference_cues':cues,'rejected_reference_cue_count':rejected,
        'cue_kinds':kinds,'deferred_cue_kind_count':deferred}


def referenced_phrases(event,parsed,registry):
    current=evidence_source(event);sources=parsed.get('reference_sources',());by_alias={}
    if not isinstance(sources,(list,tuple)):raise ValueError('reference window source list differs')
    for item in sources:
        if not isinstance(item,Mapping):raise ValueError('reference window source item differs')
        alias=item.get('reference_id');source=item.get('source')
        if not isinstance(alias,str) or alias in by_alias or not isinstance(source,Mapping):
            raise ValueError('reference window source aliases differ')
        by_alias[alias]=source
    rows=parsed.get('reference_cues',())
    if not isinstance(rows,(list,tuple)) or len(rows)+len(parsed.get('cues',()))>16:
        raise ValueError('reference cue resource limit differs')
    phrases=[]
    for row in rows:
        source=row.get('reference_source',{});alias=row.get('reference_id')
        if alias not in by_alias or dict(source)!=dict(by_alias[alias]):raise ValueError('reference source is not in delivered window')
        prior=registry.get((source.get('binding'),source.get('fact_fingerprint')))
        if (prior is None or any(source.get(k)!=prior.get(k) for k in ('event_id','event_fingerprint','source_ref'))
            or prior['event_kind']!='peer_input' or event.event_kind!='peer_input'
            or prior['actor_ref']!=event.actor_ref or prior['owner_ref']!=event.owner_ref or prior['scope_ref']!=event.scope_ref
            or prior['coordinate']>event.position.coordinate or prior['event_id']==event.event_id
            or prior['content_kinds']!=['speech'] or {c.kind for c in event.contents}!={'speech'}):
            raise ValueError('reference source differs from committed visible input')
        previous=prior['text'];a,b=row['span'];c,d=row['reference_span'];term=row['text']
        if (any(type(v)is not int for v in (a,b,c,d)) or not 0<=a<b<=len(current) or not 0<=c<d<=len(previous)
            or not isinstance(term,str) or not term.strip() or len(term)>48 or b-a>256 or d-c>256
            or current.count(current[a:b])!=1 or previous.count(previous[c:d])!=1 or term not in previous[c:d]
            or not permits(event,parsed,row)):
            raise ValueError('reference source lacks unique current/source quote evidence')
        if term not in phrases:phrases.append(term)
    return phrases
