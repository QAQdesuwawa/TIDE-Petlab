"""Versioned product projection of accepted memory-influence edges."""
from dataclasses import replace
from model_packages.cx_six_seven_v1.realizer_packet import WriterSubcard
from ops.memory_link_history_source import history_memory_link_subcard

VERSION='desktop-pet-directed-memory-influence-v1'


def relation_child(parent,links,*,current_event_id):
    if links is None:return None
    ids=tuple(parent.provenance['selected_memory_ids'])
    rows=parent.fields.get('相关记忆（按发生顺序）',(parent.fields,))
    if len(ids)!=len(rows):raise ValueError('relation markers differ from selected sources')
    markers={i:row['记忆标记'] for i,row in zip(ids,rows)}
    if len(set(markers.values()))!=len(markers):raise ValueError('duplicate relation markers')
    if current_event_id is not None:
        if current_event_id in markers:raise ValueError('current event cannot also be historical memory')
        markers[current_event_id]='本次输入'
    chains=[]
    for edge in links.related_chains:
        if len(edge)!=2 or not set(edge)<=set(markers):raise ValueError('unmapped or non-direct influence edge')
        chains.append(' → '.join(markers[i] for i in edge))
    return WriterSubcard(subcard_id=parent.card_id+':directed-influence',kind='memory_link_subcard',source_id=VERSION,
        fields={'关系含义':'A → B 表示：记忆A在事件B的正式结算中被唤起并产生了影响。标记对应上方记忆；本次输入对应本轮当前输入。',
                '记忆影响有向边':chains},source_receipt_refs=links.source_receipt_refs,
        provenance={'extension_version':VERSION,'parent_card_id':parent.card_id,
                    'selected_memory_ids':ids,'related_chains':links.related_chains,'current_event_id':current_event_id})


def attach(cards,*,application,event_id,version):
    if version is None:return cards
    if version!=VERSION:raise ValueError('unsupported memory relation projection')
    history=application.runtime._history()
    from petlab_app.idle_opening import is_idle_tick
    current=None if is_idle_tick(application,event_id) else history.fact(event_id)
    result=[]
    for parent in cards:
        ids=tuple(parent.provenance['selected_memory_ids'])
        current_id=event_id if current is not None else None
        endpoints=(*ids,current_id) if current_id is not None else ids
        if len(endpoints)!=len(set(endpoints)):raise ValueError('relation source repeats current endpoint')
        before=(current.accepted_runtime_ordinal+1 if current is not None else
                max((history.fact(i).accepted_runtime_ordinal for i in ids),default=-1)+1)
        read=history.authorize_read(reader_ref=application.owner_ref,trigger_fact_id=parent.card_id+':relation-projection',
            before_runtime_ordinal=before,requested_fact_ids=endpoints,
            authorization_ref={'presentation_version':VERSION,'parent_card_id':parent.card_id,
                               'current_fact_fingerprint':current.fingerprint if current is not None else None})
        links=history_memory_link_subcard(history=history,history_read=read,parent_card_id=parent.card_id,
            selected_memory_ids=endpoints,enabled=True)
        child=relation_child(parent,links,current_event_id=current_id)
        kept=tuple(c for c in parent.subcards if c.kind!='memory_link_subcard')
        result.append(replace(parent,subcards=(*kept,*((child,) if child is not None else ()))))
    return tuple(result)
