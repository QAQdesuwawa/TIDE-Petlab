"""Readonly Tk geometry for registered visible pet windows, independent of time/state."""
import time,uuid
from model_packages.desktop_pet_v1 import spatial

class DesktopScene:
 def __init__(self,windows):
  self.windows=windows;self.source_ref='desktop:'+uuid.uuid4().hex
 def snapshot(self,owner_ref):
  rows=[];owner=None
  for alias,window in self.windows.items():
   if window.closed or not window.root.winfo_viewable():continue
   widget=window.view.sprite
   ref=window.configuration.definition.owner_ref
   rows.append({'ref':ref,'label':window.configuration.definition.name,'bounds':[widget.winfo_rootx(),widget.winfo_rooty(),widget.winfo_width(),widget.winfo_height()]})
   if ref==owner_ref:owner=window.root
  if owner is None:return None
  return spatial.create(owner_ref=owner_ref,source_ref=self.source_ref,sampled_at_ms=time.time_ns()//1_000_000,
   viewport=[owner.winfo_vrootx(),owner.winfo_vrooty(),owner.winfo_vrootwidth(),owner.winfo_vrootheight()],participants=rows)
