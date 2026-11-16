"""Read-only owner aliases from the historical event, never current roots."""
from framework.model_api import thaw_model_data


def attribute_speech_properties(row,event,parsed):
    """Separate measured speech claims using their original evidence spans."""
    from model_packages.desktop_pet_v1.events import PetEvent
    from model_packages.desktop_pet_v1.semantic import evidence_parts

    properties=row.get('语义属性',())
    if not properties or not parsed or not event:
        return row
    original=PetEvent.from_record(event)
    if parsed.get('hierarchy_event_fingerprint')!=original.fingerprint:
        return row
    speech_spans=[]
    start=0
    for content,part in zip(original.contents,evidence_parts(original),strict=True):
        end=start+len(part)
        if content.kind=='speech':
            speech_spans.append((start,end))
        start=end+1
    speech,other=[],[]
    measured=thaw_model_data(parsed.get('properties',()))
    for prop in properties:
        matching=[p['span'] for p in measured
                  if 'span' in p and {k:v for k,v in p.items() if k!='span'}==prop]
        if matching and all(any(a<=lo<hi<=b for a,b in speech_spans) for lo,hi in matching):
            speech.append(prop)
        else:
            other.append(prop)
    if not speech:
        return row
    result={k:v for k,v in row.items() if k!='语义属性'}
    if other:
        result['语义属性']=other
    result['话语语义（说话者的说法）']={'说话者':original.actor_ref,'语义标签':speech}
    return result


def annotate(fields,provenance,*,owner,events=None,evidence=None):
    fields=thaw_model_data(fields)
    records=provenance.get('source_records',())
    if events is not None:
        ids=provenance['selected_memory_ids']
        records=[{'event':events[i],'properties':None if evidence is None else evidence.get(i)} for i in ids]
    def one(row,event=None,parsed=None):
        if not isinstance(row,dict) or '语义属性' not in row:return row
        event=event or {}
        subject=event.get('owner_ref',owner)
        peer=None
        if event.get('event_kind')=='peer_input' and event.get('actor_ref')!=subject:
            peer=event.get('actor_ref')
        # A multi-recipient self output needs its historical relation binding;
        # its visible audience or today's active peer cannot supply that fact.
        elif event.get('event_kind')=='npc_output' and len(event.get('target_refs',()))==1:
            peer=event['target_refs'][0]
        if peer==subject:peer=None
        result=attribute_speech_properties(row,event,parsed) if parsed is not None else row
        return {**result,'属性归属绑定':{'current_npc':subject,
            'user':peer or '该条原量测的关系对端，当前卡片未确定其身份',
            'third_party':'该条关系双方以外的对象','unresolved':'原文未确定的对象'}}
    if '相关记忆（按发生顺序）' in fields:
        rows=fields['相关记忆（按发生顺序）']
        if records and len(records)!=len(rows):raise ValueError('memory owner event count differs')
        fields['相关记忆（按发生顺序）']=[one(row,records[i]['event'],records[i].get('properties')) if records else one(row) for i,row in enumerate(rows)]
        return fields
    return one(fields,records[0]['event'],records[0].get('properties')) if len(records)==1 else one(fields)
