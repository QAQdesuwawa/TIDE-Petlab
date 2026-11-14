"""Optional speech behavior choices bound to delivered experience and audience."""
import hashlib
import json

from framework.model_api import model_data_fingerprint, thaw_model_data

VERSION = 'desktop-pet-share-experience-v1'


def augment(delivery, app, *, readers, budget_chars):
    from petlab_app.writer_visibility import allowed

    readers = tuple(readers)
    choices = {}
    if not readers:
        return delivery
    for alias, event_id in delivery['memory_aliases'].items():
        memory = app.runtime.memory_instance(event_id)
        contents = memory.event.visible_contents(app.owner_ref)
        if (not any(c.kind in {'speech', 'action'} for c in contents)
                or any(c.private or c.kind == 'thought' for c in contents)
                or not allowed(app, memory, readers)):
            continue
        choices[alias] = {'event_id': event_id, 'fact_fingerprint': memory.fact.fingerprint}
    if not choices:
        return delivery

    instruction = (
        '\n言语行为可选项：分享经历。可以提起一条已交付的实际经历，'
        '并在原 speech 里自然说出来。选中时额外填 '
        '"speech_act":{"kind":"share_experience","memory_id":"对应标记"}。'
        '可用记忆标记：' + json.dumps(list(choices), ensure_ascii=False)
        + '。普通接话或留白时省略 speech_act。'
    )
    if len(delivery['system']) + len(delivery['user']) + len(instruction) > budget_chars:
        return delivery
    result = thaw_model_data(delivery)
    result.pop('fingerprint')
    result['system'] += instruction
    result['budget_chars'] = budget_chars
    result['prompt_fingerprint'] = hashlib.sha256(
        (result['system'] + '\0' + result['user']).encode('utf8')
    ).hexdigest()
    result['speech_behaviors'] = {'version': VERSION, 'readers': list(readers), 'choices': choices}
    return {**result, 'fingerprint': model_data_fingerprint(result)}


def split(text, offered):
    value = json.loads(text)
    if not isinstance(value, dict) or 'speech_act' not in value:
        return text, None
    choice = value.pop('speech_act')
    if choice is None:
        return json.dumps(value, ensure_ascii=False), None
    if (offered is None or not isinstance(choice, dict)
            or set(choice) != {'kind', 'memory_id'} or choice['kind'] != 'share_experience'
            or not isinstance(choice['memory_id'], str)
            or choice['memory_id'] not in offered['choices']):
        raise ValueError('speech behavior differs from delivered experience options')
    return json.dumps(value, ensure_ascii=False), choice


def validate_share(choice, offered, *, speech, readers):
    if choice is None:
        return None
    if not speech or not readers or not set(readers) <= set(offered['readers']):
        raise ValueError('shared experience requires speech in its authorized audience')
    return {**choice, **offered['choices'][choice['memory_id']], 'readers': list(readers)}
