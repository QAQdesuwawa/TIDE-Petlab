"""Three-channel results and bounded formal feedback cache, not psychology."""
import math
from collections.abc import Mapping
from framework.model_api import model_data_fingerprint, thaw_model_data
from .face_expression import CODES

VERSION='desktop-pet-reaction-v2'
LEGACY_VERSION='desktop-pet-reaction-v1'
THOUGHT_VERSION='desktop-pet-reaction-v3-private-thought'
SEQUENCE_VERSION='desktop-pet-reaction-v4-action-sequence'
CHANNELS=('action','expression','speech')
ROOTS=('E_env','P_need','V_attr','V_rep','L_self','L_opponent')


def results_for(contents, *, actual_ids, child_results=None, declarations=None):
    """Total result includes holds/silence; only confirmed effects carry tokens."""
    contents=tuple(contents)
    actual=set(actual_ids); rows=child_results or {}
    channels=CHANNELS+(('thought',) if any(c.kind=='thought' for c in contents) or any(r['kind']=='thought' for r in (declarations or {}).values()) else ())
    result={k:{'status':'silent' if k=='speech' else 'hold','token':None,'content_ids':[]}
            for k in channels}
    seen=set()
    for item in contents:
        if item.kind not in result:continue
        seen.add(item.content_id)
        row=result[item.kind]
        row['content_ids'].append(item.content_id)
        row['status']=rows.get(item.content_id,{}).get('status',
            'completed' if item.content_id in actual else 'cancelled')
        if item.content_id not in actual:continue
        if item.kind=='expression':row['token']=item.parameters['presentation']['expression']
        elif item.kind=='action':row['token']=item.semantic_key
        else:row['token']=item.kind
    for identity,item in (declarations or {}).items():
        if identity in seen:continue
        row=result[item['kind']]
        row['content_ids'].append(identity)
        row['status']=rows.get(identity,{}).get('status','cancelled')
    action=result['action']
    if len(action['content_ids'])>1:
        by_id={item.content_id:item for item in contents}
        items=[]
        for identity in action['content_ids']:
            item=by_id.get(identity)
            status=rows.get(identity,{}).get('status','completed' if identity in actual else 'cancelled')
            items.append({'content_id':identity,'status':status,
                'token':item.semantic_key if item is not None and identity in actual else None})
        completed=[r['token'] for r in items if r['status']=='completed' and r['token'] is not None]
        action.update(items=items,token=completed[-1] if completed else None,
            status='completed' if all(r['status']=='completed' for r in items) else 'interrupted' if actual.intersection(action['content_ids']) else 'cancelled')
    return result


def validate(value):
    if (not isinstance(value,Mapping) or value.get('version') not in {VERSION,LEGACY_VERSION,THOUGHT_VERSION,SEQUENCE_VERSION}
        or value.get('fingerprint')!=model_data_fingerprint({k:v for k,v in value.items() if k!='fingerprint'})
        or set(value.get('channels',{}))!=set(CHANNELS+(('thought',) if value.get('version')==THOUGHT_VERSION or (value.get('version')==SEQUENCE_VERSION and 'thought' in value.get('channels',{})) else ()))
        or type(value.get('ordinal')) is not int or not value.get('source')):
        raise ValueError('invalid formal reaction')
    feedback=value.get('feedback',{})
    if feedback.get('expression',{}).get('token') not in CODES:
        raise ValueError('invalid formal expression token')
    if value['version'] in {VERSION,THOUGHT_VERSION,SEQUENCE_VERSION}:
        from .expression_tokens import verify
        numeric=value['numeric_tokens']
        if len(numeric)!=len(ROOTS) or {row['axis'] for row in numeric}!=set(ROOTS):
            raise ValueError('reaction numeric axes differ')
        for row in numeric:
            if row['delta']!=row['value']-row['before']:
                raise ValueError('reaction delta differs')
        verify(value['expression_plan'],before={r['axis']:r['before'] for r in numeric},
            after={r['axis']:r['value'] for r in numeric},source=value['source'])
        if (feedback['expression']['token']!=value['expression_plan']['selected']
            or feedback['expression']['source']!=value['source']
            or value['channels']['expression'].get('next_token')!=value['expression_plan']['selected']):
            raise ValueError('feedback differs from expression plan')
    return value


def build(*, previous, before, after, source, ordinal, results=None, constant_binding=None):
    if previous is not None:validate(previous)
    channels=thaw_model_data(results if results is not None else results_for((),actual_ids=()))
    if set(channels) not in (set(CHANNELS),set(CHANNELS)|{'thought'}):raise ValueError('reaction channels differ')
    feedback=({'expression':{'token':'normal','source':None},'action':{'token':'idle','source':None}}
        if previous is None else thaw_model_data(previous['feedback']))
    for kind in ('expression','action'):
        item=channels[kind]
        if item['status']=='hold':item['token']=feedback[kind]['token']
        # A cancelled or interrupted action is never a new reusable action token.
        if (item['status']=='completed' or (kind=='action' and 'items' in item)) and item['token'] is not None:
            feedback[kind]={'token':item['token'],'source':source}
    numeric=[]
    for axis in ROOTS:
        old,new=float(before[axis]),float(after[axis])
        if not all(math.isfinite(v) and 0<=v<=1 for v in (old,new)):
            raise ValueError('invalid settled numeric token input')
        numeric.append({'axis':axis,'before':old,'value':new,'delta':new-old,
                        'domain':{'low':-1.0,'high':1.0},'neutral':0.0})
    from .expression_tokens import project
    if constant_binding is None:
        expression_plan=project(before=before,after=after,source=source)
    else:
        from .expression_tokens import project_existing
        expression_plan=project_existing(before=before,after=after,source=source,constant_binding=constant_binding,version="pet-existing-token-expression-v2")
    feedback['expression']={'token':expression_plan['selected'],'source':source}
    channels['expression']['next_token']=expression_plan['selected']
    value={'version':SEQUENCE_VERSION if 'items' in channels['action'] else THOUGHT_VERSION if 'thought' in channels else VERSION,'source':source,'ordinal':ordinal,'channels':channels,
           'feedback':feedback,'numeric_tokens':numeric,'expression_plan':expression_plan}
    value['fingerprint']=model_data_fingerprint(value)
    validate(value)
    return value
