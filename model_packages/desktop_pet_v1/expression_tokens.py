"""Read-only, versioned desktop expression projection. No scheduling authority.

Prototype coordinates describe display poses, not psychological truth. Scores
retain absolute level and signed change; no direction descriptor lookup.
"""
import json
import math
from functools import lru_cache
from pathlib import Path
from framework.model_api import model_data_fingerprint

VERSION='pet-numeric-expression-v1'

@lru_cache(maxsize=1)
def _resources():
    return json.loads((Path(__file__).parent/'resources/expression-prototypes-v1.json').read_text(encoding='utf8'))


def project(*,before,after,source):
    resources=_resources(); axes=resources['axes']
    vectors=[]
    for roots in (before,after):
        vector=[]
        for axis in axes:
            value=roots[axis]
            if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:
                raise ValueError('expression needs finite formal unit roots')
            vector.append(float(value))
        vectors.append(vector)
    tokens=[]
    for code,prototype in resources['prototypes'].items():
        old,new=(-sum((x-y)**2 for x,y in zip(vector,prototype))/len(axes) for vector in vectors)
        delta=new-old
        tokens.append({'key':'expression:'+code,'expression':code,'previous_level':old,
            'level':new,'delta':delta,'score':new+delta,'domain':{'low':-2.,'high':1.},
            'neutral':0.,'source':source})
    # Exact ties retain neutral-first resource order. No emotion threshold.
    selected=max(tokens,key=lambda token:token['score'])['expression']
    value={'version':VERSION,'resource_version':resources['version'],
        'resource_fingerprint':model_data_fingerprint(resources),'source':source,
        'tokens':tokens,'selected':selected}
    value['fingerprint']=model_data_fingerprint(value)
    return value



def verify(plan,*,before,after,source):
    expected=(project_existing(before=before,after=after,source=source,constant_binding=plan['constant_binding'],version=plan['version'])
        if plan.get('version') in {'pet-existing-token-expression-v1','pet-existing-token-expression-v2'} else project(before=before,after=after,source=source))
    from framework.model_api import thaw_model_data
    if thaw_model_data(plan)!=expected:
        raise ValueError('expression plan differs from formal numerical source')


def assemble_existing(self_tokens, change_tokens, variable_tokens, *, version="pet-existing-token-expression-v1"):
    """Compose published token meanings into assets; no numerical state reads."""
    if version not in {'pet-existing-token-expression-v1','pet-existing-token-expression-v2'}:raise ValueError('unknown expression token assembly version')
    phases={t['var']:tuple(t['cell']) for t in variable_tokens if t.get('category')=='phase'}
    pains=[t['text'] for t in variable_tokens if t.get('category')=='pain']
    aligned=any(t in self_tokens for t in ('[行为高度同向]','[行为中等同向]'))
    level=lambda key:phases.get(key,('unknown','stable'))[0]
    if any('恨意攀升' in t for t in pains):
        code,reason='angry','existing_pain_repulsion'
    elif pains:
        code,reason='sad','existing_pain_loss'
    elif version=='pet-existing-token-expression-v2' and level('P_need')=='high' and level('V_attr')=='high':
        code,reason='aggrieved','existing_unmet_approach'
    elif version=='pet-existing-token-expression-v2' and level('V_rep')=='high':
        code,reason='angry','existing_repulsion'
    elif aligned:
        code,reason=('happy','aligned_open') if level('V_rep')=='low' and level('V_attr')=='high' else ('normal','aligned_self')
    elif level('V_rep')=='high':
        code,reason='angry','unaligned_repulsion'
    elif level('P_need')=='high' and level('V_attr')=='high':
        code,reason='aggrieved','unaligned_unmet_approach'
    elif level('V_attr')=='low' and '[行为完全无关]' in self_tokens:
        code,reason='sad','unaligned_withdrawn'
    else:
        code,reason='normal','no_specific_expression_evidence'
    return {'selected':code,'rule':reason}


def project_existing(*,before,after,source,constant_binding,version="pet-existing-token-expression-v1"):
    from framework.model_api import thaw_model_data
    from core.drives import calculate_drives,calculate_baseline
    from core.vectors import get_drive_vector,get_baseline_vector,cosine_similarity,component_difference
    from core.tokens.behavior import generate_universal_tokens,analyze_self_change
    from core.tokens.variable import compute_variable_tokens
    from model_packages.cx_six_seven_v1.preference_parameters import CONSTANT_ORDER
    binding=thaw_model_data(constant_binding)
    constants=binding['effective_constants']
    if set(constants)!=set(CONSTANT_ORDER) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1 for v in constants.values()):
        raise ValueError('invalid expression constant binding')
    for values in (before,after):
        for axis in ('E_env','P_need','V_attr','V_rep','L_self','L_opponent'):
            v=values[axis]
            if isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) or not 0<=v<=1:
                raise ValueError('invalid expression roots')
    old=get_drive_vector(calculate_drives(constants,before))
    current=get_drive_vector(calculate_drives(constants,after))
    baseline=get_baseline_vector(calculate_baseline(constants))
    comparison=generate_universal_tokens(cosine_similarity(current,baseline),0,component_difference(current,baseline))
    # This source has no measured satisfaction operand. Keep ONLY same-direction
    # descriptors, never the legacy fixed-zero satisfaction or dominance labels.
    self_tokens=[t for t in comparison if t.startswith('[行为')]
    change=analyze_self_change(current,old)
    variables=compute_variable_tokens(after,before)['npc']
    for token in variables:
        if 'cell' in token:token['cell']=list(token['cell'])
    selection=assemble_existing(self_tokens,change['tokens'],variables,version=version)
    value={'version':version,'source':source,
           'constant_binding':binding,'coordinate_version':'core-drives-v4-self-comparison',
           'self_tokens':self_tokens,'change_tokens':change['tokens'],
           'variable_tokens':variables,'source_generators':['core.tokens.behavior','core.tokens.variable'],
           **selection}
    value=thaw_model_data(value)
    value['fingerprint']=model_data_fingerprint(value)
    return value
