"""Readonly Tk geometry for registered visible pet windows, independent of time/state."""
import time,uuid
from model_packages.desktop_pet_v1 import spatial

def _native_rect(widget):
 import sys
 if sys.platform!='win32' or not hasattr(widget,'tk'):return None
 import ctypes as c
 from ctypes import wintypes as w
 rect=w.RECT();getrect=c.WinDLL('user32',use_last_error=True).GetWindowRect
 getrect.argtypes=[w.HWND,c.POINTER(w.RECT)];getrect.restype=w.BOOL
 if not getrect(widget.winfo_id(),c.byref(rect)):raise c.WinError(c.get_last_error())
 return [rect.left,rect.top,rect.right,rect.bottom]

def sprite_screen_position(root,widget):
 rect=_native_rect(widget)
 if rect is not None:return rect[:2]
 # A mapped Win32 top-level can already have its final position while Tk's
 # child screen-origin cache is still zero until Configure is dispatched.
 # DesktopExecutor's sprite is a direct child; use its local layout offset.
 return [root.winfo_rootx()+widget.winfo_x(),root.winfo_rooty()+widget.winfo_y()]

def move_sprite(root,widget,point,hwnd=None):
 """Move the sprite to screen coordinates, accounting for window decoration."""
 if hwnd:
  import ctypes as c
  from ctypes import wintypes as w
  api=c.WinDLL('user32',use_last_error=True);rect=w.RECT()
  api.GetWindowRect.argtypes=[w.HWND,c.POINTER(w.RECT)];api.GetWindowRect.restype=w.BOOL
  if not api.GetWindowRect(hwnd,c.byref(rect)):raise c.WinError(c.get_last_error())
  actual=sprite_screen_position(root,widget)
  left=round(rect.left+point[0]-actual[0]);top=round(rect.top+point[1]-actual[1])
  api.SetWindowPos.argtypes=[w.HWND,w.HWND,c.c_int,c.c_int,c.c_int,c.c_int,w.UINT];api.SetWindowPos.restype=w.BOOL
  if not api.SetWindowPos(hwnd,None,left,top,0,0,0x15):raise c.WinError(c.get_last_error())
 else:
  actual=sprite_screen_position(root,widget)
  left=round(root.winfo_x()+point[0]-actual[0]);top=round(root.winfo_y()+point[1]-actual[1])
  root.geometry(f'{left:+d}{top:+d}')

class DesktopScene:
 def __init__(self,windows,*,objects=(),screen=None):
  self.windows=windows;self.objects=spatial.world_objects(objects);self.source_ref='desktop:'+uuid.uuid4().hex
  self.screen=screen
 def approach_landings(self,owner_ref):
  landings=[]
  for window in self.windows.values():
   if window.closed or not window.root.winfo_viewable():continue
   if window.configuration.definition.owner_ref==owner_ref:continue
   task=window.view.active
   if task is not None and not task.done.is_set() and task.movement_landing is not None:
    landings.append(task.movement_landing)
  return landings
 def snapshot(self,owner_ref):
  rows=[];owner=None
  for alias,window in self.windows.items():
   if window.closed or not window.root.winfo_viewable():continue
   widget=window.view.sprite
   ref=window.configuration.definition.owner_ref
   rows.append({'ref':ref,'label':window.configuration.definition.name,'bounds':[*sprite_screen_position(window.root,widget),widget.winfo_width(),widget.winfo_height()]})
   if ref==owner_ref:owner=window.root
  if owner is None:return None
  own=next(r for r in rows if r['ref']==owner_ref)
  objects=self.objects
  if self.screen is not None and self.screen.visible(own['bounds']):
   objects=[*objects,self.screen.object]
  return spatial.create(owner_ref=owner_ref,source_ref=self.source_ref,sampled_at_ms=time.time_ns()//1_000_000,
   viewport=[owner.winfo_vrootx(),owner.winfo_vrooty(),owner.winfo_vrootwidth(),owner.winfo_vrootheight()],participants=rows,objects=objects)
