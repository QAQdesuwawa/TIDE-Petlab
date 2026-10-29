"""Finite original-text source window; not a memory selection or state effect."""
import json
from framework.model_api import model_data_fingerprint
from model_packages.desktop_pet_v1.semantic import evidence_source
from model_packages.desktop_pet_v1.reference_window import VERSION
from .input_reference import source


def render(rows):
    return json.dumps([{'reference_id':x['reference_id'],'text':x['text']} for x in rows],ensure_ascii=False,separators=(',',':'))


def build(runtime,event,*,limit_chars=2048):
    if type(limit_chars)is not int or limit_chars<=0:raise ValueError('reference window budget must be positive')
    if event.event_kind!='peer_input' or {c.kind for c in event.contents}!={'speech'}:return None
    from . import EVENT_JOURNAL_KEY
    journal=runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    before=event.event_id not in journal;rows=[];boundary=None;first=None;source_chars=0;history=None
    for identity in reversed(journal):
        # During replay the journal also contains later accepted events.
        if not before:
            if identity==event.event_id:before=True
            continue
        raw=journal[identity]['event']
        if (raw['event_kind']!='peer_input' or raw['actor_ref']!=event.actor_ref
            or raw['owner_ref']!=event.owner_ref or raw['scope_ref']!=event.scope_ref
            or raw['position']['coordinate']>event.position.coordinate
            or {c['kind'] for c in raw['contents']}!={'speech'}):continue
        if history is None:history=runtime._history()
        from model_packages.desktop_pet_v1.events import PetEvent,MemoryInstance
        prior=MemoryInstance(PetEvent.from_record(raw),history.fact(identity))
        if len(prior.event.visible_contents(event.owner_ref))!=len(prior.event.contents):continue
        original=evidence_source(prior.event);ref=source(runtime,prior)
        if first is None:first=ref
        source_chars+=len(original)
        item={'reference_id':str(len(rows)+1),'source':ref,'text':original}
        if len(render([*rows,item]))>limit_chars:
            boundary=ref;break
        rows.append(item)
    if first is None:return None
    row={'version':VERSION,'event_fingerprint':event.fingerprint,'source':first,'sources':rows,
         'limit_chars':limit_chars,'source_chars':source_chars,'budget_boundary_source':boundary,
         'status':'delivered' if rows else 'deferred_budget','text':render(rows) if rows else ''}
    row['fingerprint']=model_data_fingerprint(row)
    return row


def verify_runtime(runtime,event,parsed):
    from model_packages.desktop_pet_v1.reference_window import USED_SCOPE,FULL_SCOPE
    expected=build(runtime,event)
    scope=parsed.get('reference_source_scope',FULL_SCOPE)
    if scope not in (USED_SCOPE,FULL_SCOPE):raise ValueError('unknown reference source scope')
    used={x['reference_id'] for x in parsed.get('reference_cues',())}
    if (expected is None or expected['status']!='delivered'
        or parsed.get('reference_context_fingerprint')!=expected['fingerprint']
        or model_data_fingerprint(parsed.get('reference_sources'))!=model_data_fingerprint(
            [{'reference_id':x['reference_id'],'source':x['source']} for x in expected['sources'] if scope==FULL_SCOPE or x['reference_id'] in used])
        or dict(parsed.get('reference_source',{}))!=expected['source']):
        raise ValueError('reference window differs from actual formal sources')
