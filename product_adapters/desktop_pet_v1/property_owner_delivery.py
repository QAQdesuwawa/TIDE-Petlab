"""Read-only owner aliases from the historical event, never current roots."""
from framework.model_api import thaw_model_data


def annotate(fields,provenance,*,owner,events=None):
    fields=thaw_model_data(fields)
    records=provenance.get('source_records',())
    if events is not None:
        ids=provenance['selected_memory_ids']
        records=[{'event':events[i]} for i in ids]
    def one(row,event=None):
        if not isinstance(row,dict) or '语义属性' not in row:return row
        event=event or {}
        subject=event.get('owner_ref',owner)
        peer=None
        if event.get('event_kind')=='peer_input' and event.get('actor_ref')!=subject:
            peer=event.get('actor_ref')
        # A multi-recipient self output needs its historical relation binding;
        # its visible audience or today's active peer cannot supply that fact.
        elif event.get('event_kind')=='npc_output' and len(event.get('target_refs',()))==1:
            peer=event['target_refs'][0]
        if peer==subject:peer=None
        return {**row,'属性归属绑定':{'current_npc':subject,
            'user':peer or '该条原量测的关系对端，当前卡片未确定其身份',
            'third_party':'该条关系双方以外的对象','unresolved':'原文未确定的对象'}}
    if '相关记忆（按发生顺序）' in fields:
        rows=fields['相关记忆（按发生顺序）']
        if records and len(records)!=len(rows):raise ValueError('memory owner event count differs')
        fields['相关记忆（按发生顺序）']=[one(row,records[i]['event'] if records else None) for i,row in enumerate(rows)]
        return fields
    return one(fields,records[0]['event'] if len(records)==1 else None)
