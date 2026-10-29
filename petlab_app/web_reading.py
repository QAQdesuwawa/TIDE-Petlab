"""Read-only website tool adapter and durable observation delivery."""
from pathlib import Path
import hashlib,json,time,urllib.request
from urllib.parse import urlsplit
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from compatibility.windows_proxy import provider_proxy_handler
from .providers import _NoRedirect


def validate_url(url):
 if not isinstance(url,str) or not url.strip() or len(url)>8192 or any(c.isspace() for c in url):raise ValueError('网页地址无效')
 parsed=urlsplit(url)
 if parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password:raise ValueError('网页地址需要不含账号密码的HTTP或HTTPS链接')
 return url


class DeepSeekFetchReader:
 """Existing DeepSeek Fetch tool; no browser/parser reimplementation."""
 identity='deepseek-fetch-v1'
 def __init__(self,*,api_key,endpoint='https://api.deepseek.com/v1/tools/fetch',timeout_s=45):
  if not api_key:raise ValueError('联网阅读需要模型提供方的API密钥')
  self.api_key=api_key;self.endpoint=endpoint;self.timeout_s=timeout_s
 def __call__(self,url):
  validate_url(url)
  request=urllib.request.Request(self.endpoint,data=json.dumps({'url':url}).encode('utf8'),
   headers={'Content-Type':'application/json','Authorization':'Bearer '+self.api_key},method='POST')
  with urllib.request.build_opener(_NoRedirect(),provider_proxy_handler()).open(request,timeout=self.timeout_s) as response:
   raw=response.read(1048577)
  if len(raw)>1048576:raise ValueError('网页工具返回超过1MiB，未归档为已阅读')
  data=json.loads(raw)
  if not isinstance(data,dict) or not isinstance(data.get('content'),str) or not data['content'].strip():raise ValueError('网页工具未返回可阅读正文')
  title=data.get('title','')
  if not isinstance(title,str) or len(title)>500:raise ValueError('网页工具标题无效')
  return {'url':url,'title':title,'author':None,'content':data['content'],'provider':self.identity}


def default_reader(product):
 transport=product.gateway.transport
 binding=getattr(product,'web_reader_binding',None)
 if binding is not None:
  target=urlsplit(binding['endpoint']);origin=urlsplit(transport.endpoint)
  if (target.scheme,target.netloc)!=(origin.scheme,origin.netloc):raise ValueError('网页工具与聊天API必须属于同一提供方地址')
  return DeepSeekFetchReader(api_key=transport.api_key,endpoint=binding['endpoint'],timeout_s=binding.get('timeout_s',45))
 parsed=urlsplit(transport.endpoint)
 if parsed.scheme!='https' or parsed.hostname!='api.deepseek.com':raise ValueError('当前模型提供方没有默认网页工具；可通过统一reader适配器提供读取能力')
 return DeepSeekFetchReader(api_key=transport.api_key)


def read_page(product,*,event_id,url,executor,reader=None,coordinate=None,completion_coordinate=None,is_cancelled=lambda:False,respond=True,retry_failed=False,activity_source=None,reading_checkpoint=None,response_policy="required"):
 """A read success becomes a real external input; the normal reply path follows."""
 from .product import wall_clock_ms
 from .external_input import archive_root,validate,SOURCE_VERSION,ACTIVITY_SOURCE_VERSION,READING_SOURCE_VERSION
 from model_packages.desktop_pet_v1.web_observation import content
 url=validate_url(url);app=product.application
 if activity_source is not None:
  from .activity_sources import validate as validate_activity
  activity_source=validate_activity(app,activity_source,url=url)
 if reading_checkpoint is not None:
  from .reading_progress import validate_checkpoint
  validate_checkpoint(app,reading_checkpoint,event_id=event_id,url=url)
 root=archive_root(app);root.mkdir(parents=True,exist_ok=True)
 token=hashlib.sha256(event_id.encode('utf8')).hexdigest();manifest=root/(token+'.receipt.json')
 saved=app._product(source_id=event_id)['sources'].get(event_id);received=saved is not None
 if manifest.exists() or received:
  receipt=json.loads(manifest.read_text(encoding='utf8')) if manifest.exists() else saved['evidence'].get('input_source')
  if not isinstance(receipt,dict) or received and receipt!=saved['evidence'].get('input_source'):raise ValueError('阅读原来源回执不一致')
  if receipt['origin']['url']!=url:raise ValueError('同一阅读事件不能更换网页')
  if activity_source is not None and receipt.get('activity_source')!=activity_source:raise ValueError('同一阅读事件不能更换发起活动')
  if reading_checkpoint is not None and receipt.get('reading_checkpoint')!=reading_checkpoint:raise ValueError('阅读区间不得变更')
 else:
  if is_cancelled() and not received:return None
  page=(reader or default_reader(product))(url)
  page,artifact,digest=store_page(app,page,url)
  # New manually fetched pages are checked before becoming observations.
  # Previously sealed reading checkpoints retain their original replay contract.
  if reading_checkpoint is None:
   from .web_result import unavailable,UnavailableWebPage
   classification=unavailable(page)
   if classification is not None:raise UnavailableWebPage({**classification,'artifact':artifact.name,'artifact_sha256':digest})
  total=len(page['content']);end=min(4096,total)
  receipt={'version':READING_SOURCE_VERSION if reading_checkpoint is not None else ACTIVITY_SOURCE_VERSION if activity_source is not None else SOURCE_VERSION,'kind':'web_page','actor_ref':'web:'+urlsplit(url).hostname,
   'reader_ref':app.owner_ref,'requested_by':app.owner_ref if activity_source is not None else app.user_ref,'session_binding_fingerprint':app.runtime.session.session_binding.fingerprint,
   'event_id':event_id,'origin':{'url':url,'title':page['title'],'author':page['author']},
   'provider':page.get('provider','external-reader'),'artifact':artifact.name,'artifact_sha256':digest,
   'source_chars':total,'read_span':[0,end],'coverage':'complete' if end==total else 'prefix','fetched_at_ms':time.time_ns()//1000000}
  if reading_checkpoint is not None:
   if reading_checkpoint['artifact_sha256']!=digest:raise ValueError('阅读片段与原件不一致')
   receipt['reading_checkpoint']=reading_checkpoint
   receipt['read_span']=list(reading_checkpoint['read_span']);receipt['coverage']='complete' if receipt['read_span']==[0,total] else 'interval'
  if activity_source is not None:receipt['activity_source']=activity_source
  receipt['fingerprint']=fp(receipt)
  # Exclusive publication: an interrupted read can reuse the same obtained source.
  with manifest.open('x',encoding='utf8') as f:json.dump(receipt,f,ensure_ascii=False,separators=(',',':'))
 original=(root/receipt['artifact']).resolve()
 if original.parent!=root.resolve() or original.suffix!='.json':raise ValueError('网页原件超出来源目录')
 page=json.loads(original.read_text(encoding='utf8'))
 contents=(content(receipt,page['content'][receipt['read_span'][0]:receipt['read_span'][1]],app.owner_ref),)
 validate(app,receipt,contents)
 if is_cancelled() and not received:return None
 existing=app._product(source_id=event_id)['sources'].get(event_id)
 at=existing['event']['position']['coordinate'] if existing else (coordinate() if callable(coordinate) else coordinate if coordinate is not None else wall_clock_ms())
 result=product.interact(event_id=event_id,contents=contents,coordinate=at,executor=executor,
  completion_coordinate=completion_coordinate or wall_clock_ms,is_cancelled=(is_cancelled if respond else lambda:True),input_source=receipt,retry_failed=retry_failed,response_policy=response_policy)
 if response_policy=='eligible' and result is not None and not result.replayed:
  rag=getattr(product,'rag_memory',None)
  if rag is not None:rag.prime()
 return result


def verify_job(product,job,event=None):
 app=product.application
 event=event or app.runtime.memory_instance(job['event_id']).event
 app._verify_source(event)
 receipt=app._product()['sources'][event.event_id]['evidence'].get('input_source')
 if (not receipt or receipt['kind']!='web_page' or receipt['event_id']!=job['event_id']
  or receipt['origin']['url']!=job['url'] or event.position.coordinate<job['coordinate']):
  raise ValueError('阅读任务与实际网页观察来源不一致')
 return event



def store_page(app,page,url):
 """One shared validation/storage path for fetched data, not an observed fact."""
 from .external_input import archive_root
 if (not isinstance(page,dict) or page.get('url')!=url or not isinstance(page.get('content'),str)
  or not page['content'].strip() or not isinstance(page.get('title'),str) or len(page['title'])>500
  or page.get('author') is not None and (not isinstance(page['author'],str) or len(page['author'])>500)
  or not isinstance(page.get('provider','external-reader'),str) or not 0<len(page.get('provider','external-reader'))<=128):raise ValueError('读取工具的来源或正文无效')
 payload={k:page[k] for k in ('url','title','content','author')};raw=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode('utf8')
 if len(raw)>1048576:raise ValueError('网页原件超过1MiB，未归档为已阅读')
 digest=hashlib.sha256(raw).hexdigest();root=archive_root(app);root.mkdir(parents=True,exist_ok=True);artifact=root/(digest+'.json')
 if not artifact.exists():artifact.write_bytes(raw)
 elif hashlib.sha256(artifact.read_bytes()).hexdigest()!=digest:raise ValueError('网页原件已变更')
 return page,artifact,digest
