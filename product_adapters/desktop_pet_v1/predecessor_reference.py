"""Bounded direct-dialogue predecessor sources for one v8 measurement."""
import json

from model_packages.desktop_pet_v1.events import PetEvent
from model_packages.desktop_pet_v1.predecessor_reference import HEADER
from . import EVENT_JOURNAL_KEY
from .input_reference import source


def build(app,event):
    current=app._product()['sources'][event.event_id]
    if (event.event_kind!='peer_input' or event.actor_ref!=app.user_ref
        or current.get('evidence',{}).get('input_source')
        or not event.contents or any(c.kind!='speech' or c.private for c in event.contents)):
        return (), ''
    product_sources=app._product()['sources']
    journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    before=event.event_id not in journal
    rows=[];sources=[]
    history=app.runtime._history()
    facts={(f.accepted_runtime_ordinal,f.temporal_position_fingerprint):f.fact_id for f in history.facts()}
    reply_source_key=getattr(getattr(app,'rag_memory',None),'reply_source_key',None)
    for identity in reversed(journal):
        if not before:
            if identity==event.event_id:before=True
            continue
        prior=PetEvent.from_record(journal[identity]['event'])
        if (prior.owner_ref!=event.owner_ref or prior.scope_ref!=event.scope_ref
            or prior.position.coordinate>event.position.coordinate):
            continue
        accepted=product_sources[identity]
        speech=tuple(c for c in prior.visible_contents(event.owner_ref) if c.kind=='speech' and not c.private)
        if prior.event_kind=='peer_input' and prior.actor_ref==event.actor_ref:
            if accepted['origin']!='input' or accepted.get('evidence',{}).get('input_source'):continue
            speaker='用户'
        elif (reply_source_key is not None and prior.event_kind=='npc_output'
              and prior.actor_ref==event.owner_ref and accepted['origin']=='execution'):
            parent_id=facts.get(reply_source_key(accepted))
            parent=journal.get(parent_id)
            if (parent is None or parent['event']['event_kind']!='peer_input'
                or parent['event']['actor_ref']!=event.actor_ref
                or parent['event']['scope_ref']!=event.scope_ref
                or product_sources[parent_id].get('evidence',{}).get('input_source')):continue
            speech=tuple(c for c in speech if event.actor_ref in c.recipient_refs)
            speaker='角色'
        else:continue
        if not speech:continue
        app._verify_source(prior)
        text='\n'.join(c.description for c in speech)
        row={'编号':str(len(rows)+1),'说话者':speaker,'原文':text}
        material=json.dumps([*rows,row],ensure_ascii=False,separators=(',',':'))
        if len(material)>2048:break
        rows.append(row)
        sources.append((text,source(app.runtime,app.runtime.memory_instance(identity))))
        if len(rows)==16:break
    return tuple(sources),json.dumps(rows,ensure_ascii=False,separators=(',',':'))



def build_public(app,event):
    """Reuse the verified public Writer view for literal speech references."""
    if event.actor_ref!=app.user_ref or not event.contents or any(c.kind!='speech' or c.private for c in event.contents):
        return (), ''
    from .conversation_delivery import card_for
    card=card_for(app,event,readers=None,typed=True,public_human=True)
    if card is None:
        return (), ''
    rows=[];sources=[]
    pairs=zip(card.fields['已见的同会话前文'],card.provenance['source_event_ids'])
    for row,identity in reversed(tuple(pairs)):
        prior=app.runtime.memory_instance(identity)
        admitted=card.provenance['source_content_ids'][identity]
        text='\n'.join(c.description for c in prior.event.contents if c.kind=='speech' and c.content_id in admitted)
        if not text:
            continue
        item={'编号':str(len(rows)+1),'说话者':row['实际说话或行动者'],'原文':text}
        if len(json.dumps([*rows,item],ensure_ascii=False,separators=(',',':')))>card.provenance['limit_chars']:
            break
        rows.append(item)
        sources.append((text,source(app.runtime,prior)))
    return tuple(sources),json.dumps(rows,ensure_ascii=False,separators=(',',':'))

