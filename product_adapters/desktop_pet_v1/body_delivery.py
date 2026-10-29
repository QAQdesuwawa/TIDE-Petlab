"""Bounded readonly selected-body context; this does not select the body."""
import hashlib
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1 import shared_meal as m

VERSION='desktop-pet-body-delivery-v2'


def plan_fields(context, *, owner_ref):
    """Project the same sealed selection before the form decides 'no body'."""
    c=thaw_model_data(context)
    if (not isinstance(c,dict) or c.get('version')!=m.VERSION
            or c.get('actor_ref')!=owner_ref or not c.get('selection_ref')):
        raise ValueError('selected body source/owner differs')
    s=m.validate(c['scene'])
    if (c['key'] not in m.available(s,c['actor_ref'],c['target_ref'])
            or c['description']!=m.LABELS[c['key']]):
        raise ValueError('selected body identity or description differs')
    return {'本轮已选行动计划':{'角色':c['actor_ref'],'对象':c['target_ref'],
            '动作':c['description']},
        '计划含义':'同一父反应中已选中的身体子项；实际完成和对方是否承接以各自执行记录为准。'}


def augment(delivery,context,*,budget_chars,include_scene=True):
    c=thaw_model_data(context);s=m.validate(c['scene'])
    if c['key'] not in m.available(s,c['actor_ref'],c['target_ref']):raise ValueError('body context identity differs')
    text='\n\n【本轮身体行动与共同道具】\n'+(m.card(s)+'\n' if include_scene else '')+c['actor_ref']+'本轮已由符号层选择：'+c['description']+'；对象：'+c['target_ref']+'。话语可与行动配合。实际完成和对方是否接过由各自执行回执确认。'
    if len(text)>600:raise ValueError('body context exceeds bounded description')
    out=thaw_model_data(delivery);out['user']+=text
    if len(out['system'])+len(out['user'])>budget_chars:raise ValueError('selected body context exceeds Writer budget')
    out['body_context_fingerprint']=fp(c)
    out['prompt_fingerprint']=hashlib.sha256((out['system']+'\0'+out['user']).encode()).hexdigest()
    out['fingerprint']=fp({k:v for k,v in out.items() if k!='fingerprint'})
    return out
