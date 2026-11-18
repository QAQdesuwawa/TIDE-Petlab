"""Optional shared-object actions; objective affordances, existing symbolic tokens.

No psychological root, score, clock or compulsory reply is introduced here.
"""
import re
from copy import deepcopy
from dataclasses import replace
from framework.model_api import model_data_fingerprint as fp, thaw_model_data

VERSION='desktop-pet-shared-meal-v1'
MULTI_VERSION='desktop-pet-shared-meal-v2'
CATALOG_VERSION='desktop-pet-shared-meal-actions-v4'
SEQUENCE_CATALOG_VERSIONS={'desktop-pet-shared-meal-actions-v3',CATALOG_VERSION}
AUDIENCE_CATALOG_VERSIONS={'desktop-pet-shared-meal-actions-v2',*SEQUENCE_CATALOG_VERSIONS}
HOLDER_TRANSFER_ACTION = 'accept_bowl'
ACCEPTANCE_V1 = 'bound-speech-v1'
ACCEPTANCE_V2 = 'bound-speech-v2'
KEYS=('eat_rice','request_bowl','offer_bowl','keep_bowl',HOLDER_TRANSFER_ACTION,'decline_bowl')
LABELS=dict(zip(KEYS,('端着饭碗吃一口饭','向对方伸手索要饭碗','把饭碗递向对方，等待对方决定','把饭碗护在怀里，拒绝这次索要','伸手接过对方递来的饭碗','摆手谢绝对方递来的饭碗')))

def signed(value):return {**value,'fingerprint':fp(value)}


def bound_description(key,actor,target):
    """Describe an instantiated action using its bound identities, not pronouns."""
    return {
        'eat_rice':f'{actor}端着饭碗吃一口饭',
        'request_bowl':f'{actor}向{target}伸手索要饭碗',
        'offer_bowl':f'{actor}把饭碗递向{target}，等待{target}决定',
        'keep_bowl':f'{actor}把饭碗护在怀里，拒绝{target}这次索要',
        'accept_bowl':f'{actor}伸手接过{target}递来的饭碗',
        'decline_bowl':f'{actor}摆手谢绝{target}递来的饭碗',
    }[key]

def validate(value):
    v=thaw_model_data(value)
    if not isinstance(v,dict) or v.get('version') not in (VERSION,MULTI_VERSION) or v.get('fingerprint')!=fp({k:x for k,x in v.items() if k!='fingerprint'}):
        raise ValueError('meal snapshot integrity differs')
    if set(v)!={'version','interaction_id','revision','participants','holder_ref','portions','request','offer','last_effect_ref','fingerprint'}:
        raise ValueError('meal snapshot fields differ')
    people=v['participants']
    limit=2 if v['version']==VERSION else 16
    if not isinstance(people,list) or not 2<=len(people)<=limit or any(not isinstance(p,str) or not p for p in people) or len(set(people))!=len(people):raise ValueError('invalid meal participants')
    if v['holder_ref'] not in v['participants'] or type(v['portions'])is not int or not 0<=v['portions']<=16 or type(v['revision'])is not int or v['revision']<0 or not isinstance(v['interaction_id'],str) or not v['interaction_id']:raise ValueError('invalid meal state')
    for key in ('request','offer'):
        r=v[key]
        if r is not None and (set(r)!={'actor_ref','target_ref','effect_ref'} or r['actor_ref']==r['target_ref'] or not {r['actor_ref'],r['target_ref']}<=set(people) or not r['effect_ref']):raise ValueError('invalid meal counterpart')
    if v['offer'] is not None and v['offer']['actor_ref']!=v['holder_ref']:raise ValueError('offer not held by giver')
    if v['request'] is not None and v['request']['target_ref']!=v['holder_ref']:raise ValueError('request not addressed to holder')
    return v

def bowls(context):
    if context is None:return ()
    return tuple(context) if isinstance(context,(list,tuple)) else (context,)


def context_snapshot(rows):
    rows=tuple(rows)
    return rows if len(rows)>1 else rows[0] if rows else None


def validate_context(context,owner):
    rows=tuple(validate(row) for row in bowls(context))
    if any(owner not in row['participants'] for row in rows):
        raise ValueError('meal owner differs')
    if len({row['interaction_id'] for row in rows})!=len(rows) or len({row['holder_ref'] for row in rows})!=len(rows):
        raise ValueError('duplicate meal object or holder')
    return context_snapshot(rows)


def held_by(context,owner):
    return next((row for row in bowls(context) if row['holder_ref']==owner and row['portions']),None)


def available(value,actor,target):
    s=validate(value)
    if actor==target or actor not in s['participants'] or not s['portions']:return ()
    if target not in s['participants']:
        # Eating is the holder's own activity. It does not borrow the other
        # NPC's relationship just because that NPC can see it.
        return ('eat_rice',) if actor==s['holder_ref'] and not s['offer'] and not s['request'] else ()
    if s['offer']:
        if actor==s['offer']['target_ref'] and target==s['offer']['actor_ref']:return ('accept_bowl','decline_bowl')
        return ()  # Holding out a bowl is not repeatedly offering it.
    if actor==s['holder_ref']:
        if s['request'] and target!=s['request']['actor_ref']:return ()
        return ('offer_bowl','keep_bowl') if s['request'] else ('eat_rice','offer_bowl')
    return () if s['request'] or target!=s['holder_ref'] else ('request_bowl',)

def binding(value,actor,target,key):
    s=validate(value)
    if key not in available(s,actor,target):raise ValueError('meal action is not currently available')
    return signed({'version':s['version'],'key':key,'actor_ref':actor,'target_ref':target,'scene':s})

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

def catalog(base,value,actor,target,*,include_self_activities=False):
    from .action_candidates import CATALOG
    original=deepcopy(thaw_model_data(CATALOG if base is None else base))
    if value is None:return original
    s=validate(value)
    if actor==target or actor not in s['participants'] or not s['portions']:return original
    if target not in s['participants'] and not available(s,actor,target):return original
    # Preserve the existing, independently token-qualified approach entity.
    # Other unrelated self activities are not part of this meal sequence.
    candidates=[row for row in original['candidates'] if include_self_activities or row['key']=='move_near']
    for key in available(s,actor,target):
        positive=key not in ('keep_bowl','decline_bowl')
        rule={'key':key,'description':bound_description(key,actor,target),'meal':binding(s,actor,target,key),
              'category':['action','self' if key=='eat_rice' else 'outward','meal',key]}
        if key in ('offer_bowl','accept_bowl'):
            from .meal_space import VERSION as REACH
            rule['physical_contract']=REACH
            approach=next((r['motion'] for r in original['candidates'] if r['key']=='move_near'),None)
            if approach is not None:
                rule['approach']=deepcopy(approach)
                rule['description']=actor+'走到'+target+'身旁后，'+bound_description(key,actor,target)
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
    return Action(content_id='activity',semantic_key=p['key'],description=bound_description(p['key'],p['actor_ref'],p['target_ref']),recipient_refs=(target,),parameters={'behavior_version':BEHAVIOR,'meal':p,**physical})

def result_content(content,result,*,status):
    p=verify_binding(content.parameters['meal']);r=thaw_model_data(result)
    if r.get('interaction_id',p['scene']['interaction_id'])!=p['scene']['interaction_id']:raise ValueError('meal receipt bowl differs')
    if not isinstance(r,dict) or r.get('fingerprint')!=fp({k:v for k,v in r.items() if k!='fingerprint'}) or r.get('binding_ref')!=p['fingerprint'] or r.get('status')!=status:raise ValueError('meal effect does not match executed action')
    if r.get('version')!=p['version'] or any(r.get(k)!=p[k] for k in ('actor_ref','target_ref','key')) or type(r.get('frames'))is not int or r['frames']<=0:raise ValueError('meal effect identity differs')
    transfer=p['key']==HOLDER_TRANSFER_ACTION and status=='completed'
    if r.get('transferred') is not transfer or (transfer and (r['holder_before']!=p['target_ref'] or r['holder_after']!=p['actor_ref'])) or (not transfer and r['holder_after']!=r['holder_before']):raise ValueError('meal ownership effect differs')
    actor,target=p['actor_ref'],p['target_ref']
    text=bound_description(p['key'],actor,target)
    if status=='completed':
        text={'accept_bowl':f'{actor}从{target}手里接过了饭碗，现在由{actor}持有',
              'offer_bowl':f'{actor}把饭碗递向{target}；碗仍在{actor}手中，等待{target}决定',
              'request_bowl':f'{actor}向拿碗的{target}伸手索要，{actor}尚未拿到饭碗'}.get(p['key'],text)
    else:text='开始'+text+'，中途停止；未完成饭碗交接。'
    return replace(content,description=text,parameters={**dict(content.parameters),'meal_result':r,'execution_status':status})

def handoff_evidence_definition(action_keys):
    handoff = ('request_bowl', 'offer_bowl', HOLDER_TRANSFER_ACTION)
    if set(action_keys).isdisjoint(handoff):
        return None
    return {
        '行为分支': '饭碗交接',
        '子项定义': {key: LABELS[key] for key in handoff},
        '归属变更条件': f'{HOLDER_TRANSFER_ACTION} 的完成回执确认同一只碗转给接碗者。',
    }


def _without_vocative(text, addressee):
    parts = re.split('[，,]', text, maxsplit=1)
    if addressee and len(parts) == 2 and parts[0] in (addressee, addressee.split()[-1]):
        return parts[1].lstrip()
    return text


def acceptance_addressed(text, *, target, recipients, direct, addressee):
    if target not in recipients:
        return False
    if direct is not None:
        return tuple(direct) == (target,)
    text = text.strip()
    return tuple(recipients) == (target,) or _without_vocative(text, addressee) != text


def bound_acceptance_code(key, offer):
    plain, sep, target = key.partition('@')
    if plain != HOLDER_TRANSFER_ACTION or not sep or plain not in offer['allowed_keys']:
        return key
    row = next(row for row in offer['catalog']['candidates'] if row['key'] == plain)
    if 'meal' not in row:
        return key
    bound = verify_binding(row['meal'])
    if (bound['key'] == plain and bound['actor_ref'] == offer['owner_ref']
            and target == bound['target_ref'] == row.get('target_ref', offer['target_ref'])):
        return plain
    return key


def acceptance_speech(contents, *, addressee, version):
    speeches = [content for content in contents if content.kind == 'speech']
    if version != ACCEPTANCE_V2 and len(speeches) != 1:
        return None
    return next((speech for speech in speeches if explicit_acceptance(
        speech.description, addressee=addressee, discourse=version == ACCEPTANCE_V2)), None)


def explicit_acceptance(text, *, addressee=None, discourse=False):
    # The first revision recognizes a complete first-person acceptance
    # clause. Quotes, negation, questions and conditional sentences remain
    # speech. The caller must separately verify the addressee and live offer.
    text = _without_vocative(text.strip(), addressee)
    if discourse:
        # A separate addressed aside ending in a sentence particle can precede
        # a fresh acceptance clause. Keep questions, quotations and conditions out.
        if addressee:
            aside, separator, response = text.replace(',', '，').partition('，')
            if (separator and aside.startswith((addressee, addressee.split()[-1]))
                    and re.fullmatch(r'[^。！？；“”「」『』"？，]+[嘛呀啊呢啦]', aside)
                    and not re.search(r'如果|要是|假如|假设|倘若|除非|只要|说|听|问|以为|没|不', aside)
                    and response.lstrip().startswith('那')):
                text = response.lstrip()
        text = text.removeprefix('那').removeprefix('么') if text.startswith('那') else text
    return re.fullmatch(
        r'(?:好[啊呀啦]?[，,]\s*)?'
        r'(?:(?:这碗(?:白饭|饭|米饭)|(?:这只|这个)?饭碗)[，,]?\s*我就?(?:收下|接下)|'
        r'我就?(?:收下|接下)(?:这碗(?:白饭|饭|米饭)|(?:这只|这个)?饭碗)?)'
        r'(?:了|啦)?[。！!]*',
        text,
    ) is not None


def handoff_behavior_distinctions(action_keys):
    children = ('offer_bowl', HOLDER_TRANSFER_ACTION, 'eat_rice')
    if action_keys.isdisjoint((*children, 'request_bowl')):
        return None
    return {
        '父类': '身体动作',
        '子项定义': {key: LABELS[key] for key in children},
        '归属变更条件': f'{HOLDER_TRANSFER_ACTION} 的完成回执确认同一只碗转给接碗者。',
    }


def card(value, *, snapshot=False):
    s=validate(value)
    request='无' if s['request'] is None else s['request']['actor_ref']+'向'+s['request']['target_ref']+'索要'
    offer='无' if s['offer'] is None else s['offer']['actor_ref']+'递向'+s['offer']['target_ref']+'，尚未接过'
    present=('采样时已有饭碗，由'+s['holder_ref']+'持有；'+f'还剩{s["portions"]}份。' if snapshot else
             '当前饭碗由'+s['holder_ref']+'持有；'+('还有饭。' if s['portions'] else '饭已吃完。'))
    return present+'索要：'+request+'；递出：'+offer+'。'


def placement_event_id(interaction_id):
    return 'meal:placement:'+interaction_id


def placement(value, *, from_button=False):
    from .events import Interaction
    s=validate(value)
    audience='两只角色' if s['version']==VERSION else '在场角色'
    text=('用户按下出饭按钮，出现一碗饭，由'+s['holder_ref']+'拿到，'
          if from_button else '用户通过“开饭”菜单给'+s['holder_ref']+'放了一碗饭，')
    text+=audience+'都看见了。'+card(s)
    return Interaction(content_id='meal:placement',semantic_key='place_meal',
        description=text,recipient_refs=tuple(s['participants']),
        parameters={'input_kind':'desktop_button' if from_button else 'context_menu','meal_scene':s,
            'definition':{'semantic_key':'place_meal','description':'用户放置饭碗，角色看到当前道具和持有者'}})
