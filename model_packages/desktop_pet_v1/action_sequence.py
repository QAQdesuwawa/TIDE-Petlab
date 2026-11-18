"""Bounded model proposals over executable actions; legacy token offers replay unchanged."""
from dataclasses import replace
from framework.model_api import thaw_model_data,model_data_fingerprint as fp
from . import action_candidates as a
from .composite import CompositePlan,DEPENDENCY_VERSION

VERSION='desktop-pet-action-sequence-v1'
MAX_ACTIONS=4  # One response/physical plan capacity, not a behavioral quota.


def body_order(children):
    """Describe validated dependencies, not mapping insertion order."""
    visited=set();ordered=[]
    def visit(key):
        if key in visited:return
        for dependency in children[key].get('after',()):visit(dependency)
        visited.add(key)
        if children[key]['kind']=='action':ordered.append(key)
    for key in children:visit(key)
    return ordered


def describe_stage(content,index):
    """Public actual chronology, without exposing unexecuted suffixes."""
    return replace(content,description='同一动作序列第'+str(index+1)+'段：'+content.description)


def offer(selection,catalog,*,owner_ref,target_ref):
    selected=a.validate(selection);catalog=thaw_model_data(catalog)
    if fp(catalog)!=selected['catalog_fingerprint']:raise ValueError('sequence catalog differs from formal selection')
    model_choice=selected['version']==a.MODEL_VERSION
    builder=a.propose if model_choice else a.choose
    if builder(eligible=True,tokens=selected['tokens'],source=selected['source'],catalog=catalog,
            allow_speech=selected['allow_speech'])!=selected:raise ValueError('sequence symbolic source differs')
    if not owner_ref or not target_ref or owner_ref==target_ref:raise ValueError('invalid sequence participants')
    keys=[r['key'] for r in catalog['candidates'] if model_choice or a._matches(r,selected['tokens'])]
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
    if value['selection'].get('version')==a.MODEL_VERSION and list(keys).count('read_web')>1:
        raise ValueError('one phone executor accepts one reading activity per response')
    by_key={r['key']:r for r in value['catalog']['candidates']}
    contact_targets=[by_key[k]['social_contact']['target'] for k in keys if 'social_contact' in by_key[k]]
    if len(contact_targets)!=len(set(contact_targets)):
        raise ValueError('one contact decision per participant per response')
    return list(keys)


def validate_speech_timing(timing,keys,*,has_speech):
    if timing is None:return None
    timing=thaw_model_data(timing)
    if (not isinstance(timing,dict) or set(timing)!={'action_index','phase'}
            or type(timing['action_index']) is not int or not 0<=timing['action_index']<len(keys)
            or timing['phase'] not in ('start','end') or not has_speech):
        raise ValueError('speech timing must reference a selected action and actual speech')
    return timing


def _meal_continuation(value, keys, contents, target, addressee, contract):
    from . import shared_meal

    if target != value['target_ref']:
        return None
    speech = shared_meal.acceptance_speech(contents, addressee=addressee, version=contract)
    if speech is None:
        return None
    catalog = {row['key']: row for row in value['catalog']['candidates']}
    key = shared_meal.HOLDER_TRANSFER_ACTION
    if any('meal' in catalog[k] and (k != key or contract != shared_meal.ACCEPTANCE_V2) for k in keys):
        return None
    row = catalog.get(key)
    if key not in value['allowed_keys'] or not row or 'meal' not in row:
        return None
    binding = shared_meal.verify_binding(row['meal'])
    if binding['actor_ref'] != value['owner_ref'] or binding['target_ref'] != target:
        return None
    return key, speech.content_id


def compose(keys,value,other_contents,*,audience,allow_thought=False,speech_timing=None,speech_timings=None,
            expression_timings=None,meal_acceptance_target=None,meal_acceptance_addressee=None,
            meal_acceptance_contract=None,dependency_contract=None):
    """One frozen list becomes local dependencies. No model is called here."""
    if dependency_contract not in (None,DEPENDENCY_VERSION):
        raise ValueError('unknown sequence dependency contract')
    separate=dependency_contract==DEPENDENCY_VERSION
    value=validate_offer(value);keys=validate_keys(keys,value)
    from .behavior import content
    by_key={r['key']:r for r in value['catalog']['candidates']}
    other_contents=tuple(other_contents)
    speech_timing=validate_speech_timing(speech_timing,keys,has_speech=any(c.kind=='speech' for c in other_contents))
    voices=[c.content_id for c in other_contents if c.kind=='speech']
    if speech_timings is not None:
        speech_timings=thaw_model_data(speech_timings)
        if speech_timing is not None or set(speech_timings)!=set(voices) or not 1<=len(voices)<=4:
            raise ValueError('speech sequence bindings differ')
        for timing in speech_timings.values():validate_speech_timing(timing,keys,has_speech=True)
    faces=[c.content_id for c in other_contents if c.kind=='expression']
    if expression_timings is not None:
        expression_timings=thaw_model_data(expression_timings)
        if set(expression_timings)!=set(faces) or not 1<=len(faces)<=4:raise ValueError('expression timing identities differ')
        for timing in expression_timings.values():validate_speech_timing(timing,keys,has_speech=True)
    items=[];children={};previous=None
    continuation = _meal_continuation(value, keys, other_contents, meal_acceptance_target,
                                      meal_acceptance_addressee, meal_acceptance_contract)
    body_keys = keys
    acceptance_index = None
    if continuation:
        if continuation[0] not in body_keys:
            body_keys = [*body_keys, continuation[0]]
        acceptance_index = body_keys.index(continuation[0])
        preceding_speech = set(voices[:voices.index(continuation[1])+1])
    for index,key in enumerate(body_keys):
        prerequisites = [] if previous is None else [previous]
        required_success = []
        if index == acceptance_index:
            prerequisites.append(continuation[1])
            required_success.append(continuation[1])
        target=by_key[key].get('target_ref',value['target_ref'])
        semantic_key=by_key[key].get('action_key',key)
        required=by_key[key].get('approach')
        if required is not None and (not items or items[-1].semantic_key!='move_near' or items[-1].parameters.get('movement',{}).get('target_ref')!=target):
            from . import movement,meal_space
            from .social_contact import KEYS as CONTACT_KEYS, VERSION as CONTACT_VERSION
            contact_approach=(semantic_key in CONTACT_KEYS and semantic_key.startswith('accept_')
                              and by_key[key].get('physical_contract')==CONTACT_VERSION)
            prop_approach=semantic_key in ('move_prop','press_meal_button') and required==movement.arrival_definition()
            if not prop_approach and not contact_approach and (semantic_key not in ('offer_bowl','accept_bowl') or by_key[key].get('physical_contract')!=meal_space.VERSION):
                raise ValueError('unexpected action approach prerequisite')
            description='走到道具旁' if prop_approach else '走到接触对象身旁' if semantic_key=='accept_kiss' else '走到交接对象身旁'
            approach={'key':'move_near','description':description,'motion':required}
            if prop_approach:
                approach['world_object']=by_key[key]['world_object']
            prep=replace(movement.content(target,approach),
                content_id='action:'+str(index)+':approach',recipient_refs=tuple(audience))
            children[prep.content_id]={'kind':'action','key':'move_near','after':prerequisites}
            if separate:children[prep.content_id]['requires']=list(required_success)
            prerequisites=[prep.content_id]
            required_success=[prep.content_id]
            items.append(prep);previous=prep.content_id
        elif required is not None:
            # The selected preceding approach fulfills a physical prerequisite,
            # unlike ordinary body-channel sequencing.
            required_success.append(previous)
        if value['selection']['version']==a.MODEL_VERSION:
            selected=a.bind_proposal(value['selection'],by_key[key])
        else:
            selected=a.choose(eligible=True,tokens=value['selection']['tokens'],source=value['selection']['source'],
                catalog={'version':VERSION,'candidates':[by_key[key]]},allow_speech=False)
        item=replace(content(target,selection=selected),content_id='action:'+str(index),recipient_refs=tuple(audience))
        children[item.content_id]={'kind':'action','key':semantic_key,'after':prerequisites}
        if separate:children[item.content_id]['requires']=list(dict.fromkeys(required_success))
        items.append(item);previous=item.content_id
    previous_voice=None;previous_face=None
    for item in other_contents:
        if item.kind not in {'speech','thought','expression'} or item.content_id in children:
            raise ValueError('invalid non-body sequence child')
        children[item.content_id]={'kind':item.kind,'key':'selected_presentation' if item.kind=='expression' else 'reply' if item.kind=='speech' else 'inner_monologue','after':[]}
        if item.kind=='speech':
            timing=speech_timing if speech_timings is None else speech_timings[item.content_id]
            # Voice must precede its handoff even if an original timing hint
            # put the acceptance at the start/end of that action or its suffix.
            if (timing is not None and continuation and item.content_id in preceding_speech
                    and timing['action_index'] >= acceptance_index):
                timing = None
            if timing is not None:
                anchor='action:'+str(timing['action_index'])
                children[item.content_id]['after']=([anchor] if timing['phase']=='end' else list(children[anchor]['after']))
            if previous_voice is not None:children[item.content_id]['after'].append(previous_voice)
            previous_voice=item.content_id
        if item.kind=='expression':
            timing=None if expression_timings is None else expression_timings[item.content_id]
            if timing is not None:
                anchor='action:'+str(timing['action_index'])
                children[item.content_id]['after']=([anchor] if timing['phase']=='end' else list(children[anchor]['after']))
            if previous_face is not None:children[item.content_id]['after'].append(previous_face)
            children[item.content_id]['after']=list(dict.fromkeys(children[item.content_id]['after']))
            previous_face=item.content_id
        items.append(item)
    if allow_thought and not any(c.kind=='thought' for c in items):
        children['thought']={'kind':'thought','key':'inner_monologue','after':[]}
    if separate:
        for row in children.values():row.setdefault('requires',[])
    plan=CompositePlan.create(children) if children else None
    if plan is not None:plan.validate_contents(items)
    declaration={'version':VERSION,'keys':keys,'children':children}
    if separate:declaration['dependency_contract']=dependency_contract
    if continuation:
        declaration['meal_acceptance'] = {'speech_id': continuation[1], 'target_ref': meal_acceptance_target}
    if speech_timing is not None:declaration['speech_timing']=speech_timing
    if speech_timings is not None:declaration['speech_timings']=speech_timings
    if expression_timings is not None:
        declaration['expression_timings']=expression_timings
        declaration['expression_sequence_version']='desktop-pet-expression-sequence-v1'
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
    acceptance = row.get('meal_acceptance')
    if acceptance is not None and not request.control.get('meal_acceptance_contract'):
        raise ValueError('meal acceptance is unavailable for this request')
    expected,declaration=compose(row['keys'],context,tuple(c for c in actual if c.kind!='action'),
        meal_acceptance_target=acceptance['target_ref'] if acceptance else None,
        meal_acceptance_addressee=request.readonly_facts.get('meal_acceptance_addressee'),
        meal_acceptance_contract=request.control.get('meal_acceptance_contract'),
        dependency_contract=request.control.get('dependency_contract'),
        audience=request.readonly_facts.get('participant_destination',{}).get('audience',(request.target_ref,)),allow_thought=request.control.get('allow_thought',False),speech_timing=row.get('speech_timing'),speech_timings=row.get('speech_timings'),expression_timings=row.get('expression_timings'))
    if expected!=actual or row!=declaration:raise ValueError('sequence contents differ from frozen proposal')
    return CompositePlan.create(declaration['children']) if declaration['children'] else None
