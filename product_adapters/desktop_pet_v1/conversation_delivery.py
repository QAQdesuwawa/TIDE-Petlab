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


def card_for(app,event,*,readers,typed=False,timeline=False):
    context=build(app,event)
    if context is None:return None
    rows=json.loads(context['text']) if context['text'] else []
    kept=[];refs=[];excluded=[];content_ids={}
    if len(rows)!=len(context['sources']):raise ValueError('visible context rows differ from source references')
    for row,ref in zip(rows,context['sources']):
        memory=app.runtime.memory_instance(ref['event_id'])
        whole_allowed=allowed(app,memory,readers)
        if not whole_allowed and not typed:
            excluded.append({'event_id':ref['event_id'],'reason':'outside_output_audience'})
            continue
        if typed:
            # build/allowed have already checked this exact accepted memory.
            # Keep its validated event rather than decoding the source again.
            prior=memory.event
            contents=tuple(c for c in prior.visible_contents(event.owner_ref) if not c.private and c.kind in ('speech','action','expression')
                and (prior.event_kind!='npc_output' or event.actor_ref in c.recipient_refs))
            if not whole_allowed:
                # A private speech sibling must not hide publicly performed
                # actions. Project actual child audiences, never the parent.
                public=tuple(c for c in contents if set(readers)<=set(c.recipient_refs)|{event.owner_ref,prior.actor_ref})
                removed=[c.content_id for c in contents if c not in public]
                excluded.append({'event_id':ref['event_id'],'reason':'outside_output_audience',
                                 'excluded_content_ids':removed})
                contents=public
            if not contents:continue
            content_ids[ref['event_id']]=[c.content_id for c in contents]
            row=typed_row(prior.actor_ref,event.owner_ref,contents)
            if timeline:
                from .event_timeline import describe
                row['事实时序'],row['父事件']=describe(app,prior)
        kept.append(row);refs.append(ref)
    if typed:
        while kept and len(json.dumps(kept,ensure_ascii=False,separators=(',',':')))>context['limit_chars']:
            kept.pop(0);removed=refs.pop(0)
            content_ids.pop(removed['event_id'],None)
            excluded.append({'event_id':removed['event_id'],'reason':'typed_context_budget'})
    if timeline:
        from .event_timeline import group
        kept=group(kept)
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
            **({'source_content_ids':content_ids} if typed else {}),
            'limit_chars':context['limit_chars'],'max_events':context['max_events'],
            'budget_boundary_event_id':context['budget_boundary_event_id'],
            'formal_recall':False,'state_commit_authority':False})


def execution_progress(receipt,contents):
    """Bounded actual receipt view; cancelled intentions are not memories of acts."""
    if not receipt:return None
    rows=receipt.get('outcome',{}).get('details',{}).get('child_results',{})
    if not rows or not any(r.get('status') in ('interrupted','cancelled') for r in rows.values()):return None
    actual={c.content_id:c for c in contents}
    labels={'completed':'已完成','interrupted':'进行中被打断','cancelled':'未执行已取消','failed':'未执行失败'}
    shown=[];cancelled=0
    for identity,row in rows.items():
        if identity in actual:
            c=actual[identity]
            shown.append({'通道':{'speech':'说话','action':'身体动作','expression':'表情'}.get(c.kind,c.kind),'内容':c.description,'结果':labels.get(row['status'],row['status']),
                **({'中止原因':{'user_contact':'用户触碰打断','physical_contact':'外部接触打断','destination_occupied':'落脚位置被占，尚未迈步','source_expired':'新的交互使旧计划失效','technical_failure':'播放故障','window_closed':'窗口关闭'}.get(row['reason'],'执行中止')} if row.get('reason') else {})})
        elif row.get('status')=='cancelled':cancelled+=1
    return {'实际进度':shown,'未执行后续项数':cancelled,'后续状态':'旧计划已停止，结合本次交互重新安排'}


def progress_card(app,event,*,readers):
    """Latest own execution only, bounded lookup, checked against output audience."""
    from itertools import islice
    from product_adapters.desktop_pet_v1 import EVENT_JOURNAL_KEY
    from model_packages.desktop_pet_v1.events import PetEvent
    journal=app.runtime.session.profile.get(EVENT_JOURNAL_KEY,{})
    for identity in islice(reversed(journal),16):
        prior=PetEvent.from_record(journal[identity]['event'])
        if prior.event_kind!='npc_output' or prior.owner_ref!=event.owner_ref or prior.position.coordinate>event.position.coordinate:continue
        source=app._product()['sources'][identity]
        if source['origin']!='execution':continue
        memory=app.runtime.memory_instance(identity)
        if not allowed(app,memory,readers):return None
        app._verify_source(prior)
        receipt=source['evidence']['fact']['model_payload']['execution_receipt']
        contents=tuple(c for c in prior.contents if not c.private)
        progress=execution_progress(receipt,contents)
        if progress is None:return None
        return WriterCard(card_id='conversation:'+event.event_id+':execution_progress',lane='conversation',kind='actual_execution_progress',
            source_id=identity,fields={'最近一次执行的实际进度':progress},
            source_receipt_refs=(memory.fact.fingerprint,receipt['fingerprint']),
            provenance={'version':'petlab-execution-progress-v1','formal_recall':False,'state_commit_authority':False})
    return None
