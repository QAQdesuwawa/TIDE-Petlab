"""Current literal topics and unresolved reference slots; no numerical score."""
from .semantic import evidence_source

EXPLICIT='explicit_topic'
SLOT='reference_slot'
KINDS={EXPLICIT,SLOT}


def classify(cues,data,event):
    text=evidence_source(event);kinds=[];deferred=0
    for cue in cues:
        a,b=cue['span']
        matches=[item.get('cue_kind') for item in data.get('memory_cues',())
            if isinstance(item,dict) and item.get('text')==cue['text'] and item.get('evidence_text')==text[a:b]]
        if matches and all(isinstance(value,str) and value==matches[0] and value in KINDS for value in matches):
            kinds.append(matches[0])
        else:kinds.append(EXPLICIT);deferred+=1
    return kinds,deferred


def _typed(parsed):
    cues=parsed.get('cues',());kinds=parsed.get('cue_kinds')
    if (not isinstance(cues,(list,tuple)) or len(cues)>16
            or not isinstance(kinds,(list,tuple)) or len(kinds)!=len(cues)
            or any(not isinstance(kind,str) or kind not in KINDS for kind in kinds)):
        raise ValueError('reference cue kinds differ from literal evidence')
    return zip(cues,kinds)


def _spans(text,cue):
    a,b=cue['span'];term=cue['text']
    if (type(a)is not int or type(b)is not int or not 0<=a<b<=len(text) or b-a>256
            or not isinstance(term,str) or not term or len(term)>48
            or term not in text[a:b] or text.count(text[a:b])!=1):
        raise ValueError('typed cue lacks actual current evidence')
    spans=[];at=a
    while True:
        found=text.find(term,at,b)
        if found<0:return spans
        spans.append((found,found+len(term)));at=found+1


def permits(event,parsed,row):
    text=evidence_source(event);a,b=row['span'];slot=False
    for cue,kind in _typed(parsed):
        spans=_spans(text,cue)
        if kind==EXPLICIT and any(s<b and a<t for s,t in spans):return False
        if kind==SLOT and all(a<=s and t<=b for s,t in spans):slot=True
    return slot


def retained_phrases(event,parsed):
    text=evidence_source(event);phrases=[]
    for cue,kind in _typed(parsed):
        spans=_spans(text,cue)
        covered=kind==SLOT and any(all(row['span'][0]<=s and t<=row['span'][1] for s,t in spans)
            for row in parsed.get('reference_cues',()))
        if not covered and cue['text'] not in phrases:phrases.append(cue['text'])
    return phrases
