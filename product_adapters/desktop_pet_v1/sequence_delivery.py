"""One optional Writer proposal for an existing, formally eligible body set."""
import json
from framework.model_api import thaw_model_data
from model_packages.desktop_pet_v1 import action_sequence as seq,shared_meal as meal


def context_for(app,event_id,selection):
    from model_packages.desktop_pet_v1.action_candidates import MODEL_VERSION
    if not selection or (selection.get('version')!=MODEL_VERSION and selection.get('catalog_version') not in meal.SEQUENCE_CATALOG_VERSIONS):return None
    from petlab_app.idle_opening import KEY as IDLE_KEY
    from petlab_app.reaction_action import KEY as INPUT_KEY
    idle=app.runtime.session.profile.get(IDLE_KEY,{}).get('ticks',{}).get(event_id)
    row=idle if idle is not None else app.runtime.session.profile.get(INPUT_KEY,{}).get(event_id,{})
    if row.get('action_sequence_context') is not None:return seq.validate_offer(row['action_sequence_context'])
    return None  # Old pending inputs/ticks retain their single-action contract.


def verify_context(app,event_id,value):
    from model_packages.desktop_pet_v1.basic_actions import KEYS as BASIC_KEYS
    from petlab_app.reaction_action import selection,authorization
    value=seq.validate_offer(value);selected=selection(app,event_id);auth=authorization(app,event_id)
    if (selected is None or auth is None or value['selection']!=thaw_model_data(selected)
            or value['owner_ref']!=app.owner_ref or value['target_ref']!=auth.target_ref
            or value['selection']['source']!=auth.source_settlement_fingerprint):
        raise ValueError('sequence proposal differs from formal opportunity')
    # Catalog is bounded and frozen; current physical state is checked at each
    # actual child effect, not substituted into an old sealed Writer request.
    for row in value['catalog']['candidates']:
        target=row.get('target_ref',value['target_ref'])
        if 'world_object' in row:
            from model_packages.desktop_pet_v1 import spatial
            obj=spatial.world_object(row['world_object'])
            if target!=obj['ref'] or row['key']!='move_near@'+target or row.get('action_key')!='move_near':
                raise ValueError('world movement candidate differs from its object')
        elif target!=value['target_ref']:
            from petlab_app.destinations import audience
            from petlab_app.writer_visibility import output_readers
            from petlab_app.idle_opening import KEY as IDLE_KEY
            from petlab_app.reaction_action import KEY as INPUT_KEY
            source=app.runtime.session.profile.get(IDLE_KEY,{}).get('ticks',{}).get(event_id)
            if source is None:source=app.runtime.session.profile.get(INPUT_KEY,{}).get(event_id,{})
            policy=source.get('output_target_policy')
            if policy not in (None,'owner-scene-v1'):raise ValueError('unknown output target policy')
            readers=output_readers(app,event_id) if policy else audience(app,event_id)
            if target not in readers:raise ValueError('action target outside authorized audience')
        if 'meal' in row:
            bound=meal.verify_binding(row['meal'])
            if bound['actor_ref']!=value['owner_ref'] or bound['target_ref']!=target:
                raise ValueError('sequence candidate participant differs')
        elif row.get('action_key',row['key']) in ('move_near','stroll','move_away','relocate'):
            from model_packages.desktop_pet_v1 import movement
            movement.content(target,{**row,'key':row.get('action_key',row['key'])})
        elif value['selection'].get('version')=='desktop-pet-model-action-candidates-v2' and row['key'] in ('play_tail','read_web')+BASIC_KEYS:
            from model_packages.desktop_pet_v1.action_candidates import bind_proposal
            from model_packages.desktop_pet_v1.behavior import content
            content(value['target_ref'],selection=bind_proposal(value['selection'],row))
        else:raise ValueError('unknown sequence candidate entity')
    return value


def fields(value):
    value=seq.validate_offer(value)
    options=[]
    for row in value['catalog']['candidates']:
        if row['key'] not in value['allowed_keys']:continue
        item={'编码':row['key'],'含义':row['description']}
        if 'target_ref' in row:item['对象']=row['target_ref']
        if row.get('action_key',row['key']) in ('move_near','move_away'):
            target=row.get('target_ref',value['target_ref'])
            item['对象']=target
            item['含义']=item['含义'].replace('当前交谈对象',target)
        options.append(item)
    result={'可选动作序列':{'对象':value['target_ref'],'候选':options,'容量上限':value['max_actions']},
        '计划含义':'actions按希望执行的顺序填写候选编码数组，可为空。各片段由本地时间轴连续播放；speech与动作同属本轮反应，可同时出现，也可为null。实际发生的范围以执行记录为准，对方自行决定如何承接。'}
    if value['selection'].get('version')=='desktop-pet-model-action-candidates-v2':
        result['选择依据']='候选表示当前具备执行条件。结合完整人设、当前状态和记忆选择这一刻想做的动作，也可以不做动作；同一动作在不同心情下可以有不同含义。'
        if 'read_web' in value['allowed_keys']:result['执行容量']='本轮手机阅读最多一项。'
    return result


def _selected_keys(keys,context):
    """Resolve explicit spelling of an already bound movement, never a new target."""
    context=seq.validate_offer(context)
    aliases={}
    for row in context['catalog']['candidates']:
        key=row['key']
        if key in ('move_near','move_away') and key in context['allowed_keys']:
            explicit=key+'@'+row.get('target_ref',context['target_ref'])
            if explicit not in context['allowed_keys']:aliases[explicit]=key
    if isinstance(keys,(list,tuple)):
        keys=[aliases.get(k,k) if isinstance(k,str) else k for k in keys]
    return seq.validate_keys(keys,context)


def normalize_single_speech(text,*,implicit_timing=False):
    """Accept the exact single segment shape without changing its meaning."""
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('sequence output must be an object')
    segment=value.get('speech')
    if implicit_timing:
        def explicit(row):
            return {**row,'timing':None} if isinstance(row,dict) and set(row)=={'text'} else row
        if isinstance(segment,list):
            value['speech']=[explicit(row) for row in segment]
            return json.dumps(value,ensure_ascii=False)
        segment=explicit(segment)
    if not isinstance(segment,dict):return text
    if set(segment)!={'text','timing'} or value.get('speech_timing') is not None:
        raise ValueError('ambiguous single speech segment')
    value['speech']=[segment]
    return json.dumps(value,ensure_ascii=False)


def normalize_action_code_objects(text):
    """Unwrap an exact displayed-code field; leave authorization to the offer."""
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('sequence output must be an object')
    keys=value.get('actions')
    if not isinstance(keys,list):return text
    for item in keys:
        if isinstance(item,dict) and (set(item)!={'编码'} or not isinstance(item['编码'],str)):
            raise ValueError('ambiguous action code object')
    value['actions']=[item['编码'] if isinstance(item,dict) else item for item in keys]
    return json.dumps(value,ensure_ascii=False)


def isolate_unavailable_thought(text):
    """An undeclared optional lane cannot invalidate independent valid lanes."""
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('writer output must be an object')
    if 'thought' not in value:return text,[]
    thought=value.pop('thought')
    if thought is not None and (not isinstance(thought,str) or not thought.strip()):
        raise ValueError('invalid undeclared thought proposal')
    rejected=[] if thought is None else [{'kind':'thought','description':thought,
        'status':'not_executed','reason':'outside_declared_channels'}]
    return json.dumps(value,ensure_ascii=False),rejected


def isolate_unavailable(text,context):
    """Keep independent siblings; never repair a dependent plan by guessing."""
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError('sequence output must be an object')
    context=seq.validate_offer(context)
    keys=value.get('actions')
    if not isinstance(keys,list) or len(keys)>context['max_actions'] or any(not isinstance(k,str) for k in keys):
        raise ValueError('invalid action array')
    kept=[];rejected=[]
    for index,key in enumerate(keys):
        try:resolved=_selected_keys([key],context)
        except ValueError:
            rejected.append({'index':index,'proposed_key':key,'status':'not_executed','reason':'outside_frozen_candidates'})
        else:kept.extend(resolved)
    if not rejected:return text,[]
    # Explicit timing and multi-part plans retain strict all-plan validation;
    # dropping a prerequisite could change the meaning of later speech/actions.
    if value.get('speech_timing') is not None or isinstance(value.get('speech'),(list,dict)) or value.get('expressions') is not None:
        raise ValueError('unavailable action in anchored plan')
    first=rejected[0]['index']
    # A later body action can depend on the failed step even without a time
    # anchor. Cancel that suffix; keep independent speech and expression.
    kept=[]
    for index,key in enumerate(keys):
        if index<first:kept.extend(_selected_keys([key],context))
        elif not any(r['index']==index for r in rejected):
            rejected.append({'index':index,'proposed_key':key,'status':'not_executed','reason':'after_unavailable_action'})
    value['actions']=kept
    return json.dumps(value,ensure_ascii=False),sorted(rejected,key=lambda r:r['index'])


def split_output(text,context,*,include_timing=False):
    value=json.loads(text)
    if not isinstance(value,dict) or 'actions' not in value:raise ValueError('sequence output requires actions array')
    keys=_selected_keys(value.pop('actions'),context)
    if include_timing:
        timing=seq.validate_speech_timing(value.pop('speech_timing',None),keys,has_speech=isinstance(value.get('speech'),str) and bool(value['speech'].strip()))
        return json.dumps(value,ensure_ascii=False),keys,timing
    return json.dumps(value,ensure_ascii=False),keys


def split_speeches(text,context):
    value=json.loads(text);segments=value.get('speech')
    if not isinstance(segments,list):return text,None
    if not 1<=len(segments)<=4 or value.get('speech_timing') is not None:raise ValueError('invalid speech sequence')
    # Plain ordered text carries no action anchor. Preserve every segment and
    # let the existing speech lane serialize it; never invent action timing.
    if all(isinstance(row,str) and row.strip() for row in segments):
        segments=[{'text':row,'timing':None} for row in segments]
    keys=_selected_keys(value.get('actions'),context)
    previous=-1
    for row in segments:
        if not isinstance(row,dict) or set(row)!={'text','timing'} or not isinstance(row['text'],str) or not row['text'].strip():
            raise ValueError('invalid speech segment')
        timing=seq.validate_speech_timing(row['timing'],keys,has_speech=True)
        position=-1 if timing is None else timing['action_index']*2+(timing['phase']=='end')
        if position!=-1 and position<previous:raise ValueError('speech action anchors must not move backwards')
        if position!=-1:previous=position
    # The existing speech codec checks the shared expression and memory claims.
    # Individual text remains in segments and becomes separate actual contents.
    value['speech']=segments[0]['text']
    return json.dumps(value,ensure_ascii=False),segments


def split_expressions(text,context,*,allowed):
    value=json.loads(text);segments=value.pop('expressions',None)
    if segments is None:return text,None
    if not isinstance(segments,list) or not 1<=len(segments)<=4:raise ValueError('invalid expression sequence')
    keys=_selected_keys(value.get('actions'),context)
    for row in segments:
        if not isinstance(row,dict) or set(row)!={'expression','hold_ms','timing'} or row['expression'] not in allowed:
            raise ValueError('invalid expression segment')
        if type(row['hold_ms']) is not int or not 100<=row['hold_ms']<=10000:raise ValueError('invalid expression hold')
        seq.validate_speech_timing(row['timing'],keys,has_speech=True)
    value['expression']=segments[0]['expression']
    return json.dumps(value,ensure_ascii=False),segments
