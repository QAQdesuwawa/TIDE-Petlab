"""Executable per-target options within the already authorized audience."""
from copy import deepcopy
from . import movement,shared_meal,spatial

def expand(base,*,scene,meal,owner,primary,audience):
    from .basic_actions import catalog as basics
    base=movement.basic_catalog(basics(base,meal=meal,owner=owner),scene=scene,target_ref=primary)
    result=shared_meal.catalog(movement.catalog(base,scene=scene,target_ref=primary),meal,owner,primary,include_self_activities=True)
    if scene is None:return result
    peers=[p for p in spatial.targets(scene) if p not in ('front',owner,primary) and p in audience]
    result=deepcopy(result)
    for peer in peers:
        other=shared_meal.catalog(movement.catalog(movement.basic_catalog({'version':'target-options','candidates':[]},scene=scene,target_ref=peer),scene=scene,target_ref=peer),meal,owner,peer,include_self_activities=True)
        for row in other['candidates']:
            if row['key'] in ('eat_rice','stroll','relocate'):continue
            item=deepcopy(row);item.update(action_key=row['key'],key=row['key']+'@'+peer,target_ref=peer)
            result['candidates'].append(item)
    if scene.get('objects'):
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
    return result
