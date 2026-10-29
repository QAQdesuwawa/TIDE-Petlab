"""Frozen body-action entities; legacy fixed modes remain replay-compatible.

New symbolic selections come from action_candidates after original eligibility.
Neither the renderer nor Writer chooses an action or authorizes an opportunity.
"""
from dataclasses import replace
from .events import Action
from .shared_meal import KEYS as MEAL_KEYS

VERSION = 'desktop-pet-idle-behavior-v1'
MODES = ('speech', 'play_tail', 'play_tail_composite', 'play_tail_speech', 'symbolic')


def select_idle(*, eligible, mode):
    if mode not in MODES or type(eligible) is not bool:
        raise ValueError('invalid package idle behavior binding')
    if mode=='symbolic':raise ValueError('symbolic selection requires formal token evidence')
    if not eligible:
        return None
    return {'version': VERSION, 'mode': mode,
            'action_keys': [] if mode == 'speech' else ['play_tail']}


def selected_action(selection):
    if selection is None:return None
    from framework.model_api import thaw_model_data
    selection=thaw_model_data(selection)
    if selection.get('mode')=='symbolic':
        from .action_candidates import validate
        return next(iter(validate(selection)['action_keys']),None)
    for mode in MODES:
        if mode!='symbolic' and selection==select_idle(eligible=True,mode=mode):
            return None if mode=='speech' else 'play_tail'
    raise ValueError('unknown frozen action selection')


def staged_selection(selection):
    if selection is None:return False
    from framework.model_api import thaw_model_data
    selection=thaw_model_data(selection)
    if selection.get('mode')=='symbolic':
        from .action_candidates import validate
        value=validate(selection)
        return bool(value['action_keys']) and value['allow_speech']
    return selection==select_idle(eligible=True,mode='play_tail_speech')


def content(user_ref, *, selection=None):
    key='play_tail' if selection is None else selected_action(selection)
    if key is None:raise ValueError('empty action selection has no body content')
    if key in MEAL_KEYS:
        from .shared_meal import content as meal_content
        return meal_content(user_ref,selection['definition'])
    if key=='move_near':
        from .movement import content as moving_content
        return moving_content(user_ref,selection['definition'])
    if key=='read_web':
        from .web_activity import content as reading_content
        definition=selection.get('definition') or {}
        tool=definition.get('tool',{})
        if set(tool)!={'kind','url'} or tool.get('kind')!='read_web':raise ValueError('selected reading action lacks frozen tool binding')
        return reading_content(tool['url'],user_ref)
    description=(selection['definition']['description'] if selection is not None and selection.get('mode')=='symbolic' else '摆动自己的尾巴自娱')
    return Action(content_id='activity', semantic_key=key,
                  description=description, recipient_refs=(user_ref,),
                  parameters={'behavior_version': VERSION})


def realized_contents(contents, outcome):
    """Describe only physical effects confirmed by the executor, never feelings."""
    results = outcome.details.get('action_results', {})
    actual = []
    for item in contents:
        if item.content_id not in outcome.completed_content_ids:
            continue
        if item.kind == 'action' and item.parameters.get('behavior_version') == VERSION:
            row = results.get(item.content_id, {})
            status = row.get('status')
            frames = row.get('rendered_frames')
            if status not in ('completed', 'interrupted') or type(frames) is not int or frames <= 0:
                raise ValueError('behavior lacks actual rendered outcome')
            text = (item.description+'，完成了这次活动。' if status == 'completed'
                    else '开始'+item.description+'，活动进行中被打断并停止。')
            extra={}
            if 'child_results' in outcome.details:
                from .composite import VERSION as COMPOSITE_VERSION
                extra['composite_version']=outcome.details.get('composite_version',COMPOSITE_VERSION)
            if 'child_results' in outcome.details or 'reason' in row:
                reason=row.get('reason')
                if status=='interrupted':
                    explanations={'user_contact':'停止原因是用户接触。','source_expired':'原行动的现场适用条件已失效。',
                        'technical_failure':'停止原因是播放执行故障。','window_closed':'停止原因是显示窗口关闭。'}
                    text+=explanations.get(reason,'停止原因未确认。')
                    extra.update(interruption_reason=reason or 'unknown',cause_ref=row.get('cause_ref'))
            item = replace(item, description=text, parameters={**dict(item.parameters),
                           'execution_status': status,**extra})
        if item.kind=='action' and item.semantic_key=='move_near':
            from .movement import realized
            row=results.get(item.content_id,{})
            item=realized(item,row.get('movement_result'),status=row.get('status'),reason=row.get('reason'))
        if item.kind=='action' and 'meal' in item.parameters:
            from .shared_meal import result_content
            row=results.get(item.content_id,{})
            item=result_content(item,row.get('meal_result'),status=row.get('status'))
        if item.kind=='action' and outcome.details.get('composite_version')=='desktop-pet-composite-v4-sequence':
            from .action_sequence import describe_stage
            bodies=[c.content_id for c in contents if c.kind=='action']
            item=describe_stage(item,bodies.index(item.content_id))
        actual.append(item)
    from .spatial import realized_contents as realized_gaze
    return realized_gaze(tuple(actual),outcome)
