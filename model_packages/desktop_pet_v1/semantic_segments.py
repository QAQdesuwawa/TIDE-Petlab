"""Optional same-call source segmentation for v8 address measurement."""
import json

from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
from .semantic import evidence_source

SUFFIX=':source-segments-v1'


class SegmentCodec:
    def __init__(self,base):
        self.base=base
        self.request_version=base.request_version+SUFFIX

    def __getattr__(self,name):
        return getattr(self.base,name)

    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        system+=(
            '\n【原文片段】同一轮中各自会查找不同历史的意思，分别放在 semantic_segments。'
            '这是当前原文的片段数组，每项为可唯一定位的连续原文，最多16项，每项最多256字。'
            '条件、否定、时间和归属与它们限定的意思保留在同一片段；'
            '例如「如果下雨我会晚到，你刚才说在哪见？」把条件和晚到留在一起，地点追问单列。'
            '按每个片段分别填写现有属性和前序引用，属性证据保留改变其意义的限定。'
            '归属或切分不确定时保留原句和已有父层，可用 [] 表示没有独立片段。'
            '数值仍对本轮量测一次，各条属性和引用继续使用原有总上限。')
        return system,user


def bind(raw,event,measurement):
    """Attach exact spans without dropping an otherwise usable property or reference."""
    items=json.loads(_strip_fences(raw)).get('semantic_segments',[])
    rejected=int(not isinstance(items,list) or len(items)>16)
    if rejected:items=[]
    text=evidence_source(event)
    candidates=set()
    for quote in items:
        if not isinstance(quote,str) or not quote.strip() or len(quote)>256 or text.count(quote)!=1:
            rejected+=1
            continue
        start=text.index(quote)
        candidates.add((start,start+len(quote)))
    spans=[]
    for start,end in sorted(candidates,key=lambda span:(span[0],-span[1])):
        if spans and start<spans[-1][1]:
            rejected+=1
            continue
        spans.append([start,end])

    def container(start,end):
        return next((n for n,(a,b) in enumerate(spans) if a<=start<end<=b),None)

    ref_spans=[]
    for ref in measurement.get('predecessor_refs',()):
        quote=ref['current_quote']
        start=text.index(quote)
        ref_spans.append(container(start,start+len(quote)))
    return {'segment_spans':spans,'rejected_segment_count':rejected,
            'property_segments':[container(*row['span']) for row in measurement['properties']],
            'predecessor_segments':ref_spans}
