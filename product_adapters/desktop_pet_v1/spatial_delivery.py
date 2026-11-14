"""A sealed bounded factual scene supplement to the existing Writer packet."""
import json,hashlib
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
from model_packages.desktop_pet_v1 import spatial
LEGACY_VERSION='desktop-pet-scene-delivery-v3-distances'
VERSION='desktop-pet-scene-delivery-v4-optional-gaze'
INSTRUCTION='\n另含gaze_target字段：从现场卡列出的视线目标中选择看谁；front表示朝向屏幕前方。视线属于表情，由本地显示器落实。现场坐标是采样时的客观背景，移动行动仍沿本轮已选计划。'


def guidance(independent=True, *, version=VERSION, concise=False):
 if concise:
  return ('\ngaze_target可填现场的视线目标，front表示看向屏幕前方。省略或填null沿用视线。'
          + ('' if independent else '本轮新视线随话语呈现。'))
 if version==LEGACY_VERSION:
  return INSTRUCTION+('\ngaze_target也可为null，表示沿用视线；speech为空而选了视线时，单独显示表情。' if independent else '\n本轮身体行动与表情已在执行，新增视线随speech展示；安静时gaze_target填null。')
 if version!=VERSION:raise ValueError('unknown scene delivery version')
 return INSTRUCTION+('\ngaze_target是可选表情字段，省略或填null均沿用视线。speech为空而选了视线时，单独显示表情。' if independent else '\ngaze_target是可选表情字段，省略或填null均沿用视线；新增视线随speech展示。')


def _scene_text(scene,*,concise=False):
 card=spatial.card(scene,include_distances=True)
 if concise:
  view=json.loads(card);view.pop('采样毫秒',None)
  card=json.dumps(view,ensure_ascii=False,separators=(',',':'))
 text='\n\n[当前桌面场景]\n'+card
 visible=[{'对象':o['ref'],'可见窗口标题':o['label']} for o in scene.get('objects',())
          if o['ref'].startswith(spatial.SCREEN_PREFIX)]
 if visible:
  text+='\n\n[当前环境 · 屏幕]\n'+json.dumps(visible,ensure_ascii=False,separators=(',',':'))
 return text


def reserve(scene,*,independent=True,version=VERSION,concise=False):return len(guidance(independent,version=version,concise=concise))+len(_scene_text(scene,concise=concise))


def augment(delivery,scene,*,budget_chars=None,independent=True,version=VERSION,concise=False):
 scene=spatial.validate(scene);result=thaw_model_data(delivery)
 system=delivery['system']+guidance(independent,version=version,concise=concise)
 user=delivery['user']+_scene_text(scene,concise=concise)
 budget=delivery['budget_chars'] if budget_chars is None else budget_chars
 if len(system)+len(user)>budget:raise ValueError('scene and required Writer materials exceed prompt budget')
 result.update(system=system,user=user,budget_chars=budget,
  prompt_fingerprint=hashlib.sha256((system+'\0'+user).encode()).hexdigest(),
  scene_delivery={'version':version,'scene_fingerprint':scene['fingerprint'],'base_delivery_fingerprint':delivery['fingerprint'],'independent':independent})
 result.pop('fingerprint',None);result['fingerprint']=fp(result)
 return result


def split_output(text,scene,*,independent=True,human_ref=None):
 value=json.loads(text)
 if not isinstance(value,dict):raise ValueError('scene expression requires an object')
 target=value.pop('gaze_target',None)
 # The registered human is outside the desktop sprite scene. Preserve the
 # chosen person, resolving only its display direction to the front anchor.
 if human_ref is not None and target==human_ref:target='front'
 if target is not None:
  spatial.intent(scene,target)
  if not independent and value.get('speech') is None:raise ValueError('late gaze needs its declared speech surface')
 return json.dumps(value,ensure_ascii=False),target
