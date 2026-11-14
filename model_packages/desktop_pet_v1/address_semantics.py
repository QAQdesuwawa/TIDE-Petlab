"""Explicit source-property address wire; original numbers and old codecs remain."""
from .hierarchy_semantics import HierarchyReferentCodec,parse_response as parse_properties,validate
VERSION='desktop-pet-property-address-recall-v8'
SUFFIX=':source-property-address-v1'
QUERY_SUFFIX=':source-property-address-v2-query-values'


def parse_response(raw,event):
 result=parse_properties(raw,event)
 result['version']=VERSION
 return result


def parse_query_response(raw,event,*,unknown_certainty=False):
 """A question's offered value is not a known answer; retain its wire audit."""
 import json
 from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
 data=json.loads(_strip_fences(raw));rows=data.get('semantic_properties')
 if not isinstance(rows,list) or len(rows)>16:raise ValueError('semantic properties missing or over budget')
 repairs=[];normalized=[]
 for index,row in enumerate(rows):
  if not isinstance(row,dict):raise ValueError('property shape differs')
  if row.get('kind')=='query' and row.get('certainty') in ('stated','unknown') and row.get('value') is not None:
   # Apply the original JSON/value budget before normalization, not after it.
   value=json.dumps(row['value'],ensure_ascii=False,allow_nan=False)
   if len(value)>256:raise ValueError('property value exceeds budget')
   repairs.append({'property_index':index,'reason':'question_candidate_is_not_an_asserted_answer','original_value':row['value']})
   row={**row,'value':None}
   if unknown_certainty:row['certainty']='unknown'
  normalized.append(row)
 result=parse_response(json.dumps({**data,'semantic_properties':normalized},ensure_ascii=False),event)
 if repairs:result['property_normalizations']=repairs
 return result


def accepts_received_contract(old,new):
 return old.count(SUFFIX)==1 and old.replace(SUFFIX,QUERY_SUFFIX,1)==new


def parse_available_response(raw,event):
 """Keep independent usable branches; uncertain interpretation stays a parent.

 Original wire and event remain the authority. This only repairs interpretation,
 never evidence, schema budgets, or an asserted unknown value.
 """
 import json
 from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
 from .semantic import evidence_source
 from .hierarchy_semantics import OWNERS
 data=json.loads(_strip_fences(raw));rows=data.get('semantic_properties')
 if not isinstance(rows,list) or len(rows)>16:raise ValueError('semantic properties missing or over budget')
 normalized=[];fallbacks=[];unlocated=[];formats=[];text=evidence_source(event)
 for i,row in enumerate(rows):
  if isinstance(row,dict) and isinstance(row.get('evidence_text'),str):
   quote=row['evidence_text'];trimmed=quote.strip()
   if (len(quote)<=256 and trimmed and trimmed!=quote
       and text.count(quote)!=1 and text.count(trimmed)==1):
    formats.append({'property_index':i,'reason':'outer_evidence_whitespace','original_evidence_text':quote})
    row={**row,'evidence_text':trimmed}
  try:
   parse_query_response(json.dumps({'semantic_properties':[row]},ensure_ascii=False),event)
  except ValueError as error:
   # A source-bound parent can survive a missing interpretation; invented or
   # ambiguous quotes cannot acquire an authoritative span through this path.
   if not isinstance(row,dict):raise
   quote=row.get('evidence_text');path=row.get('path')
   if len(json.dumps(row,ensure_ascii=False,allow_nan=False))>1024:raise
   if not isinstance(path,list) or not 1<=len(path)<=8:raise
   if not isinstance(quote,str) or not 0<len(quote)<=256 or text.count(quote)!=1:
    # An unlocated sibling is retained as interpretation evidence, never as
    # a grounded property. It must not discard independently valid siblings.
    unlocated.append({'property_index':i,'reason':str(error),'original_property':row})
    continue
   prefix=[]
   for part in path:
    if not isinstance(part,str) or not part.strip() or len(part)>48:break
    prefix.append(part)
   if not prefix:prefix=[quote[:48]]
   fallbacks.append({'property_index':i,'reason':str(error),'original_property':row})
   row={'owner':row.get('owner') if isinstance(row.get('owner'),str) and row.get('owner') in OWNERS else 'unresolved',
        'path':prefix,'value':None,'kind':'query' if row.get('kind')=='query' else 'assertion',
        'certainty':'unknown','evidence_text':quote}
  normalized.append(row)
 # The committed execution is the source of an actual NPC output. Optional
 # retrieval labels need their own anchors and can all be unavailable.
 if unlocated and not normalized and event.event_kind!='npc_output':
  raise ValueError('no source-located property remains')
 result=parse_query_response(json.dumps({**data,'semantic_properties':normalized},ensure_ascii=False),event)
 if fallbacks:result['property_fallbacks']=fallbacks
 if unlocated:result['unlocated_properties']=unlocated
 if formats:result['property_format_normalizations']=formats
 return result


class AddressPropertyCodec(HierarchyReferentCodec):
 memory_recall_version=VERSION
 def __init__(self,base,*,referent_text=''):
  super().__init__(base,referent_text=referent_text)
  self.request_version=base.request_version+SUFFIX
 def build_request(self,*,event,recall_hint):
  system,user=super().build_request(event=event,recall_hint=recall_hint)
  marker='- reference_frame：'
  if system.count(marker)!=1:raise ValueError('property output contract anchor differs')
  system=system.replace(marker,'- semantic_properties：必填数组，与数值共同放在同一个JSON对象中，按下文的属性合同填写；没有可定位属性时为[]。\n'+marker,1)
  system+='\n【共享属性地址】\nowner表示属性的所属对象。path使用来源支持的开放属性层级；本轮问法与前文陈述同一属性时沿用相同路径。值是路径下的内容，陈述和询问分别保留。对象或细层未确定时，保留已有父层及未知标记；同一说话者可描述多个不同对象。'
  return system,user


class QueryAddressPropertyCodec(AddressPropertyCodec):
 normalize_query_candidates=True
 def __init__(self,base,*,referent_text=''):
  super().__init__(base,referent_text=referent_text)
  self.request_version=base.request_version+QUERY_SUFFIX
