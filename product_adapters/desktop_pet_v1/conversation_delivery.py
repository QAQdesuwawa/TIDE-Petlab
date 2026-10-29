"""Deliver the existing bounded witnessed context in the conversation lane.

This is source interpretation, not recall, state influence or send permission.
The same source builder is used by measurement; no other Actor store is read.
"""
import json
from model_packages.cx_six_seven_v1.realizer_packet import WriterCard
from .visible_context import build
from petlab_app.writer_visibility import allowed

VERSION='desktop-pet-witnessed-conversation-delivery-v1'
KIND='witnessed_conversation_context'

def typed_row(actor,owner,contents):
    labels={'speech':'本角色已经说过的话' if actor==owner else '对方已经说过的话',
        'action':'实际动作阶段','expression':'已显示表情'}
    return {'实际说话或行动者':actor,'已发生内容':[{'内容身份':labels[c.kind],'原文':c.description} for c in contents]}


def card_for(app,event,*,readers,typed=False):
    context=build(app,event)
    if context is None:return None
    rows=json.loads(context['text']) if context['text'] else []
    kept=[];refs=[];excluded=[]
    if len(rows)!=len(context['sources']):raise ValueError('visible context rows differ from source references')
    for row,ref in zip(rows,context['sources']):
        memory=app.runtime.memory_instance(ref['event_id'])
        if not allowed(app,memory,readers):
            excluded.append({'event_id':ref['event_id'],'reason':'outside_output_audience'})
            continue
        if typed:
            from model_packages.desktop_pet_v1.events import PetEvent
            prior=PetEvent.from_record(app._product()['sources'][ref['event_id']]['event'])
            contents=tuple(c for c in prior.visible_contents(event.owner_ref) if not c.private and c.kind in ('speech','action','expression')
                and (prior.event_kind!='npc_output' or event.actor_ref in c.recipient_refs))
            row=typed_row(prior.actor_ref,event.owner_ref,contents)
        kept.append(row);refs.append(ref)
    if typed:
        while kept and len(json.dumps(kept,ensure_ascii=False,separators=(',',':')))>context['limit_chars']:
            kept.pop(0);removed=refs.pop(0)
            excluded.append({'event_id':removed['event_id'],'reason':'typed_context_budget'})
    fields={'上下文范围':'同一会话中，本角色此前实际收到或公开表达的内容。',
        '已见的同会话前文':kept}
    status='delivered' if kept else 'deferred_budget' if context['status']=='deferred_budget' else 'withheld_visibility'
    source_ids=[r['event_id'] for r in refs]
    return WriterCard(card_id='conversation:'+event.event_id+':witnessed',lane='conversation',kind=KIND,
        source_id=event.event_id,fields=fields,
        source_receipt_refs=(context['fingerprint'],app.runtime.memory_instance(event.event_id).fact.fingerprint,
            *(r['fact_fingerprint'] for r in refs)),
        provenance={'version':VERSION if not typed else 'desktop-pet-witnessed-conversation-delivery-v2-typed','status':status,'context_fingerprint':context['fingerprint'],
            'source_event_ids':source_ids,'source_count':len(refs),'excluded':excluded,
            'limit_chars':context['limit_chars'],'max_events':context['max_events'],
            'budget_boundary_event_id':context['budget_boundary_event_id'],
            'formal_recall':False,'state_commit_authority':False})
