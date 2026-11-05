"""Observed drag contact subtype carried by the existing human input contract."""
from .events import Interaction
from framework.model_api import model_data_fingerprint as fp

VERSION='desktop-pet-drag-contact-v1'

def contact_content(*,gesture_id,episode,moved_ref,other_ref,started_at,ended_at,position):
 if not isinstance(gesture_id,str) or not gesture_id.startswith('gesture:'):raise ValueError('contact gesture source missing')
 if type(episode)is not int or episode<1:raise ValueError('invalid contact episode')
 if any(not isinstance(x,str) or not x for x in (moved_ref,other_ref)) or moved_ref==other_ref:raise ValueError('distinct contact participants required')
 if any(type(t)is not int or t<0 for t in (started_at,ended_at)) or ended_at<started_at:raise ValueError('contact time moved backwards')
 if not isinstance(position,(tuple,list)) or len(position)!=2 or any(type(x)is not int for x in position):raise ValueError('actual position required')
 identity='contact:'+fp({'gesture_id':gesture_id,'episode':episode,'moved_ref':moved_ref,'other_ref':other_ref})
 text=f'用户拖动{moved_ref}时，在{other_ref}占据的身体边界受到阻挡；角色停在实际位置{list(position)}。本段接触持续{ended_at-started_at}毫秒。'
 return Interaction(content_id=identity,semantic_key='drag_contact',description=text,recipient_refs=(moved_ref,other_ref),parameters={
  'input_kind':'physical_contact','contact_version':VERSION,'gesture_id':gesture_id,'episode':episode,
  'moved_ref':moved_ref,'other_ref':other_ref,'started_at_ms':started_at,'ended_at_ms':ended_at,'position':list(position),
  'measurement_scope':'本段新增的角色间边界接触；拖动动作本身由原鼠标事件承担，两个角色的影响分别量测。',
  'definition':{'semantic_key':'drag_contact','description':'用户拖动造成两角色边界接触，包含实际阻挡事实及双方身份'}})
