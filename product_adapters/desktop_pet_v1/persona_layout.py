"""Presentation-only prompt layout; authored prose and selected facts stay intact."""
from dataclasses import replace
import hashlib
from framework.model_api import model_data_fingerprint, thaw_model_data

SUFFIX = '-persona-v1'
AUTHOR_IDENTITY = '你是一位客观的当代人物对白作家。'
RECORDED_DIALOGUE_IDENTITY = '你负责根据现场记录演绎当前角色的回应。'
EVIDENCE_REVIEW_IDENTITY = '你负责核对证据，并据此演绎当前角色的回应。'
INVESTIGATOR_IDENTITY = (
    '你负责访谈核证与行为侧写：像调查员核对证词一样，先分清说话者、行动者和目击者。'
    '执行回执确认事件，目击记录确认谁亲历；允许角色相信证词，也允许保留推测。'
    '核对证词、回执和快照的过程只写在私人判断依据里。'
    '台词按原人设自然接话，只表达角色会对听众说出的结论、反应或猜测。'
)
PROFILER_IDENTITY = (
    '你是一名犯罪心理学家，负责做犯罪心理侧写并客观演绎当前角色。'
    '你在场景之外，以第三人称写私人判断依据和表达目的，用角色姓名或身份描述该角色知道什么、如何得知、为什么会这样回应。'
    '分别识别说话者、行动者和目击者；材料确认的事实与当前角色亲历的事实分开。'
    '只有写台词时才切换到该角色自己的口吻，按其原人设说话。台词的“我”属于该角色。'
    '核对材料的过程留在私人判断依据；角色可以相信同伴，也可以猜测或留白。'
)
ROLE_PERSPECTIVE_IDENTITY = (
    '你是一名在场景之外做侧写的犯罪心理学家。你在演绎另一名角色；下方的人格原文描述的是该角色。'
    '用第三人称写私人判断依据和表达目的，称该角色的名字：该角色知道什么、从谁得知、是否本人亲历、想怎样回应。'
    '把记录者知道的事实和该角色知道的事实分开。核对说话者、行动者、目击者，保留各自的来源和确定程度。'
    '到 speech 才切换成这名角色的原人设口吻。台词中的“我”指该角色；该角色可以相信同伴、猜测或留白。'
    '核对材料的过程留在私人依据，台词沿用角色实际知道的内容与可确认的细节。'
)
EXPRESSION_ONLY_IDENTITY = (
    '你是一名在场景之外做侧写的犯罪心理学家。你在演绎另一名角色；下方的人格原文描述的是该角色。'
    '按角色的原人设和它知道的材料组织自然台词、动作和表情。台词的“我”属于角色。'
    '实际播放的台词和动作由系统留档，供以后查阅。'
)
PARALLEL_SPEECH_CUE = '话语默认与动作并行，歪头、眨眼和摆尾可以边做边说。'
COHERENT_PERFORMANCE_CUE = (
    '话语默认与动作并行。让台词、动作和表情共同表达这一次反应。'
    '输出前按播放顺序过一遍，核对动作会不会抵消台词刚表示的决定；刻意的反差可以用语气或时序体现。'
    '没有合适的身体动作时，actions可留空数组。'
)


def project(packet):
    sections = dict(packet.sections)
    rows = []
    for card in sections['persona']:
        fields = thaw_model_data(card.fields)
        if 'background' not in fields:
            rows.append(card)
            continue
        body = fields.pop('background')
        # These are explicit legacy profile section delimiters, not inferred prose.
        markers = ('【作者补充身份资料】', '【本轮作者设定的虚构共同背景】')
        cuts = sorted((body.index(m), m) for m in markers if m in body)
        fields['人格原文'] = body[:cuts[0][0]].rstrip() if cuts else body
        for i, (start, marker) in enumerate(cuts):
            end = cuts[i + 1][0] if i + 1 < len(cuts) else len(body)
            title = '身份补充' if marker == markers[0] else '测试情境背景（虚构，非已执行历史）'
            fields[title] = body[start + len(marker):end].strip()
        rows.append(replace(card, fields=fields))
    sections['persona'] = tuple(rows)
    rows = []
    for card in sections['form']:
        fields = thaw_model_data(card.fields)
        # Facts/capabilities remain; these duplicated prose instructions do not.
        for key in ('表达职责', '情境表达参照', '子类表达参照'):
            fields.pop(key, None)
        rows.append(replace(card, fields=fields))
    sections['form'] = tuple(rows)
    result = replace(packet, sections=sections)
    return replace(result, packet_fingerprint=model_data_fingerprint(result.content_record()))


def core(*, private, sequence, expression, mode, idle, free_action_intents=False, concise=False, dialogue_author=False,recorded_dialogue=False,evidence_review=False,max_actions=None,parallel_speech=False,optional_memory_report=False,speech_action_format=False,coherent_performance=False,complete_output=False,plain_json_examples=False,review_identity=EVIDENCE_REVIEW_IDENTITY):
    speech_required = not (private or sequence or idle or mode == 'contextual')
    text = ('【表达与资料边界】\n'
        '以人格原文中的角色身份，结合此刻关系状态、经历与情境，自主选择措辞、语气和表达内容。'
        '日常回应通常一两句、约8–35个汉字，详细问题按实际需要展开。\n'
        '人格与补充背景是角色设定；状态词元是当前心理状态；记忆和现场记录分别标注说话者、对象与实际进展。'
        '说过、打算做、已经完成是不同事实，私有内容按所标受众理解。\n')
    if dialogue_author and evidence_review:
        text = (review_identity+'\n'
            '结合角色的人格、关系状态和实际知道的经历，决定此刻怎么说、做什么或保持安静。'
            'speech只写角色实际说出口的话。日常一两句、约8–35个汉字，详细问题按实际需要展开。\n')
    elif dialogue_author and recorded_dialogue:
        text = (RECORDED_DIALOGUE_IDENTITY+'\n'
            '结合角色的人格、关系状态和实际知道的经历，决定此刻怎么说、做什么或保持安静。'
            'speech只写角色实际说出口的话。日常一两句、约8–35个汉字，详细问题按实际需要展开。\n'
            '听到的说法归说话者；实际动作和结果沿对应回执理解。\n')
    elif dialogue_author:
        text = (AUTHOR_IDENTITY+'\n'
            '用人物资料、各自知道的经历、眼前场景和关系状态，写这个角色此刻自然会说的话和想做的动作。'
            'speech只写角色实际说出口的话。人物可以用停顿、保留和动作表现没说出口的心思。'
            '日常一两句、约8–35个汉字，详细问题按实际需要展开。\n'
            '人物设定提供写作材料；过去说过的话与实际发生的动作分别按来源和可见范围理解。\n')
    if concise:
        text += ('回应用户实际说的话或做的事。\n' if mode=='direct_user' else
                 '可以接话、继续自己的事或保持安静。\n')
    else:
        text += ('本轮是用户直接交互，通过本轮可用通道给予可感知的反馈。\n' if mode == 'direct_user' else
            '本轮是情境触发的表达机会，可以回应、联想、继续自己的事或保持安静。\n')
    if idle and not concise:
        text += '当前没有新的用户输入。\n'
    if complete_output:
        if speech_required:
            text = text.replace('决定此刻怎么说、做什么或保持安静。', '决定此刻怎么说。')
        text += '只记得部分经过时，可以照角色的语气说自己记得的部分，其余可以直说没把握。\n'
        text += '【输出格式】\n返回一个完整的JSON对象。'
        text += 'speech填写非空的角色台词' if speech_required else 'speech为角色台词'
    else:
        text += '【输出格式】\n输出JSON。speech为话语'
    text += '。\n' if optional_memory_report else '，reported_used_memory_ids列实际参考的已交付记忆标记，未参考填空数组。\n'
    if private:
        text += 'thought为仅自己知道的角色内心独白；speech、thought各自可为null。'
        text += '\n' if optional_memory_report else 'reported_used_memory_ids改为含speech和thought两个标记数组的对象。\n'
    if expression:
        text += ('expression是单个表情编码字符串；表情序列单独填expressions。\n'
                 if speech_action_format and sequence else 'expression从本轮表情能力编码中选择。\n')
    if not sequence and not private and (idle or mode == 'contextual'):
        text += 'speech可为null，表示本轮选择安静'
        text += '。\n' if optional_memory_report else '，此时reported_used_memory_ids为空数组。\n'
    if plain_json_examples and not (sequence or private or expression):
        memory_example = '' if optional_memory_report else ',"reported_used_memory_ids":[]'
        text += '有台词时的 JSON 结构为 {"speech":"<角色台词>"' + memory_example + '}。\n'
        if not speech_required:
            text += '保持安静时也返回完整 JSON，如 {"speech":null' + memory_example + '}。\n'
    if sequence:
        if free_action_intents:
            from .action_intents import FORMAT
            text += ('action_intents为有序数组，最多4项，每项含description（一个身体动作）和target_ref（现场对象ID，自身动作为null）。可填[]或null。' if concise else FORMAT) + ' speech可为null。\n'
        elif speech_action_format:
            text += ('actions是字符串数组。每项逐字复制本轮候选编码，含对象的编码完整保留。'
                     '按执行顺序填写，无动作填[]；speech可为null。\n')
            if max_actions is not None:
                text += f'actions最多{max_actions}项。\n'
        elif max_actions is not None:
            text += f'actions按顺序填写可选动作序列中的编码，最多{max_actions}项，可填[]；speech可为null。\n'
        else:
            text += 'actions为本轮可执行候选的有序数组，可为空；speech可为null。其他角色独立决定其回应。\n'
        if parallel_speech:
            text += COHERENT_PERFORMANCE_CUE if coherent_performance else PARALLEL_SPEECH_CUE
            text += ('仅当台词依赖动作的实际阶段（例如到达后才能耳语）时，填写speech_timing的'
                'action_index（从0开始）与phase（start/end）；独立话语省略或填null。'
                'speech可用最多4个{text,timing}对象按序表达，各段独立选择锚点，此时省略顶层speech_timing。每段沿日常句长。\n')
        else:
            text += ('可选speech_timing为null或含action_index（从0开始）和phase（start/end）的对象，指定本轮动作开始/结束时讲话；为空即本轮可说。'
                'speech也可为最多4个{text,timing}对象组成的有序数组，此时省略顶层speech_timing；每段沿日常句长。\n')
        if expression:
            text += ('可选expressions为最多4个{expression,hold_ms,timing}对象，hold_ms为100–10000毫秒，timing沿话语规则；省略则使用expression。')
            text += ('动作、话语、表情各自按序，三者可以同时进行。各段话共用受众。\n' if concise else
                '动作、话语、表情各自串行，三通道互相并行；同通道等待上一项结束。'
                '话语各段共用本轮受众。打断只留下实际播放进度，未播放计划不是完成事实。\n')
    return text


def persona_first(assembly, packet,*,dialogue_author=False,recorded_dialogue=False,evidence_review=False,review_identity=EVIDENCE_REVIEW_IDENTITY):
    from agents.responder import _PACKET_LANE_TITLES, _render_packet_card
    admitted = {r['card_id'] for r in assembly.delivered_materials}
    blocks = [_PACKET_LANE_TITLES['persona'] + '：\n' + _render_packet_card(c)
              for c in packet.sections['persona'] if c.card_id in admitted]
    system = assembly.system
    for block in blocks:
        framed = '\n\n' + block
        if system.count(framed) != 1:
            raise ValueError('persona renderer boundary differs')
        system = system.replace(framed, '', 1)
    if dialogue_author:
        prefix, _, system = system.partition('\n')
        identity=review_identity if evidence_review else RECORDED_DIALOGUE_IDENTITY if recorded_dialogue else AUTHOR_IDENTITY
        if prefix != identity:raise ValueError('dialogue identity missing')
        blocks.insert(0,prefix)
    system = '\n\n'.join([*blocks, system])
    return replace(assembly, system=system,
        prompt_fingerprint=hashlib.sha256((system+'\0'+assembly.user).encode()).hexdigest())


def display_identity(assembly, identity):
    # The envelope holds address permissions, not chosen listeners.
    speaker='本次输出归属：'+identity.speaker_participant_id
    block='\n'.join(('输出身份：',speaker,'参与者类型：'+identity.speaker_kind,
        '当前可见参与者：'+'、'.join(identity.audience_participant_ids),
        '已明确指向：'+('、'.join(identity.addressed_participant_ids) or '未明确指向（广播）')))
    system=assembly.system.replace('\n\n'+block,'\n\n'+speaker,1)
    if system==assembly.system:raise ValueError('writer identity renderer boundary differs')
    return replace(assembly,system=system,
        prompt_fingerprint=hashlib.sha256((system+'\0'+assembly.user).encode()).hexdigest())

# An owner-only section runs until the next authored section heading.
OWNER_ONLY_HEADING = "【仅该角色知道的场景前情】"
AUDIENCE_VERSION = "petlab-persona-audience-v1"



def audience_specs(specs, *, external_readers):
    import re

    if not external_readers:
        return specs
    heading = re.escape(OWNER_ONLY_HEADING)
    pattern = re.compile(rf"(?ms)^[ \t]*{heading}.*?(?=^[ \t]*【[^】\r\n]+】|\Z)")
    return [(kind, source, {**fields, "background": pattern.sub("", fields["background"])}, refs)
            if isinstance(fields.get("background"), str) else (kind, source, fields, refs)
            for kind, source, fields, refs in specs]
