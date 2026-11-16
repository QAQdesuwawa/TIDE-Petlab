"""Bound desktop prop moves and their actual visible result."""

from dataclasses import replace

from framework.model_api import thaw_model_data
from . import movement, spatial
from .events import Action

KEY='move_prop'
PREFIX='world:playground:'
OFFSETS={'left':(-88,0),'right':(88,0),'up':(0,-64),'down':(0,64)}


def movable(obj):
    return obj['ref'].startswith(PREFIX) and obj['ref']!=PREFIX+'meal-button' and 'bounds' in obj


def _destination(obj,direction):
    if not movable(obj) or direction not in OFFSETS:raise ValueError('invalid movable prop target')
    return [a+b for a,b in zip(obj['point'],OFFSETS[direction])]


def _clear(scene,obj,point):
    x,y,w,h=obj['bounds']
    left,top=(start+end-before for start,end,before in zip((x,y),point,obj['point']))
    vx,vy,vw,vh=scene['viewport']
    if not (vx<=left<=vx+vw-w and vy<=top<=vy+vh-h):return False
    others=(*scene['participants'],*scene['objects'])
    return not any(left<ox+ow and ox<left+w and top<oy+oh and oy<top+h
                   for row in others if row['ref']!=obj['ref'] and 'bounds' in row
                   for ox,oy,ow,oh in (row['bounds'],))


def choices(scene,obj):
    if not movable(obj):return
    for direction in OFFSETS:
        point=_destination(obj,direction)
        if _clear(scene,obj,point):
            x,y=point
            yield {'key':KEY+'_'+direction+'@'+obj['ref'],'action_key':KEY,'target_ref':obj['ref'],
                   'world_object':obj,'prop_direction':direction,'destination':point,
                   'approach':movement.arrival_definition(),
                   'description':f'走到{obj["label"]}旁，将它挪到（{x}，{y}）'}


def content(target,row):
    obj=spatial.world_object(row['world_object'])
    direction=row['prop_direction']; point=_destination(obj,direction)
    if (target!=obj['ref'] or list(row['destination'])!=point
            or row['key'] not in (KEY,KEY+'_'+direction+'@'+target)
            or row['approach']!=movement.arrival_definition()):
        raise ValueError('prop move offer differs')
    return Action(content_id='activity',semantic_key=KEY,description=row['description'],recipient_refs=(target,),
                  parameters={'prop_move':{'object':obj,'direction':direction,'destination':point}})


def check(scene,binding):
    scene=spatial.validate(scene)
    old=binding['object'];point=list(binding['destination'])
    current=next((obj for obj in scene.get('objects',()) if obj['ref']==old['ref']),None)
    if (current!=thaw_model_data(old) or point!=_destination(current,binding['direction'])
            or not _clear(scene,current,point)):
        raise ValueError('prop source changed or destination is blocked')
    require_reach(scene,current)
    return {'ref':current['ref'],'from':list(current['point']),'to':point}


def require_reach(scene,current):
    own=next(row['bounds'] for row in scene['participants'] if row['ref']==scene['owner_ref'])
    x,y,w,h=own
    ox,oy,ow,oh=current['bounds']
    if max(x-ox-ow,ox-x-w,y-oy-oh,oy-y-h,0)>48:
        raise ValueError('prop is out of reach')


def realized(item,result,status):
    binding=item.parameters['prop_move'];obj=binding['object']
    expected={'ref':obj['ref'],'from':list(obj['point']),'to':list(binding['destination'])}
    if status=='completed':
        if thaw_model_data(result)!=expected:raise ValueError('prop move lacks the actual coordinate result')
        text=f'把{obj["label"]}从 {expected["from"]} 挪到了 {expected["to"]}。'
        return replace(item,description=text,parameters={'prop_move_result':expected})
    if result is not None:raise ValueError('unfinished prop move cannot have a result')
    text=f'开始伸手挪{obj["label"]}。' if status=='started' else f'挪动{obj["label"]}的动作中止，未完成搬动。'
    return replace(item,description=text,parameters={})
