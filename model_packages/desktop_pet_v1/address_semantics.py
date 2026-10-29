"""Explicit source-property address wire; original numbers and old codecs remain."""
from .hierarchy_semantics import HierarchyReferentCodec,parse_response as parse_properties,validate
VERSION='desktop-pet-property-address-recall-v8'
SUFFIX=':source-property-address-v1'


def parse_response(raw,event):
 result=parse_properties(raw,event)
 result['version']=VERSION
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
