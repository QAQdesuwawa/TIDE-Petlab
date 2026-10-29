"""Current-event convention declarations; source metadata, never root state."""
import json
from .semantic import evidence_source
from framework.model_api import model_data_fingerprint

VERSION='desktop-pet-declared-speech-forms-v1'
RECALL_VERSION='desktop-pet-declared-form-recall-v4'


def _peer_speech(event):
    return (event.event_kind=='peer_input' and event.actor_ref!=event.owner_ref
        and {c.kind for c in event.contents}=={'speech'}
        and len(event.visible_contents(event.owner_ref))==len(event.contents))


def validate_declarations(event,parsed):
    if parsed.get('declared_forms_version')!=VERSION or parsed.get('declaration_event_fingerprint')!=event.fingerprint:
        raise ValueError('declaration differs from current event source')
    rows=parsed.get('declared_forms')
    if not isinstance(rows,(list,tuple)) or len(rows)>16:raise ValueError('declaration resource limit differs')
    original=evidence_source(event);out=[]
    for row in rows:
        if not isinstance(row,dict) and not hasattr(row,'items'):raise ValueError('invalid declaration row')
        span=row.get('span');text=row.get('text')
        if (not _peer_speech(event) or row.get('scope')!='speech' or row.get('mode')!='ongoing'
            or not isinstance(text,str) or not text.strip() or len(text)>48
            or not isinstance(span,(list,tuple)) or len(span)!=2 or any(type(x)is not int for x in span)
            or not 0<=span[0]<span[1]<=len(original) or span[1]-span[0]>256
            or text not in original[span[0]:span[1]] or original.count(original[span[0]:span[1]])!=1):
            raise ValueError('declaration lacks exact current peer speech evidence')
        value={'text':text,'span':list(span),'scope':'speech','mode':'ongoing'}
        if value not in out:out.append(value)
    return out


def parse_declarations(raw,event):
    from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences
    data=json.loads(_strip_fences(raw));items=data.get('declared_forms',[])
    if not isinstance(items,list) or len(items)>16:raise ValueError('declaration output resource limit differs')
    original=evidence_source(event);rows=[];rejected=0
    for item in items:
        if not isinstance(item,dict):rejected+=1;continue
        text=item.get('text');quote=item.get('evidence_text')
        if (not _peer_speech(event) or item.get('scope')!='speech' or item.get('mode')!='ongoing'
            or not isinstance(text,str) or not text.strip() or len(text)>48
            or not isinstance(quote,str) or not 0<len(quote)<=256
            or text not in quote or original.count(quote)!=1):
            rejected+=1;continue
        at=original.index(quote);row={'text':text,'span':[at,at+len(quote)],'scope':'speech','mode':'ongoing'}
        if row not in rows:rows.append(row)
    result={'declared_forms_version':VERSION,'declaration_event_fingerprint':event.fingerprint,
        'declared_forms':rows,'rejected_declared_form_count':rejected}
    validate_declarations(event,result)
    return result


class DeclaredFormCodec:
    memory_recall_version=RECALL_VERSION
    def __init__(self,base):
        self.base=base;self.request_version=base.request_version+':current-form-declaration-v1'
    def __getattr__(self,name):return getattr(self.base,name)
    def build_request(self,*,event,recall_hint):
        system,user=self.base.build_request(event=event,recall_hint=recall_hint)
        if _peer_speech(event):
            system+='\n当前话语的记忆属性：当用户在本条原文明确提出持续适用于该角色后续讲话的约定，将该约定记为 declared_forms；这是对本条事实的分类，原文留档，后续是否唤起由原机制判定。临时请求、引用转述按各自范围理解。每项为 {"text":"约定原词","evidence_text":"当前话语中完整唯一的约定片段","scope":"speech","mode":"ongoing"}。当前话语没有这种约定时为空数组。最多16项，原词48字符、证据256字符以内；该属性与本次数值量测分别输出。'
        return system,user
