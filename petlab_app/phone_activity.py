"""Product tool lifecycle. Only fetching runs off Actor; state settles on Actor."""
from copy import deepcopy
import hashlib,json,threading,time
from .web_result import paused,unavailable,failure
from .external_input import archive_root
from .providers import _save_trace
from framework.model_api import model_data_fingerprint as fp
VERSION='pet-phone-tool-host-v1'


class PhoneActivityHost:
 def __init__(self,product,*,sources,on_ready,reader=None,on_status=None,searches=(),searcher=None):
  from .web_search import resources
  self.searcher=searcher
  self.product=product;self.sources=tuple(sources)+resources(searches);self.on_ready=on_ready;self.reader=reader;self.on_status=None
  self._lock=threading.RLock();self._worker=None;self._result=None;self._reading=None
  self.root=archive_root(product.application);self.root.mkdir(parents=True,exist_ok=True)
  self.path=self.root/'phone-host.json';self._state=None;self.cursor=0
  if self.path.exists():
   d=json.loads(self.path.read_text(encoding='utf8'))
   if d.get('fingerprint')!=fp({k:v for k,v in d.items() if k!='fingerprint'}) or d.get('binding')!=product.application.runtime.session.session_binding.fingerprint:raise ValueError('phone host binding differs')
   self.cursor=d['cursor'];self._state=d
   if d['status'] in {'fetching','delivering'}:self.interrupt(reason='window_closed')
  self.on_status=on_status

 @property
 def state(self):
  with self._lock:return deepcopy(self._state)

 def catalog(self):
  from model_packages.desktop_pet_v1.phone_catalog import catalog
  with self._lock:
   busy=self._worker is not None and self._worker.is_alive() or self._state is not None and self._state['status'] in {'fetching','delivering'}
   resource=self.sources[self.cursor%len(self.sources)] if self.sources and not busy and not paused(self._state) else None
   from .web_search import decode
   topic=decode(resource) if resource is not None else None
   writer=getattr(self.product,'writer',None)
   if writer is None and topic is not None and 'query' not in topic:resource=None
   if writer is not None and self._state is not None and self._state.get('result_event_id') and not self._state.get('result_consumed',False):resource=None
   return catalog(resource)

 def resume_reading(self,event_id):
  # Technical capability reset only. The next original Tick still selects actions.
  with self._lock:
   if not paused(self._state) or self._state['event_id']!=event_id:return False
   self._state.update(reading_resumed=True,resumed_at_ms=time.time_ns()//1000000)
   self._save();return True

 def _fail(self,evidence,*,reason='technical_failure'):
  self._state.update(status='failed',reason=reason,failure=evidence,reading_resumed=False)
  if 'exception_type' in evidence:self._state['error_type']=evidence['exception_type']
  self._save()

 def _save(self):
  d=self._state;d['fingerprint']=fp({k:v for k,v in d.items() if k!='fingerprint'})
  _save_trace(self.root/(hashlib.sha256(d['event_id'].encode()).hexdigest()+'.phone.json'),d)
  _save_trace(self.path,d)
  if self.on_status is not None:self.on_status(deepcopy(d))

 def start(self,event_id):
  from .activity_sources import bind
  from .web_reading import default_reader
  with self._lock:
   if not self.sources or paused(self._state) or self._worker is not None and self._worker.is_alive():return False
   saved=self.root/(hashlib.sha256(event_id.encode()).hexdigest()+'.phone.json')
   if saved.exists():return False
   event=self.product.application.runtime.memory_instance(event_id).event
   actions=[c for c in event.contents if c.kind=='action' and c.semantic_key=='read_web' and c.parameters.get('execution_status')=='completed']
   if not actions:return False
   if len(actions)!=1:raise ValueError('phone output has multiple tool actions')
   action=actions[0];url=action.parameters['reading_url']
   if url not in self.sources:raise ValueError('selected phone URL is not configured')
   source=bind(self.product.application,event_id=event_id,url=url,content_id=action.content_id)
   reader=self.reader or default_reader(self.product)
   from .web_search import decode,default_search,select_result
   topic=decode(url);search_resource=url
   if topic is not None and 'query' not in topic:
    from .reading_intent import from_activity
    from .web_search import resources
    intent=from_activity(self.product.application,event_id,url)
    if intent is None:
     self._state={'version':VERSION,'binding':self.product.application.runtime.session.session_binding.fingerprint,
      'event_id':event_id,'url':url,'activity_source':source,'cursor':self.cursor,'status':'skipped','reason':'no_search_intent','cause_ref':None}
     self._save();return False
    search_resource=resources([{'query':intent['query'],'site':topic['site']}])[0]
   searcher=(self.searcher or default_search(self.product)) if topic else None
   self.cursor+=1;self._reading=None;self._result=None
   self._state={'version':VERSION,'binding':self.product.application.runtime.session.session_binding.fingerprint,
    'event_id':event_id,'url':url,'activity_source':source,'cursor':self.cursor,'status':'fetching','reason':None,'cause_ref':None}
   self._save()
   def fetch():
    result={'tool_calls':[]};stage='search' if searcher is not None else 'fetch'
    def invoke(name,callback,argument):
     row={'stage':name,'started_at_ms':time.time_ns()//1000000};start=time.perf_counter()
     result['tool_calls'].append(row)
     try:
      value=callback(argument);row['status']='completed';return value
     except Exception as error:
      row.update(status='failed',**failure(error,name));raise
     finally:
      row.update(ended_at_ms=time.time_ns()//1000000,duration_ms=round((time.perf_counter()-start)*1000,3))
    try:
     data=invoke('search',searcher,search_resource) if searcher is not None else None
     with self._lock:
      if self._state['event_id']!=event_id:return
      cancelled=self._state['status']!='fetching';cursor=self._state['cursor']-1
     if not cancelled:
      stage='selection'
      actual=select_result(data,site=topic['site'],cursor=cursor)['url'] if data is not None else url
      if data is not None:result.update(search=data,resource=url,cursor=cursor)
      stage='fetch';result['page']=invoke('fetch',reader,actual)
    except Exception as error:result['failure']=failure(error,stage)
    with self._lock:
     if self._state['event_id']!=event_id:return
     self._result=result
    self.on_ready(event_id)
   self._worker=threading.Thread(target=fetch,name='pet-phone-fetch',daemon=True);self._worker.start()
   return True

 def interrupt(self,*,reason,cause_ref=None):
  from .reading_progress import REASONS
  if reason not in REASONS or reason in {'user_contact','source_expired'} and not cause_ref:raise ValueError('phone stop requires reason and source')
  with self._lock:
   if self._state is None or self._state['status'] not in {'fetching','delivering'}:return
   if self._reading is not None:self._reading.interrupt(reason=reason,cause_ref=cause_ref)
   self._state.update(status='stopped',reason=reason,cause_ref=cause_ref);self._save()

 def deliver(self,event_id,*,coordinate):
  from .reading_progress import ReadingSession
  with self._lock:
   if self._state is None or event_id!=self._state['event_id']:return None
   result=self._result;self._result=None
   if result is None:return None
   self._state['tool_calls']=result['tool_calls']
   if self._state['status']!='fetching':
    self._save();return None # Actual tool costs remain visible; interruption stays authoritative.
   try:
    if 'search' in result:
     from .web_search import bind_selection
     source=bind_selection(self.product.application,activity=self._state['activity_source'],resource=result['resource'],data=result['search'],cursor=result['cursor'])
     self._state.update(url=source['url'],activity_source=source)
    if 'failure' in result:
     self._fail(result['failure']);return None
    classification=unavailable(result['page'])
    if classification is not None:
     from .web_reading import store_page
     _,artifact,digest=store_page(self.product.application,result['page'],self._state['url'])
     self._fail({**classification,'artifact':artifact.name,'artifact_sha256':digest},reason='page_unavailable');return None
    reading=ReadingSession(self.product,episode_id='phone:'+event_id,url=self._state['url'],activity_source=self._state['activity_source'],reader=lambda _:result['page'])
   except Exception as error:
    self._fail(failure(error,'source_validation'));raise
   self._reading=reading;self._state['status']='delivering';self._save()
  try:
   length=min(4096,len(result['page']['content']))
   value=reading.observe(end=length,coordinate=coordinate) if length else None
  except Exception:
   self.interrupt(reason='technical_failure');raise
  with self._lock:
   if self._state['status']=='delivering':
    self._state.update(status='completed',result_event_id=reading.state['previous_event_id'],result_consumed=False);self._save()
  return value

 def context(self):
  # Only the last completed public tool result, never another actor's history.
  with self._lock:
   state=self._state
   if state is None or not state.get('result_event_id') or state.get('result_consumed',False):return None
   app=self.product.application;identity=state['result_event_id']
   event=app.runtime.memory_instance(identity).event
   app._verify_source(event)
   receipt=app._product(source_id=identity)['sources'][identity]['evidence']['input_source']
   if receipt.get('kind')!='web_page' or receipt.get('reader_ref')!=app.owner_ref or receipt.get('requested_by')!=app.owner_ref:raise ValueError('reading result ownership differs')
   from .activity_sources import validate
   activity=validate(app,receipt['activity_source'],url=receipt['origin']['url'])
   original=activity.get('activity',activity)
   if original['event_id']!=state['event_id']:raise ValueError('reading result belongs to another action')
   content=event.contents[0].description.split('网页原文：\n',1)[1]
   return {'event_id':identity,'source_receipt_fingerprint':receipt['fingerprint'],
    'title':receipt['origin']['title'][:180],'url':receipt['origin']['url'][:512],
    'excerpt':content[:1000],'coverage':{'read_span':receipt['read_span'],'excerpt_chars':min(1000,len(content)),'source_chars':receipt['source_chars']}}

 def consume_result(self,event_id):
  with self._lock:
   state=self._state
   if state is None or state.get('result_consumed',False):return False
   if getattr(self.product.application,'_reading_context_offered',None)!=(event_id,state.get('result_event_id')):return False
   state['result_consumed']=True;state['result_consumer']=event_id;self._save();return True

 def close(self):self.interrupt(reason='window_closed')
