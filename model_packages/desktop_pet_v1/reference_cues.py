"""Literal two-source cue evidence; no recall decision or numeric operator."""
import json
from .semantic import evidence_source

LEGACY_VERSION='desktop-pet-input-reference-cues-v1'
SLOT_VERSION='desktop-pet-input-reference-cues-v2-slot-substitution'
VERSION='desktop-pet-input-reference-cues-v3-typed-slots'
VERSIONS={LEGACY_VERSION,SLOT_VERSION,VERSION}

class ReferenceSemanticCodec:
    def __init__(self,base,*,version=VERSION):
        if version not in VERSIONS:raise ValueError('unknown reference codec version')
        self.base=base;self.reference_version=version
        self.request_version=base.request_version+(':input-reference-v3-typed-slots' if version==VERSION else ':input-reference-v2')
    def __getattr__(self,name):return getattr(self.base,name)
    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        user=user.replace('【已召回上下文】','【前次用户话语：指代参照】',1)
        system+='\n前次用户话语是有来源的指代参照，本次事件仍是量测对象。当前话语若指向前次的对象或话题，可另给reference_cues数组，每项为{"text":"前次话语中的主题原词","evidence_text":"本次指代的唯一原文片段","reference_text":"前次包含主题的唯一原文片段"}。本次memory_cues仍按本次原文摘取；reference_cues将本次指代和前次主题配对，两类合计最多16项，text最多48字，每段证据最多256字。没有明确指代或参照不足时reference_cues为空数组。参照本身不是已召回记忆，也不是本轮新的影响。'
        if self.reference_version==VERSION:
            from pathlib import Path
            system+='\n'+Path(__file__).with_name('resources').joinpath('semantic/reference-cue-kinds-v1.md').read_text(encoding='utf8').strip()
        return system,user


def parse_reference_cues(raw,event,context,*,available=16,version=VERSION):
    if version not in VERSIONS:raise ValueError("unknown reference cue version")
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    data=json.loads(_strip_fences(raw));items=data.get('reference_cues',[])
    if not isinstance(items,list) or len(items)>available:raise ValueError('reference cue output exceeds contract')
    current=evidence_source(event);previous=context['text'];cues=[];rejected=0
    typed={}
    if version==VERSION:
        from .grounded_recall import parse_cues
        from .reference_scope import classify
        literal=parse_cues(raw,event)['cues'];kinds,deferred=classify(literal,data,event)
        typed={'cue_kinds':kinds,'deferred_cue_kind_count':deferred}
        scope={'cues':literal,**typed}
    for item in items:
        if not isinstance(item,dict):rejected+=1;continue
        term=item.get('text');quote=item.get('evidence_text');prior=item.get('reference_text')
        if (not isinstance(term,str) or not term.strip() or len(term)>48
            or not isinstance(quote,str) or not 0<len(quote)<=256 or current.count(quote)!=1
            or not isinstance(prior,str) or not 0<len(prior)<=256 or previous.count(prior)!=1 or term not in prior):
            rejected+=1;continue
        a=current.index(quote);b=previous.index(prior)
        row={'text':term,'span':[a,a+len(quote)],'reference_span':[b,b+len(prior)]}
        if version==VERSION:
            from .reference_scope import permits
            if not permits(event,scope,row):rejected+=1;continue
        if row not in cues:cues.append(row)
    return {'reference_version':version,'reference_source':dict(context['source']),
        'reference_cues':cues,'rejected_reference_cue_count':rejected,**typed}


def referenced_phrases(event,parsed,registry):
    if parsed.get('reference_version') not in VERSIONS:raise ValueError('unknown reference cue version')
    source=parsed.get('reference_source',{});prior=registry.get((source.get('binding'),source.get('fact_fingerprint')))
    if (prior is None or any(source.get(k)!=prior.get(k) for k in ('event_id','event_fingerprint','source_ref'))
        or prior['event_kind']!='peer_input' or event.event_kind!='peer_input'
        or prior['actor_ref']!=event.actor_ref or prior['owner_ref']!=event.owner_ref or prior['scope_ref']!=event.scope_ref
        or prior['coordinate']>event.position.coordinate or prior['event_id']==event.event_id
        or prior['content_kinds']!=['speech'] or sorted({c.kind for c in event.contents})!=['speech']):
        raise ValueError('reference cue source differs from committed visible input')
    current=evidence_source(event);previous=prior['text'];phrases=[]
    rows=parsed.get('reference_cues',())
    if not isinstance(rows,(list,tuple)) or len(rows)+len(parsed.get('cues',()))>16:raise ValueError('reference cue resource limit differs')
    for row in rows:
        a,b=row['span'];c,d=row['reference_span'];term=row['text']
        if (any(type(v)is not int for v in (a,b,c,d)) or not 0<=a<b<=len(current) or not 0<=c<d<=len(previous)
            or not isinstance(term,str) or not term.strip() or len(term)>48 or b-a>256 or d-c>256
            or current.count(current[a:b])!=1 or previous.count(previous[c:d])!=1 or term not in previous[c:d]):
            raise ValueError('reference cue lacks unique actual source evidence')
        if parsed['reference_version']==VERSION:
            from .reference_scope import permits
            if not permits(event,parsed,row):raise ValueError('reference cue overlaps an explicit topic or lacks a classified slot')
        if term not in phrases:phrases.append(term)
    return phrases
