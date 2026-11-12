"""Target-facing preparation, then finite movement under the original guard."""
import time
from model_packages.desktop_pet_v1 import movement
from product_adapters.desktop_pet_v1.execution import ExecutionCancelled
from . import movement_route


def play(view,task,index,planned=None,started=None,orient_until=None,target_bounds=None):
 if view.closed or task.done.is_set():return
 try:
  if task.is_cancelled is not None and task.is_cancelled():raise ExecutionCancelled('movement source expired')
  content=task.contents[index];identity=content.content_id
  scene=view.scene_snapshot() if callable(view.scene_snapshot) else None
  basic=content.parameters['movement'].get('version')==movement.FREE_VERSION
  free=basic and content.parameters['movement']['mode']!='move_away'
  target=content.parameters['movement']['target_ref']
  if scene is None:raise ExecutionCancelled('movement scene unavailable')
  current_target=[] if free else movement.target_bounds(scene,target)
  if current_target is None:raise ExecutionCancelled('movement target left visible scene')
  # The authorization selects a participant, not a frozen screen coordinate.
  # Re-route from our actual position when that same participant moves. Keep
  # the parent intention and its speech dependencies; all guards above and
  # collision checks below still apply to every frame.
  if not basic and target_bounds is not None and list(current_target)!=list(target_bounds):
   if not target.startswith('world:'):raise ExecutionCancelled('movement target changed its frozen location')
   planned=None;started=None
  solid_approach=not basic and any(obj['ref']==target and 'bounds' in obj for obj in scene.get('objects',()))
  if planned is None:
   task.movement_landing=None
   # Plan against the other approaches' future landing spots as well as live geometry.
   landings=getattr(view,'approach_landings',None)
   reserved=landings() if solid_approach and callable(landings) else ()
   routes={}
   def reachable(start,end):
    path=movement_route.route(scene,start,end,reserved=reserved)
    routes[tuple(end)]=path
    return path is not None
   try:
    if solid_approach:
     planned=movement.plan(scene,content.parameters['movement'],
      reserved=reserved,reachable=reachable)
    else:planned=movement.plan(scene,content.parameters['movement'])
   except ValueError as error:
    if not target.startswith('world:'):raise
    raise ExecutionCancelled('world object has no usable approach') from error
   target_bounds=list(current_target)
   import math
   path=routes[tuple(planned['end'])] if solid_approach else movement_route.route(scene,planned['start'],planned['end'])
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
   _,heading=movement_route.sample(path,0,heading=getattr(view,'_movement_heading',None) or 0)
   planned={**planned,'route':path,'heading':heading,
            'duration_ms':max(planned['duration_ms'],round(planned['duration_ms']*length/max(1,planned['distance_px'])))}
   if solid_approach:
    own=next(row['bounds'] for row in scene['participants'] if row['ref']==scene['owner_ref'])
    task.movement_landing=[*planned['end'],*own[2:]]
   first_segment=identity not in task.movement_results
   if first_segment:
    view._select_execution_target(task)
    task.movement_results[identity]=movement.result(planned,planned['start'])
   task.child_results.setdefault(identity,{'status':'running','started_at_ms':time.time_ns()//1_000_000,'ended_at_ms':None})
   visual=task.movement_visuals.get(identity)
   turning=visual is not None and visual['first_motion_at_ms'] is None
   lead=view._prepare_target_facing(task,identity,planned,('front' if basic else target)) if first_segment or turning else 0
   orient_until=time.monotonic()+lead/1000
  if started is None:
   if time.monotonic()<orient_until:
    view._schedule(16,lambda:play(view,task,index,planned,None,orient_until,target_bounds));return
   started=time.monotonic()
  elapsed=(time.monotonic()-started)*1000
  point,heading=movement_route.sample(planned['route'],elapsed/planned['duration_ms'],heading=planned['heading'])
  from .spatial_contact import blocker
  contact=blocker(scene,point,include_objects=True)
  if contact is not None:
   reason='source_expired' if contact.startswith('world:') else 'physical_contact'
   view.interrupt_activity(reason=reason,cause_ref=contact)
   return
  view._walk_pose(task,identity,{**planned,'heading':heading},point)
  from .scene_host import move_sprite
  move_sprite(view.root,view.sprite,point,view.alpha_surface.hwnd if view.alpha_surface is not None else None)
  view.root.update_idletasks()
  from .scene_host import sprite_screen_position
  actual=sprite_screen_position(view.root,view.sprite)
  # Actual-effect provenance spans the whole action, including re-routes.
  origin=task.movement_results[identity]['start']
  task.movement_results[identity]=movement.result({**planned,'start':origin},actual)
  visual=task.movement_visuals.get(identity)
  if visual is not None and visual.get('first_motion_at_ms') is None and actual!=list(planned['start']):visual['first_motion_at_ms']=time.time_ns()//1_000_000
  task.action_frames[identity]=task.action_frames.get(identity,0)+1
  if elapsed>=planned['duration_ms']:
   task.movement_landing=None
   view._end_walk(task)
   task.completed.append(identity)
   if task.composite is not None:
    task.child_results[identity].update(status='completed',ended_at_ms=time.time_ns()//1_000_000)
    view._notify_visible(task);view._maybe_finish_composite(task)
   else:view._next_content(task,index+1)
   return
  view._schedule(16,lambda:play(view,task,index,planned,started,orient_until,target_bounds))
 except Exception as error:view._failed(task,error)
