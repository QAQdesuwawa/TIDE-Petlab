"""Bounded proposals inside a granted symbolic action set; no root/tick math."""
from dataclasses import replace
from framework.model_api import thaw_model_data,model_data_fingerprint as fp
from . import action_candidates as a
from .composite import CompositePlan

VERSION='desktop-pet-action-sequence-v1'
MAX_ACTIONS=4  # One response/physical plan capacity, not a behavioral quota.


def describe_stage(content,index):
    """Public actual chronology, without exposing unexecuted suffixes."""
    return replace(content,description='同一动作序列第'+str(index+1)+'段：'+content.description)


def offer(selection,catalog,*,owner_ref,target_ref):
    selected=a.validate(selection);catalog=thaw_model_data(catalog)
    if fp(catalog)!=selected['catalog_fingerprint']:raise ValueError('sequence catalog differs from formal selection')
    if a.choose(eligible=True,tokens=selected['tokens'],source=selected['source'],catalog=catalog,
            allow_speech=selected['allow_speech'])!=selected:raise ValueError('sequence symbolic source differs')
    if not owner_ref or not target_ref or owner_ref==target_ref:raise ValueError('invalid sequence participants')
    keys=[r['key'] for r in catalog['candidates'] if a._matches(r,selected['tokens'])]
    if not keys:return None
    data={'version':VERSION,'owner_ref':owner_ref,'target_ref':target_ref,
        'selection':selected,'catalog':catalog,'allowed_keys':keys,'max_actions':MAX_ACTIONS}
    return {**data,'fingerprint':fp(data)}


def validate_offer(value):
    value=thaw_model_data(value)
    if not isinstance(value,dict) or value.get('version')!=VERSION:raise ValueError('unknown sequence offer')
    expected=offer(value['selection'],value['catalog'],owner_ref=value['owner_ref'],target_ref=value['target_ref'])
    if expected!=value:raise ValueError('sequence offer changed')
    return value


def validate_keys(keys,value):
    value=validate_offer(value)
    if (not isinstance(keys,(list,tuple)) or len(keys)>value['max_actions']
            or any(not isinstance(k,str) or k not in value['allowed_keys'] for k in keys)):
        raise ValueError('action sequence is outside offered candidates')
    return list(keys)


def compose(keys,value,other_contents,*,audience,allow_thought=False):
    """One frozen list becomes local dependencies. No model is called here."""
    value=validate_offer(value);keys=validate_keys(keys,value)
    from .behavior import content
    by_key={r['key']:r for r in value['catalog']['candidates']}
    items=[];children={};previous=None
    for index,key in enumerate(keys):
        required=by_key[key].get('approach')
        if required is not None and (not items or items[-1].semantic_key!='move_near'):
            from . import movement,meal_space
            if key not in ('offer_bowl','accept_bowl') or by_key[key].get('physical_contract')!=meal_space.VERSION:
                raise ValueError('unexpected action approach prerequisite')
            prep=replace(movement.content(value['target_ref'],{'key':'move_near','description':'走到交接对象身旁','motion':required}),
                content_id='action:'+str(index)+':approach',recipient_refs=tuple(audience))
            children[prep.content_id]={'kind':'action','key':'move_near','after':[] if previous is None else [previous]}
            items.append(prep);previous=prep.content_id
        selected=a.choose(eligible=True,tokens=value['selection']['tokens'],source=value['selection']['source'],
            catalog={'version':VERSION,'candidates':[by_key[key]]},allow_speech=False)
        item=replace(content(value['target_ref'],selection=selected),content_id='action:'+str(index),recipient_refs=tuple(audience))
        children[item.content_id]={'kind':'action','key':key,'after':[] if previous is None else [previous]}
        items.append(item);previous=item.content_id
    for item in other_contents:
        if item.kind not in {'speech','thought','expression'} or item.content_id in children:
            raise ValueError('invalid non-body sequence child')
        children[item.content_id]={'kind':item.kind,'key':'selected_presentation' if item.kind=='expression' else 'reply' if item.kind=='speech' else 'inner_monologue','after':[]}
        items.append(item)
    if allow_thought and not any(c.kind=='thought' for c in items):
        children['thought']={'kind':'thought','key':'inner_monologue','after':[]}
    plan=CompositePlan.create(children) if children else None
    if plan is not None:plan.validate_contents(items)
    declaration={'version':VERSION,'keys':keys,'children':children}
    return tuple(items),declaration


def validate_candidate(request,payload):
    from .events import Content
    if request.control.get('action_sequence_contract')!=VERSION or set(payload)!={'contents','action_sequence'}:
        raise ValueError('sequence candidate contract differs')
    context=validate_offer(request.readonly_facts['action_sequence_context'])
    if context['owner_ref']!=request.subject_ref or context['target_ref']!=request.target_ref:
        raise ValueError('sequence owner/target differs from authorization')
    row=thaw_model_data(payload['action_sequence'])
    if row.get('version')!=VERSION:raise ValueError('unknown action sequence result')
    actual=tuple(Content.from_record(c) for c in payload['contents'])
    expected,declaration=compose(row['keys'],context,tuple(c for c in actual if c.kind!='action'),
        audience=request.readonly_facts.get('participant_destination',{}).get('audience',(request.target_ref,)),allow_thought=request.control.get('allow_thought',False))
    if expected!=actual or row!=declaration:raise ValueError('sequence contents differ from frozen proposal')
    return CompositePlan.create(declaration['children']) if declaration['children'] else None
