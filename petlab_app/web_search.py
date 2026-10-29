"""Bounded discovery resources; results authorize a URL, never a memory or action."""
import json,re,urllib.request,hashlib
from urllib.parse import urlsplit,parse_qs,urlencode
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint as fp
from .web_reading import validate_url
RESOURCE='https://api.deepseek.com/v1/tools/search'
VERSION='pet-search-selection-v1'

def resources(rows):
 if not isinstance(rows,(list,tuple)) or len(rows)>16:raise ValueError('search sources exceed bound')
 result=[]
 for row in rows:
  if not isinstance(row,Mapping) or set(row) not in ({'query','site'},{'site'}):raise ValueError('search source fields differ')
  site=row['site'];query=row.get('query')
  if query is None:
   if set(row)!={'site'} or not isinstance(site,str) or len(site)>253 or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}',site):raise ValueError('invalid search site')
   result.append(RESOURCE+'?'+urlencode({'mode':'intent','site':site}));continue
  if (not isinstance(query,str) or not query.strip() or ':' in query or any(ord(c)<32 for c in query)
   or not isinstance(site,str) or len(site)>253 or not re.fullmatch(r'[a-z0-9]+(?:[.-][a-z0-9]+)*\.[a-z]{2,}',site)
   or len(query+' site:'+site)>128):raise ValueError('invalid bounded search topic/site')
  result.append(RESOURCE+'?'+urlencode({'query':query,'site':site}))
 if len(set(result))!=len(result):raise ValueError('duplicate search source')
 return tuple(result)

def decode(url):
 p=urlsplit(url)
 if p.scheme+'://'+p.netloc+p.path!=RESOURCE:return None
 values=parse_qs(p.query,strict_parsing=True)
 if any(len(v)!=1 for v in values.values()):raise ValueError('search resource differs')
 if set(values)=={'mode','site'} and values['mode']==['intent']:row={'site':values['site'][0]}
 elif set(values)=={'query','site'}:row={k:v[0] for k,v in values.items()}
 else:raise ValueError('search resource differs')
 if resources([row])[0]!=url:raise ValueError('noncanonical search resource')
 return row

def select_result(data,*,site,cursor):
 if not isinstance(data,dict) or not isinstance(data.get('results'),list) or len(data['results'])>5:raise ValueError('search result bound differs')
 allowed=[];seen=set()
 for row in data['results']:
  if not isinstance(row,dict):raise ValueError('invalid search result')
  url=row.get('url');title=row.get('title','')
  try:validate_url(url)
  except ValueError:continue
  host=urlsplit(url).hostname
  if host!=site and not host.endswith('.'+site):continue
  if not isinstance(title,str) or len(title)>500:continue
  if url not in seen:allowed.append({'url':url,'title':title});seen.add(url)
 if not allowed:raise ValueError('search returned no allowed page')
 return allowed[cursor%len(allowed)]

class DeepSeekSearch:
 def __init__(self,*,api_key,timeout_s=30):
  if not api_key:raise ValueError('search requires API key')
  self.api_key=api_key;self.timeout_s=timeout_s
 def __call__(self,resource):
  from .providers import _NoRedirect
  from compatibility.windows_proxy import provider_proxy_handler
  row=decode(resource)
  if row is None or 'query' not in row:raise ValueError('search needs a realized query')
  request=urllib.request.Request(RESOURCE,data=json.dumps({'query':row['query']+' site:'+row['site'],'count':5,'need_content':False}).encode(),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.api_key},method='POST')
  with urllib.request.build_opener(_NoRedirect(),provider_proxy_handler()).open(request,timeout=self.timeout_s) as response:raw=response.read(1048577)
  if len(raw)>1048576:raise ValueError('search response exceeds bound')
  data=json.loads(raw)
  # Drop provider summaries; these are discovery metadata, not observed prose.
  if not isinstance(data,dict) or not isinstance(data.get('results'),list) or len(data['results'])>5:raise ValueError('search result bound differs')
  result={'results':[{'url':v.get('url'),'title':v.get('title','')} for v in data['results'] if isinstance(v,dict)]}
  select_result(result,site=row['site'],cursor=0)
  return result

def default_search(product):
 p=urlsplit(product.gateway.transport.endpoint)
 if p.scheme!='https' or p.hostname!='api.deepseek.com':raise ValueError('provider has no default search tool')
 return DeepSeekSearch(api_key=product.gateway.transport.api_key)

def bind_selection(app,*,activity,resource,data,cursor):
 from .external_input import archive_root
 from .activity_sources import validate
 validate(app,activity,url=resource)
 topic=decode(resource)
 if topic is None:raise ValueError('search resource required')
 chosen=select_result(data,site=topic['site'],cursor=cursor)
 extra={}
 if 'query' not in topic:
  from .reading_intent import _from_verified_activity
  intent=_from_verified_activity(app,activity['event_id'],topic)
  if intent is None:raise ValueError('dynamic search has no committed intent')
  extra={'query_resource':resources([{'query':intent['query'],'site':topic['site']}])[0],'intent':intent}
 record={'version':VERSION,'resource':resource,'results':data['results'],'cursor':cursor,'selected':chosen,**extra}
 raw=json.dumps(record,ensure_ascii=False,separators=(',',':')).encode('utf8')
 if len(raw)>65536:raise ValueError('search provenance exceeds bound')
 digest=hashlib.sha256(raw).hexdigest();root=archive_root(app);root.mkdir(parents=True,exist_ok=True);path=root/(digest+'.search.json')
 if path.exists():
  if path.read_bytes()!=raw:raise ValueError('search provenance differs')
 else:path.write_bytes(raw)
 v={'version':'pet-activity-observation-source-v2','activity':activity,'resource':resource,'artifact':path.name,'artifact_sha256':digest,'url':chosen['url']}
 return {**v,'fingerprint':fp(v)}

def validate_selection(app,value,*,url):
 from .external_input import archive_root
 from .activity_sources import validate,VERSION as ACTIVITY_VERSION
 if (set(value)!={'version','activity','resource','artifact','artifact_sha256','url','fingerprint'}
  or value['version']!='pet-activity-observation-source-v2' or value['fingerprint']!=fp({k:v for k,v in value.items() if k!='fingerprint'})
  or value['url']!=url or value['activity'].get('version')!=ACTIVITY_VERSION):raise ValueError('search selection binding differs')
 validate(app,value['activity'],url=value['resource']);topic=decode(value['resource'])
 if topic is None:raise ValueError('missing search resource')
 root=archive_root(app).resolve();path=(root/value['artifact']).resolve()
 if path.parent!=root or path.name!=value['artifact_sha256']+'.search.json':raise ValueError('search provenance escapes archive')
 raw=path.read_bytes()
 if len(raw)>65536 or hashlib.sha256(raw).hexdigest()!=value['artifact_sha256']:raise ValueError('search provenance changed')
 record=json.loads(raw)
 if record.get('version')!=VERSION or record.get('resource')!=value['resource'] or type(record.get('cursor'))is not int or record['cursor']<0:raise ValueError('search provenance invalid')
 if 'query' not in topic:
  from .reading_intent import _from_verified_activity
  intent=_from_verified_activity(app,value['activity']['event_id'],topic)
  if intent is None or record.get('intent')!=intent or record.get('query_resource')!=resources([{'query':intent['query'],'site':topic['site']}])[0]:raise ValueError('search intent differs from committed parent')
 selected=select_result(record,site=topic['site'],cursor=record['cursor'])
 if selected!=record['selected'] or selected['url']!=url:raise ValueError('selected page differs')
 return value
