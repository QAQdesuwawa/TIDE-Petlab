"""Current visible object facts, independent of action selection or recall."""
import hashlib
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1 import shared_meal
from model_packages.desktop_pet_v1.meal_button import KEY as PRESS_KEY, REF as BUTTON_REF

INPUT_SCOPE_VERSION='desktop-pet-meal-delivery-v2-button-scope'
AVAILABILITY_VERSION='desktop-pet-meal-delivery-v3-button-availability'
TIMELINE_VERSION='desktop-pet-meal-delivery-v4-receipt-timeline'
PLACEMENT_SOURCE_VERSION='desktop-pet-meal-delivery-v5-placement-source'
VERSION='desktop-pet-meal-delivery-v6-action-stage'
EMPTY_SCENE_VERSION='desktop-pet-meal-delivery-v7-empty-scene'
MULTIPLE_VERSION='desktop-pet-meal-delivery-v8-independent-bowls'
PLACEMENT_VERSIONS=(PLACEMENT_SOURCE_VERSION,VERSION)
TIMELINE_VERSIONS=(TIMELINE_VERSION,*PLACEMENT_VERSIONS,MULTIPLE_VERSION)
VERSIONS=(INPUT_SCOPE_VERSION,AVAILABILITY_VERSION,*TIMELINE_VERSIONS)


def text(context,*,version=None,contents=()):
    heading='\n\n【眼前的饭碗】\n'
    if version==EMPTY_SCENE_VERSION:
        if context is not None:
            raise ValueError('empty meal card cannot carry a bowl')
        return heading+'本地现场确认桌面当前没有饭碗。'
    if version==MULTIPLE_VERSION:
        rows=shared_meal.bowls(context)
        return (heading+'现场各饭碗分别记录，交接及吃饭按对应物件结算。\n'
                +'\n'.join(f"物件 {row['interaction_id']} · 持有人 {row['holder_ref']} · 剩 {row['portions']} 份"
                           +(f" · 正递给 {row['offer']['target_ref']}" if row['offer'] else '')
                           for row in rows))
    if version is None:
        value=heading+shared_meal.card(context)
    elif version in VERSIONS:
        pressed=any((item.kind=='action' and item.semantic_key==PRESS_KEY)
                    or (item.kind=='interaction' and item.semantic_key=='place_meal'
                        and item.parameters.get('input_kind')=='desktop_button')
                    for item in contents)
        value=(heading+shared_meal.card(context,snapshot=True)
               +'\n本次输入的按键动作记录：'+('有，结果见该条动作记录。' if pressed else '无。'))
        if version in (AVAILABILITY_VERSION,*TIMELINE_VERSIONS) and context['portions']>0:
            value+='\n这碗饭吃完前，按钮不会再出饭。'
        if version==VERSION:
            value+='\n道具还在谁手里，以现场状态和实际动作阶段为准。台词若配合递拿等实际动作，选本轮可执行编码并绑定对应阶段；没有该动作时，可以说想怎么做。'
    else:
        raise ValueError('unknown meal delivery version')
    if len(value)>400:raise ValueError('meal scene description exceeds bounded context')
    return value


def accepted_placement(app, context):
    """Resolve the current object's creation source in the owner's accepted history."""
    from model_packages.cx_six_seven_v1.multi_participant.events import CommittedConversationEvent
    from model_packages.cx_six_seven_v1.multi_participant.router import route_committed_event
    from petlab_app.multi_pet import _key
    from petlab_app.session import PRODUCT_KEY
    from . import EVENT_JOURNAL_KEY

    workspace=getattr(app,'multi_workspace',None)
    if workspace is None:
        return None
    try:
        source=workspace.source_event(shared_meal.placement_event_id(context['interaction_id']))
    except FileNotFoundError:
        return None
    event=CommittedConversationEvent.from_record(source['event'])
    delivery=next((row for row in route_committed_event(event)
                   if row.recipient_participant_id==app.owner_ref),None)
    if delivery is None:
        return None
    identity='participant:'+_key(delivery.delivery_id)
    profile=app.runtime.session.profile
    if identity not in profile.get(EVENT_JOURNAL_KEY,{}):
        return None
    from .visible_context import _artifact
    accepted=profile[PRODUCT_KEY]['sources'][identity]
    artifact=_artifact(app,accepted)
    if artifact is None or artifact['source']['fingerprint']!=source['fingerprint']:
        raise ValueError('accepted meal placement differs from committed source')
    return identity


def timeline(app,identities,context,*,covered_contents=None):
    """Project related, owner-visible receipts from the existing Writer sources."""
    from model_packages.desktop_pet_v1.events import PetEvent
    from petlab_app.visible_delivery import expected_items
    from .visible_context import _artifact

    sources=app._product()['sources']
    meal_ids={row['interaction_id'] for row in shared_meal.bowls(context)}
    rows={}
    for identity in dict.fromkeys(identities):
        source=sources[identity]
        event=PetEvent.from_record(source['event'])
        for item in event.visible_contents(app.owner_ref):
            if item.private or (covered_contents and item.content_id in covered_contents.get(identity, ())):
                continue
            is_press=item.semantic_key==PRESS_KEY or item.parameters.get('input_kind')=='desktop_button'
            if item.kind=='interaction' and item.semantic_key=='place_meal':
                meal=item.parameters.get('meal_scene')
                if source['origin']!='input' or not meal or meal['interaction_id'] not in meal_ids:
                    continue
                artifact=_artifact(app,source)
                if artifact is None:
                    continue
                at=artifact['source']['event']['committed_at_ms']
                key=(identity,item.content_id)
            elif item.kind=='action' and (item.semantic_key==PRESS_KEY or
                    (item.semantic_key=='move_near'
                     and item.parameters.get('movement_result',{}).get('target_ref')==BUTTON_REF)):
                if source['origin']=='execution':
                    receipt=source['evidence']['fact']['model_payload']['execution_receipt']
                    if item.content_id not in receipt['outcome']['completed_content_ids']:
                        continue
                    detail=receipt['outcome']['details'].get('child_results',{}).get(item.content_id,{})
                    at=detail.get('ended_at_ms',event.position.coordinate)
                    key=(identity,item.content_id)
                elif source['origin']=='input':
                    artifact=_artifact(app,source)
                    if artifact is None or 'visible_manifest' not in artifact['source']:
                        continue
                    original=artifact['source']
                    phase=next((r for r in expected_items(original,app.owner_ref)
                                if r['content_id']==item.content_id and r.get('phase')=='completed'),None)
                    if phase is None:
                        continue
                    at=phase['at_ms']
                    key=(original['visible_manifest']['parent_event_id'],item.content_id)
                else:
                    continue
            else:
                continue
            rows[key]=(at,event.actor_ref,item.description,identity,is_press)
    return tuple(sorted(rows.values()))


def timeline_text(rows,*,processing_ms=None):
    if not rows:
        return ''
    from .event_timeline import iso
    if processing_ms is None:
        text=('\n\n【饭碗与按钮的已见回执】\n'
              +'\n'.join(f'{iso(at)} · {actor} · {description}' for at,actor,description,_,_ in rows))
    else:
        text=('\n\n【饭碗与按钮的已见回执】\n'
              +'\n'.join(f'历史回执 · 距处理时刻（秒）：{(processing_ms-at)/1000:g} · {actor} · 原文：{description}'
                         for at,actor,description,_,_ in rows))
    if not any(row[4] for row in rows):
        text+='\n以上回执未记录按键动作。'
    return text


def reserve(context,*,version=None,contents=(),history=(),processing_ms=None):
    return 0 if context is None and version!=EMPTY_SCENE_VERSION else len(text(context,version=version,contents=contents))+len(timeline_text(history,processing_ms=processing_ms))


def augment(delivery,context,*,budget_chars,version=None,contents=(),history=(),processing_ms=None):
    out=thaw_model_data(delivery)
    out['user']+=text(context,version=version,contents=contents)+timeline_text(history,processing_ms=processing_ms)
    if len(out['system'])+len(out['user'])>budget_chars:raise ValueError('meal scene exceeds Writer budget')
    if version==MULTIPLE_VERSION:
        out['meal_scene_fingerprint']=fp(context)
    elif context is not None:
        out['meal_scene_fingerprint']=shared_meal.validate(context)['fingerprint']
    if version is not None:out['meal_scene_delivery_version']=version
    out['prompt_fingerprint']=hashlib.sha256((out['system']+'\0'+out['user']).encode()).hexdigest()
    out['fingerprint']=fp({k:v for k,v in out.items() if k!='fingerprint'})
    return out
