"""Observed window departure, not an inferred character intention."""
from .events import Interaction
from framework.model_api import model_data_fingerprint as fp

def departure(*,scene_ref,owner_ref,observers,at):
    if not scene_ref or not owner_ref or owner_ref in observers or not observers:raise ValueError('departure observers differ')
    return Interaction(content_id='departure:'+fp([scene_ref,owner_ref,at]),semantic_key='scene_departure',
        description=f'{owner_ref}的桌宠窗口已经关闭，角色从当前桌面场景中消失。离开原因未说明。',
        recipient_refs=tuple(observers),parameters={'input_kind':'scene_presence','departed_ref':owner_ref,
        'observed_at_ms':at,'scene_ref':scene_ref,'definition':{'semantic_key':'scene_departure',
        'description':'用户界面关闭造成的角色离场；只观察实际消失，不推定角色意愿或私有经历。'}})
