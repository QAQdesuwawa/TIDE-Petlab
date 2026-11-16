"""A physical press on the playground's shared meal button."""

from dataclasses import replace

from framework.model_api import thaw_model_data
from . import movement, shared_meal, spatial
from .events import Action
from .prop_move import require_reach

KEY = 'press_meal_button'
REF = 'world:playground:meal-button'


def choice(obj, owner):
    return {'key': KEY+'@'+REF, 'action_key': KEY, 'target_ref': REF,
            'world_object': obj, 'actor_ref': owner, 'approach': movement.arrival_definition(),
            'description': '走到出饭按钮旁，按一次按钮'}


def content(target, row):
    obj = spatial.world_object(row['world_object'])
    expected = choice(obj, row['actor_ref'])
    if (target != REF or obj['ref'] != REF or 'bounds' not in obj
            or row['key'] not in (KEY, expected['key'])
            or {**row, 'key': expected['key']} != expected):
        raise ValueError('meal button offer differs')
    return Action(content_id='activity', semantic_key=KEY, description=row['description'],
                  recipient_refs=(target,), parameters={'meal_button': {
                      'object': obj, 'actor_ref': row['actor_ref']}})


def check(scene, binding):
    scene = spatial.validate(scene)
    current = next((obj for obj in scene.get('objects', ()) if obj['ref'] == REF), None)
    if (scene['owner_ref'] != binding['actor_ref'] or current is None or 'bounds' not in current
            or current['bounds'] != list(binding['object']['bounds'])):
        raise ValueError('meal button source changed')
    require_reach(scene, current)


def realized(item, result, status):
    if status != 'completed':
        if result is not None:
            raise ValueError('unfinished button action cannot have a result')
        description = '向出饭按钮伸手。' if status == 'started' else '收回了手，没有按下出饭按钮。'
        return replace(item, description=description, parameters={})
    result = thaw_model_data(result)
    binding = item.parameters['meal_button']
    if (not isinstance(result, dict) or set(result) != {'ref', 'actor_ref', 'status', 'meal'}
            or result['ref'] != REF or result['actor_ref'] != binding['actor_ref']):
        raise ValueError('meal button lacks an actual press result')
    outcome = result['status']
    if outcome == 'served':
        meal = shared_meal.validate(result['meal'])
        if meal['holder_ref'] != result['actor_ref'] or meal['portions'] != 3 or meal['revision'] != 0:
            raise ValueError('meal award differs from actual press')
        text = '按下了出饭按钮，得到一碗热饭。'
    elif outcome in ('empty', 'busy') and result['meal'] is None:
        text = ('按下了出饭按钮，这次没有出饭。' if outcome == 'empty'
                else '按下了出饭按钮，桌上还有一碗没吃完，没有再出饭。')
    else:
        raise ValueError('invalid meal button result')
    return replace(item, description=text, parameters={'meal_button_result': result})
