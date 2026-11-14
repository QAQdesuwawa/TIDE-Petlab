"""Lossless string-preserving lexical reduction before the existing codecs."""
import hashlib
import json

VERSION='petlab-json-punctuation-v1'


def normalize(text):
    try:
        json.loads(text)
        return text,None
    except json.JSONDecodeError:
        pass
    quoted=False;escaped=False;chars=[];changes=[]
    for index,char in enumerate(text):
        if quoted:
            if escaped:escaped=False
            elif char=='\\':escaped=True
            elif char=='"':quoted=False
        elif char=='"':quoted=True
        elif char in '，：':
            replacement={ '，':',','：':':' }[char]
            changes.append({'offset':index,'from':char,'to':replacement});char=replacement
        chars.append(char)
    normalized=''.join(chars)
    # Missing data, broken quotes and other malformed syntax remain errors.
    json.loads(normalized)
    if not changes:return text,None
    digest=lambda value:hashlib.sha256(value.encode('utf8')).hexdigest()
    return normalized,{'version':VERSION,'raw_sha256':digest(text),
        'normalized_sha256':digest(normalized),'changes':changes}


def separate_absent_memory_claims(text, *, aliases, private=False, numbered=True):
    """Keep references from absent channels as audit, never as actual use."""
    body = json.loads(text)
    claims = body.get('reported_used_memory_ids')
    channels = claims if private else {'speech': claims}
    if not isinstance(channels, dict):
        return text, {}
    discarded = {}
    for channel in ('speech', 'thought') if private else ('speech',):
        markers = channels.get(channel)
        if channel not in body or body[channel] is not None or not isinstance(markers, list) or not markers:
            continue
        types = (str, int) if numbered and not private else (str,)
        if any(type(marker) not in types for marker in markers):
            raise ValueError('absent channel memory claims contain invalid markers')
        labels = [str(marker) for marker in markers]
        if len(labels) != len(set(labels)) or not set(labels).issubset(aliases):
            raise ValueError('absent channel memory claims differ from delivered IDs')
        discarded[channel] = [aliases[label] for label in labels]
        channels[channel] = []
    if not discarded:
        return text, {}
    body['reported_used_memory_ids'] = channels if private else channels['speech']
    return json.dumps(body, ensure_ascii=False), discarded

EXPRESSION_LABEL_VERSION = 'petlab-writer-expression-label-v1'


def normalize_expression_label(text, choices):
    """Resolve only an exact, unique display label in the sealed vocabulary."""
    body = json.loads(text)
    if not isinstance(body, dict):
        return text, None
    label = body.get('expression')
    if not isinstance(label, str) or label in choices:
        return text, None
    codes = [code for code, display in choices.items() if display == label]
    if len(codes) != 1:
        return text, None
    body['expression'] = codes[0]
    return json.dumps(body, ensure_ascii=False), {
        'field': 'expression', 'reason': 'declared_expression_label',
        'original': label, 'normalized': codes[0],
    }
