"""Objective desktop observations and gaze presentation; no psychological state."""
from collections.abc import Mapping
import json,math
from framework.model_api import model_data_fingerprint as fp,thaw_model_data
VERSION='desktop-pet-scene-v1'
PRESENTATION='desktop-pet-face-gaze-v2'
GAZE='desktop-pet-gaze-v1'


def _ref(v):
 if not isinstance(v,str) or not v or len(v)>128 or v.strip()!=v:raise ValueError('invalid scene reference')
 return v


def _bounds(v):
 if (not isinstance(v,(list,tuple)) or len(v)!=4 or any(type(x)is not int for x in v)
     or any(abs(x)>65536 for x in v[:2]) or any(not 0<x<=16384 for x in v[2:])):raise ValueError('invalid scene bounds')
 return list(v)


def create(*,owner_ref,source_ref,sampled_at_ms,viewport,participants):
 _ref(owner_ref);_ref(source_ref)
 if type(sampled_at_ms)is not int or sampled_at_ms<0:raise ValueError('invalid scene sample time')
 if not isinstance(participants,(list,tuple)) or not 1<=len(participants)<=2:raise ValueError('dual scene requires one or two visible participants')
 rows=[]
 for row in participants:
  if not isinstance(row,Mapping) or set(row)!={'ref','label','bounds'}:raise ValueError('scene is objective geometry only')
  if not isinstance(row['label'],str) or not row['label'] or len(row['label'])>32:raise ValueError('invalid scene label')
  rows.append({'ref':_ref(row['ref']),'label':row['label'],'bounds':_bounds(row['bounds'])})
 if len({r['ref'] for r in rows})!=len(rows) or owner_ref not in {r['ref'] for r in rows}:raise ValueError('scene owner/identity differs')
 rows.sort(key=lambda r:(r['ref']!=owner_ref,r['ref']))
 body={'version':VERSION,'owner_ref':owner_ref,'source_ref':source_ref,'sampled_at_ms':sampled_at_ms,
       'coordinate_space':'desktop_pixels_top_left','viewport':_bounds(viewport),'participants':rows}
 return {**body,'fingerprint':fp(body)}


def validate(value):
 if not isinstance(value,Mapping) or set(value)!={'version','owner_ref','source_ref','sampled_at_ms','coordinate_space','viewport','participants','fingerprint'}:raise ValueError('invalid scene fields')
 actual=create(**{k:value[k] for k in ('owner_ref','source_ref','sampled_at_ms','viewport','participants')})
 if thaw_model_data(value)!=actual:raise ValueError('scene fingerprint differs')
 return actual


def targets(scene):
 scene=validate(scene)
 return ('front',)+tuple(r['ref'] for r in scene['participants'] if r['ref']!=scene['owner_ref'])


def direction(scene,target):
 scene=validate(scene)
 if target not in targets(scene):raise ValueError('gaze target is not visible')
 if target=='front':return {'horizontal':0.0,'vertical':0.0,'elevation_degrees':0.0}
 owner=next(r for r in scene['participants'] if r['ref']==scene['owner_ref'])['bounds']
 peer=next(r for r in scene['participants'] if r['ref']==target)['bounds']
 # Both anchors are at the same normalized eye height, not a fabricated 3D position.
 dx=peer[0]+peer[2]*.5-owner[0]-owner[2]*.5
 dy=peer[1]+peer[3]*.38-owner[1]-owner[3]*.38
 distance=math.hypot(dx,dy)
 return {'horizontal':round(dx/max(distance,owner[2]),4),'vertical':round(dy/max(distance,owner[3]),4),
         'elevation_degrees':round(math.degrees(math.atan2(-dy,abs(dx))),2) if distance else 0.0}


def card(scene):
 scene=validate(scene)
 rows=[]
 for r in scene['participants']:
  row={'对象':r['ref'],'称呼':r['label'],'窗口左上及宽高':r['bounds']}
  if r['ref']!=scene['owner_ref']:
   d=direction(scene,r['ref']);row['相对自己']=('右' if d['horizontal']>0 else '左' if d['horizontal']<0 else '同列')+('上方' if d['vertical']<0 else '下方' if d['vertical']>0 else '侧')
  rows.append(row)
 return json.dumps({'现场':'桌面窗口当前采样，单位像素，横轴向右/纵轴向下','自己':scene['owner_ref'],
  '采样毫秒':scene['sampled_at_ms'],'可见对象':rows,'视线可选目标':list(targets(scene)),
  'front含义':'朝向屏幕前方'},ensure_ascii=False,separators=(',',':'))


def intent(scene,target):
 scene=validate(scene)
 if target not in targets(scene):raise ValueError('gaze target is not visible')
 return {'version':GAZE,'target_ref':target,'scene_fingerprint':scene['fingerprint'],'status':'planned'}


def presentation(expression,scene,target):
 from .face_expression import CODES
 if expression not in CODES:raise ValueError('unknown expression')
 return {'version':PRESENTATION,'expression':expression,'gaze':intent(scene,target)}


def validate_gaze(value):
 if (not isinstance(value,Mapping) or set(value)!={'version','target_ref','scene_fingerprint','status'}
     or value['version']!=GAZE or value['status'] not in {'planned','shown','unavailable'}):raise ValueError('invalid gaze presentation')
 _ref(value['target_ref'])
 digest=value['scene_fingerprint']
 if not isinstance(digest,str) or len(digest)!=64 or any(c not in '0123456789abcdef' for c in digest):raise ValueError('gaze scene source missing')
 return thaw_model_data(value)


def resolve(presentation,scene,*,renderable):
 gaze=validate_gaze(presentation['gaze'])
 if gaze['status']!='planned' or type(renderable)is not bool:raise ValueError('gaze must be a planned presentation')
 available=renderable and scene is not None and gaze['target_ref'] in targets(scene)
 return {**gaze,'status':'shown' if available else 'unavailable'}


def realized_presentation(presentation,result):
 planned=validate_gaze(presentation['gaze']);actual=validate_gaze(result)
 if planned['status']!='planned' or actual['status']=='planned' or any(planned[k]!=actual[k] for k in ('version','target_ref','scene_fingerprint')):raise ValueError('gaze actual result differs from planned source')
 return {**thaw_model_data(presentation),'gaze':actual}


def realized_content(content,result):
 from dataclasses import replace
 from .face_expression import LABELS
 p=realized_presentation(content.parameters['presentation'],result)
 text=content.description
 if content.kind=='expression':
  text='呈现'+LABELS[p['expression']]+'的表情。'
  if p['gaze']['status']=='shown':text+=('视线朝向屏幕前方。' if p['gaze']['target_ref']=='front' else '视线看向'+p['gaze']['target_ref']+'。')
 return replace(content,description=text,parameters={'presentation':p})


def realized_contents(contents,outcome):
 from dataclasses import replace
 results=outcome.details.get('gaze_results',{})
 result=[]
 for c in contents:
  p=c.parameters.get('presentation',{})
  if p.get('version')==PRESENTATION:
   c=realized_content(c,results.get(c.content_id))
  result.append(c)
 return tuple(result)
