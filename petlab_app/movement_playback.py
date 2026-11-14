"""Target-facing preparation, then finite movement under the original guard."""
import time
from model_packages.desktop_pet_v1 import movement
from product_adapters.desktop_pet_v1.execution import ExecutionCancelled
from . import movement_route


def play(view,task,index,planned=None,started=None,orient_until=None,target_bounds=None,*,rerouted=frozenset(),detour_from=None):
 if view.closed or task.done.is_set():return
 try:
  if task.is_cancelled is not None and task.is_cancelled():raise ExecutionCancelled('movement source expired')
  content=task.contents[index];identity=content.content_id
  child=getattr(task,'child_results',{}).get(identity)
  if child is not None and child['status']!='running':return
  scene=view.scene_snapshot() if callable(view.scene_snapshot) else None
  basic=content.parameters['movement'].get('version')==movement.FREE_VERSION
  free=basic and content.parameters['movement']['mode']!='move_away'
  target=content.parameters['movement']['target_ref']
  if scene is None:raise ExecutionCancelled('movement scene unavailable')
  current_target=[] if free else movement.target_bounds(scene,target)
  if current_target is None:
   stop=getattr(view,'_stop_movement_child',None)
   if getattr(task,'composite',None) is not None and stop is not None:
    stop(task,identity,reason='target_unavailable',cause_ref=target)
    return
   raise ExecutionCancelled('movement target left visible scene')
  # The authorization selects a participant, not a frozen screen coordinate.
  # Re-route from our actual position when that same participant moves. Keep
  # the parent intention and its speech dependencies; all guards above and
  # collision checks below still apply to every frame.
  if not basic and target_bounds is not None and list(current_target)!=list(target_bounds):
   planned=None;started=None
  solid_approach=not basic and any(obj['ref']==target and 'bounds' in obj for obj in scene.get('objects',()))
  peer_approach=not basic and not target.startswith('world:') and content.parameters['movement'].get('version')==movement.STEP_VERSION
  routed_approach=solid_approach or peer_approach
  alongside=bool(content.parameters.get('audience_context') or content.parameters.get('whisper_context'))
  if planned is None:
   task.movement_landing=None
   # Plan against the other approaches' future landing spots as well as live geometry.
   landings=getattr(view,'approach_landings',None)
   reserved=landings() if routed_approach and callable(landings) else ()
   routes={}
   def reachable(start,end):
    path=movement_route.route(scene,start,end,reserved=reserved)
    routes[tuple(end)]=path
    return path is not None
   try:
    if routed_approach:
     planned=movement.plan(scene,content.parameters['movement'],
      reserved=reserved,reachable=reachable,
      alongside_only=alongside)
    else:planned=movement.plan(scene,content.parameters['movement'])
   except ValueError as error:
    if detour_from is not None:
     view.interrupt_activity(reason='physical_contact',cause_ref=detour_from)
     return
    if peer_approach and alongside and task.composite is not None:
     # A reservation is transient. Prove a route would exist without it,
     # but only ever start walking with reservations and live bodies applied.
     if reserved and child is None:
      try:
       movement.plan(scene,content.parameters['movement'],alongside_only=True,
        reachable=lambda start,end:movement_route.route(scene,start,end) is not None)
      except ValueError:
       pass
      else:
       if not hasattr(task,'waiting_approaches'):task.waiting_approaches=set()
       task.waiting_approaches.add(identity)
       def recheck():
        task.waiting_approaches.discard(identity)
        play(view,task,index)
       view._schedule(80,recheck)
       return
     view._stop_movement_child(task,identity,reason='target_unavailable',cause_ref=target)
     return
    if peer_approach:
     # Preserve the existing occupied-destination receipt if the search
     # cannot find an alternate route. This fallback is never played.
     planned=movement.plan(scene,content.parameters['movement'])
    elif target.startswith('world:'):
     raise ExecutionCancelled('target has no usable approach') from error
    else:raise
   target_bounds=list(current_target)
   import math
   path=routes.get(tuple(planned['end'])) if routed_approach else movement_route.route(scene,planned['start'],planned['end'])
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
   from .rear_view import route_vector
   planned={**planned,'route':path,'route_px':length,'heading':heading,'vector':route_vector(path,0),
            'duration_ms':max(planned['duration_ms'],round(planned['duration_ms']*length/max(1,planned['distance_px'])))}
   if routed_approach:
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
   # An already satisfied arrival still publishes its result and checks the
   # live geometry. It has no walking or look-before-walk interval.
   orient_until=time.monotonic()+(lead/1000 if length else 0)
  if started is None:
   if time.monotonic()<orient_until:
    view._schedule(16,lambda:play(view,task,index,planned,None,orient_until,target_bounds,rerouted=rerouted));return
   started=time.monotonic()
  elapsed=(time.monotonic()-started)*1000
  stationary=all(point==planned['route'][0] for point in planned['route'])
  progress=1.0 if stationary else elapsed/planned['duration_ms']
  point,heading=movement_route.sample(planned['route'],progress,heading=planned['heading'])
  from .spatial_contact import blocker
  contact=blocker(scene,point,include_objects=True)
  if contact is not None:
   peer_contact=not contact.startswith('world:')
   if routed_approach and peer_contact and contact not in rerouted:
    # Another actor can enter the route after it was planned. Preserve this
    # action and its actual start, and try a fresh route once per blocker.
    view._schedule(16,lambda:play(view,task,index,rerouted=rerouted|{contact},detour_from=contact))
    return
   reason='physical_contact' if peer_contact else 'source_expired'
   view.interrupt_activity(reason=reason,cause_ref=contact)
   return
  from .rear_view import route_vector
  view._walk_pose(task,identity,{**planned,'heading':heading,'vector':route_vector(planned['route'],progress),
   'walked_px':min(1.,progress)*planned['route_px']},point)
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
  if actual!=list(planned['start']):view.set_activity_badge(content.semantic_key)
  task.action_frames[identity]=task.action_frames.get(identity,0)+1
  if progress>=1:
   task.movement_landing=None
   view._end_walk(task)
   view.set_activity_badge()
   task.completed.append(identity)
   if task.composite is not None:
    task.child_results[identity].update(status='completed',ended_at_ms=time.time_ns()//1_000_000)
    view._notify_visible(task);view._maybe_finish_composite(task)
   else:view._next_content(task,index+1)
   return
  view._schedule(16,lambda:play(view,task,index,planned,started,orient_until,target_bounds,rerouted=rerouted))
 except Exception as error:view._failed(task,error)
