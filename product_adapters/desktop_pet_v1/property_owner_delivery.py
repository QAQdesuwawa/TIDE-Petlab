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


def statement_evidence(row,event,parsed,content_ids=None):
    """Describe what an authorized speech fragment establishes."""
    from model_packages.desktop_pet_v1.events import PetEvent
    from model_packages.desktop_pet_v1.hierarchy_semantics import SPEECH_REPORT_PATH,scope_speech_reports,validate
    from model_packages.desktop_pet_v1.semantic import evidence_parts

    if not event or not row.get('正文'):return row
    original=PetEvent.from_record(event)
    # An Event may contain more than was selected. Never derive annotations
    # for text that is absent from this card's already-authorized projection.
    speech=[]
    start=0
    lines=set(row['正文'].splitlines())
    selected=None if content_ids is None else set(content_ids)
    for content,part in zip(original.contents,evidence_parts(original),strict=True):
        end=start+len(part)
        visible=selected is None or content.content_id in selected
        if content.kind=='speech' and visible and all(line in lines for line in content.as_text().splitlines()):
            speech.append((start,end,part))
        start=end+1
    if not speech:return row
    claims=[]
    raw=[]
    if parsed and parsed.get('hierarchy_event_fingerprint')==original.fingerprint:
        properties,legacy=scope_speech_reports(original,validate(original,parsed))
        claims=[prop for prop in properties if prop['kind']=='assertion'
                and tuple(prop['path'][:2])==SPEECH_REPORT_PATH]
        raw=(*parsed.get('property_speech_attributions',()),*legacy)
    if original.event_kind=='peer_input':
        counterparty=original.actor_ref
    else:
        counterparty=original.target_refs[0] if len(original.target_refs)==1 else None
    identities={'current_npc':original.owner_ref,'user':counterparty}
    notes=[]
    for start,end,part in speech:
        for claim in claims:
            lo,hi=claim['span']
            if not start<=lo<hi<=end:continue
            roles={source['owner'] for source in raw if tuple(source.get('span',()))==(lo,hi)}
            subject=identities.get(next(iter(roles))) if len(roles)==1 else None
            note={'原文片段':part[lo-start:hi-start],'性质':'转述',
                  '所述主体':subject or '未确定',
                  '证明范围':'此片段确认说话者转述过此事；所述事件须由独立来源确认。'}
            if note not in notes:notes.append(note)
    return {**row,'话语证据':{'说话者':original.actor_ref,
        '证明范围':'确认该说话者说过卡中所列原话。',
        **({'片段':notes} if notes else {})}}


def behavior_path(content):
    if content.kind == 'speech':
        return ['话语', '发言']
    if content.kind == 'expression':
        return ['表现', '表情']
    if content.kind == 'action':
        return ['身体动作', content.semantic_key]
    return None


def evidence_view(row, event, parsed, content_ids, *, owner, played_facts=None,
                  preserve_acquisition=False, fragment_behavior=False):
    """Render the authorized record once, with speech claims attached to their source."""
    from model_packages.desktop_pet_v1.events import PetEvent
    from .speaker_view import acquisition_view

    original = PetEvent.from_record(event)
    visible = original.visible_contents(owner)
    if content_ids is None:
        contents = visible
    else:
        selected = set(content_ids)
        contents = tuple(content for content in visible if content.content_id in selected)
        if len(contents) != len(selected):
            return row
    if not any(content.kind == 'speech' for content in contents) and not any(
        content.content_id in (played_facts or {}) or (fragment_behavior and behavior_path(content))
        for content in contents
    ):
        return row
    if row.get('正文') != '\n'.join(content.as_text() for content in contents):
        return row
    acquired = acquisition_view(row, owner=owner,preserve_verified=preserve_acquisition)
    attrs = acquired.get('内容属性', ())
    material = {part['片段']: part for part in acquired.get('片段材料', ())}
    if attrs and len(attrs) != len(contents):
        return row

    fragments = []
    for index, content in enumerate(contents, 1):
        fragment = dict(material.get(index, {'片段': index}))
        attributes = dict(attrs[index-1]) if attrs else {}
        if content.kind == 'speech':
            speech = statement_evidence({'正文': content.as_text()}, event, parsed, (content.content_id,))
            notes = speech['话语证据'].get('片段', ())
            audience = attributes.pop('可见受众', None)
            recorded_recipients = attributes.pop('对象', None)
            if audience is None:
                audience = recorded_recipients if attrs else list(content.recipient_refs)
            fragment.update({'材料性质': '话语记录', '说话者': original.actor_ref,
                             '原文': content.description, '直接证明': '说话者在当时说了这段原话'})
            if audience:
                fragment['可见受众'] = audience
            if original.actor_ref == owner:
                fragment['角色取得方式'] = '角色自己当时说出'
            if notes:
                fragment['原文中的说法'] = notes
            attributes.pop('类型', None)
        else:
            fragment['记录主体'] = original.actor_ref
            fragment['原文'] = content.as_text()
        if attributes:
            fragment['片段属性'] = attributes
        if played_facts is not None:
            fragment.update(played_facts.get(content.content_id, {}))
        if fragment_behavior and (path := behavior_path(content)):
            fragment['行为路径'] = path
        fragments.append(fragment)

    record_fields = {'说话者', '对象', '正文', '内容属性', '片段材料', '记录含义', '本次观察者'}
    result = {key: value for key, value in acquired.items() if key not in record_fields}
    if acquired.get('对象'):
        result['原事件指向'] = acquired['对象']
    return {**result, '原事件片段': fragments}


def annotate(fields,provenance,*,owner,events=None,evidence=None,statement_scope=False,integrated_evidence=False,played_facts=None,fragment_behavior=False):
    fields=thaw_model_data(fields)
    records=provenance.get('source_records',())
    if events is not None:
        ids=provenance['selected_memory_ids']
        records=[{'event':events[i],'properties':None if evidence is None else evidence.get(i)} for i in ids]
    def one(row,event=None,parsed=None):
        if not isinstance(row,dict):return row
        if statement_scope and event:
            selected=provenance.get('source_content_ids',{}).get(event['event_id'],provenance.get('projected_content_ids'))
            directions = None if played_facts is None else played_facts.get(event['event_id'], {})
            view = evidence_view(row, event, parsed, selected, owner=owner,
                                 played_facts=directions, fragment_behavior=fragment_behavior) if integrated_evidence else row
            row = statement_evidence(row, event, parsed, selected) if view is row else view
        if '语义属性' not in row:return row
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
