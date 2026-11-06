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
