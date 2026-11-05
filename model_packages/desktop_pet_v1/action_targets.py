"""Executable per-target options within the already authorized audience."""
from copy import deepcopy
from . import movement,shared_meal,spatial

def expand(base,*,scene,meal,owner,primary,audience):
    from .basic_actions import catalog as basics
    base=movement.basic_catalog(basics(base,meal=meal,owner=owner),scene=scene,target_ref=primary)
    result=shared_meal.catalog(movement.catalog(base,scene=scene,target_ref=primary),meal,owner,primary,include_self_activities=True)
    if scene is None:return result
    peers=[p for p in spatial.targets(scene) if p not in ('front',owner,primary) and p in audience]
    if not peers:return result
    result=deepcopy(result)
    for peer in peers:
        other=shared_meal.catalog(movement.catalog(movement.basic_catalog({'version':'target-options','candidates':[]},scene=scene,target_ref=peer),scene=scene,target_ref=peer),meal,owner,peer,include_self_activities=True)
        for row in other['candidates']:
            if row['key'] in ('eat_rice','stroll','relocate'):continue
            item=deepcopy(row);item.update(action_key=row['key'],key=row['key']+'@'+peer,target_ref=peer)
            result['candidates'].append(item)
    return result
