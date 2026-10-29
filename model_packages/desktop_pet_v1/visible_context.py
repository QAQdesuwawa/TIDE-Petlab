"""Versioned literal conversation context; existing parsing stays source-local."""
SUFFIX=':visible-conversation-context-v1'

class VisibleConversationContextCodec:
 def __init__(self,base,text):
  if not isinstance(text,str) or not text or len(text)>2048:raise ValueError('visible conversation context invalid')
  self.base=base;self.text=text;self.request_version=base.request_version+SUFFIX
 def __getattr__(self,name):return getattr(self.base,name)
 def build_request(self,*,event,recall_hint):
  system,user=self.base.build_request(event=event,recall_hint=recall_hint)
  system+='\n【同会话前文的用途】\n以下是本角色已实际接收或公开完成的同会话事实，用于理解当前所见的语境。量测对象仍是本次新收到的表现，变化量描述本次新增作用；原文证据锚仍来自当前事件。观点立场、正在进行的任务和针对关系对端的实际作用分别按事实理解。'
  return system,user+'\n\n【已见的同会话前文】\n'+self.text


class CurrentVisibleConversationContextCodec(VisibleConversationContextCodec):
 """Older witnessed facts precede the one current observation under v2."""
 def __init__(self,base,text):
  super().__init__(base,text)
  self.request_version=base.request_version+':visible-conversation-context-v2'
 def build_request(self,*,event,recall_hint):
  system,user=self.base.build_request(event=event,recall_hint=recall_hint)
  system+='\n【同会话前文的用途】\n前文提供本角色已经见到的语境。后列的当前观察是本次新增量测对象，tokens和memory_query_nodes的父项证据取自该当前观察；前文来源保留为理解参照，关系变化描述本次新作用。'
  return system,'【已见的同会话前文】\n'+self.text+'\n\n'+user


def parse_visible_grounded_response(raw,event,input_context,visible_context):
 """Reject known prior-only query evidence, without granting recall bindings.

 The bounded view was built from this Actor's accepted source artifacts. Its
 text can identify a stale parent, but never supplies a new current parent or
 a reference child. Unknown/ambiguous evidence retains the original rejection.
 """
 import json
 from framework.model_api import model_data_fingerprint as fp,thaw_model_data
 from .semantic import evidence_source
 from .query_tree import parse_grounded_response
 from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
 if visible_context is None or visible_context.get('status')!='delivered':
  return parse_grounded_response(raw,event,input_context)
 context=thaw_model_data(visible_context)
 if (context.get('version')!='desktop-pet-visible-conversation-context-v1'
     or context.get('event_fingerprint')!=event.fingerprint
     or context.get('fingerprint')!=fp({k:v for k,v in context.items() if k!='fingerprint'})
     or not isinstance(context.get('text'),str) or len(context['text'])>2048):
  raise ValueError('visible query context differs from accepted observation')
 prior=json.loads(context['text']);sources=context.get('sources',[])
 if not isinstance(prior,list) or not isinstance(sources,list) or len(prior)!=len(sources) or len(prior)>16:
  raise ValueError('visible query source shape differs')
 if any(not isinstance(row,dict) or not isinstance(row.get('原文'),str) for row in prior):
  raise ValueError('visible query source text invalid')
 data=json.loads(_strip_fences(raw));nodes=data.get('memory_query_nodes') if isinstance(data,dict) else None
 if not isinstance(nodes,list) or len(nodes)>16:return parse_grounded_response(raw,event,input_context)
 count=sum(1+len(n.get('references',[])) for n in nodes if isinstance(n,dict) and isinstance(n.get('references',[]),list))
 if count>16:raise ValueError('query nodes exceed existing cue budget')
 text=evidence_source(event);kept=[];rejected=[]
 for index,node in enumerate(nodes):
  quote=node.get('evidence_text') if isinstance(node,dict) else None
  term=node.get('text') if isinstance(node,dict) else None
  known=[sources[i]['fact_fingerprint'] for i,row in enumerate(prior)
   if isinstance(quote,str) and quote and row['原文'].count(quote)==1]
  shape=(isinstance(node,dict) and {'kind','text','evidence_text'}<=set(node)
   and not set(node)-{'kind','text','evidence_text','references'}
   and node['kind'] in ('literal','query','unresolved') and node.get('references',[])==[]
   and isinstance(term,str) and term.strip() and len(term)<=48
   and isinstance(quote,str) and quote.strip() and len(quote)<=256)
  if (shape and quote not in text and known
      and (term in quote or (node['kind']=='query' and len(quote)<=48))):
   rejected.append({'node_index':index,'node':node,'source_fact_fingerprints':known,
    'reason':'prior_visible_source_is_not_current_event_evidence'})
  else:kept.append(node)
 data['memory_query_nodes']=kept
 result=parse_grounded_response(json.dumps(data,ensure_ascii=False),event,input_context)
 if rejected:
  result['query_visible_source_validation_version']='petlab-visible-query-source-validation-v1'
  result['rejected_query_nodes']=[*result.get('rejected_query_nodes',[]),*rejected]
 return result


class LiteralParentGroundingCodec:
 """Version a parser-only grounding repair; keep the measured text unchanged."""
 def __init__(self,base):
  self.base=base;self.request_version=base.request_version+':visible-literal-parent-grounding-v1'
 def __getattr__(self,name):return getattr(self.base,name)
 def build_request(self,*,event,recall_hint):return self.base.build_request(event=event,recall_hint=recall_hint)


def parse_visible_grounded_response_v2(raw,event,input_context,visible_context):
 """A uniquely quoted current fragment survives as an unresolved parent.

 The model's paraphrased literal is discarded, not accepted as a value or a
 referenced child. The original wire, root measurements and source bounds stay
 intact; unquoted, ambiguous, long or child-bearing invalid nodes still fail.
 """
 import json
 from .semantic import evidence_source
 from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
 data=json.loads(_strip_fences(raw));text=evidence_source(event);repairs=[]
 nodes=data.get('memory_query_nodes') if isinstance(data,dict) else None
 if isinstance(nodes,list) and len(nodes)<=16:
  for i,node in enumerate(nodes):
   if not isinstance(node,dict):continue
   term=node.get('text');quote=node.get('evidence_text')
   if (node.get('kind') in ('literal','unresolved') and node.get('references',[])==[]
       and isinstance(term,str) and term.strip() and len(term)<=48
       and isinstance(quote,str) and quote.strip() and len(quote)<=48
       and term not in quote and text.count(quote)==1):
    repairs.append({'node_index':i,'model_text':term,'grounded_text':quote,
     'reason':'unanchored_literal_retained_as_unresolved_parent'})
    nodes[i]={**node,'kind':'unresolved','text':quote}
 result=parse_visible_grounded_response(json.dumps(data,ensure_ascii=False),event,input_context,visible_context)
 if repairs:
  result['query_visible_parent_grounding_version']='visible-literal-parent-grounding-v1'
  result['query_visible_parent_grounding_repairs']=repairs
 return result
