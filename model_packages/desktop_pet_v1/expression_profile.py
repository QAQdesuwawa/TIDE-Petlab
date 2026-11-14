"""Authored presentation data; no state, measurement or selection authority."""
from collections.abc import Mapping
from framework.model_api import freeze_model_payload, model_data_fingerprint

VERSION="desktop-pet-expression-profile-v1"
RESOURCE_VERSION="desktop-pet-expression-profile-v2-resources"
NATURAL_EXPRESSION_VERSION="desktop-pet-natural-expression-v1"


def validate_expression_profile(value):
    if value is None:return None
    if isinstance(value,Mapping) and value.get('version')==RESOURCE_VERSION:
        if set(value)!={'version','description','source_ref','resources'}:
            raise ValueError('expression resource profile fields differ')
        body=value['resources']
        if (not isinstance(value['description'],str) or not isinstance(value['source_ref'],str)
                or not value['source_ref'].strip() or not isinstance(body,Mapping)
                or set(body)!={'语气','文风','口癖'}
                or any(not isinstance(body[key],str) or not body[key].strip() or len(body[key])>300
                       for key in ('语气','文风'))
                or not isinstance(body['口癖'],(tuple,list)) or len(body['口癖'])>4
                or any(not isinstance(item,str) or not item.strip() or len(item)>60 for item in body['口癖'])):
            raise ValueError('expression resources differ')
        return freeze_model_payload(value)
    if (not isinstance(value,Mapping) or set(value)!={"version","description","source_ref"}
            or value["version"]!=VERSION
            or any(not isinstance(value[key],str) or not value[key].strip()
                   for key in ("description","source_ref"))):
        raise ValueError("expression profile fields or version are invalid")
    return freeze_model_payload(value)


def natural_expression_policy(*,selected_memory_count):
    """Form guidance only, consuming the host's exact authorized recall count."""
    if isinstance(selected_memory_count,bool) or not isinstance(selected_memory_count,int) or selected_memory_count<0:
        raise ValueError("selected memory count must be a nonnegative integer")
    return {
        "version":NATURAL_EXPRESSION_VERSION,
        "voice":"结合人物、当前互动、状态与记忆，自主形成措辞、语气和表达方式。",
        "memory_body_count":selected_memory_count,
        "memory_scope":("往事只依本轮实际交付的记忆正文表达具体内容；未交付正文不能据此声称已回忆到。"
            if selected_memory_count else "本轮没有正式选择的记忆正文，不声称已唤起旧事；可以回应对方在本轮输入明确提供的事情。"),
        "memory_expression":"具体往事以交付材料支持的内容为据；如何提起、怎样表达感受，由角色结合本轮语境决定。",
        "measurement_reference":"本轮量测是reply_target原事件的已接受结果；conversation是当前累计状态，二者不可互相替代或读作两次输入。数值和来源仅用于理解，不对用户播报。",
    }


def persona_card_specs(role,profile):
    if profile is None:
        return [('pet_definition',role.owner_ref,{'name':role.name,'background':role.background},(role.fingerprint,))]
    refs=(role.fingerprint,model_data_fingerprint(profile),profile['source_ref'])
    if profile['version']==VERSION:
        return [('pet_definition',role.owner_ref,{'name':role.name,'background':profile['description']},refs)]
    identity={'name':role.name}
    if profile['description']:identity['background']=profile['description']
    style=profile['resources']
    specs=[('pet_definition',role.owner_ref,identity,refs),
           ('pet_persona_voice',role.owner_ref+':voice',{'语气':style['语气'],'文风':style['文风']},refs)]
    if style['口癖']:
        specs.append(('pet_persona_habits',role.owner_ref+':habits',
                      {'本人常用表达（供参考）':list(style['口癖'])},refs))
    return specs
