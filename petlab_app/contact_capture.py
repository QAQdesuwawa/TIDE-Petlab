"""Bounded contact intervals, published through the existing participant source."""
import json
from pathlib import Path
from ops.storage import atomic_write_json
from model_packages.desktop_pet_v1.contact import contact_content

class ContactCapture:
 def __init__(self,workspace,owner,path):
  self.workspace=workspace;self.owner=owner;self.path=Path(path)
  self.state=json.loads(self.path.read_text('utf8')) if self.path.exists() else {'active':None,'serial':0,'pending':None}
  self._publish()
  # A restored contact ended at its last durable observation, not restart time.
  if self.state['active'] is not None:
   if self.state['active'].get('continued'):
    self.state['active']=None;self._save()
   else:self.cut(self.state['active']['last'],ended=True)
 def _save(self):
  self.path.parent.mkdir(parents=True,exist_ok=True)
  atomic_write_json(str(self.path),self.state,durable=True)
 def _publish(self):
  row=self.state['pending']
  if row is None:return
  c=contact_content(**row)
  self.workspace.submit_human(event_id=c.content_id,conversation='public',text=c.as_text(),at=row['ended_at'],addressed=c.recipient_refs,input_content=c)
  self.state['pending']=None;self._save()
 def sample(self,gesture,peer,position,at):
  self._publish();active=self.state['active']
  if active is not None and (active['gesture']!=gesture or active['peer']!=peer):self.cut(at,ended=True)
  if peer is None:return
  if self.state['active'] is None:
   self.state['active']={'gesture':gesture,'peer':peer,'start':at,'last':at,'position':list(position)};self._save()
  else:
   active=self.state['active']
   if at<active['last']:raise ValueError('contact capture time moved backwards')
   active.update(last=at,position=list(position))
 def cut(self,at,*,ended=False):
  self._publish();a=self.state['active']
  if a is None:return
  if at<a['last']:raise ValueError('contact boundary moved backwards')
  if a.get('continued') and at==a['start']==a['last']:
   if ended:self.state['active']=None;self._save()
   return
  self.state['serial']+=1
  self.state['pending']={'gesture_id':a['gesture'],'episode':self.state['serial'],'moved_ref':self.owner,'other_ref':a['peer'],
   'started_at':a['start'],'ended_at':at,'position':a['position']}
  self.state['active']=None if ended else {**a,'start':at,'last':at,'continued':True}
  self._save();self._publish()
