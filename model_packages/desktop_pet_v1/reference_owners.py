"""Query-local property ownership measurements; not action direction or state."""
import json
from . import reference_window as legacy
from .semantic import evidence_source
VERSION='desktop-pet-reference-property-owners-v1'
RESOLVED=frozenset({'user','current_npc'})


def validate(parsed):
    if parsed.get('reference_owner_version')!=VERSION:raise ValueError('reference owner contract missing')
    for row in parsed.get('reference_cues',()):
        if not isinstance(row.get('slot_owner'),str) or row.get('slot_owner') not in RESOLVED or row.get('source_owner')!=row['slot_owner']:
            raise ValueError('reference property owner differs or unresolved')


def parse_response(raw,event,context,*,available=16,source_scope=legacy.USED_SCOPE):
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    parsed=legacy.parse_response(raw,event,context,available=available,source_scope=source_scope)
    data=json.loads(_strip_fences(raw));text=evidence_source(event);accepted=[];rejected=0
    by_alias={x['reference_id']:x for x in context['sources']}
    for row in parsed['reference_cues']:
        a,b=row['span'];c,e=row['reference_span'];prior=by_alias[row['reference_id']]['text']
        originals=[x for x in data.get('reference_cues',()) if isinstance(x,dict)
            and str(x.get('reference_id'))==row['reference_id'] and x.get('text')==row['text']
            and x.get('evidence_text')==text[a:b] and x.get('reference_text')==prior[c:e]]
        if len(originals)!=1:rejected+=1;continue
        original=originals[0];owner=original.get('slot_owner');source=original.get('source_owner')
        if not isinstance(owner,str) or owner not in RESOLVED or source!=owner:rejected+=1;continue
        accepted.append({**row,'slot_owner':owner,'source_owner':source})
    used={row['reference_id'] for row in accepted}
    result={**parsed,'reference_owner_version':VERSION,'reference_cues':accepted,
        'reference_sources':[x for x in parsed['reference_sources'] if source_scope==legacy.FULL_SCOPE or x['reference_id'] in used],
        'rejected_owner_reference_cue_count':rejected}
    validate(result)
    return result


class OwnerReferenceCodec:
    memory_recall_version='desktop-pet-owner-bound-reference-recall-v6'
    def __init__(self,base):
        self.base=base
        self.request_version=base.request_version+':reference-property-owners-v1'
    def __getattr__(self,name):return getattr(self.base,name)
    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        from .declared_forms import _peer_speech
        if _peer_speech(event):
            system+='\n引用属性归属量测：每项reference_cue同时给slot_owner（本轮所问属性属于谁）和source_owner（被引原话中该属性属于谁），取user、current_npc、third_party或unresolved。它们表示属性所有者，与谁对谁说话分别理解。沿各自完整原文独立标注，两者相同且对象明确时表示这条来源回答该槽；第三方及未明对象保留其不确定性。原词、reference_id、两段唯一原文证据照原合同给出。'
        return system,user
