"""Versioned display projection of selected cards; no model-state authority."""
from dataclasses import replace
from collections.abc import Mapping
from framework.model_api import thaw_model_data,model_data_fingerprint

VERSION='desktop-pet-readable-card-view-v1'


def visible_facts(value):
    if isinstance(value,dict):
        fields={key:visible_facts(item) for key,item in value.items()}
        timing=fields.get('事实时序')
        if isinstance(timing,dict) and '原发生时间' in timing:
            fields['事实时序']={'原发生时间':timing['原发生时间']}
        if fields.get('内容身份')=='已显示表情' and isinstance(fields.get('原文'),str):
            fields['原文']=fields['原文'].removeprefix('说话时')
        attrs=fields.get('内容属性',())
        if attrs and all(isinstance(row,dict) and row.get('类型')=='expression' for row in attrs) and isinstance(fields.get('正文'),str):
            fields['正文']=fields['正文'].replace('[可见表情] 说话时呈现','[可见表情] 呈现')
        return fields
    if isinstance(value,list):return [visible_facts(item) for item in value]
    return value

def public_value(value):
    if isinstance(value,Mapping):
        return {k:public_value(v) for k,v in value.items() if k not in {
            'version','fingerprint','参数','axis_presence','variable_deltas','confidence',
            'operator_versions','source_receipt_refs','parameter_fingerprint','schema_fingerprint',
            '推进序号'}}
    if isinstance(value,(tuple,list)):return [public_value(v) for v in value]
    return value

def project(packet, *, memory_evidence=False,shared_bodies=False,optional_context=False,independent_children=False,current_time=False,subtype_references=False,speaker_framing=False,conversation_context=False,shared_metadata=False,grouped_metadata=False,body_context=None,action_sequence_context=None,contextual=False,intention_time_labels=False,property_owners=False,property_owner_events=None,free_action_intents=False,scene_facts=False,concise=False,compact_actions=False,memory_occurrences=None,world_sources=False):
    sections={}
    owner=packet.output_identity.speaker_participant_id
    bodies={}
    idle_opportunity=independent_children and any('idle_opening_reference' in c.fields for c in packet.sections.get('form',()))
    selected_body=None
    if body_context is not None:
        from .body_delivery import plan_fields
        selected_body=plan_fields(body_context,owner_ref=owner)
    planned_action=selected_body is not None or any('本轮已选行动计划' in c.fields for c in packet.sections.get('form',()))
    body_written=False
    for lane,cards in packet.sections.items():
        if lane=='memory' and memory_occurrences is not None:
            cards=sorted(cards,key=lambda card:min((memory_occurrences.get(i,'') for i in card.provenance.get('selected_memory_ids',())),default=''))
        rows=[]
        for card in cards:
            fields=thaw_model_data(card.fields)
            if lane=='persona':
                keys={'pet_persona_voice':{'语气','文风'},
                      'pet_persona_habits':{'本人常用表达（供参考）'}}.get(card.kind,{'name','background'})
                fields={k:v for k,v in fields.items() if k in keys}
            elif lane=='current_input':fields={k:public_value(v) for k,v in fields.items() if k in {'说话者','对象','接收者','父级回应对象','本次观察者','正文','内容属性','事实时序','触发来源','本轮来源','当前事实','已发生事实','内容归属注解','本轮指代参照'}}
            elif lane=='conversation' and conversation_context and card.kind=='witnessed_conversation_context':
                fields={k:public_value(v) for k,v in fields.items() if k in {'上下文范围','已见的同会话前文'}}
                if memory_occurrences is not None and '已见的同会话前文' in fields:
                    from .event_timeline import chronological_context
                    fields=chronological_context(fields)
            elif lane=='conversation' and card.kind=='actual_execution_progress':
                fields={k:public_value(v) for k,v in fields.items() if k=='最近一次执行的实际进度'}
            elif lane=='conversation':
                at=fields.get('时间')
                if concise and not (current_time and at is not None):continue
                fields={} if concise else {'状态参照':'当前正式态度见本轮已选表达资源。'}
                if current_time and at is not None:
                    from datetime import datetime,timezone
                    fields['本轮正式时刻']=datetime.fromtimestamp(at/1000,tz=timezone.utc).isoformat()
            elif lane=='form':
                guidance=fields.get('subtype_realization_guidance')
                idle='idle_opening_reference' in fields
                deferred='deferred_reply_reference' in fields
                fields={k:public_value(v) for k,v in fields.items() if k in {'output_fields','presentation_contract','本轮已选行动计划','计划含义'}}
                if selected_body is not None:
                    # Older idle packets may already contain this selection.
                    # Keep one copy in the output contract, never a contradictory
                    # no-body claim followed by an appended action card.
                    fields.pop('本轮已选行动计划',None);fields.pop('计划含义',None)
                    if not body_written:fields.update(selected_body);body_written=True
                if subtype_references and guidance is not None:
                    from model_packages.desktop_pet_v1.presentation import render_guidance
                    fields['子类表达参照']=render_guidance(guidance)
                if idle:fields['触发事实']='这是角色主动搭话，当前没有新的用户输入。'
                if deferred:fields['回应对象']='此前已收到且尚未回应的原问题，以当前输入栏原文为准。'
                fields['表达职责']='本通道负责讲话，身体行动由已选计划与执行回执说明。'
                if idle and optional_context:
                    fields['触发事实']='动态Tick给出本轮表达资格，当前没有新的用户输入。'
                    fields['表达职责']='语音子项为本轮实际想说的话或安静（speech:null），身体活动沿已选计划和执行回执。'
                if idle and scene_facts:fields['触发事实']='当前没有新的用户输入。'
                if idle_opportunity:
                    fields['表达职责']='身体行动和语音子项独立；本通道可给出话语或安静（speech:null），已选动作按自己的执行回执发生。'
                    if '本轮已选行动计划' in fields:
                        fields['计划含义']='这是同一次父行动中由包选定的身体子项；是否讲话是独立结果。实际完成或中断由播放回执确认。'
                fields['身体行动事实']='身体行动见本轮已选行动计划，完成情况沿实际事实。' if planned_action else '本轮没有已选身体行动。'
                if action_sequence_context is not None:
                    from .sequence_delivery import fields as sequence_fields
                    fields.pop('本轮已选行动计划',None)
                    fields.pop('身体行动事实',None)
                    fields['表达职责']=('本轮可自由提出身体动作意图；讲话、表情与私有独白是并行子项。' if free_action_intents
                                      else '按已获准候选形成本轮动作序列；讲话、表情与私有独白是并行子项。')
                    field='action_intents' if free_action_intents else 'actions'
                    fields['output_fields']=list(dict.fromkeys([*fields.get('output_fields',[]),field]))
                    if not free_action_intents:fields.update(sequence_fields(action_sequence_context,compact=compact_actions))
                    if subtype_references and guidance is not None:
                        # Validate the old source references, replace only the
                        # single-action common role for this versioned contract.
                        render_guidance(guidance)
                        fields['子类表达参照']='\n'.join([*guidance['source_references'].values(),*guidance['output_references'].values()])
                if contextual:
                    fields.pop('子类表达参照',None)
                    fields['情境表达参照']='收到的话语、动作和表情是外界事实；结合各对象的关系词元、人物资料与已交付记忆，选择自己的关注、活动和表达。'
            else:fields=public_value(fields)
            if concise and lane=='form':
                if 'presentation_contract' in fields or '本轮已选行动计划' in fields or (compact_actions and '可选动作序列' in fields):
                    fields.pop('output_fields',None)
                if '本轮已选行动计划' in fields:
                    fields['计划含义']='计划中的动作，完成情况见实际动作记录。'
            if concise and lane in {'current_input','conversation'}:
                fields=visible_facts(fields)
            if lane=='memory':
                if property_owners:
                    from .property_owner_delivery import annotate
                    fields=annotate(fields,card.provenance,owner=owner,events=property_owner_events)
                if memory_occurrences is not None and card.provenance.get('selected_memory_ids'):
                    from .event_timeline import chronological_memory
                    fields=chronological_memory(fields,card.provenance['selected_memory_ids'],memory_occurrences)
                def event_time(row):
                    if not isinstance(row,dict):return row
                    row=dict(row);position=row.pop('位置',None)
                    if intention_time_labels and '自身意图进度' in row and '语义属性' in row:
                        row['意图形成时的语义属性']=row.pop('语义属性')
                    if memory_evidence and '正文' in row:
                        attrs=row.get('内容属性',[])
                        kinds={item.get('类型') for item in attrs if isinstance(item,dict)}
                        if kinds=={'speech'}:
                            row['记录含义']=('用户在此原话提出了对该角色讲话的持续约定，按所记适用范围理解。'
                                if row.get('记忆子类',{}).get('类型')=='用户提出的持续讲话约定' else
                                '历史话语：记录说话者当时说过的内容；其中的意向、评价与设想按话语理解，实际做过的动作以执行记录为据。')
                        elif 'speech' in kinds:
                            row['记录含义']='混合事件：话语记录当时说过的内容，动作部分按各自的执行记录及完成情况理解。'
                        elif kinds:
                            row['记录含义']='已记录的非话语事件：发生范围与完成情况以事件正文和内容属性中的执行记录为据。'
                    if position and position.get('domain')=='tide.wall-clock-ms':
                        from datetime import datetime,timezone
                        row['发生时间']=datetime.fromtimestamp(position['coordinate']/1000,tz=timezone.utc).isoformat()
                    if shared_bodies and '正文' in row:
                        key=model_data_fingerprint({k:row.get(k) for k in ('说话者','对象','接收者','正文','内容属性')})
                        marker=row.get('记忆标记')
                        if key in bodies:
                            row.pop('正文');row['相同正文参照']=bodies[key]
                        elif marker is not None:bodies[key]=marker
                    if '相关记忆（按发生顺序）' in row:row['相关记忆（按发生顺序）']=[event_time(x) for x in row['相关记忆（按发生顺序）']]
                    if speaker_framing:
                        from .speaker_view import frame_event
                        row=frame_event(row,owner=owner,historical=True,world_sources=world_sources)
                    return row
                fields=event_time(fields)
                if shared_metadata:
                    from .memory_metadata import compact_fields
                    fields=compact_fields(fields)
                if grouped_metadata:
                    from .memory_metadata import compact_grouped_fields
                    fields=compact_grouped_fields(fields)
            if speaker_framing and lane=='current_input':
                from .speaker_view import frame_event
                fields=frame_event(fields,owner=owner,historical=False,world_sources=world_sources)
            if not fields:fields={'内容参照':'本栏目沿其他已交付事实理解。'}
            rows.append(replace(card,fields=fields))
        sections[lane]=tuple(rows)
    view=replace(packet,sections=sections)
    return replace(view,packet_fingerprint=model_data_fingerprint(view.content_record()))
