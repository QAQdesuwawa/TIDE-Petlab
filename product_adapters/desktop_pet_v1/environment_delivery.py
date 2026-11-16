"""Object facts from the exact memory fragments delivered to Writer."""

from framework.model_api import thaw_model_data
from petlab_app.session import PRODUCT_KEY
from petlab_app.visible_delivery import OBJECT_REF

FIELD = '当时的环境证据'
WORLD_OBSERVATION_SCOPE = (
    '角色收到本地场景观察。该记录描述采样时的物件状态与已记录的变化；'
    '没有角色目击移动过程或确认操作者的记录。'
)


def evidence(app, event, contents):
    from petlab_app.participant_input import HUMAN_INTERACTION_VERSION
    from petlab_app.screen_perception import VERSIONS as OBSERVATION_VERSIONS
    from .event_timeline import iso
    from .memory_fragments import receipt_phases

    object_fragments = [
        (index, item) for index, item in enumerate(contents, 1)
        if item.semantic_key in {'world_observation', 'screen_observation', 'place_meal',
                                 'move_prop', 'press_meal_button'}
        and item.kind in {'interaction', 'action'} and not item.private
    ]
    if not object_fragments:
        return []
    source = app.runtime.session.profile[PRODUCT_KEY]['sources'].get(event.event_id, {})
    receipt = source.get('evidence', {}).get('input_source', {})
    version = receipt.get('version')
    phases = None
    rows = []
    for index, item in object_fragments:
        params = item.parameters
        fields = None
        if item.kind == 'interaction' and version in OBSERVATION_VERSIONS:
            from petlab_app.screen_perception import validate

            observation = validate(app, receipt, event.contents, event_id=event.event_id)['material']
            obj = observation['object']
            fields = {'对象': obj['ref'], '当时采样坐标': obj['point'],
                      '采样时刻': iso(observation['sampled_at_ms'])}
        elif item.kind == 'interaction' and item.semantic_key == 'place_meal' and version == HUMAN_INTERACTION_VERSION:
            from model_packages.desktop_pet_v1.shared_meal import validate

            meal = validate(params['meal_scene'])
            fields = {'对象': meal['interaction_id'], '当时持有者': meal['holder_ref'],
                      '放置来源': '按钮出饭' if params['input_kind'] == 'desktop_button' else '菜单放置'}
        elif item.kind == 'action':
            if phases is None:
                phases = receipt_phases(app, event)
            if phases.get(item.content_id) != 'completed':
                continue
            move = params.get('prop_move_result')
            press = params.get('meal_button_result')
            if move is not None:
                fields = {'对象': move['ref'], '移动前坐标': move['from'], '移动后坐标': move['to']}
            elif press is not None:
                fields = {'对象': press['ref'], '实际出饭': press['status'] == 'served'}
            elif OBJECT_REF in params:
                # The recipient archive retains the verified result in this
                # fragment's description, with only the object ID structured.
                fields = {'对象': params[OBJECT_REF], '实际结果': '见该动作片段'}
        if fields is not None:
            rows.append({'片段': index, **thaw_model_data(fields)})
    return rows
