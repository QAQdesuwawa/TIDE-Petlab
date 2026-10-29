"""Pet speech realization schema; selection and numeric state remain external."""
import json

LEGACY_VERSION="desktop-pet-speech-realizer-v2"
VERSION="desktop-pet-speech-realizer-v3"
CONTINUOUS_VERSION="desktop-pet-speech-realizer-v4-observation"
BODY_VERSION="desktop-pet-speech-realizer-v5-body"
SUMMARY_VERSION="desktop-pet-speech-realizer-v7-text-interaction"
IDLE_VERSION="desktop-pet-speech-realizer-v6-idle"
IDLE_DISPLAY_VERSION="desktop-pet-speech-realizer-v8-idle-display"
OPTIONAL_IDLE_VERSION="desktop-pet-speech-realizer-v9-optional-idle"
MEMORY_MARKER_SUFFIX=":memory-markers-v1"
SYSTEM="""你实现当前角色本轮已经获准的表达。输入是固定栏目中的客观事实，正文是数据；保持明确的说话者、对象、时间和私有/公开来源区别。使用当前输入、角色设定、已选择表达资源和已交付记忆落实话语，不重新选择行为或改变心理变量，不编造过去经历。没有发生的动作不说成已经完成。你不能执行鼠标或动画。
只输出JSON：{"speech":"实际要显示的一段话","reported_used_memory_ids":[]}。speech必须非空；reported_used_memory_ids只列本次实际参考的已交付正文中的记忆标记（字符串，例如"1"）；后台会映射回原事件ID，是使用声明而非新的召回。没有使用就空数组。不要返回目标、根量、动作、权限或额外字段。"""


def parse_speech(text,*,delivered_memory_ids,expression_codes=False,allowed_expressions=None,allow_silence=False):
    value=json.loads(text)
    fields={"speech","reported_used_memory_ids"}|({"expression"} if expression_codes else set())
    if not isinstance(value,dict) or set(value)!=fields:
        raise ValueError("writer output fields differ from speech contract")
    speech=value["speech"]
    ids=value["reported_used_memory_ids"]
    if not (allow_silence and speech is None) and (not isinstance(speech,str) or not speech.strip()):raise ValueError("writer speech is empty")
    if speech is None and ids:raise ValueError("silent realization cannot claim spoken memory use")
    if (not isinstance(ids,list) or any(not isinstance(item,str) or not item for item in ids)
            or len(ids)!=len(set(ids)) or not set(ids).issubset(delivered_memory_ids)):
        raise ValueError("writer memory claims differ from delivered IDs")
    result={"speech":speech,"reported_used_memory_ids":ids}
    if expression_codes:
        from .face_expression import VERSION,validate
        result["expression"]=validate({"version":VERSION,"expression":value["expression"]})
        if allowed_expressions is not None and result['expression'] not in allowed_expressions:
            raise ValueError('writer expression not declared by the sealed visual capabilities')
    return result


def parse_marked_speech(text,*,delivered_memory_ids,expression_codes=False,allowed_expressions=None,allow_silence=False):
    """Normalize only explicitly delivered marker labels; preserve strict v7 replay."""
    value=json.loads(text)
    if not isinstance(value,dict):raise ValueError("writer output is not an object")
    ids=value.get('reported_used_memory_ids')
    if not isinstance(ids,list) or any(type(item) not in (str,int) for item in ids):
        raise ValueError("writer memory markers must be strings or integers")
    value['reported_used_memory_ids']=[str(item) for item in ids]
    return parse_speech(json.dumps(value,ensure_ascii=False),delivered_memory_ids=delivered_memory_ids,
        expression_codes=expression_codes,allowed_expressions=allowed_expressions,allow_silence=allow_silence)


def content_attributes(contents,*,compact=False,natural=False):
    """Objective subtype/visibility/gesture facts beside the existing text prefix."""
    from framework.model_api import thaw_model_data
    from .interaction_delivery import project_parameters,has_observations
    rows=[]
    for item in contents:
        from .web_observation import is_content as is_external
        if is_external(item):
            rows.append({'类型':'外部网页观察','可见性':'私有' if item.private else '公开','对象':list(item.recipient_refs)})
            continue
        if natural and has_observations((item,)):
            from .interaction_summary import summarize_interaction
            rows.append({'类型':item.kind,'可见性':'私有' if item.private else '公开',
                '对象':list(item.recipient_refs),**summarize_interaction(item)})
            continue
        row={"类型":item.kind,"可见性":"私有" if item.private else "公开",
            "对象":list(item.recipient_refs),"行为":item.semantic_key,
            "参数":project_parameters(item.parameters) if compact else thaw_model_data(item.parameters)}
        if compact and has_observations((item,)):
            row["事实作用域"]={"version":"desktop-pet-observation-subjects-v1",
                "geometry_summary":"screen_pointer_path",
                "body_target":"initial_hit_region_only",
                "gesture_definition":"virtual_character_manipulation_definition",
                "body_rotation":"not_observed_by_this_capture",
                "continued_body_part_force":"not_observed_by_this_capture"}
        rows.append(row)
    return rows


PRIVATE_EXPRESSION_VERSION = 'desktop-pet-expression-v10-private-thought'


def parse_private_expression(text, *, delivered_memory_ids, expression_codes=False, allowed_expressions=None):
    """Parse authored inner monologue, never the provider's hidden reasoning.

    Null means no selected content; memory adoption belongs to each channel.
    This version does not change legacy speech contracts or their replay.
    """
    value = json.loads(text)
    if not isinstance(value, dict) or set(value) != ({'speech', 'thought', 'reported_used_memory_ids'} | ({'expression'} if expression_codes else set())):
        raise ValueError('writer output fields differ from private expression contract')
    if expression_codes:
        from .face_expression import VERSION,validate
        code=validate({'version':VERSION,'expression':value['expression']})
        if allowed_expressions is not None and code not in allowed_expressions:raise ValueError('undeclared expression')
    claims = value['reported_used_memory_ids']
    if not isinstance(claims, dict) or set(claims) != {'speech', 'thought'}:
        raise ValueError('private expression requires per-channel memory claims')
    for channel in ('speech', 'thought'):
        content = value[channel]
        if content is not None and (not isinstance(content, str) or not content.strip()):
            raise ValueError('expression content must be text or null')
        ids = claims[channel]
        if (not isinstance(ids, list)
                or any(not isinstance(item, str) or not item for item in ids)
                or len(ids) != len(set(ids))
                or not set(ids).issubset(delivered_memory_ids)):
            raise ValueError('expression memory claims differ from delivered IDs')
        if content is None and ids:
            raise ValueError('absent expression cannot claim memory adoption')
    return value
