"""Current visible object facts, independent of action selection or recall."""
import hashlib
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1 import shared_meal


def text(context):
    value='\n\n【眼前的饭碗】\n'+shared_meal.card(context)
    if len(value)>400:raise ValueError('meal scene description exceeds bounded context')
    return value


def reserve(context):return 0 if context is None else len(text(context))


def augment(delivery,context,*,budget_chars):
    out=thaw_model_data(delivery);out['user']+=text(context)
    if len(out['system'])+len(out['user'])>budget_chars:raise ValueError('meal scene exceeds Writer budget')
    out['meal_scene_fingerprint']=shared_meal.validate(context)['fingerprint']
    out['prompt_fingerprint']=hashlib.sha256((out['system']+'\0'+out['user']).encode()).hexdigest()
    out['fingerprint']=fp({k:v for k,v in out.items() if k!='fingerprint'})
    return out
