"""Host capture cursor for actual handed-off webpage intervals, never a clock/score."""
from pathlib import Path
from copy import deepcopy
import hashlib,json,threading
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from .external_input import archive_root
from .providers import _save_trace
VERSION='pet-reading-session-v1'
CHECKPOINT='pet-reading-checkpoint-v1'
REASONS={'user_contact','source_expired','technical_failure','window_closed'}


def validate_checkpoint(app,value,*,event_id,url):
 row=thaw_model_data(value)
 expected={'version','episode_id','event_id','sequence','previous_event_id','url','session_binding_fingerprint','artifact_sha256','read_span','coordinate','fingerprint'}
 if 'activity_source' in row:expected.add('activity_source')
 if (set(row)!=expected or row.get('version')!=CHECKPOINT or row.get('fingerprint')!=fp({k:v for k,v in row.items() if k!='fingerprint'})
  or row.get('event_id')!=event_id or row.get('url')!=url or row.get('session_binding_fingerprint')!=app.runtime.session.session_binding.fingerprint
  or type(row.get('sequence'))is not int or row['sequence']<1 or type(row.get('coordinate'))is not int):raise ValueError('reading checkpoint binding differs')
 if not isinstance(row.get('episode_id'),str) or not 0<len(row['episode_id'])<=128 or row['event_id']!='read:'+hashlib.sha256(row['episode_id'].encode('utf8')).hexdigest()+':'+str(row['sequence']):raise ValueError('reading episode identity differs')
 span=row['read_span']
 if not isinstance(span,(list,tuple)) or len(span)!=2 or any(type(n)is not int for n in span) or not 0<=span[0]<span[1]<=4096:raise ValueError('reading checkpoint range differs')
 if span[0]==0:
  if row['previous_event_id'] is not None or row['sequence']!=1:raise ValueError('reading first checkpoint differs')
 else:
  previous=app._product(source_id=row['previous_event_id'])['sources'].get(row['previous_event_id'])
  if previous is None:raise ValueError('reading previous interval missing')
  source=previous['evidence'].get('input_source',{});prior=source.get('reading_checkpoint',{})
  if (prior.get('episode_id')!=row['episode_id'] or prior.get('artifact_sha256')!=row['artifact_sha256']
   or prior.get('sequence')!=row['sequence']-1 or list(prior.get('read_span',[]))[-1:]!=[span[0]]
   or prior.get('coordinate',row['coordinate']+1)>row['coordinate']):raise ValueError('reading intervals are not contiguous')
 return row


class ReadingSession:
 """The caller supplies a real handoff boundary; never infer progress from time.

 Only one interval may be in flight. Stop cancels the unread suffix, while an
 already received interval can finish measurement/archive through recovery.
 """
 def __init__(self,product,*,episode_id,url,reader=None,activity_source=None):
  from .web_reading import validate_url,default_reader,store_page
  self.product=product;self.app=product.application;self._lock=threading.RLock();self._processing=False
  if not isinstance(episode_id,str) or not 0<len(episode_id)<=128:raise ValueError('reading episode identity invalid')
  url=validate_url(url);root=archive_root(self.app);root.mkdir(parents=True,exist_ok=True)
  self.path=root/(hashlib.sha256(episode_id.encode('utf8')).hexdigest()+'.reading.json')
  if self.path.exists():
   self._data=json.loads(self.path.read_text(encoding='utf8'))
   d=self._data
   if (d.get('version')!=VERSION or d.get('fingerprint')!=fp({k:v for k,v in d.items() if k!='fingerprint'})
    or d.get('episode_id')!=episode_id or d.get('url')!=url or d.get('binding')!=self.app.runtime.session.session_binding.fingerprint
    or activity_source is not None and d.get('activity_source')!=thaw_model_data(activity_source)):raise ValueError('reading session binding differs')
  else:
   if activity_source is not None:
    from .activity_sources import validate
    activity_source=validate(self.app,activity_source,url=url)
   page=(reader or default_reader(product))(url);page,artifact,digest=store_page(self.app,page,url)
   self._data={'version':VERSION,'episode_id':episode_id,'url':url,'binding':self.app.runtime.session.session_binding.fingerprint,
    'artifact':artifact.name,'artifact_sha256':digest,'provider':page.get('provider','external-reader'),'activity_source':activity_source,
    'accepted_end':0,'settled_end':0,'sequence':0,'previous_event_id':None,'pending':None,'stopped':False,'stop_reason':None,'cause_ref':None}
   self._save()
  self._page() # Validate source custody even if no new read will occur.

 @property
 def state(self):
  with self._lock:return deepcopy(self._data)

 def _save(self):
  self._data['fingerprint']=fp({k:v for k,v in self._data.items() if k!='fingerprint'});_save_trace(self.path,self._data)

 def _page(self):
  root=archive_root(self.app).resolve();path=(root/self._data['artifact']).resolve()
  if path.parent!=root or path.suffix!='.json':raise ValueError('reading artifact escapes archive')
  raw=path.read_bytes()
  if len(raw)>1048576 or hashlib.sha256(raw).hexdigest()!=self._data['artifact_sha256']:raise ValueError('reading original changed')
  page=json.loads(raw)
  if page['url']!=self._data['url']:raise ValueError('reading URL differs')
  return {**page,'provider':self._data['provider']}

 def _received(self,pending):return pending is not None and pending['event_id'] in self.app._product(source_id=pending['event_id'])['sources']

 def interrupt(self,*,reason,cause_ref=None):
  if cause_ref is not None and (not isinstance(cause_ref,str) or len(cause_ref)>512):raise ValueError('reading cause reference exceeds bound')
  if reason not in REASONS or reason in {'user_contact','source_expired'} and (not isinstance(cause_ref,str) or not cause_ref):raise ValueError('reading stop needs actual reason/source')
  with self._lock:
   d=self._data
   if d['stopped']:return self.state
   d.update(stopped=True,stop_reason=reason,cause_ref=cause_ref)
   self._save();return self.state

 def observe(self,*,end,coordinate,executor=None):
  with self._lock:
   d=self._data
   if d['stopped']:return None
   if self._processing or d['pending'] is not None:raise ValueError('finish or recover the current reading interval first')
   if type(end)is not int or not d['settled_end']<end<=min(4096,len(self._page()['content'])):raise ValueError('reading interval exceeds bounded handoff')
   seq=d['sequence']+1;identity='read:'+hashlib.sha256(d['episode_id'].encode('utf8')).hexdigest()+':'+str(seq)
   cp={'version':CHECKPOINT,'episode_id':d['episode_id'],'event_id':identity,'sequence':seq,'previous_event_id':d['previous_event_id'],
    'url':d['url'],'session_binding_fingerprint':d['binding'],'artifact_sha256':d['artifact_sha256'],'read_span':[d['settled_end'],end],'coordinate':coordinate}
   if d['activity_source'] is not None:cp['activity_source']=d['activity_source']
   cp['fingerprint']=fp(cp);validate_checkpoint(self.app,cp,event_id=identity,url=d['url'])
   before=deepcopy(d);d['pending']=cp;d['accepted_end']=end
   try:self._save()
   except BaseException:self._data=before;raise
  return self._process(executor=executor,retry_failed=False)

 def recover(self,*,executor=None):
  if self.state['pending'] is None:return None
  return self._process(executor=executor,retry_failed=True)

 def _process(self,*,executor,retry_failed):
  from .web_reading import read_page
  from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
  with self._lock:
   cp=deepcopy(self._data['pending'])
   if cp is None:return None
   if self._processing:raise ValueError('reading interval is already being processed')
   self._processing=True
  try:
   # This checkpoint was durably handed off before stop. Archive it even after
   # interruption; eligible input creates no pending direct reply or old speech.
   return read_page(self.product,event_id=cp['event_id'],url=cp['url'],reader=lambda _:self._page(),activity_source=self._data['activity_source'],
    reading_checkpoint=cp,coordinate=cp['coordinate'],executor=executor,response_policy='eligible',respond=False,
    is_cancelled=lambda:False,retry_failed=retry_failed)
  finally:
   with self._lock:
    self._processing=False
    if self._received(cp):self._data['accepted_end']=cp['read_span'][1]
    if cp['event_id'] in self.app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{}):
     self._data.update(settled_end=cp['read_span'][1],sequence=cp['sequence'],previous_event_id=cp['event_id'],pending=None)
    self._save()


def restore_host_readings(product):
 """Discover durable handoffs at startup without advancing time or invoking tools.

 Host loss terminates further delivery. Already handed-off intervals remain
 recoverable at their captured coordinates through the existing resume action.
 """
 sessions=[]
 for path in archive_root(product.application).glob('*.reading.json'):
  data=json.loads(path.read_text(encoding='utf8'))
  reading=ReadingSession(product,episode_id=data['episode_id'],url=data['url'],
   reader=lambda _:(_ for _ in ()).throw(ValueError('reading recovery must not fetch')))
  if reading.path.resolve()!=path.resolve():raise ValueError('reading archive identity differs')
  reading.interrupt(reason='window_closed')
  checkpoint=reading.state['pending']
  if checkpoint is not None:
   validate_checkpoint(product.application,checkpoint,event_id=checkpoint['event_id'],url=checkpoint['url'])
   sessions.append(reading)
 return sorted(sessions,key=lambda r:(r.state['pending']['coordinate'],r.state['pending']['event_id']))
