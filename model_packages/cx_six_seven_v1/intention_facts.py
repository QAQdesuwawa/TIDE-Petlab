"""Six-seven private intention facts; no Runtime policy or state mutation."""
from copy import deepcopy
from framework.model_api import model_data_fingerprint as fp

VERSION='cx67-private-intention-facts-v1'
ACTIVATION='cx67-private-intention-memory-activation-v1'
PHASES={'formed','planned','interrupted','abandoned','completed','effect'}

def signed(value):return {**value,'fingerprint':fp(value)}

def validate(facts):
    previous=None;seen=set();owner=None;identity=None;last_time=None
    for index,row in enumerate(facts):
        if row['fingerprint']!=fp({k:v for k,v in row.items() if k!='fingerprint'}):raise ValueError('intention fact changed')
        if row['version']!=VERSION or row['previous']!=previous:raise ValueError('intention lineage changed')
        if index==0:
            if row['phase']!='formed':raise ValueError('intention lacks formation')
            owner=row['owner'];identity=row['intention_id']
        elif row['phase']=='formed':raise ValueError('intention formed twice')
        if row['owner']!=owner or row['intention_id']!=identity or row['visibility']!='self':raise ValueError('intention identity or visibility changed')
        if row['phase'] not in PHASES:raise ValueError('unknown intention phase')
        if type(row['at_ms']) is not int or (last_time is not None and row['at_ms']<last_time):raise ValueError('intention time reversed')
        if not row['evidence_ref'] or not row['transition_id'] or row['transition_id'] in seen:raise ValueError('intention evidence missing or repeated')
        if row['phase']!='formed' and not row['attempt_id']:raise ValueError('attempt identity missing')
        if row['phase']=='effect' and not row.get('actual_effect_ref'):raise ValueError('actual effect lacks receipt')
        seen.add(row['transition_id']);previous=row['fingerprint'];last_time=row['at_ms']
    return facts

def form(*,owner,source_ref,grant_ref,at_ms,eligible):
    if not eligible:return ()
    if not all(isinstance(v,str) and v for v in (owner,source_ref,grant_ref)):raise ValueError('formation source missing')
    identity=fp({'owner':owner,'source_ref':source_ref,'grant_ref':grant_ref})
    row=signed({'version':VERSION,'intention_id':identity,'owner':owner,'visibility':'self',
        'phase':'formed','at_ms':at_ms,'source_ref':source_ref,'evidence_ref':grant_ref,
        'transition_id':identity+':formed','attempt_id':None,'previous':None})
    return tuple(validate((row,)))

def append(facts,*,transition_id,attempt_id,phase,at_ms,evidence_ref,actual_effect_ref=None):
    validate(facts)
    if not facts:raise ValueError('no accepted intention')
    base=facts[0]
    fields={'version':VERSION,'intention_id':base['intention_id'],'owner':base['owner'],
        'visibility':'self','phase':phase,'at_ms':at_ms,'evidence_ref':evidence_ref,
        'transition_id':transition_id,'attempt_id':attempt_id,'actual_effect_ref':actual_effect_ref}
    for old in facts:
        if old['transition_id']==transition_id:
            if any(old.get(k)!=v for k,v in fields.items()):raise ValueError('replayed transition differs')
            return facts
    result=(*facts,signed({**fields,'previous':facts[-1]['fingerprint']}))
    validate(result)
    return result

def read_private(facts,*,reader):
    validate(facts)
    return deepcopy(facts) if facts and facts[0]['owner']==reader else ()


def technical_interruption(facts,proof):
    """A technical stop is recoverable evidence, not a withdrawn intention.

    This does not grant execution: current-source and dispatch checks still apply.
    """
    validate(facts)
    if not facts or facts[-1]['phase']!='interrupted':return False
    last=facts[-1]
    if (fp(proof)!=last['evidence_ref'] or proof.get('owner')!=last['owner']
            or proof.get('visibility')!='self' or proof.get('phase')!='interrupted'
            or proof.get('attempt_ref',proof.get('source_ref'))!=last['attempt_id']):
        raise ValueError('intention interruption evidence differs')
    return proof.get('reason')=='technical_failure'


def interrupted_children(outcome):
    """Unselected channels are empty choices, not interrupted intentions."""
    return tuple(identity for identity,row in outcome.get('details',{}).get('child_results',{}).items()
        if row.get('status')=='interrupted' or (row.get('status')=='cancelled'
            and row.get('reason') not in {'no_content_selected','not_selected'}))
