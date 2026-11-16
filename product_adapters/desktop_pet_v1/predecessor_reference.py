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


def build_visible_peer(app, event):
    """Offer witnessed same-edge excerpts under the current speech audience."""
    if event.event_kind != 'peer_input' or event.actor_ref in (app.user_ref, app.owner_ref):
        return (), ''
    speeches = tuple(c for c in event.contents if c.kind == 'speech' and not c.private)
    if not speeches:
        return (), ''
    private_sources = ()
    if _has_private_peer_delivery(app, event, speeches):
        private_sources = _private_peer_sources(app, event)
    readers = tuple(dict.fromkeys((event.actor_ref, *(ref for c in speeches for ref in c.recipient_refs
                                                     if ref != app.owner_ref))))
    from .conversation_delivery import card_for
    card = card_for(app, event, readers=readers, typed=True, precommit=True)
    rows, sources = [], []
    seen = set()
    for memory, text in private_sources:
        prior = memory.event
        rows.append({'编号': str(len(rows) + 1), '说话者或动作主体': prior.actor_ref, '原文': text})
        sources.append((text, source(app.runtime, memory)))
        seen.add(prior.event_id)
    if card is None:
        return tuple(sources), json.dumps(rows, ensure_ascii=False, separators=(',', ':'))
    for identity in reversed(card.provenance['source_event_ids']):
        if identity in seen:
            continue
        if len(rows) >= 16:
            break
        memory = app.runtime.memory_instance(identity)
        prior = memory.event
        if prior.actor_ref not in (app.owner_ref, event.actor_ref):
            continue
        admitted = card.provenance['source_content_ids'][identity]
        text = '\n'.join(c.description for c in prior.contents if c.content_id in admitted)
        if not text:
            continue
        row = {'编号': str(len(rows) + 1), '说话者或动作主体': prior.actor_ref, '原文': text}
        if len(json.dumps([*rows, row], ensure_ascii=False, separators=(',', ':'))) > card.provenance['limit_chars']:
            break
        rows.append(row)
        sources.append((text, source(app.runtime, memory)))
    return tuple(sources), json.dumps(rows, ensure_ascii=False, separators=(',', ':'))


def _has_private_peer_delivery(app, event, speeches):
    from petlab_app.participant_input import VISIBLE_VERSION, validate

    if not all(set(c.recipient_refs) == {app.owner_ref} for c in speeches):
        return False
    row = app.runtime.session.profile['desktop_pet_product_v1']['sources'][event.event_id]
    receipt = row['evidence'].get('input_source', {})
    if receipt.get('version') != VISIBLE_VERSION:
        return False
    _, artifact = validate(app, receipt, event.contents, event_id=event.event_id,
                           return_artifact=True)
    origin = artifact['source']['origin']
    selected = origin.get('selected_speech_audience')
    return (artifact['delivery']['delivery_scope'] == 'addressed'
            and (selected == [app.owner_ref] or
                 (selected is None and origin.get('private_speech_target') == app.owner_ref)))


def _private_peer_sources(app, event):
    """Read only completed speech exchanged by this pair before this ingress."""
    profile = app.runtime.session.profile
    journal = profile.get(EVENT_JOURNAL_KEY, {})
    product_sources = profile['desktop_pet_product_v1']['sources']
    before = event.event_id not in journal
    rows, sources = [], []
    visited = 0
    for identity in reversed(journal):
        if not before:
            before = identity == event.event_id
            continue
        visited += 1
        if visited > 16:
            break
        prior = PetEvent.from_record(journal[identity]['event'])
        if (prior.owner_ref != app.owner_ref or prior.scope_ref != event.scope_ref
                or prior.position.coordinate > event.position.coordinate):
            continue
        origin = product_sources[identity]['origin']
        if prior.actor_ref == event.actor_ref and origin == 'input':
            listener = app.owner_ref
        elif prior.actor_ref == app.owner_ref and origin == 'execution':
            listener = event.actor_ref
        else:
            continue
        speech = tuple(c for c in prior.visible_contents(app.owner_ref)
                       if c.kind == 'speech' and not c.private
                       and set(c.recipient_refs) == {listener})
        if not speech:
            continue
        if origin == 'input' and not _has_private_peer_delivery(app, prior, speech):
            continue
        app._verify_source(prior)
        text = '\n'.join(c.description for c in speech)
        row = {'编号': str(len(rows) + 1), '说话者或动作主体': prior.actor_ref, '原文': text}
        if len(json.dumps([*rows, row], ensure_ascii=False, separators=(',', ':'))) > 2048:
            break
        rows.append(row)
        sources.append((app.runtime.memory_instance(identity), text))
    return tuple(sources)
