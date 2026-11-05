"""Presentation-only prompt layout; authored prose and selected facts stay intact."""
from dataclasses import replace
import hashlib
from framework.model_api import model_data_fingerprint, thaw_model_data

SUFFIX = '-persona-v1'


def project(packet):
    sections = dict(packet.sections)
    rows = []
    for card in sections['persona']:
        fields = thaw_model_data(card.fields)
        body = fields.pop('background', '')
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


def core(*, private, sequence, expression, mode, idle):
    text = ('【表达与资料边界】\n'
        '以人格原文中的角色身份，结合此刻关系状态、经历与情境，自主选择措辞、语气和表达内容。'
        '日常回应通常一两句、约8–35个汉字，详细问题按实际需要展开。\n'
        '人格与补充背景是角色设定；状态词元是当前心理状态；记忆和现场记录分别标注说话者、对象与实际进展。'
        '说过、打算做、已经完成是不同事实，私有内容按所标受众理解。\n')
    text += ('本轮是用户直接交互，通过本轮可用通道给予可感知的反馈。\n' if mode == 'direct_user' else
        '本轮是情境触发的表达机会，可以回应、联想、继续自己的事或保持安静。\n')
    if idle:
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
        text += ('actions为本轮可执行候选的有序数组，可为空；speech可为null。其他角色独立决定其回应。\n'
            '可选speech_timing为null或含action_index（从0开始）和phase（start/end）的对象，指定本轮动作开始/结束时讲话；为空即本轮可说。'
            'speech也可为最多4个{text,timing}对象组成的有序数组，此时省略顶层speech_timing；每段沿日常句长。\n')
        if expression:
            text += ('可选expressions为最多4个{expression,hold_ms,timing}对象，hold_ms为100–10000毫秒，timing沿话语规则；'
                '省略则使用expression。动作、话语、表情各自串行，三通道互相并行；同通道等待上一项结束。'
                '话语各段共用本轮受众。打断只留下实际播放进度，未播放计划不是完成事实。\n')
    return text


def persona_first(assembly, packet):
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
    system = '\n\n'.join([*blocks, system])
    return replace(assembly, system=system,
        prompt_fingerprint=hashlib.sha256((system+'\0'+assembly.user).encode()).hexdigest())
