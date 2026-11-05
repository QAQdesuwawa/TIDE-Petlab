"""Target-facing preparation, then finite movement under the original guard."""
import time
from model_packages.desktop_pet_v1 import movement,spatial
from product_adapters.desktop_pet_v1.execution import ExecutionCancelled


def play(view,task,index,planned=None,started=None,orient_until=None,target_bounds=None):
 if view.closed or task.done.is_set():return
 try:
  if task.is_cancelled is not None and task.is_cancelled():raise ExecutionCancelled('movement source expired')
  content=task.contents[index];identity=content.content_id
  scene=view.scene_snapshot() if callable(view.scene_snapshot) else None
  basic=content.parameters['movement'].get('version')==movement.FREE_VERSION
  free=basic and content.parameters['movement']['mode']!='move_away'
  if scene is None or (not free and content.parameters['movement']['target_ref'] not in spatial.targets(scene)):raise ExecutionCancelled('movement target left visible scene')
  current_target=[] if free else next(r['bounds'] for r in scene['participants'] if r['ref']==content.parameters['movement']['target_ref'])
  if not basic and target_bounds is not None and list(current_target)!=list(target_bounds):raise ExecutionCancelled('movement target changed its frozen location')
  if planned is None:
   planned=movement.plan(scene,content.parameters['movement']);target_bounds=list(current_target)
   from .movement_route import route
   import math
   path=route(scene,planned['start'],planned['end'])
   if path is None:
    from model_packages.desktop_pet_v1.physical_space import overlaps
    own=next(r for r in scene['participants'] if r['ref']==scene['owner_ref'])
    occupied=next((r for r in scene['participants'] if r['ref']!=scene['owner_ref'] and overlaps([*planned['end'],*own['bounds'][2:]],r['bounds'])),None)
    if occupied is not None:
     from product_adapters.desktop_pet_v1.execution import ExecutionBlocked
     raise ExecutionBlocked({'owner_ref':scene['owner_ref'],'blocker_ref':occupied['ref'],
      'action_id':task.action_id,'content_id':identity,'target_ref':planned.get('target_ref',content.parameters['movement'].get('target_ref')),
      'position':list(own['bounds'][:2]),'destination':list(planned['end']),
      'blocker_bounds':list(occupied['bounds']),'occurred_at_ms':time.time_ns()//1_000_000})
    raise ExecutionCancelled('movement has no free desktop route')
   length=sum(math.dist(a,b) for a,b in zip(path,path[1:]))
   planned={**planned,'route':path,'duration_ms':max(planned['duration_ms'],round(planned['duration_ms']*length/max(1,planned['distance_px'])))}
   view._select_execution_target(task)
   task.movement_results[identity]=movement.result(planned,planned['start'])
   task.child_results.setdefault(identity,{'status':'running','started_at_ms':time.time_ns()//1_000_000,'ended_at_ms':None})
   lead=view._prepare_target_facing(task,identity,planned,('front' if basic else content.parameters['movement']['target_ref']))
   orient_until=time.monotonic()+lead/1000
  if started is None:
   if time.monotonic()<orient_until:
    view._schedule(16,lambda:play(view,task,index,planned,None,orient_until,target_bounds));return
   started=time.monotonic()
  elapsed=(time.monotonic()-started)*1000;point=movement.position(planned,elapsed/planned['duration_ms'])
  if 'route' in planned:
   from .movement_route import point as route_point
   point=route_point(planned['route'],elapsed/planned['duration_ms'])
  from .spatial_contact import blocker
  contact=blocker(scene,point)
  if contact is not None:
   view.interrupt_activity(reason='physical_contact',cause_ref=contact)
   return
  from .scene_host import move_sprite
  move_sprite(view.root,view.sprite,point,view.alpha_surface.hwnd if view.alpha_surface is not None else None)
  view.root.update_idletasks()
  from .scene_host import sprite_screen_position
  actual=sprite_screen_position(view.root,view.sprite)
  task.movement_results[identity]=movement.result(planned,actual)
  visual=task.movement_visuals.get(identity)
  if visual is not None and visual.get('first_motion_at_ms') is None and actual!=list(planned['start']):visual['first_motion_at_ms']=time.time_ns()//1_000_000
  view._walk_pose(task,identity,planned,actual)
  task.action_frames[identity]=task.action_frames.get(identity,0)+1
  if elapsed>=planned['duration_ms']:
   view._end_walk(task)
   task.completed.append(identity)
   if task.composite is not None:
    task.child_results[identity].update(status='completed',ended_at_ms=time.time_ns()//1_000_000)
    view._notify_visible(task);view._maybe_finish_composite(task)
   else:view._next_content(task,index+1)
   return
  view._schedule(16,lambda:play(view,task,index,planned,started,orient_until,target_bounds))
 except Exception as error:view._failed(task,error)
