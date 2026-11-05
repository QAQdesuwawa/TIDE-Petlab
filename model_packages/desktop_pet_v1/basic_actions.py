"""Finite whole-pose actions, offered by execution capability, not mood."""
from copy import deepcopy
KEYS=('head_bow','head_tilt','turn_left','turn_right','nod','shake_head','wave_hand','beckon','lie_down','stand_up')
DESCRIPTIONS={
 'head_bow':'轻轻低头看向下方，再自然抬起头',
 'head_tilt':'轻轻歪头停留一下，再回正',
 'turn_left':'双脚留在原地，身体转向屏幕左侧',
 'turn_right':'双脚留在原地，身体转向屏幕右侧',
 'nod':'轻轻点头',
 'shake_head':'轻轻摇头',
 'wave_hand':'抬手挥一挥',
 'beckon':'伸手招一招',
 'lie_down':'慢慢侧躺下来休息，保持躺姿',
 'stand_up':'从休息姿态起身站好'}

def catalog(base,*,meal=None,owner=None):
    from .action_candidates import CATALOG
    from framework.model_api import thaw_model_data
    value=deepcopy(thaw_model_data(CATALOG if base is None else base))
    # These particular whole-pose assets have empty hands. No prop is hidden.
    if meal is not None and meal.get('holder_ref')==owner and meal.get('portions',0):return value
    known={r['key'] for r in value['candidates']}
    value['candidates'].extend({'key':k,'description':DESCRIPTIONS[k]} for k in KEYS if k not in known)
    value['version']='desktop-pet-basic-actions-v1'
    return value
