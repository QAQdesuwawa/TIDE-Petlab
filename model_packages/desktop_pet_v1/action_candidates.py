"""Package action entities matched against published symbolic tokens, never faces."""
import json
from copy import deepcopy
from pathlib import Path
from framework.model_api import model_data_fingerprint,thaw_model_data

VERSION='desktop-pet-symbolic-action-selection-v1'
CATALOG=json.loads((Path(__file__).parent/'resources/action-candidates-v1.json').read_text(encoding='utf8'))

def compact_tokens(plan):
    tokens=[{'kind':'self','text':text} for text in plan.get('self_tokens',())]
    tokens.extend({'kind':'change','text':text} for text in plan.get('change_tokens',()))
    for token in plan.get('variable_tokens',()):
        if token.get('category')=='phase':
            tokens.append({'kind':'phase','var':token['var'],'level':token['cell'][0],'trend':token['cell'][1]})
        elif token.get('category')=='pain':tokens.append({'kind':'pain','text':token['text']})
    return tokens

def _matches(candidate,tokens):
    def present(pattern):return any(all(token.get(k)==v for k,v in pattern.items()) for token in tokens)
    return (all(present(p) for p in candidate.get('all',()))
        and (not candidate.get('any') or any(present(p) for p in candidate['any']))
        and not any(present(p) for p in candidate.get('none',())))

def choose(*,eligible,tokens,source,catalog=None,allow_speech=True):
    if type(eligible) is not bool or type(allow_speech) is not bool:raise ValueError('action eligibility and speech channel must be boolean')
    if not eligible:return None
    if not isinstance(source,str) or not source:raise ValueError('action tokens need a formal source')
    catalog=CATALOG if catalog is None else catalog
    catalog=deepcopy(thaw_model_data(catalog))
    candidates=catalog['candidates'];tokens=deepcopy(thaw_model_data(tokens))
    keys=[c['key'] for c in candidates]
    if len(keys)!=len(set(keys)) or any(not isinstance(k,str) or not k for k in keys):raise ValueError('duplicate or empty action entity')
    matches=[c for c in candidates if _matches(c,tokens)]
    # Optional fallback entities do not suppress an already matched activity.
    primary=[c for c in matches if not c.get('fallback',False)]
    resolved=primary or matches
    definition=resolved[0] if len(resolved)==1 else None
    value={'version':VERSION,'mode':'symbolic','action_keys':[] if definition is None else [definition['key']],
        'allow_speech':allow_speech,'definition':definition,'source':source,'tokens':tokens,
        'catalog_version':catalog['version'],'catalog_fingerprint':model_data_fingerprint(catalog),
        'considered':[{'key':c['key'],'matched':c in matches} for c in candidates],
        'decision':'selected' if definition else 'ambiguous' if matches else 'no_match'}
    return {**value,'fingerprint':model_data_fingerprint(value)}

def validate(value):
    value=thaw_model_data(value)
    if (value.get('version')!=VERSION or value.get('mode')!='symbolic'
        or value.get('fingerprint')!=model_data_fingerprint({k:v for k,v in value.items() if k!='fingerprint'})
        or type(value.get('allow_speech')) is not bool):raise ValueError('symbolic action selection integrity differs')
    definition=value.get('definition')
    if value['action_keys']!=([] if definition is None else [definition['key']]):raise ValueError('selected action differs from entity')
    if definition is not None and not _matches(definition,value['tokens']):raise ValueError('selected action differs from symbolic evidence')
    return value
