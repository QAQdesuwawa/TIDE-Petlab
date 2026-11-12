"""Resolve Writer descriptions against a frozen, locally executable action offer."""
import json
import re
import unicodedata

from model_packages.desktop_pet_v1.action_sequence import validate_keys, validate_offer
from model_packages.desktop_pet_v1.basic_actions import KEYS as BASIC_ACTIONS

VERSION = 'desktop-pet-free-action-intents-v1'
EMOJI_VERSION = 'desktop-pet-free-action-intents-v2-emoji'
ORDINARY_VERSION = 'desktop-pet-free-action-intents-v3-ordinary-fallback'
CURRENT_VERSION = 'desktop-pet-free-action-intents-v4-eye-presentation'
EMOJI_VERSIONS = frozenset({EMOJI_VERSION,ORDINARY_VERSION,CURRENT_VERSION})
_OWN_HINT_ACTIONS = frozenset((*BASIC_ACTIONS,'play_tail','stroll','relocate'))
FORMAT = ('动作意图与话语、表情各自选择。结合此刻人设、关系和经历，用自然短语写想做的身体动作。'
          'action_intents为有序数组，每项含description和target_ref，最多4项，可为[]或null。'
          '涉及特定对象时，target_ref填现场给出的对应标识；纯自身动作填null。'
          '每项只描述一个身体动作，意图能否执行以本地结果为准。')
EMOJI_FORMAT = ('普通、自身的动作意图可以附加emoji字段，填一两个能表示这项活动的Emoji或null。'
                '本地没有对应动画时，Emoji可作为短暂的公开活动提示。')
EYE_FORMAT = '睁眼、闭眼和打盹也可以用普通描述提出，角色会沿本轮实际可用的表现呈现。'
_SLEEP = re.compile(r'(?:(?:闭眼|闭目|闭上眼睛?)(?:小憩|休息)|小憩|小睡|打盹|打个盹|打个瞌睡|眯一会儿?|睡一会儿?|(?:躺下来?)?睡觉)')
_CLOSE_EYES = re.compile(r'(?:闭眼|闭上眼|闭上眼睛|闭起眼睛|合上眼睛)')
_OPEN_EYES = re.compile(r'(?:睁眼|睁开眼|睁开眼睛|张开眼睛)')
_EMOJI = re.compile(r'(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]\ufe0f?){1,2}')
_RESTRICTED_HINT = re.compile(r'拥抱|抱住|抱一下|亲吻|亲脸|吻|交接|递碗|抢碗|截图|录屏|发消息|搜索|联网|操作窗口|关闭窗口')

# Recognize equivalent short descriptions. Entire phrases must match so that
# negation, compound acts and unrelated gestures do not become physical effects.
PHRASES = {
    'head_bow': r'低头(?:看看|看向下方|沉思)?',
    'head_tilt': r'(?:歪头|歪了歪头|歪一下头|歪着头)(?:想想|思考|打量)?',
    'nod': r'(?:点头|点点头|点了点头|点一下头)(?:示意|应和|致意|表示同意|表示听到了)?',
    'shake_head': r'(?:摇头|摇摇头|摇了摇头)(?:示意|表示不同意)?',
    'wave_hand': r'(?:挥手|挥挥手|挥了挥手|抬手挥一挥|摆摆手)(?:打招呼|告别)?',
    'beckon': r'(?:招手|招招手|伸手招一招|招手示意过来)',
    'turn_left': r'(?:向左|往左)(?:转身|转过去|转向)|转向左侧',
    'turn_right': r'(?:向右|往右)(?:转身|转过去|转向)|转向右侧',
    'lie_down': r'(?:侧躺|躺下|躺下来)(?:休息)?',
    'stand_up': r'(?:起身|站起来|站起身)(?:站好)?',
    'play_tail': r'(?:摇尾巴|摆尾巴|晃尾巴|摇摇尾巴|摆摆尾巴|摇动尾巴|摆动尾巴|玩自己的尾巴)',
    'stroll': r'(?:散步|走走|在附近走走|原地走一走|散步一小段)',
    'relocate': r'(?:换个位置|换个地方|换个位置待着|挪到空地|换到空处)',
    'move_near': r'(?:走近|靠近|凑近|走过去|走到身边|到身旁停下)',
    'move_away': r'(?:走开|退开|走远|往后退|拉开距离)',
    'invite_hug': r'(?:邀请拥抱|提出拥抱邀请|想抱一下|请求拥抱)',
    'accept_hug': r'(?:接受拥抱邀请|同意拥抱)',
    'decline_hug': r'(?:拒绝拥抱邀请|谢绝拥抱)',
    'invite_cheek_kiss': r'(?:邀请亲脸颊|提出亲脸颊邀请|请求亲脸颊)',
    'accept_cheek_kiss': r'(?:接受亲脸颊邀请|同意亲脸颊)',
    'decline_cheek_kiss': r'(?:拒绝亲脸颊邀请|谢绝亲脸颊)',
    'eat_rice': r'(?:吃饭|吃一口饭|吃一口白饭)',
    'request_bowl': r'(?:要碗|借碗|请求递碗|请求饭碗)',
    'offer_bowl': r'(?:递饭|递碗|递出饭碗|把碗递过去|分享碗里的饭)',
    'accept_bowl': r'(?:接碗|接过饭碗|收下饭碗)',
    'decline_bowl': r'(?:拒绝接碗|谢绝递来的饭碗)',
    'keep_bowl': r'(?:留着饭碗|不把碗递出去)',
    'read_web': r'(?:看手机|翻看手机|用手机看网页|上网看看|浏览网页)',
}
TARGETED = frozenset({
    'move_near', 'move_away', 'request_bowl', 'offer_bowl', 'accept_bowl',
    'decline_bowl', 'keep_bowl',
})
MATCHERS = {key: re.compile(pattern) for key, pattern in PHRASES.items()}


def _phrase(text, target):
    text = unicodedata.normalize('NFKC', text).strip(' 。.!！')
    if target:
        for prefix in ('朝', '向', '对'):
            start = prefix + target
            if text.startswith(start):
                text = text[len(start):].lstrip(' ，,')
                break
    return re.sub(r'(?:一下|一点|一些)$', '', re.sub(r'^(?:轻轻地?|慢慢地?|稍微)', '', text))


def _option(intent, offer, *, ordinary_fallback=False):
    text, target = intent['description'], intent['target_ref']
    phrase = _phrase(text, target)
    meanings = {key for key, pattern in MATCHERS.items()
                if pattern.fullmatch(phrase) or pattern.fullmatch(text)}
    matches = []
    for row in offer['catalog']['candidates']:
        key = row.get('action_key', row['key'])
        directed = key in TARGETED or 'social_contact' in row
        expected = row.get('target_ref', offer['target_ref']) if directed else None
        named_object = False
        if expected == target and 'world_object' in row:
            label = unicodedata.normalize('NFKC', row['world_object']['label'])
            for suffix in (label, '「'+label+'」'):
                if phrase.endswith(suffix) and MATCHERS[key].fullmatch(phrase[:-len(suffix)].rstrip()):
                    named_object = True
                    break
        if (key in meanings or named_object) and expected == target and row['key'] in offer['allowed_keys']:
            matches.append(row['key'])
    if len(matches) == 1:
        return matches[0], None
    if ordinary_fallback and not matches and target is None and meanings and meanings<=_OWN_HINT_ACTIONS:
        return None, 'ordinary_animation_unavailable'
    return None, ('ambiguous' if len(matches) > 1 else
                  'target_or_capability_unavailable' if meanings else 'no_close_match')


def valid_emoji(value):
    return isinstance(value, str) and bool(_EMOJI.fullmatch(value))


def _eye_effect(description):
    phrase = _phrase(description,None)
    if _SLEEP.fullmatch(phrase):return 'sleep'
    if _CLOSE_EYES.fullmatch(phrase):return 'closed'
    if _OPEN_EYES.fullmatch(phrase):return 'open'
    return None


def reduce_output(text, context, *, version=VERSION):
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError('writer output must be an object')
    offer = validate_offer(context)
    proposed = value.pop('action_intents', None)
    coded = value.pop('actions', None)
    keys, decisions, indices = [], [], {}
    if coded or (proposed is not None and not isinstance(proposed, list)):
        decisions.append({'status': 'not_executed', 'reason': 'invalid_intent_format'})
    for index, intent in enumerate(proposed[:offer['max_actions']] if isinstance(proposed, list) else []):
        emoji = None
        if version in EMOJI_VERSIONS and isinstance(intent, dict):
            intent = dict(intent)
            proposed_emoji = intent.pop('emoji', None)
            if valid_emoji(proposed_emoji):emoji = proposed_emoji
        if (not isinstance(intent, dict) or set(intent) != {'description', 'target_ref'}
                or not isinstance(intent['description'], str) or not 0 < len(intent['description'].strip()) <= 96
                or (intent['target_ref'] is not None and
                    (not isinstance(intent['target_ref'], str) or not 0 < len(intent['target_ref']) <= 96))):
            decisions.append({'index': index, 'status': 'not_executed', 'reason': 'invalid_intent'})
            continue
        key, reason = _option(intent, offer,ordinary_fallback=version in (ORDINARY_VERSION,CURRENT_VERSION))
        if key is not None:
            try:
                validate_keys([*keys, key], offer)
            except ValueError:
                key, reason = None, 'sequence_conflict'
        if key is not None:
            indices[index] = len(keys)
            keys.append(key)
        decisions.append({'index': index, **intent, 'status': 'matched', 'key': key} if key is not None else
                         {'index': index, **intent, 'status': 'not_executed', 'reason': reason})
        if emoji is not None:decisions[-1]['emoji'] = emoji
    if isinstance(proposed, list) and len(proposed) > offer['max_actions']:
        decisions.append({'status': 'not_executed', 'reason': 'beyond_sequence_capacity'})
    value['actions'] = keys
    if 'read_web' not in keys:
        value.pop('search_query', None)

    def timing(anchor):
        if (isinstance(anchor, dict) and set(anchor) == {'action_index', 'phase'}
                and type(anchor['action_index']) is int and anchor['phase'] in ('start', 'end')):
            index = indices.get(anchor['action_index'])
            return None if index is None else {**anchor, 'action_index': index}
        return anchor

    if 'speech_timing' in value:
        value['speech_timing'] = timing(value['speech_timing'])
    for channel in ('speech', 'expressions'):
        if isinstance(value.get(channel), list):
            value[channel] = [{**row, 'timing': timing(row['timing'])}
                              if isinstance(row, dict) and 'timing' in row else row
                              for row in value[channel]]
    return json.dumps(value, ensure_ascii=False), {
        'version': version, 'offer_fingerprint': offer['fingerprint'], 'decisions': decisions,
    }


def presentation_hint(resolution, *, destination, envelope):
    if resolution['version'] not in EMOJI_VERSIONS:return None
    if destination is not None and destination['conversation']['conversation_id'] != 'petlab:public':
        return None
    # The optional icon is an explicitly public output. Its source context
    # must be public, independently of the audience selected for this line.
    if envelope is not None and set(envelope['allowed_speech']) != set(envelope['physical_observers']):
        return None
    for row in resolution['decisions']:
        if resolution['version']==CURRENT_VERSION and row.get('target_ref') is None and row.get('description'):
            eyes=_eye_effect(row['description'])
            if (row.get('reason')=='ordinary_animation_unavailable'
                    and _phrase(row['description'],None) in ('躺下休息','躺下来休息','侧躺休息')):
                eyes='sleep'
            if eyes is not None:
                return {'intent_index':row['index'],'eye_pose':eyes,
                        'emoji':row.get('emoji') or ('💤' if eyes=='sleep' else None)}
        if (row.get('reason') in {'no_close_match','ordinary_animation_unavailable'} and row.get('target_ref') is None
                and valid_emoji(row.get('emoji')) and not _RESTRICTED_HINT.search(row['description'])):
            return {'emoji': row['emoji'], 'intent_index': row['index']}
    return None
