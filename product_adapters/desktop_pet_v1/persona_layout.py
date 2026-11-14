"""Presentation-only prompt layout; authored prose and selected facts stay intact."""
from dataclasses import replace
import hashlib
from framework.model_api import model_data_fingerprint, thaw_model_data

SUFFIX = '-persona-v1'
AUTHOR_IDENTITY = '你是一位客观的当代人物对白作家。'


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


def core(*, private, sequence, expression, mode, idle, free_action_intents=False, concise=False, dialogue_author=False,max_actions=None,parallel_speech=False):
    text = ('【表达与资料边界】\n'
        '以人格原文中的角色身份，结合此刻关系状态、经历与情境，自主选择措辞、语气和表达内容。'
        '日常回应通常一两句、约8–35个汉字，详细问题按实际需要展开。\n'
        '人格与补充背景是角色设定；状态词元是当前心理状态；记忆和现场记录分别标注说话者、对象与实际进展。'
        '说过、打算做、已经完成是不同事实，私有内容按所标受众理解。\n')
    if dialogue_author:
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
    text += '【输出格式】\n输出JSON。speech为话语，reported_used_memory_ids列实际参考的已交付记忆标记，未参考填空数组。\n'
    if private:
        text += ('thought为仅自己知道的角色内心独白；speech、thought各自可为null。'
                 'reported_used_memory_ids改为含speech和thought两个标记数组的对象。\n')
    if expression:
        text += 'expression从本轮表情能力编码中选择。\n'
    if not sequence and not private and (idle or mode == 'contextual'):
        text += 'speech可为null，表示本轮选择安静，此时reported_used_memory_ids为空数组。\n'
    if sequence:
        if free_action_intents:
            from .action_intents import FORMAT
            text += ('action_intents为有序数组，最多4项，每项含description（一个身体动作）和target_ref（现场对象ID，自身动作为null）。可填[]或null。' if concise else FORMAT) + ' speech可为null。\n'
        elif max_actions is not None:
            text += f'actions按顺序填写可选动作序列中的编码，最多{max_actions}项，可填[]；speech可为null。\n'
        else:
            text += 'actions为本轮可执行候选的有序数组，可为空；speech可为null。其他角色独立决定其回应。\n'
        if parallel_speech:
            text += ('话语默认与动作并行，歪头、眨眼和摆尾可以边做边说。'
                '仅当台词依赖动作的实际阶段（例如到达后才能耳语）时，填写speech_timing的'
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


def persona_first(assembly, packet,*,dialogue_author=False):
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
        if prefix != AUTHOR_IDENTITY:raise ValueError('dialogue author identity missing')
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
