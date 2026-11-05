"""Multiple pet views over independent Actor processes and canonical sources."""
from pathlib import Path
import json,uuid
import tkinter as tk
from .multi_pet import MultiPetWorkspace,multi_configurations
from .process_actor import ProcessDesktopActor
from .desktop_app import DesktopPetWindow,load_configuration
from .product import wall_clock_ms

class MultiPetWindow(DesktopPetWindow):
 def __init__(self,root,*,workspace,alias,**kwargs):
  self.workspace=workspace;self.alias=alias;self.public_mode=True
  from .contact_worker import ContactWorker
  self.contact_capture=ContactWorker(workspace,workspace.definitions[alias].owner_ref,workspace.actor_path(alias)/'contact-capture.json')
  self._contact_error_shown=False
  self._public_target_refs=(workspace.definitions[alias].owner_ref,);self.notify_source=None
  try:super().__init__(root,**kwargs)
  except BaseException:
   self.contact_capture.close(wall_clock_ms());self.contact_capture.join()
   raise
  name=self.configuration.definition.name
  self.context_menu.entryconfigure(0,label='对'+name+'说（公开）',command=self.open_addressed_dialog)
  self.context_menu.insert_command(1,label='同时对所有角色说（公开）',command=self.open_public_dialog)
  self.context_menu.insert_command(2,label='私下与'+name+'交谈',command=self.open_private_dialog)
  self.context_menu.insert_separator(3)
  peers=tk.Menu(self.context_menu,tearoff=False)
  owner=self.configuration.definition.owner_ref
  for ref,label in workspace.aliases.items():
   if ref==owner:continue
   name=workspace.definitions[label].name
   peers.add_command(label='与'+name+'交谈时说悄悄话',command=lambda ref=ref:workspace.set_speech_mode(owner,ref,'private'))
   peers.add_command(label='与'+name+'交谈时公开说',command=lambda ref=ref:workspace.set_speech_mode(owner,ref,'public'))
  self.context_menu.add_cascade(label='同伴交谈方式（不触发开口）',menu=peers)
 def _poll(self):
  if self.contact_capture.error is not None and not self._contact_error_shown:
   self._contact_error_shown=True
   self._show_failure('接触记录未完成：'+str(self.contact_capture.error))
  super()._poll()
 def close(self):
  self.contact_capture.close(wall_clock_ms())
  super().close()
 def open_dialog(self):
  # Error/recovery surfaces reopen the current mode; a private draft must not
  # silently become public when an unfinished interaction is reported.
  super().open_dialog()
  name=self.configuration.definition.name
  text=('对'+name+'说；其他角色可以听见' if self._public_target_refs else '同时对所有角色说；它们分别决定回应') if self.public_mode else '这条消息仅与 '+name+' 分享'
  self.status.configure(text=text)
 def open_addressed_dialog(self):
  # Visibility and addressing are different edges. The other pet witnesses
  # this public input, but is not directly asked for a simultaneous answer.
  self.public_mode=True;self._public_target_refs=(self.configuration.definition.owner_ref,)
  self.open_dialog()
 def open_public_dialog(self):
  self.public_mode=True;self._public_target_refs=None
  self.open_dialog()
 def open_private_dialog(self):
  self.public_mode=False;self._public_target_refs=None
  self.open_dialog()
 def send_text(self,event=None):
  if self.closed:return 'break'
  text=self.entry.get()
  if text.strip():
   if self._needs_ingress_recovery():return 'break'
   self.entry.delete(0,'end')
   targets=((self._public_target_refs or tuple(d.owner_ref for d in self.workspace.definitions.values()))
    if self.public_mode else (self.configuration.definition.owner_ref,))
   if self.notify_source is not None:self.notify_source(targets)
   else:self.product.note_participant_activity()
   self.hide_speech();self.view.clear_gaze()
   self.view.select_presentation_target(self.configuration.definition.user_ref,pending=True)
   self.workspace.submit_human(event_id='input:'+uuid.uuid4().hex,conversation='public' if self.public_mode else 'private:'+self.alias,text=text,at=wall_clock_ms(),addressed=targets)
   self.hide_dialog()
  return 'break'

class MultiDesktop:
 def __init__(self,root,*,configuration,workspace_root,asset_dir,count=None):
  self.root=root;root.withdraw();self.configurations=multi_configurations(configuration,workspace_root=workspace_root,count=count)
  self.workspace=MultiPetWorkspace(workspace_root,{k:c.definition for k,c in self.configurations.items()})
  self.windows={};self.actors={};self.leases=[];self._close_alarm=None;self._scene_alarm=None;self.asset_dir=Path(asset_dir).resolve()
  try:
   for index,(alias,cfg) in enumerate(self.configurations.items()):
    local=self.workspace.actor_path(alias);local.mkdir(parents=True,exist_ok=True)
    from .launch_lease import acquire
    self.leases.append(acquire(local/'session.json'))
    from .asset_inspection import preflight_visual_capabilities
    from .desktop_view import DesktopExecutor
    import sys
    declared=preflight_visual_capabilities(self.asset_dir,native=sys.platform=='win32') if cfg.expression_codes else None
    actor=ProcessDesktopActor(configuration=cfg,session_path=local/'session.json',trace_dir=local/'providers',checkpoint_storage_mode='delta-v1',visual_capabilities=declared,defer_startup=True,multi_workspace={'root':str(self.workspace.root),'alias':alias})
    self.actors[alias]=actor
    surface=tk.Toplevel(root)
    view=DesktopExecutor(surface,asset_dir=self.asset_dir,trace_dir=local/'playback',speech_presenter=lambda text:None)
    if cfg.expression_codes:
     view.set_expression('normal')
     if view.presentation_capabilities()!=declared:raise ValueError('multi view capabilities differ from actor declaration')
    actor.wait_until_ready()
    window=MultiPetWindow(surface,workspace=self.workspace,alias=alias,product=actor,configuration=cfg,asset_dir=self.asset_dir,trace_dir=local/'playback',view=view)
    surface.geometry('+'+str(80+(index%4)*min(260,max(160,(root.winfo_screenwidth()-320)//4)))+'+'+str(200+(index//4)*300))
    self.windows[alias]=window
    view.content_slot_available=lambda content,alias=alias:self._content_slot_available(alias,content)
    window.notify_source=self._notify_source
   from .scene_host import DesktopScene
   self.scene=DesktopScene(self.windows)
   from .meal_host import MealHost
   self.meal=(MealHost(self.workspace.root/'shared-meal.sqlite3',tuple(c.definition.owner_ref for c in self.configurations.values())) if len(self.configurations)>=2 else None)
   self._meal_fingerprint=None
   if self.meal is not None:self.meal.subscribe(self._present_meal)
   for window in self.windows.values():
    owner=window.configuration.definition.owner_ref
    window.view.visible_activity_observer=lambda parent,items,owner=owner:self._notice_playback(owner,parent,items)
    window.view.scene_snapshot=lambda owner=owner:self.scene.snapshot(owner)
    window.view.meal_host=self.meal
    window.view.meal_owner=owner
    if self.meal is not None:window.context_menu.insert_command(0,label='开饭：给 '+window.configuration.definition.name+' 一碗饭',command=lambda owner=owner:self.start_meal(owner))
   self._scene_signatures={}
   for actor in self.actors.values():actor.activate()
   self._refresh_scene()
   self._close_alarm=root.after(100,self._observe_closed)
  except Exception:
   self.close();self.join();raise
 def start_meal(self,owner):
  if self.meal is None:raise ValueError('shared bowl requires at least two pets')
  # One actual menu interaction, routed to both owners through the same inbox.
  if any(w.closed or w._needs_ingress_recovery() for w in self.windows.values()):return
  for window in self.windows.values():
   window.product.note_participant_activity()
   window.view.interrupt_activity(reason='user_contact',cause_ref='meal:new-scene')
  self.meal.start(owner)
  self._publish_meal()
  from model_packages.desktop_pet_v1.shared_meal import placement
  value=self.meal.snapshot();content=placement(value)
  self.workspace.submit_human(event_id='meal:placement:'+value['interaction_id'],conversation='public',
   text=content.as_text(),at=wall_clock_ms(),addressed=content.recipient_refs,input_content=content)
 def _publish_meal(self):
  if self.meal is None:return
  value=self.meal.snapshot();signature=None if value is None else value['fingerprint']
  if signature==self._meal_fingerprint:return
  for window in self.windows.values():
   if not window.closed:
    # A visible meal is not permission for every observer to operate the bowl.
    # Public execution milestones still reach observers through the normal relay.
    owner=window.configuration.definition.owner_ref
    window.product.update_meal(value if value is not None and owner in value['participants'] else None)
    window.view.update_meal_pose(value)
  self._meal_fingerprint=signature
 def _present_meal(self,value):
  # Both visual owners change in this Tk turn; Actor facts still travel
  # through the existing publication/receipt path on the next scene refresh.
  for window in self.windows.values():
   if not window.closed:window.view.update_meal_pose(value)
 def _notice_playback(self,owner,parent,items):
  # Actual UI stage boundaries arrive before output measurement/archival.
  # This only turns an idle observer; formal evidence uses the existing recorder.
  for item in items:
   content=item['content']
   if content['kind']!='action' or item['phase']!='started':continue
   for window in self.windows.values():
    ref=window.configuration.definition.owner_ref
    if not window.closed and ref!=owner and ref in content['recipient_refs']:
     window.view.notice_visible_action(owner,parent+'#'+content['content_id'])

 def _refresh_scene(self):
  # Latest geometry replaces itself, never enters the input/state journal.
  self._publish_meal()
  for alias,window in self.windows.items():
   if window.closed:continue
   value=self.scene.snapshot(window.configuration.definition.owner_ref)
   signature=None if value is None else (value['viewport'],value['participants'])
   if alias not in self._scene_signatures or self._scene_signatures[alias]!=signature:
    window.product.update_scene(value);self._scene_signatures[alias]=signature
  self._scene_alarm=self.root.after(100,self._refresh_scene)
 def _content_slot_available(self,alias,content):
  """Each pet owns its bubble; peer influence is gated by committed input."""
  # The local Executor still owns same-pet reading order and cancellation.
  # Another pet's visible bubble never blocks this already-authorized output.
  return True
 def _notify_source(self,targets):
  for alias,window in self.windows.items():
   if window.configuration.definition.owner_ref in targets and not window.closed:
    window.product.note_participant_activity();window.hide_speech();window.view.clear_gaze()
    window.view.select_presentation_target(window.configuration.definition.user_ref,pending=True)
 def _observe_closed(self):
  # Individual user window closures are visible facts for the remaining cast.
  # Whole-host shutdown is handled separately and never schedules new work.
  if not hasattr(self,'_departures'):
   self._departures=set();self._departure_jobs=[]
   from concurrent.futures import ThreadPoolExecutor
   self._presence_worker=ThreadPoolExecutor(max_workers=1,thread_name_prefix='pet-presence')
  for alias,window in self.windows.items():
   if not window.closed or alias in self._departures:continue
   self._departures.add(alias)
   observers=tuple(w.configuration.definition.owner_ref for w in self.windows.values() if not w.closed and w.root.winfo_viewable())
   if observers:
    from model_packages.desktop_pet_v1.presence import departure
    at=wall_clock_ms();c=departure(scene_ref=self.scene.source_ref,owner_ref=window.configuration.definition.owner_ref,observers=observers,at=at)
    self._departure_jobs.append(self._presence_worker.submit(self.workspace.submit_human,event_id=c.content_id,
     conversation='public',text=c.as_text(),at=at,addressed=c.recipient_refs,input_content=c))
  for job in list(self._departure_jobs):
   if job.done():
    self._departure_jobs.remove(job)
    try:job.result()
    except Exception as error:
     for w in self.windows.values():
      if not w.closed:w._show_failure('离场记录未完成：'+str(error))
  if all(w.closed for w in self.windows.values()):self.root.destroy();return
  self._close_alarm=self.root.after(100,self._observe_closed)
 def close(self):
  if self._scene_alarm is not None:
   try:self.root.after_cancel(self._scene_alarm)
   except tk.TclError:pass
   self._scene_alarm=None
  if self._close_alarm is not None:
   try:self.root.after_cancel(self._close_alarm)
   except tk.TclError:pass
   self._close_alarm=None
  for window in self.windows.values():
   if not window.closed:window.close()
  for actor in self.actors.values():actor.request_stop()
  try:self.root.destroy()
  except tk.TclError:pass
 def join(self):
  if hasattr(self,'_presence_worker'):self._presence_worker.shutdown(wait=True)
  for window in self.windows.values():window.contact_capture.join()
  for window in self.windows.values():window.worker.join(15)
  for actor in self.actors.values():actor.close()
  for lease in self.leases:lease.close()
  self.leases=[]

def main():
 import argparse
 parser=argparse.ArgumentParser(description=__doc__)
 parser.add_argument('--config',required=True,type=Path);parser.add_argument('--workspace',required=True,type=Path);parser.add_argument('--assets',required=True,type=Path)
 parser.add_argument('--count',type=int,default=None,help='New workspace pet count; omitted restores the saved cast')
 args=parser.parse_args();configuration=load_configuration(args.config)
 root=tk.Tk();host=MultiDesktop(root,configuration=configuration,workspace_root=args.workspace,asset_dir=args.assets,count=args.count)
 try:root.mainloop()
 finally:host.close();host.join()
if __name__=='__main__':main()
