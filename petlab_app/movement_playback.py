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
  if scene is None or content.parameters['movement']['target_ref'] not in spatial.targets(scene):raise ExecutionCancelled('movement target left visible scene')
  current_target=next(r['bounds'] for r in scene['participants'] if r['ref']==content.parameters['movement']['target_ref'])
  if target_bounds is not None and list(current_target)!=list(target_bounds):raise ExecutionCancelled('movement target changed its frozen location')
  if planned is None:
   planned=movement.plan(scene,content.parameters['movement']);target_bounds=list(current_target)
   view._select_execution_target(task)
   task.movement_results[identity]=movement.result(planned,planned['start'])
   task.child_results.setdefault(identity,{'status':'running','started_at_ms':time.time_ns()//1_000_000,'ended_at_ms':None})
   lead=view._prepare_target_facing(task,identity,planned,content.parameters['movement']['target_ref'])
   orient_until=time.monotonic()+lead/1000
  if started is None:
   if time.monotonic()<orient_until:
    view._schedule(16,lambda:play(view,task,index,planned,None,orient_until,target_bounds));return
   started=time.monotonic()
  elapsed=(time.monotonic()-started)*1000;point=movement.position(planned,elapsed/planned['duration_ms'])
  left=point[0]-view.sprite.winfo_x();top=point[1]-view.sprite.winfo_y()
  if view.alpha_surface is not None and view.alpha_surface.hwnd:
   import ctypes as c
   from ctypes import wintypes as w
   setpos=c.WinDLL('user32',use_last_error=True).SetWindowPos
   setpos.argtypes=[w.HWND,w.HWND,c.c_int,c.c_int,c.c_int,c.c_int,w.UINT];setpos.restype=w.BOOL
   if not setpos(view.alpha_surface.hwnd,None,left,top,0,0,0x15):raise c.WinError(c.get_last_error())
  else:view.root.geometry(f'{left:+d}{top:+d}')
  view.root.update_idletasks()
  actual=[view.sprite.winfo_rootx(),view.sprite.winfo_rooty()]
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
