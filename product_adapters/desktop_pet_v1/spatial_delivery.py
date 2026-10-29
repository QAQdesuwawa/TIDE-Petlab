"""A sealed bounded factual scene supplement to the existing Writer packet."""
import json,hashlib
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1 import spatial
VERSION='desktop-pet-scene-delivery-v2-optional-gaze'
INSTRUCTION='\n另含gaze_target字段：从现场卡列出的视线目标中选择看谁；front表示朝向屏幕前方。视线属于表情，由本地显示器落实。现场坐标是采样时的客观背景，移动行动仍沿本轮已选计划。'


def guidance(independent=True):
 return INSTRUCTION+('\ngaze_target也可为null，表示沿用视线；speech为空而选了视线时，单独显示表情。' if independent else '\n本轮身体行动与表情已在执行，新增视线随speech展示；安静时gaze_target填null。')

def reserve(scene,*,independent=True):return len(guidance(independent))+len('\n\n[当前桌面场景]\n')+len(spatial.card(scene))


def augment(delivery,scene,*,budget_chars=None,independent=True):
 scene=spatial.validate(scene);result=thaw_model_data(delivery)
 system=delivery['system']+guidance(independent)
 user=delivery['user']+'\n\n[当前桌面场景]\n'+spatial.card(scene)
 budget=delivery['budget_chars'] if budget_chars is None else budget_chars
 if len(system)+len(user)>budget:raise ValueError('scene and required Writer materials exceed prompt budget')
 result.update(system=system,user=user,budget_chars=budget,
  prompt_fingerprint=hashlib.sha256((system+'\0'+user).encode()).hexdigest(),
  scene_delivery={'version':VERSION,'scene_fingerprint':scene['fingerprint'],'base_delivery_fingerprint':delivery['fingerprint'],'independent':independent})
 result.pop('fingerprint',None);result['fingerprint']=fp(result)
 return result


def split_output(text,scene,*,independent=True):
 value=json.loads(text)
 if not isinstance(value,dict) or 'gaze_target' not in value:raise ValueError('scene expression requires gaze_target')
 target=value.pop('gaze_target')
 if target is not None:
  spatial.intent(scene,target)
  if not independent and value.get('speech') is None:raise ValueError('late gaze needs its declared speech surface')
 return json.dumps(value,ensure_ascii=False),target
