"""Read-only actual effects linked to an input admitted during playback.

Execution evidence is available before its numerical output measurement.  This
view never treats a prepared candidate or an unsealed outcome as an actual act.
"""
from framework.model_api import model_data_fingerprint as fp, thaw_model_data
from model_packages.desktop_pet_v1.events import PetEvent
from model_packages.cx_six_seven_v1.realizer_packet import WriterCard

VERSION='petlab-bound-execution-frontier-v1'
CHECKPOINT_VERSION='petlab-bound-execution-frontier-v2-checkpoints'
PROGRESS_VERSION='petlab-bound-execution-frontier-v3-speech-progress'


# These animations express a gesture. Their natural end commits no object,
# position, contact, or lasting pose. Other actions keep their full phase.
from model_packages.desktop_pet_v1.basic_actions import DESCRIPTIONS
_TRANSIENT_GESTURES = {key: DESCRIPTIONS[key] for key in
                       ('head_bow','head_tilt','nod','shake_head','wave_hand','beckon')}
_TRANSIENT_GESTURES['play_tail'] = '摆动自己的尾巴'

def _gesture_occurrence(content, phase, started_at_ms):
    if (content.kind != 'action' or content.semantic_key not in _TRANSIENT_GESTURES
            or phase not in ('started','completed') or type(started_at_ms) is not int):
        return None
    return {'通道':'实际动作','内容':_TRANSIENT_GESTURES[content.semantic_key],
            '内容接收者':list(content.recipient_refs),'已开始于毫秒':started_at_ms}

def _occurrence_view(rows, ordinary):
    if not rows or any(row is None for _,row in rows):
        return ordinary
    if not any('已开始于毫秒' in row for _,row in rows):
        return ordinary
    return {**ordinary,'实际进度':[dict(row,片段=content_id) for content_id,row in sorted(rows)],
            '材料范围':'本次补话所涉及的已播姿势和已显示表情。姿势时间是开始时刻，不能据此判断现在仍在做。'}

def _checkpoint_row(app,event_id,action_id,readers,*,product=None,checkpoint_refs=None,progress_ref=None,live_progress=False,gesture_history=False):
    if product is None:product=app._product()
    entry=product['executions'].get(action_id,{})
    read=getattr(app,'visible_execution_records',None)
    if not callable(read) or entry.get('status') not in {'started','uncertain','executed'}:return None
    event=PetEvent.from_record(product['sources'][event_id]['event'])
    if not any(ref['event_id']==event_id and ref['source_binding']==event.source_binding_ref
               for ref in entry.get('live_input_sources',())):return None
    app._verify_source(event)
    records=read(action_id)
    if checkpoint_refs is not None:
        records=records[:len(checkpoint_refs)]
        if [row['fingerprint'] for row in records]!=list(checkpoint_refs):
            raise ValueError('frozen execution checkpoints changed')
    progress_read=getattr(app,'visible_speech_progress',None)
    progress=(progress_read(action_id,progress_ref) if callable(progress_read) and (progress_ref is not None or live_progress) else None)
    if not records and progress is None:return None
    source=records[0]['source'] if records else progress['source']
    if progress is not None and progress['source']!=source:raise ValueError('speech progress parent differs')
    if (source['parent_action_id']!=action_id or source['owner_ref']!=app.owner_ref
            or source['session_binding']!=app.runtime.session.session_binding.fingerprint
            or source.get('prepared_fingerprint')!=entry.get('prepared_fingerprint')):
        raise ValueError('execution checkpoints differ from admitted parent')
    from model_packages.desktop_pet_v1.events import Content
    latest={}
    for record in records:
        for item in record['items']:
            latest[item['content']['content_id']]=item
    if progress is not None:
        for item in progress['items']:
            key=item['content']['content_id']
            if key not in latest or latest[key]['at_ms']<item['at_ms']:latest[key]=item
    rows=[]
    occurrences=[]
    labels={'progress':'当时正在说，仅包含已显示的文字','started':'当时已开始，后续尚未确认','completed':'已完成',
            'interrupted':'已中断，内容仅含实际发生部分'}
    kinds={'speech':'已经说出的话','action':'实际动作','expression':'已显示表情'}
    for item in latest.values():
        content=Content.from_record(item['content'])
        if content.private or content.kind=='thought' or not set(readers)<=set(content.recipient_refs)|{app.owner_ref}:continue
        rows.append({'通道':kinds.get(content.kind,content.kind),'内容':content.description,
                     '结果':labels[item['phase']],'内容接收者':list(content.recipient_refs),
                     '观察时刻毫秒':item['at_ms']})
        if gesture_history:
            row=_gesture_occurrence(content,item['phase'],item['started_at_ms'])
            if content.kind=='expression' and item['phase']=='completed':
                row=rows[-1]
            occurrences.append((content.content_id,row))
    if not rows:return None
    value={'version':PROGRESS_VERSION if progress is not None else CHECKPOINT_VERSION,'event_id':event_id,'action_id':action_id,
           'owner':app.owner_ref,'readers':list(readers),
           'checkpoint_refs':[row['fingerprint'] for row in records],
           'receipt_fingerprint':records[-1]['fingerprint'] if records else progress['fingerprint'],
           **({'progress_ref':progress['fingerprint']} if progress is not None else {}),
           'progress':{'实际说话或行动者':app.owner_ref,'实际进度':rows,
                       '材料范围':'已记录的实际播放片段；父执行尚未全部封存，未记录部分不能视为已发生。'}}
    if gesture_history:
        value['gesture_history']=True
        value['progress']=_occurrence_view(occurrences,value['progress'])
    return {**value,'fingerprint':fp(value)}


def _row(app,event_id,action_id,readers,*,product=None,gesture_history=False):
    if product is None:product=app._product()
    entry=product['executions'].get(action_id,{})
    source=product['sources'].get(action_id)
    if entry.get('status')!='executed' or not source or source['origin']!='execution':return None
    input_event=PetEvent.from_record(product['sources'][event_id]['event'])
    if not any(ref['event_id']==event_id and ref['source_binding']==input_event.source_binding_ref
               for ref in entry.get('live_input_sources',())):return None
    actual=PetEvent.from_record(source['event'])
    app._verify_source(input_event)
    app._verify_source(actual)
    receipt=source['evidence']['fact']['model_payload']['execution_receipt']
    # Select only the channels available to this audience, never a whole private
    # parent merely because it contains another public channel.
    contents=[c for c in actual.contents if not c.private and c.kind!='thought'
              and set(readers)<=set(c.recipient_refs)|{app.owner_ref}]
    if not contents:return None
    results=receipt['outcome']['details'].get('child_results',{})
    labels={'completed':'已完成','interrupted':'已中断，内容仅含实际发生部分'}
    kinds={'speech':'已经说出的话','action':'实际动作','expression':'已显示表情'}
    rows=[]
    occurrences=[]
    for content in contents:
        result=results.get(content.content_id,{})
        rows.append({'通道':kinds.get(content.kind,content.kind),'内容':content.description,
                     '结果':labels.get(result.get('status'),'实际已发生'),
                     '内容接收者':list(content.recipient_refs),
                     '结束时刻毫秒':result.get('ended_at_ms')})
        if gesture_history:
            row=_gesture_occurrence(content,result.get('status'),result.get('started_at_ms'))
            if content.kind=='expression' and result.get('status')=='completed':
                row=dict(rows[-1])
                row['观察时刻毫秒']=row.pop('结束时刻毫秒')
            occurrences.append((content.content_id,row))
    value={'version':VERSION,'event_id':event_id,'action_id':action_id,
           'owner':app.owner_ref,'readers':list(readers),
           'receipt_fingerprint':receipt['fingerprint'],
           'progress':{'实际说话或行动者':app.owner_ref,'实际进度':rows,
                       '材料范围':'这次输入到达期间，上一轮实际已经播放的部分；不含未执行草稿。'}}
    if gesture_history:
        value['gesture_history']=True
        value['progress']=_occurrence_view(occurrences,value['progress'])
    return {**value,'fingerprint':fp(value)}


def build(app,event_id,readers,*,gesture_history=False):
    # Match the recorded input linkage, not time proximity or a guessed speaker.
    product=app._product()
    for action_id in reversed(product['executions']):
        entry=product['executions'][action_id]
        if not any(ref['event_id']==event_id for ref in entry.get('live_input_sources',())):continue
        value=_row(app,event_id,action_id,readers,product=product,gesture_history=gesture_history)
        if value is None:value=_checkpoint_row(app,event_id,action_id,readers,product=product,live_progress=True,gesture_history=gesture_history)
        if value is not None:return value
    return None


def validate(app,value,event_id,readers):
    value=thaw_model_data(value)
    expected=(_checkpoint_row(app,event_id,value['action_id'],readers,checkpoint_refs=value['checkpoint_refs'],progress_ref=value.get('progress_ref'),gesture_history=value.get('gesture_history',False))
              if value.get('version') in {CHECKPOINT_VERSION,PROGRESS_VERSION} else _row(app,event_id,value['action_id'],readers,gesture_history=value.get('gesture_history',False)))
    if value!=expected:
        raise ValueError('actual execution frontier differs from its bound receipt')
    return value


def card(value):
    return WriterCard(card_id='conversation:'+value['event_id']+':execution_frontier',
        lane='conversation',kind='actual_execution_progress',source_id=value['action_id'],
        fields={'最近一次执行的实际进度':value['progress']},
        source_receipt_refs=tuple(value.get('checkpoint_refs',(value['receipt_fingerprint'],)))+((value['progress_ref'],) if 'progress_ref' in value else ()),
        provenance={'version':value['version'],'formal_recall':False,'state_commit_authority':False})
