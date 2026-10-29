"""Read-only display of source-bound, already committed relation measurements."""
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint
from model_packages.desktop_pet_v1.events import MemoryInstance,PetEvent
from model_packages.desktop_pet_v1.semantic import evidence_source

VERSION='desktop-pet-committed-relation-annotation-v1'
FIELD='内容归属注解'

def for_memory(profile,memory,reader_ref):
    event=memory.event
    if (event.visible_contents(reader_ref)!=event.contents or not event.contents
        or any(c.kind!='speech' or c.private for c in event.contents)):return None
    measurement=_measurement(profile,memory)
    frame=measurement.get('committed_measurement',{}).get('reference_frame')
    if not isinstance(frame,Mapping) or frame.get('schema_version')!=2 or frame.get('resolver_source')!='local_evidence_locator':return None
    claims=frame.get('relation_claims',())
    if not isinstance(claims,(list,tuple)):raise ValueError('relation annotation claims invalid')
    names={'user':profile['desktop_pet_product_v1'].get('user_ref'),'current_npc':event.owner_ref,'third_party':'原话中的第三方','unresolved':'未确定'}
    text=evidence_source(event);rows=[]
    for claim in claims:
        if not isinstance(claim,Mapping):raise ValueError('relation annotation claim invalid')
        a,b,relation=claim.get('subject'),claim.get('target'),claim.get('relation');span=claim.get('evidence_span')
        if (a not in names or b not in names or not names[a] or not names[b]
            or not isinstance(relation,str) or not relation
            or not isinstance(span,(list,tuple)) or len(span)!=2
            or any(type(n)is not int for n in span) or not 0<=span[0]<span[1]<=len(text)):
            raise ValueError('relation annotation lacks located source evidence')
        rows.append({'主体':names[a],'涉及对象':names[b],'关系理解':relation,'原文依据':text[span[0]:span[1]]})
    return {'性质':'这段原话的关系理解','关系':rows} if rows else None


def _measurement(profile,memory):
    event=memory.event
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    product=profile.get('desktop_pet_product_v1',{})
    source=product.get('sources',{}).get(event.event_id)
    entry=profile.get(EVENT_JOURNAL_KEY,{}).get(event.event_id)
    if source is None or entry is None:raise ValueError('relation annotation requires a committed source')
    MemoryInstance(event,memory.fact)
    measurement=source.get('measurement')
    if (entry.get('fact_fingerprint')!=memory.fact.fingerprint
        or PetEvent.from_record(entry['event'])!=event or PetEvent.from_record(source['event'])!=event
        or not isinstance(measurement,Mapping)
        or model_data_fingerprint(measurement)!=entry.get('measurement_fingerprint')):
        raise ValueError('relation annotation differs from committed measurement')
    return measurement


def query_references(profile,memory,memories,reader_ref,*,markers=None):
    """Deliver accepted query children only when their exact source was recalled."""
    event=memory.event
    if (event.event_kind!='peer_input' or event.visible_contents(reader_ref)!=event.contents
        or not event.contents or any(c.kind!='speech' or c.private for c in event.contents)):return []
    parsed=_measurement(profile,memory).get('pet_memory_recall',{})
    if parsed.get('query_tree_version')!='desktop-pet-memory-query-tree-v1':return []
    ordered=tuple(sorted(memories,key=lambda m:m.fact.accepted_runtime_ordinal))
    if markers is not None and (set(markers)!={m.event.event_id for m in ordered}
        or len(set(markers.values()))!=len(markers) or any(not isinstance(x,str) or not x for x in markers.values())):
        raise ValueError('query reference markers differ from delivered memories')
    selected={m.event.event_id:((markers[m.event.event_id] if markers is not None else str(i)),m)
              for i,m in enumerate(ordered,1)}
    current=evidence_source(event);refs=parsed.get('reference_cues',());rows=[]
    def quote(text,span):
        if (not isinstance(span,(tuple,list)) or len(span)!=2 or any(type(x)is not int for x in span)
            or not 0<=span[0]<span[1]<=len(text)):raise ValueError('query delivery span invalid')
        value=text[span[0]:span[1]]
        if text.count(value)!=1:raise ValueError('query delivery evidence ambiguous')
        return value
    for node in parsed.get('query_nodes',()):
        if node.get('kind')!='query':continue
        question=quote(current,node['span']);children=[]
        for index in node.get('reference_indices',()):
            if type(index)is not int or not 0<=index<len(refs):raise ValueError('query delivery child invalid')
            ref=refs[index];source=ref['reference_source'];match=selected.get(source['event_id'])
            if match is None:continue
            marker,prior=match;pe=prior.event
            expected={'binding':prior.fact.session_binding_fingerprint,'event_id':pe.event_id,'event_fingerprint':pe.fingerprint,'fact_fingerprint':prior.fact.fingerprint,'source_ref':pe.source_binding_ref}
            if dict(source)!=expected:raise ValueError('query delivery source differs from recalled fact')
            if (pe.visible_contents(reader_ref)!=pe.contents or pe.event_kind!='peer_input' or pe.actor_ref!=event.actor_ref
                or pe.owner_ref!=event.owner_ref or pe.scope_ref!=event.scope_ref or pe.position.coordinate>event.position.coordinate
                or any(c.kind!='speech' or c.private for c in pe.contents)):raise ValueError('query delivery source outside visible input scope')
            _measurement(profile,prior)
            if tuple(ref['span'])!=tuple(node['span']):raise ValueError('query delivery parent differs')
            evidence=quote(evidence_source(pe),ref['reference_span']);term=ref['text']
            if not isinstance(term,str) or not term or term not in evidence:raise ValueError('query delivery reference lacks evidence')
            children.append({'记忆标记':marker,'所指原词':term,'原文依据':evidence})
        if children:rows.append({'当前问题原文':question,'查询内容':node['text'],'已唤起来源':children})
    return rows
