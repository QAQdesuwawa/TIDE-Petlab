"""Bounded tool context and parameters for an already selected reading action."""
import hashlib,json
from framework.model_api import thaw_model_data,model_data_fingerprint as fp
VERSION='pet-reading-parameters-v1'
RESULTS_VERSION='pet-reading-parameters-v2-linked-results'

def split_output(text,*,site):
 value=json.loads(text)
 if not isinstance(value,dict) or 'search_query' not in value:raise ValueError('reading action requires search_query or null')
 query=value.pop('search_query')
 if query is not None:
  if not isinstance(query,str) or not query.strip() or len(query)>96 or any(ord(c)<32 for c in query):raise ValueError('invalid bounded search query')
  from petlab_app.web_search import resources
  resources([{'query':query,'site':site}])
 return json.dumps(value,ensure_ascii=False),query

def context_for(app,event_id,*,selected_ids=(),readers=()):
 result=None;supplier=getattr(app,'reading_context_supplier',None)
 if callable(supplier):result=supplier()
 site=None
 from petlab_app.idle_opening import is_idle_tick,source
 if is_idle_tick(app,event_id):
  selected=source(app,event_id,current=True)[0].row.get('behavior_selection') or {}
  if selected.get('action_keys')==('read_web',) or selected.get('action_keys')==['read_web']:
   from petlab_app.web_search import decode
   topic=decode(selected['definition']['tool']['url'])
   if topic is not None and 'query' not in topic:site=topic['site']
 linked=None
 if selected_ids and not is_idle_tick(app,event_id):
  from .activity_result_context import build
  linked=build(app,event_id,selected_ids,readers)
 if site is None and result is None and linked is None:return None
 value={'version':RESULTS_VERSION if linked else VERSION,'site':site,'result':result}
 if linked:value['activity_results']=linked
 return {**value,'fingerprint':fp(value)}

def supplement(context):
 if context['version'] not in (VERSION,RESULTS_VERSION) or context['fingerprint']!=fp({k:v for k,v in context.items() if k!='fingerprint'}):raise ValueError('reading context integrity differs')
 system=''
 if context['site'] is not None:
  system='\n已选动作是阅读网页。另输出search_query：结合本轮人设、已交付上下文与自己的兴趣，给出这次想了解的简短搜索词（最多96字），没有具体想查的内容时填null。搜索词作为已选动作的参数，由工具限定在'+context['site']+'执行；speech仍可为null。'
 user=''
 if context['result'] is not None:
  result=context['result'];user='\n\n[本次阅读活动已交付的公开网页片段]\n'+json.dumps({k:result[k] for k in ['title','url','excerpt','coverage']},ensure_ascii=False,separators=(',',':'))
  system+='\n阅读活动结果是已经读到的网页引文，来源属于网页作者。可以结合当前态度表达感受或据此确定下一项想了解的问题。'
 if context['version']==RESULTS_VERSION:
  from .activity_result_context import readable,VERSION as LINK_VERSION
  linked=context['activity_results']
  if linked['version']!=LINK_VERSION or linked['fingerprint']!=fp({k:v for k,v in linked.items() if k!='fingerprint'}):raise ValueError('activity result integrity differs')
  user+='\n\n[已选动作对应的实际阅读结果，时间由近到远]\n'+json.dumps([readable(r) for r in linked['results']],ensure_ascii=False,separators=(',',':'))
 return system,user

def reserve(context):return sum(len(v) for v in supplement(context)) if context is not None else 0

def augment(delivery,context,*,budget_chars):
 system,user=supplement(context);result=thaw_model_data(delivery)
 system=delivery['system']+system;user=delivery['user']+user
 if len(system)+len(user)>budget_chars:raise ValueError('reading context exceeds Writer budget')
 result.update(system=system,user=user,budget_chars=budget_chars,prompt_fingerprint=hashlib.sha256((system+'\0'+user).encode()).hexdigest(),reading_delivery={'version':context['version'],'context_fingerprint':context['fingerprint'],'base_delivery_fingerprint':delivery['fingerprint']})
 result.pop('fingerprint',None);return {**result,'fingerprint':fp(result)}
