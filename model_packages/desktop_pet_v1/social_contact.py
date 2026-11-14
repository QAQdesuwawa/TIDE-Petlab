"""Directed, single-use physical invitations. No psychological scoring."""
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp, thaw_model_data

VERSION='desktop-pet-social-contact-v1'
KINDS={'hug':'拥抱','cheek_kiss':'轻吻脸颊','kiss':'接吻'}
KEYS=tuple(verb+'_'+kind for kind in KINDS for verb in ('invite','accept','decline'))

def signed(value):return {**value,'fingerprint':fp(value)}

def validate(snapshot):
    s=thaw_model_data(snapshot)
    if (s.get('version')!=VERSION or set(s)!={'version','participants','invitations','fingerprint'}
            or s['fingerprint']!=fp({k:v for k,v in s.items() if k!='fingerprint'})):
        raise ValueError('contact snapshot differs')
    people=s['participants']
    if not 2<=len(people)<=16 or len(set(people))!=len(people):raise ValueError('contact participants differ')
    for identity,row in s['invitations'].items():
        if (set(row)!={'actor','target','kind'} or row['kind'] not in KINDS or not identity
                or row['actor']==row['target'] or not {row['actor'],row['target']}<=set(people)):
            raise ValueError('invalid contact invitation')
    return s

def choices(snapshot,actor,target):
    s=validate(snapshot)
    if actor==target or not {actor,target}<=set(s['participants']):return []
    pending=[(k,r) for k,r in s['invitations'].items() if {actor,target}=={r['actor'],r['target']}]
    if pending:
        return [(verb+'_'+r['kind'],identity) for identity,r in pending if r['target']==actor
                for verb in ('accept','decline')]
    return [('invite_'+kind,None) for kind in KINDS]

def binding(snapshot,actor,target,key,invitation=None):
    if (key,invitation) not in choices(snapshot,actor,target):raise ValueError('contact choice unavailable')
    return signed(dict(version=VERSION,actor=actor,target=target,key=key,invitation=invitation))

def check(value,snapshot):
    b=thaw_model_data(value)
    if b!=binding(snapshot,b['actor'],b['target'],b['key'],b['invitation']):raise ValueError('contact binding changed')
    return b

def verify_binding(value):
    b=thaw_model_data(value)
    if (set(b)!={'version','actor','target','key','invitation','fingerprint'} or b['version']!=VERSION
            or b['key'] not in KEYS or b['actor']==b['target'] or not b['actor'] or not b['target']
            or b['fingerprint']!=fp({k:v for k,v in b.items() if k!='fingerprint'})
            or (b['invitation'] is None)!=b['key'].startswith('invite_')):
        raise ValueError('invalid frozen contact binding')
    return b

def description(actor,target,key):
    verb,kind=key.split('_',1);name=KINDS[kind]
    return {'invite':f'{actor}向{target}提出{name}邀请，等待答复，尚未接触',
            'accept':f'{actor}接受{target}的{name}邀请，双方靠近并{name}后分开',
            'decline':f'{actor}谢绝{target}的{name}邀请'}[verb]

def candidates(snapshot,actor,target):
    from .movement import arrival_definition
    return [dict(key=key,description=description(actor,target,key),
                 social_contact=binding(snapshot,actor,target,key,identity),
                 **({'approach':arrival_definition(),'physical_contract':VERSION} if key.startswith('accept_') else {}))
            for key,identity in choices(snapshot,actor,target)]

def content(target,definition):
    from .events import Action
    from .behavior import VERSION as BEHAVIOR
    b=verify_binding(definition['social_contact'])
    if b['target']!=target or b['key']!=definition['key'] or b['fingerprint']!=fp({k:v for k,v in b.items() if k!='fingerprint'}):
        raise ValueError('contact content binding differs')
    return Action(content_id='activity',semantic_key=b['key'],description=description(b['actor'],target,b['key']),
                  recipient_refs=(target,),parameters={'behavior_version':BEHAVIOR,'social_contact':b})

def realized(item,result,status):
    b=thaw_model_data(item.parameters['social_contact']);r=thaw_model_data(result)
    if (r.get('fingerprint')!=fp({k:v for k,v in r.items() if k!='fingerprint'})
            or r.get('binding_ref')!=b['fingerprint'] or r.get('status')!=status or r.get('frames',0)<=0):
        raise ValueError('contact requires actual execution evidence')
    text=description(b['actor'],b['target'],b['key'])
    if status=='interrupted':text='开始'+text+'；中途停止，未完成接触流程。'
    elif b['key'].startswith('accept_'):text=b['actor']+'与'+b['target']+'完成'+KINDS[b['key'][7:]]+'，随后分开。'
    return replace(item,description=text,parameters={**dict(item.parameters),'contact_result':r,'execution_status':status})
