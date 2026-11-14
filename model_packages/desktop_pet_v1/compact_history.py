"""Optional compact, source-located property rows for historical speech."""
import json
import re

from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
from .semantic import evidence_source

SUFFIX=':compact-historical-evidence-v4'
HEADER='【本轮原文定位】'
KEY='located_properties'
FIELDS={'o':'owner','p':'path','k':'kind','c':'certainty','v':'value'}


def anchors(event):
    text=evidence_source(event)
    candidates=[]
    for part in re.split(r'(?<=[.!?。！？])\s+|\n+',text):
        if not part.strip():continue
        for start in range(0,len(part),256):
            quote=part[start:start+256].strip()
            if quote and text.count(quote)==1 and quote not in candidates:
                candidates.append(quote)
    return {str(i):quote for i,quote in enumerate(candidates,1)}


class CompactHistoryCodec:
    def __init__(self,base,locations):
        self.base=base
        self.locations=locations
        self.request_version=base.request_version+SUFFIX

    def __getattr__(self,name):
        return getattr(self.base,name)

    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        # Replace only the property definition; reference, numeric and role contracts remain.
        system=system.replace('- semantic_properties：','- located_properties：',1)
        prefix,rest=system.split('【分层语义属性合同】',1)
        rest=rest.lstrip()
        _,boundary=json.JSONDecoder().raw_decode(rest)
        system=prefix+(
            '\n【原文定位的属性】located_properties 是行数组。每行 i 为定位号，o 为对象，'
            'p 为属性路径，k 为属性用途，c 为确定性，v 为值。定位号选自本轮原文定位，'
            '本地据此恢复证据原文。同一属性保留一条最具体且有依据的路径。'
            'path描述话语的内容与对象，如安排、承诺或偏好；数值轴和角色编号用于各自原有字段。'
            'owner 取 user、current_npc、third_party、unresolved；kind 取 assertion 或 query；'
            'certainty 取 stated 或 unknown，未知值或询问值为 null。'
            'v填数值、布尔值、null或{"i":"对应定位号"}。文本值用同一原文定位号，本地恢复。'
            '没有可定位属性时填 []。'
            'tokens 和 relation_claims 的原文证据同样填 i。词元 text 是简短的互动含义。使用紧凑 JSON。')+rest[boundary:]
        system=system.replace('"evidence": "支撑它的当前原文短片段"','"i": "本轮定位号"')
        system=system.replace('"evidence_text": "当前原文中的连续短片段"','"i": "本轮定位号"')
        return system,user+'\n\n'+HEADER+'\n'+json.dumps(self.locations,ensure_ascii=False,separators=(',',':'))


def restore_properties(raw,locations):
    data=json.loads(_strip_fences(raw))
    rows=data.get(KEY)
    if not isinstance(rows,list) or len(rows)>16:raise ValueError('located property rows exceed the existing budget')
    properties=[];rejected=[]
    for i,row in enumerate(rows):
        if (not isinstance(row,dict) or set(row)!=set(FIELDS)|{'i'} or not isinstance(row['i'],str)
            or row['i'] not in locations):
            rejected.append(i)
            continue
        prop={field:row[key] for key,field in FIELDS.items()}
        value=prop['value']
        if isinstance(value,dict):
            alias=value.get('i')
            if set(value)!={'i'} or alias!=row['i']:
                rejected.append(i)
                continue
            prop['value']=locations[alias]
        prop['evidence_text']=locations[row['i']]
        if prop not in properties:properties.append(prop)
    def bind_evidence(rows,field):
        bound=[]
        for row in rows or ():
            if not isinstance(row,dict):continue
            locator=row.get('i')
            if not isinstance(locator,str) or locator not in locations:continue
            bound.append({**{k:v for k,v in row.items() if k!='i'},field:locations[locator]})
        return bound

    if 'tokens' in data:
        data['tokens']=bind_evidence(data['tokens'],'evidence')
    frame=data.get('reference_frame')
    if isinstance(frame,dict):
        claims=bind_evidence(frame.get('relation_claims'),'evidence_text')
        if claims:
            data['reference_frame']={**frame,'relation_claims':claims}
        else:
            data.pop('reference_frame')
    return json.dumps({**data,'semantic_properties':properties},ensure_ascii=False),rejected
