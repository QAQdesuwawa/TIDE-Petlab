"""Exact predecessor quotes in the existing source-property measurement."""
import json

from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
from .semantic import evidence_source

SUFFIX=':committed-predecessor-references-v3'
HEADER='\n\n【已提交对话：前序来源】\n'


class PredecessorCodec:
    def __init__(self,base,text):
        self.base=base
        self.text=text
        self.request_version=base.request_version+SUFFIX

    def __getattr__(self,name):
        return getattr(self.base,name)

    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        system+=(
            '\n【前序来源】用同一个 predecessor_refs 列出理解当前原话所需的已提交对话。'
            '当当前话语在回应、追问或反驳对方的说法时，定位被回应的原话；'
            '如需其他前文帮助理解同一处承接，也可一并引用。编号只用于定位来源。'
            '独立话题或没有可定位来源时填 []。'
            '每项包含 reference_id（前序编号）、evidence_text（当前原话的唯一连续片段）、'
            'reference_text（前序原话的唯一连续片段）。最多 16 项，每段最多 256 字。')
        return system,HEADER.lstrip() + self.text + '\n\n' + user


class PublicPredecessorCodec(PredecessorCodec):
    def __init__(self,base,text):
        super().__init__(base,text)
        self.request_version+=':public-reference-choice-v2'

    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        system+=(
            '\n【公开前序引用】predecessor_refs 是本次原话中的指代与公开前文之间的证据。'
            '用 reference_id 选能解释该指代的一条原话，evidence_text 摘录本次原话，reference_text 摘录所选前文。'
            '先填 predecessor_status：public_reference（公开原话可解指代）、independent（本句可独立理解）、unresolved（本句依赖未提供的原话）。'
            '只有 public_reference 填引用，其余填 []。'
            '判断以当前问句为准：拿掉前文，仍能完整理解所问的对象，就是 independent；'
            '涉及只在私下说过的话、而公开前文没有那句话，就是 unresolved。'
            '前文1是「我把钥匙放在门口」。当本次原话是「刚才那件事怎么处理」时，'
            'predecessor_refs=[{"reference_id":"1","evidence_text":"刚才那件事","reference_text":"我把钥匙放在门口"}]。'
            '当本次原话是「明天几度」时，predecessor_status=independent，predecessor_refs=[]。'
            '当本次原话是「之前我单独告诉你的那件事呢」时，predecessor_status=unresolved，predecessor_refs=[]。'
            'semantic_properties 的证据也摘自本次原话。')
        return system,user+HEADER+self.text+(
            '\n\n先核对本次原话要求的是哪一种来源。公开原话可以解释公开交谈的指代，'
            '单独告诉角色的话需要实际的私下来源。再输出 predecessor_status 和 predecessor_refs。'
            '每个指代选择最直接的一条；独立话题或无法定位时填写 []。')

def parse(raw,event,sources,*,public=False):
    data=json.loads(_strip_fences(raw))
    items=data.get('predecessor_refs',[])
    if public and data.get('predecessor_status')!='public_reference':
        return {'predecessor_refs':[],'rejected_predecessor_refs':len(items) if isinstance(items,list) else 1}
    if not isinstance(items,list) or len(items)>16:
        return {'predecessor_refs':[],'rejected_predecessor_refs':1}
    current=evidence_source(event)
    by_id={str(i):row for i,row in enumerate(sources,1)}
    accepted=[];rejected=0
    required={'reference_id','evidence_text','reference_text'}
    for row in items:
        if (not isinstance(row,dict) or not required<=row.keys()
            or row.keys()-required-{'kind'} or row.get('kind') not in (None,'direct','background')):
            rejected+=1;continue
        alias=row['reference_id']
        prior=by_id.get(alias) if isinstance(alias,str) else None
        a=row['evidence_text'];b=row['reference_text']
        if (prior is None or not isinstance(a,str) or not a.strip() or len(a)>256 or current.count(a)!=1
            or not isinstance(b,str) or not b.strip() or len(b)>256 or prior[0].count(b)!=1):
            rejected+=1;continue
        evidence={'source':prior[1],'current_quote':a,'prior_quote':b}
        if 'kind' in row and row['kind'] is not None:evidence['kind']=row['kind']
        if evidence not in accepted:accepted.append(evidence)
    return {'predecessor_refs':accepted,'rejected_predecessor_refs':rejected}
