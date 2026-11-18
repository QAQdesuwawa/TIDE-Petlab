"""Executable per-target options within the already authorized audience."""
from copy import deepcopy
from . import meal_button,movement,shared_meal,spatial

def _meal_catalog(base, meal, owner, target):
    own=shared_meal.held_by(meal,owner)
    other=shared_meal.held_by(meal,target)
    selected=own or other
    if selected is None and len(shared_meal.bowls(meal))==1:
        selected=shared_meal.bowls(meal)[0]
    result=shared_meal.catalog(base,selected,owner,target,include_self_activities=True)
    if own is not None and other is not None:
        result['candidates']=[row for row in result['candidates'] if row['key']!='offer_bowl']
    return result


def _expand(base,*,scene,meal,owner,primary,audience):
    from .basic_actions import catalog as basics
    base=movement.basic_catalog(basics(base,meal=shared_meal.held_by(meal,owner),owner=owner),scene=scene,target_ref=primary)
    result=_meal_catalog(movement.catalog(base,scene=scene,target_ref=primary),meal,owner,primary)
    if scene is None:return result
    peers=[p for p in spatial.targets(scene) if p not in ('front',owner,primary) and p in audience]
    if not peers:return result
    result=deepcopy(result)
    for peer in peers:
        other=_meal_catalog(movement.catalog(movement.basic_catalog({'version':'target-options','candidates':[]},scene=scene,target_ref=peer),scene=scene,target_ref=peer),meal,owner,peer)
        for row in other['candidates']:
            if row['key'] in ('eat_rice','stroll','relocate'):continue
            item=deepcopy(row);item.update(action_key=row['key'],key=row['key']+'@'+peer,target_ref=peer)
            result['candidates'].append(item)
    return result


def expand(base,*,scene,meal,owner,primary,audience,executable_keys=None,contact=None):
    result=_expand(base,scene=scene,meal=meal,owner=owner,primary=primary,audience=audience)
    hands_occupied=shared_meal.held_by(meal,owner) is not None
    if hands_occupied:
        result={**result,'candidates':[row for row in result['candidates']
                                      if row['key'] not in {'peek_phone','read_web'}]}
    elif (executable_keys is not None and 'peek_phone' in executable_keys
          and not any(row['key']=='peek_phone' for row in result['candidates'])):
        result=deepcopy(result)
        result['candidates'].append({'key':'peek_phone','description':'拿出手机低头翻看一会儿，再收起来'})
    if scene is not None and scene.get('objects'):
        result=deepcopy(result)
        for obj in spatial.validate(scene)['objects']:
            motion=movement.arrival_definition()
            try:
                approach=movement.plan(scene,{**motion,'target_ref':obj['ref']})
            except ValueError:
                continue
            if approach['distance_px']:
                result['candidates'].append({'key':'move_near@'+obj['ref'],'action_key':'move_near',
                    'target_ref':obj['ref'],'world_object':obj,'motion':motion,
                    'description':'走近公共物件「'+obj['label']+'」，在旁边停下'})
            if not hands_occupied and executable_keys is not None and 'move_prop' in executable_keys:
                from .prop_move import choices
                result['candidates'].extend(choices(scene,obj))
            if (not hands_occupied and executable_keys is not None
                    and {meal_button.KEY,'move_near'} <= set(executable_keys)
                    and obj['ref']==meal_button.REF and 'bounds' in obj):
                result['candidates'].append(meal_button.choice(obj,owner))
    if contact is not None and scene is not None:
        from .social_contact import candidates
        result=deepcopy(result)
        for peer in spatial.targets(scene):
            if peer in ('front',owner) or peer not in audience:continue
            if hands_occupied or shared_meal.held_by(meal,peer) is not None:continue
            for row in candidates(contact,owner,peer):
                key=row['key']
                result['candidates'].append({**row,'key':key+'@'+peer,'action_key':key,'target_ref':peer})
    if executable_keys is None:return result
    return {**result,'candidates':[row for row in result['candidates']
        if row.get('action_key',row['key']) in executable_keys]}
