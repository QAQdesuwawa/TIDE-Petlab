"""Authored presentation data; no state, measurement or selection authority."""
from collections.abc import Mapping
from framework.model_api import freeze_model_payload

VERSION="desktop-pet-expression-profile-v1"
NATURAL_EXPRESSION_VERSION="desktop-pet-natural-expression-v1"


def validate_expression_profile(value):
    if value is None:return None
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
        "voice":"直接回应唯一reply_target，语气沿人物、当前互动与已选方向自然形成；不套用预设口头禅、例句或固定收尾，不主动变成客服式功能介绍。",
        "memory_body_count":selected_memory_count,
        "memory_scope":("往事只依本轮实际交付的记忆正文表达具体内容；未交付正文不能据此声称已回忆到。"
            if selected_memory_count else "本轮没有正式选择的记忆正文，不声称已唤起旧事；可以回应对方在本轮输入明确提供的事情。"),
        "memory_expression":"不以笼统的回忆声明起手，不宣告未来会记住某事，不宣传记忆功能。若有具体往事，直接谈事情本身。",
        "measurement_reference":"本轮量测是reply_target原事件的已接受结果；conversation是当前累计状态，二者不可互相替代或读作两次输入。数值和来源仅用于理解，不对用户播报。",
    }
