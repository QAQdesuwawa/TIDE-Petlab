"""One optional Writer proposal for an existing, formally eligible body set."""
import json
from framework.model_api import thaw_model_data
from model_packages.desktop_pet_v1 import action_sequence as seq,shared_meal as meal


def context_for(app,event_id,selection):
    if not selection or selection.get('catalog_version') not in meal.SEQUENCE_CATALOG_VERSIONS:return None
    from petlab_app.idle_opening import KEY as IDLE_KEY
    from petlab_app.reaction_action import KEY as INPUT_KEY
    idle=app.runtime.session.profile.get(IDLE_KEY,{}).get('ticks',{}).get(event_id)
    row=idle if idle is not None else app.runtime.session.profile.get(INPUT_KEY,{}).get(event_id,{})
    if row.get('action_sequence_context') is not None:return seq.validate_offer(row['action_sequence_context'])
    return None  # Old pending inputs/ticks retain their single-action contract.


def verify_context(app,event_id,value):
    from petlab_app.reaction_action import selection,authorization
    value=seq.validate_offer(value);selected=selection(app,event_id);auth=authorization(app,event_id)
    if (selected is None or auth is None or value['selection']!=thaw_model_data(selected)
            or value['owner_ref']!=app.owner_ref or value['target_ref']!=auth.target_ref
            or value['selection']['source']!=auth.source_settlement_fingerprint):
        raise ValueError('sequence proposal differs from formal opportunity')
    # Catalog is bounded and frozen; current physical state is checked at each
    # actual child effect, not substituted into an old sealed Writer request.
    for row in value['catalog']['candidates']:
        if 'meal' in row:
            bound=meal.verify_binding(row['meal'])
            if bound['actor_ref']!=value['owner_ref'] or bound['target_ref']!=value['target_ref']:
                raise ValueError('sequence candidate participant differs')
        elif row['key']=='move_near' and value['catalog']['version']==meal.CATALOG_VERSION:
            from model_packages.desktop_pet_v1 import movement
            movement.content(value['target_ref'],row)
        else:raise ValueError('unknown sequence candidate entity')
    return value


def fields(value):
    value=seq.validate_offer(value)
    options=[{'编码':r['key'],'含义':r['description']} for r in value['catalog']['candidates'] if r['key'] in value['allowed_keys']]
    return {'可选动作序列':{'对象':value['target_ref'],'候选':options,'容量上限':value['max_actions']},
        '计划含义':'actions按希望执行的顺序填写候选编码数组，可为空。各片段由本地时间轴连续播放；speech与动作同属本轮反应，可同时出现，也可为null。实际发生的范围以执行记录为准，对方自行决定如何承接。'}


def split_output(text,context):
    value=json.loads(text)
    if not isinstance(value,dict) or 'actions' not in value:raise ValueError('sequence output requires actions array')
    keys=seq.validate_keys(value.pop('actions'),context)
    return json.dumps(value,ensure_ascii=False),keys
