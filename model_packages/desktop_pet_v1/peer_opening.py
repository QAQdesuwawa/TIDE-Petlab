"""Source-grounded speech use and the 952 same-edge continuation rule."""
import json
from collections.abc import Mapping

from framework.model_api import model_data_fingerprint
from model_packages.cx_six_seven_v1.semantic_measurement import _strip_fences

KEY = 'peer_speech_use'
VERSION = 'desktop-pet-peer-speech-use-v1'
FOCUSED_VERSION = 'desktop-pet-peer-speech-use-v2'
REQUEST_SUFFIX = ':peer-speech-use-v3'
KINDS = frozenset({'light_question', 'proposal_rejection', 'refuse_continuation', 'defer_topic'})
EXCHANGE = ('a1', 'a2')


def request(system, user, event):
    speech = {c.content_id: c.description for c in event.contents
              if c.kind == 'speech' and not c.private and event.owner_ref in c.recipient_refs}
    instruction = (
        '\npeer_speech_use：从下列当前发言判断交际用途。可为 null，或填 '
        '{"content_id":"发言编号","kind":"用途","evidence_text":"该句连续原文"}。'
        'light_question 是可顺势回答的轻松真提问，proposal_rejection 是明确拒绝当前提议，'
        'refuse_continuation 是明确拒绝继续交谈，defer_topic 是明确把当前话题延后。'
        'light_question 另填 answer_focus，逐字引用 evidence_text 中实际索取回答或确认的那一小段。'
        '先区分「请对方注意并告诉对方结果」和「请对方告诉我其观察或确认我的观察」。'
        '例如「你看，花开了」是招呼注意并报告结果，填 null；「花开了，你也看见了吧」若在求证，'
        '填 light_question，answer_focus 填「你也看见了吧」。找不到求答原话时填 null。'
        '反问、引述、玩笑式句尾或用途不明时填 null。只描述发言用途。'
    )
    return system + instruction, user + '\n\n【当前发言编号】\n' + json.dumps(speech, ensure_ascii=False)


def bind(value, event, *, version=VERSION):
    if not isinstance(value, Mapping) or version not in (VERSION,FOCUSED_VERSION):
        return None
    fields={'content_id', 'kind', 'evidence_text'}
    if version==FOCUSED_VERSION and value.get('kind')=='light_question':
        fields.add('answer_focus')
    if set(value)!=fields:
        return None
    kind, quote = value['kind'], value['evidence_text']
    if not isinstance(kind, str) or kind not in KINDS or not isinstance(quote, str) or not quote.strip() or len(quote)>256:
        return None
    if 'answer_focus' in fields:
        focus=value['answer_focus']
        if not isinstance(focus,str) or not focus.strip() or quote.count(focus)!=1:
            return None
    for content in event.contents:
        if (content.content_id == value['content_id'] and content.kind == 'speech'
                and not content.private and event.owner_ref in content.recipient_refs
                and content.description.count(quote) == 1):
            return {'version': version, 'event_fingerprint': event.fingerprint, **value}
    return None


def parse(raw, event):
    value = json.loads(_strip_fences(raw))
    return bind(value.get(KEY), event) if isinstance(value, dict) else None


def parse_current(raw, event):
    value=json.loads(_strip_fences(raw))
    return bind(value.get(KEY),event,version=FOCUSED_VERSION) if isinstance(value,dict) else None


def matches(use,event):
    if not isinstance(use,Mapping):
        return False
    raw={k:v for k,v in use.items() if k not in ('version','event_fingerprint')}
    return bind(raw,event,version=use.get('version'))==use


def decide(use, channels, constants, baseline):
    evidence = {}
    if use['kind'] == 'light_question' and baseline is not None:
        for key in EXCHANGE:
            level = constants[key] * channels[key]['after']
            change = constants[key] * channels[key]['change']
            evidence[key] = {'weighted_level': level, 'reference_level': baseline[key],
                             'weighted_change': change,
                             'supports_reply': level > 0 and level >= baseline[key] and change >= 0}
    return {'version': 'desktop-pet-peer-opening-952-v1',
            'speech_use_fingerprint': model_data_fingerprint(use), 'kind': use['kind'],
            'exchange_evidence': evidence,
            'eligible': any(row['supports_reply'] for row in evidence.values())}


def reference_levels(node, constants):
    from model_packages.cx_six_seven_v1.continuous_root_projection import (
        CONTINUOUS_ROOT_PROJECTION_B_V1, project_continuous_roots,
    )
    from model_packages.cx_six_seven_v1.interaction_geometry import relationship_transition
    receipt = project_continuous_roots(
        transition=relationship_transition(node['x_before'], node['x_after']),
        constant_binding=node['constant_binding'],
        params=CONTINUOUS_ROOT_PROJECTION_B_V1,
    ).to_record()['receipt']
    return {key: constants[key] * receipt['channel_conditions'][key]['after'] for key in EXCHANGE}
