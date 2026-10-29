"""Versioned source-grounded properties, separate from unchanged CX67 numbers."""
import json
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint,thaw_model_data
from .semantic import evidence_source
VERSION='desktop-pet-source-property-tree-v1'
RECALL_VERSION='desktop-pet-hierarchical-property-recall-v7'
SUFFIX=':source-property-tree-v1'
OWNERS=frozenset(('user','current_npc','third_party','unresolved'))


def parse_response(raw,event):
 from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
 data=json.loads(_strip_fences(raw));rows=data.get('semantic_properties');text=evidence_source(event)
 if not isinstance(rows,list) or len(rows)>16:raise ValueError('semantic properties missing or over budget')
 out=[]
 for row in rows:
  if not isinstance(row,dict) or set(row)!={'owner','path','value','kind','certainty','evidence_text'}:raise ValueError('property shape differs')
  owner=row['owner'];path=row['path'];quote=row['evidence_text']
  if not isinstance(owner,str) or owner not in OWNERS:raise ValueError('property owner differs')
  if (not isinstance(path,list) or not 1<=len(path)<=8 or any(not isinstance(x,str) or not x.strip() or len(x)>48 for x in path)):raise ValueError('property path differs')
  if row['kind'] not in ('assertion','query') or row['certainty'] not in ('stated','unknown'):raise ValueError('property kind/certainty differs')
  if row['certainty']=='unknown' and row['value'] is not None:raise ValueError('unknown property value must remain null')
  if row['kind']=='query' and row['value'] is not None:raise ValueError('query is not an asserted answer')
  if not isinstance(quote,str) or not 0<len(quote)<=256 or text.count(quote)!=1:raise ValueError('property lacks unique actual source evidence')
  try:value=json.dumps(row['value'],ensure_ascii=False,allow_nan=False)
  except (ValueError,TypeError):raise ValueError('property value is not JSON') from None
  if len(value)>256:raise ValueError('property value exceeds budget')
  at=text.index(quote);item={k:row[k] for k in ('owner','path','value','kind','certainty')};item['span']=[at,at+len(quote)]
  if item not in out:out.append(item)
 result={'version':RECALL_VERSION,'hierarchy_version':VERSION,'hierarchy_event_fingerprint':event.fingerprint,'properties':out,'cues':[]}
 if len(json.dumps(out,ensure_ascii=False))>4096:raise ValueError('property material exceeds budget')
 return result


def validate(event,parsed):
 if parsed.get('hierarchy_version')!=VERSION or parsed.get('hierarchy_event_fingerprint')!=event.fingerprint:raise ValueError('property event source differs')
 text=evidence_source(event);raw=[]
 for row in parsed.get('properties',()):
  if set(row)!={'owner','path','value','kind','certainty','span'}:raise ValueError('stored property shape differs')
  a,b=row['span']
  if type(a)is not int or type(b)is not int or not 0<=a<b<=len(text):raise ValueError('property source span differs')
  raw.append({**{k:thaw_model_data(v) for k,v in row.items() if k!='span'},'evidence_text':text[a:b]})
 expected=parse_response(json.dumps({'semantic_properties':raw},ensure_ascii=False),event)
 if model_data_fingerprint(expected['properties'])!=model_data_fingerprint(parsed.get('properties')):raise ValueError('stored property source differs')
 return expected['properties']


class HierarchyCodec:
 memory_recall_version=RECALL_VERSION
 def __init__(self,base):self.base=base;self.request_version=base.request_version+SUFFIX
 def __getattr__(self,key):return getattr(self.base,key)
 def build_request(self,*,event,recall_hint):
  system,user=self.base.build_request(event=event,recall_hint=recall_hint)
  contract={'职责':'量测当前已发生事件中供记忆检索的语义属性，数值增量仍沿上面的原合同。',
   '来源':'当前事件是新量测对象；说话者与属性所有者分别理解。近期资料若出现，仅为指代参照。',
   '层级':'按原文支持从父层向子属性细化，每个分支停在有依据的最细层。同一句可有多条独立属性，尚不明确的其他含义保留粗层。属性路径开放；细分支的祖先沿路径表达一次。',
   '字段':{'semantic_properties':[{'owner':'user/current_npc/third_party/unresolved，表示属性描述的对象',
    'path':['父层含义','原文支持的细属性，可继续细化'],
    'value':'原文表达的JSON值；未知值用null，对象未知时仍保留明确的值',
    'kind':'assertion表示来源陈述，query表示当前询问目标；询问的值为null',
    'certainty':'stated表示来源明确陈述；unknown表示值未知且value=null',
    'evidence_text':'本事件支撑该条属性的唯一连续原文'}]},
   '资源':'最多16条、路径1至8层、每层48字符、每段原文256字符、每个JSON值256字符，属性材料共4096字符；没有可识别属性时数组为空。',
   '格式示例':{'仅作格式':'同一说话者可以陈述不同对象的属性；多个分支独立表达。',
    '原文':'我戴蓝帽子，你的帽子是白色。',
    'semantic_properties':[{'owner':'user','path':['外观','帽子','颜色'],'value':'蓝','kind':'assertion','certainty':'stated','evidence_text':'我戴蓝帽子'},
     {'owner':'current_npc','path':['外观','帽子','颜色'],'value':'白色','kind':'assertion','certainty':'stated','evidence_text':'你的帽子是白色。'}]}}
  return system+'\n【分层语义属性合同】\n'+json.dumps(contract,ensure_ascii=False,separators=(',',':')),user


EVIDENCE_SUFFIX=':source-property-tree-v2'


class HierarchyEvidenceCodec(HierarchyCodec):
 """Same properties and parser; explicit actual-source fields for new requests."""
 def __init__(self,base):
  super().__init__(base)
  self.request_version=base.request_version+EVIDENCE_SUFFIX
 def build_request(self,*,event,recall_hint):
  system,user=super().build_request(event=event,recall_hint=recall_hint)
  prefix,raw=system.rsplit('【分层语义属性合同】\n',1)
  contract=json.loads(raw)
  contract['属性证据源']={
   '正文':'该内容实际说出或完成的原文，支持属性定位。',
   '交互摘要':'该内容本段真实发生的交互原文，支持属性定位。',
   '操作含义与部位含义':'包内子类与部位的通用定义，作为理解参照；当前发生的属性以正文或交互摘要中的实际描述为依据。'}
  contract['字段']['semantic_properties'][0]['evidence_text']='当前内容的正文或交互摘要中，支撑本条属性的唯一连续原文。'
  return prefix+'【分层语义属性合同】\n'+json.dumps(contract,ensure_ascii=False,separators=(',',':')),user


SEPARATED_SUFFIX=':source-property-tree-v3'


class HierarchySeparatedEvidenceCodec(HierarchyEvidenceCodec):
 """Publish actual source separately; keep subtype definitions as references."""
 def __init__(self,base):
  super().__init__(base)
  self.request_version=base.request_version+SEPARATED_SUFFIX
 def build_request(self,*,event,recall_hint):
  system,user=super().build_request(event=event,recall_hint=recall_hint)
  heading='【交互事实摘要】\n' if '【交互事实摘要】\n' in user else '【当前已发生事件】\n'
  prefix,raw=user.rsplit(heading,1);facts=json.loads(raw);references=[]
  for index,row in enumerate(facts['内容']):
   reference={'内容序号':index}
   for key in ('操作含义','事实边界'):
    if key in row:reference[key]=row.pop(key)
   for key in ('起始部位','接触部位'):
    if key not in row:continue
    value=row[key];parts=value if isinstance(value,list) else [value]
    actual=[];definitions=[]
    for part in parts:
     actual.append({k:v for k,v in part.items() if k in ('名称','name')})
     definitions.append(part)
    row[key]=actual if isinstance(value,list) else actual[0]
    reference[key]=definitions if isinstance(value,list) else definitions[0]
   if len(reference)>1:references.append(reference)
  for row in facts['内容']:
   for key in ('正文','交互摘要'):
    if key in row:row['可定位原文']=row.pop(key)
  prefix_system,raw_contract=system.rsplit('【分层语义属性合同】\n',1)
  contract=json.loads(raw_contract)
  contract['属性证据源']={'可定位原文':'各内容的实际原文；多内容按原顺序以换行连接，evidence_text逐字定位于此。',
   '内容':'本次实际类型、可见性、对象及接触部位名称，帮助理解原文归属。',
   '子类解释参照':'角色包对操作和部位的一般定义，与各内容序号对应，提供解释语义。'}
  contract['字段']['semantic_properties'][0]['evidence_text']='可定位原文中支撑本条属性的唯一连续片段。'
  system=prefix_system+'【分层语义属性合同】\n'+json.dumps(contract,ensure_ascii=False,separators=(',',':'))
  if references:system+='\n【子类解释参照】\n'+json.dumps(references,ensure_ascii=False,separators=(',',':'))
  return system,prefix+'【本次量测对象】\n'+json.dumps(facts,ensure_ascii=False,separators=(',',':'))


REFERENT_SUFFIX=':source-property-tree-v4'

class HierarchyReferentCodec(HierarchySeparatedEvidenceCodec):
 referent_context_enabled=True
 """Same current-source parser; literal references supply referent context only."""
 def __init__(self,base,*,referent_text=''):
  super().__init__(base)
  if not isinstance(referent_text,str) or len(referent_text)>2048:raise ValueError('referent text exceeds budget')
  self.referent_text=referent_text
  self.request_version=base.request_version+REFERENT_SUFFIX
 def build_request(self,*,event,recall_hint):
  system,user=super().build_request(event=event,recall_hint=recall_hint)
  system+='\n【属性对象与指代】\n属性按其值所属的对象归属，说话者、行为执行者与属性所属对象分别理解。问句定位待补出的属性值，其对象和层级沿对应陈述的属性含义；表述方式体现为询问类型。已提交来源只作当前原文的指代参照；属性证据仍逐字定位于本次量测对象的可定位原文，归属不足时保留unresolved和有依据的父层。'
  if self.referent_text:
   user=user.replace('【本次量测对象】\n','【已提交来源：指代参照】\n'+self.referent_text+'\n【本次量测对象】\n',1)
  return system,user
