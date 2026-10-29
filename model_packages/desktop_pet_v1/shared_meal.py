"""Optional shared-object actions; objective affordances, existing symbolic tokens.

No psychological root, score, clock or compulsory reply is introduced here.
"""
from copy import deepcopy
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp, thaw_model_data

VERSION='desktop-pet-shared-meal-v1'
CATALOG_VERSION='desktop-pet-shared-meal-actions-v4'
SEQUENCE_CATALOG_VERSIONS={'desktop-pet-shared-meal-actions-v3',CATALOG_VERSION}
AUDIENCE_CATALOG_VERSIONS={'desktop-pet-shared-meal-actions-v2',*SEQUENCE_CATALOG_VERSIONS}
KEYS=('eat_rice','request_bowl','offer_bowl','keep_bowl','accept_bowl','decline_bowl')
LABELS=dict(zip(KEYS,('端着饭碗吃一口饭','向对方伸手索要饭碗','把饭碗递向对方，等待对方决定','把饭碗护在怀里，拒绝这次索要','伸手接过对方递来的饭碗','摆手谢绝对方递来的饭碗')))

def signed(value):return {**value,'fingerprint':fp(value)}

def validate(value):
    v=thaw_model_data(value)
    if not isinstance(v,dict) or v.get('version')!=VERSION or v.get('fingerprint')!=fp({k:x for k,x in v.items() if k!='fingerprint'}):
        raise ValueError('meal snapshot integrity differs')
    if set(v)!={'version','interaction_id','revision','participants','holder_ref','portions','request','offer','last_effect_ref','fingerprint'}:
        raise ValueError('meal snapshot fields differ')
    if len(v['participants'])!=2 or len(set(v['participants']))!=2 or any(not isinstance(p,str) or not p for p in v['participants']):raise ValueError('meal requires two participants')
    if v['holder_ref'] not in v['participants'] or type(v['portions'])is not int or not 0<=v['portions']<=16 or type(v['revision'])is not int or v['revision']<0 or not isinstance(v['interaction_id'],str) or not v['interaction_id']:raise ValueError('invalid meal state')
    for key in ('request','offer'):
        r=v[key]
        if r is not None and (set(r)!={'actor_ref','target_ref','effect_ref'} or {r['actor_ref'],r['target_ref']}!=set(v['participants']) or not r['effect_ref']):raise ValueError('invalid meal counterpart')
    if v['offer'] is not None and v['offer']['actor_ref']!=v['holder_ref']:raise ValueError('offer not held by giver')
    if v['request'] is not None and v['request']['target_ref']!=v['holder_ref']:raise ValueError('request not addressed to holder')
    return v

def available(value,actor,target):
    s=validate(value)
    if actor==target or actor not in s['participants'] or not s['portions']:return ()
    if target not in s['participants']:
        # Eating is the holder's own activity. It does not borrow the other
        # NPC's relationship just because that NPC can see it.
        return ('eat_rice',) if actor==s['holder_ref'] and not s['offer'] and not s['request'] else ()
    if s['offer']:
        if actor==s['offer']['target_ref']:return ('accept_bowl','decline_bowl')
        return ()  # Holding out a bowl is not repeatedly offering it.
    if actor==s['holder_ref']:
        return ('offer_bowl','keep_bowl') if s['request'] else ('eat_rice','offer_bowl')
    return () if s['request'] else ('request_bowl',)

def binding(value,actor,target,key):
    s=validate(value)
    if key not in available(s,actor,target):raise ValueError('meal action is not currently available')
    return signed({'version':VERSION,'key':key,'actor_ref':actor,'target_ref':target,'scene':s})

def verify_binding(value):
    v=thaw_model_data(value)
    if not isinstance(v,dict) or set(v)!={'version','key','actor_ref','target_ref','scene','fingerprint'}:raise ValueError('invalid meal action binding')
    if binding(v['scene'],v['actor_ref'],v['target_ref'],v['key'])!=v:raise ValueError('meal action binding differs')
    return v

def check_current(value,current):
    p=verify_binding(value);s=validate(current)
    if p['scene']['interaction_id']!=s['interaction_id'] or p['scene']['participants']!=s['participants']:raise ValueError('meal scene replaced')
    if p['key']=='eat_rice':
        # A request changes the next choice, not possession of the current
        # mouthful. Execution validity is distinct from the next candidate set.
        if p['actor_ref']!=s['holder_ref'] or not s['portions'] or s['offer'] is not None:raise ValueError('meal action source expired')
    elif p['key'] not in available(s,p['actor_ref'],p['target_ref']):raise ValueError('meal action source expired')
    if p['key'] in ('accept_bowl','decline_bowl') and s['offer']!=p['scene']['offer']:raise ValueError('different offer cannot inherit acceptance')
    if p['key']=='keep_bowl' and s['request']!=p['scene']['request']:raise ValueError('different request cannot inherit refusal')
    if p['key']=='offer_bowl' and s['request']!=p['scene']['request']:raise ValueError('meal request changed')
    return p,s

def catalog(base,value,actor,target):
    from .action_candidates import CATALOG
    original=deepcopy(thaw_model_data(CATALOG if base is None else base))
    if value is None:return original
    s=validate(value)
    if actor==target or actor not in s['participants'] or not s['portions']:return original
    if target not in s['participants'] and not available(s,actor,target):return original
    # Preserve the existing, independently token-qualified approach entity.
    # Other unrelated self activities are not part of this meal sequence.
    candidates=[row for row in original['candidates'] if row['key']=='move_near']
    for key in available(s,actor,target):
        positive=key not in ('keep_bowl','decline_bowl')
        rule={'key':key,'description':LABELS[key],'meal':binding(s,actor,target,key),
              'category':['action','self' if key=='eat_rice' else 'outward','meal',key]}
        if key in ('offer_bowl','accept_bowl'):
            from .meal_space import VERSION as REACH
            rule['physical_contract']=REACH
            approach=next((r['motion'] for r in original['candidates'] if r['key']=='move_near'),None)
            if approach is not None:
                rule['approach']=deepcopy(approach)
                rule['description']='走到对方身旁后，'+LABELS[key]
        if positive:
            rule.update(any=[{'kind':'self','text':'[行为高度同向]'},{'kind':'self','text':'[行为中等同向]'}])
            # Eating and asking are not a performance of happiness. Pain and
            # moderate repulsion are not universal physical-action vetoes.
            if key in ('offer_bowl','accept_bowl'):
                rule['none']=[{'kind':'phase','var':'V_rep','level':'high'}]
            if key=='offer_bowl' and not s['request']:
                rule['all']=[{'kind':'phase','var':'V_attr','level':'high'}]
            if key=='eat_rice':rule['fallback']=True
        else:rule.update(all=[{'kind':'phase','var':'V_rep','level':'high'}])
        candidates.append(rule)
    return {'version':CATALOG_VERSION,'base_catalog_version':original['version'],'candidates':candidates}

def content(target,definition):
    from .events import Action
    from .behavior import VERSION as BEHAVIOR
    p=verify_binding(definition['meal'])
    if definition['key']!=p['key'] or target!=p['target_ref']:raise ValueError('meal action target differs')
    physical={}
    if 'physical_contract' in definition:
        from .meal_space import VERSION as REACH
        if definition['physical_contract']!=REACH or p['key'] not in ('offer_bowl','accept_bowl'):raise ValueError('meal physical contract differs')
        physical={'meal_space':REACH}
    return Action(content_id='activity',semantic_key=p['key'],description=LABELS[p['key']],recipient_refs=(target,),parameters={'behavior_version':BEHAVIOR,'meal':p,**physical})

def result_content(content,result,*,status):
    p=verify_binding(content.parameters['meal']);r=thaw_model_data(result)
    if not isinstance(r,dict) or r.get('fingerprint')!=fp({k:v for k,v in r.items() if k!='fingerprint'}) or r.get('binding_ref')!=p['fingerprint'] or r.get('status')!=status:raise ValueError('meal effect does not match executed action')
    if r.get('version')!=VERSION or any(r.get(k)!=p[k] for k in ('actor_ref','target_ref','key')) or type(r.get('frames'))is not int or r['frames']<=0:raise ValueError('meal effect identity differs')
    transfer=p['key']=='accept_bowl' and status=='completed'
    if r.get('transferred') is not transfer or (transfer and (r['holder_before']!=p['target_ref'] or r['holder_after']!=p['actor_ref'])) or (not transfer and r['holder_after']!=r['holder_before']):raise ValueError('meal ownership effect differs')
    if status=='completed':
        text={**LABELS,'accept_bowl':'从对方手里接过了饭碗，现在由自己持有','offer_bowl':'把饭碗递向对方；碗仍在自己手中，等待对方决定','request_bowl':'向拿碗的对方伸手索要，尚未拿到饭碗'}[p['key']]
    else:text='开始'+LABELS[p['key']]+'，中途停止；未完成饭碗交接。'
    return replace(content,description=text,parameters={**dict(content.parameters),'meal_result':r,'execution_status':status})

def card(value):
    s=validate(value)
    request='无' if s['request'] is None else s['request']['actor_ref']+'向'+s['request']['target_ref']+'索要'
    offer='无' if s['offer'] is None else s['offer']['actor_ref']+'递向'+s['offer']['target_ref']+'，尚未接过'
    return '当前饭碗由'+s['holder_ref']+'持有；'+('还有饭。' if s['portions'] else '饭已吃完。')+'索要：'+request+'；递出：'+offer+'。'


def placement(value):
    from .events import Interaction
    s=validate(value)
    text='用户通过“开饭”菜单给'+s['holder_ref']+'放了一碗饭，两只角色都看见了。'+card(s)
    return Interaction(content_id='meal:placement',semantic_key='place_meal',
        description=text,recipient_refs=tuple(s['participants']),
        parameters={'input_kind':'context_menu','meal_scene':s,
            'definition':{'semantic_key':'place_meal','description':'用户放置饭碗，角色看到当前道具和持有者'}})
