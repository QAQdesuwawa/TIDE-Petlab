"""A finite movement action entity and factual geometry, not an eligibility rule."""
import math
from collections.abc import Mapping
from dataclasses import replace
from framework.model_api import thaw_model_data
from . import spatial
VERSION='desktop-pet-movement-v1'
ARRIVAL_VERSION='desktop-pet-movement-arrival-v2'
STEP_VERSION='desktop-pet-movement-steps-v3'
RESULT='desktop-pet-movement-result-v1'
KEY='move_near'
KEYS=('move_near','stroll','move_away','relocate')
FREE_VERSION='desktop-pet-movement-basic-v4'
LABELS={'stroll':'散步一小段，在附近空处停下','move_away':'走开一些，与指定对象拉开距离','relocate':'换一个空位置待着'}


def definition(*,duration_ms=800,gap_px=24,max_distance_px=480):
 if type(duration_ms)is not int or not 100<=duration_ms<=3000:raise ValueError('invalid movement duration')
 if type(gap_px)is not int or not 0<=gap_px<=512:raise ValueError('invalid movement gap')
 if type(max_distance_px)is not int or not 1<=max_distance_px<=2048:raise ValueError('invalid movement distance')
 return {'version':VERSION,'kind':KEY,'target':'authorized_target','duration_ms':duration_ms,'gap_px':gap_px,'max_distance_px':max_distance_px}


def arrival_definition():
 # Physical travel speed only; eligibility and model clocks are unchanged.
 return {**definition(max_distance_px=144),'version':STEP_VERSION}


def validate_definition(spec):
 if not isinstance(spec,dict):raise ValueError('movement definition missing')
 if spec.get('version')==FREE_VERSION:
  if spec.get('mode') not in LABELS or spec!={**arrival_definition(),'version':FREE_VERSION,'mode':spec['mode']}:raise ValueError('invalid basic movement')
  return spec
 expected=definition(**{k:spec[k] for k in ('duration_ms','gap_px','max_distance_px')})
 if spec.get('version') in (ARRIVAL_VERSION,STEP_VERSION):expected['version']=spec['version']
 if spec!=expected:raise ValueError('movement definition differs')
 return spec


def content(target_ref,selected):
 from .events import Action
 from .behavior import VERSION as BEHAVIOR
 spec=thaw_model_data(selected.get('motion'))
 validate_definition(spec)
 if selected['key'] not in KEYS:raise ValueError('movement entity key differs')
 if selected['key']!=(spec['mode'] if spec['version']==FREE_VERSION else KEY):raise ValueError('movement mode differs from selected entity')
 spatial._ref(target_ref)
 obj=spatial.world_object(selected['world_object']) if 'world_object' in selected else None
 if target_ref.startswith('world:')!=(obj is not None) or obj is not None and obj['ref']!=target_ref:
  raise ValueError('movement world target differs from bound object')
 return Action(content_id='activity',description=selected['description'],semantic_key=selected['key'],recipient_refs=(target_ref,),
  parameters={'behavior_version':BEHAVIOR,'movement':{**spec,'target_ref':target_ref},
              **({'world_object':obj} if obj is not None else {})})


def target_bounds(scene, target):
 for row in scene['participants']:
  if row['ref']==target and target!=scene['owner_ref']:return row['bounds']
 for obj in scene.get('objects',()):
  if obj['ref']==target:return obj.get('bounds',[*obj['point'],1,1])
 return None


def _object_destination(scene, own, obj, gap, reserved, reachable):
 from .physical_space import body,overlaps,position_clear
 x,y=obj['point']
 w,h=own[2:]
 vx,vy,vw,vh=scene['viewport']
 if not (vx<=x<vx+vw and vy<=y<vy+vh):raise ValueError('world object outside desktop viewport')
 spots=((x-w-gap,y-h+1),(x+gap+1,y-h+1),(x-w//2,y-h-gap),(x-w//2,y+gap+1))
 if 'bounds' in obj:
  ox,oy,ow,oh=obj['bounds']
  l,t,r,b=body([0,0,w,h])
  # Keep side arrivals clear of a centered arrival even for a narrow prop.
  envelopes=[body(row['bounds']) for row in scene['participants']]
  half_width=max((right-left)/2 for left,_,right,_ in envelopes)
  left=math.floor(min(ox,x-half_width)-r-gap)
  right=math.ceil(max(ox+ow,x+half_width)-l+gap)
  spots=((left,round(y-b)),(right,round(y-b)),
         (x-w//2,math.floor(oy-b-gap)),(x-w//2,math.ceil(oy+oh-t+gap)))
 valid=[spot for spot in spots if vx<=spot[0]<=vx+vw-w and vy<=spot[1]<=vy+vh-h
        and position_clear(scene,[*spot,w,h])
        and not any(overlaps([*spot,w,h],landing) for landing in reserved)]
 for spot in sorted(valid,key=lambda spot:math.dist(own[:2],spot)):
  if reachable is None or reachable(own[:2],spot):return spot
 raise ValueError('no free space near world object')


def plan(scene,parameters,*,reserved=(),reachable=None,alongside_only=False):
 if parameters.get('version')==FREE_VERSION:return basic_plan(scene,parameters)
 s=spatial.validate(scene);p=thaw_model_data(parameters)
 if not isinstance(p,dict) or set(p)!=set(definition())|{'target_ref'}:raise ValueError('movement fields differ')
 validate_definition({k:v for k,v in p.items() if k!='target_ref'})
 target=p['target_ref']
 peer=target_bounds(s,target)
 if peer is None:raise ValueError('movement target not visible')
 own=next(r['bounds'] for r in s['participants'] if r['ref']==s['owner_ref'])
 if target.startswith('world:'):
  obj=next(obj for obj in s['objects'] if obj['ref']==target)
  end=_object_destination(s,own,obj,p['gap_px'],reserved,reachable)
  distance=math.dist(own[:2],end)
  return {'version':STEP_VERSION,'target_ref':target,'scene_fingerprint':s['fingerprint'],
   'start':own[:2],'end':list(end),'duration_ms':max(p['duration_ms'],math.ceil(distance/p['max_distance_px']*p['duration_ms'])),
   'distance_px':round(distance,3)}
 # Go to the currently nearer side, without choosing a new recipient/relationship.
 # Registered whole-pose canvases include transparent side margins. New
 # small-step approaches align the bodies/hands, not the canvas borders.
 inset=.22*(own[2]+peer[2]) if p['version']==STEP_VERSION else 0
 left=peer[0]-own[2]-p['gap_px']+inset;right=peer[0]+peer[2]+p['gap_px']-inset
 x=min((left,right),key=lambda x:abs(x-own[0]));y=peer[1]+peer[3]-own[3]
 if p['version']==STEP_VERSION:
  from .physical_space import overlaps,position_clear
  vx,vy,vw,vh=s['viewport']
  side_y=max(vy,min(vy+max(0,vh-own[3]),y))
  sides=[(max(vx,min(vx+max(0,vw-own[2]),v)),side_y) for v in (left,right)]
  center=peer[0]+(peer[2]-own[2])/2
  vertical=[] if alongside_only else [
   (center,peer[1]-own[3]-p['gap_px']),(center,peer[1]+peer[3]+p['gap_px'])]
  for candidates in (sides,vertical):
   for a,b in sorted(candidates,key=lambda point:math.dist(own[:2],point)):
    spot=(round(a),round(b))
    landing=[*spot,*own[2:]]
    if (vx<=spot[0]<=vx+vw-own[2] and vy<=spot[1]<=vy+vh-own[3]
        and position_clear(s,landing) and not any(overlaps(landing,r) for r in reserved)
        and (reachable is None or reachable(own[:2],spot))):
     x,y=spot
     break
   else:continue
   break
  else:
   if reachable is not None:raise ValueError('no free route near participant')
 vx,vy,vw,vh=s['viewport'];x=max(vx,min(vx+max(0,vw-own[2]),x));y=max(vy,min(vy+max(0,vh-own[3]),y))
 dx,dy=x-own[0],y-own[1];distance=math.hypot(dx,dy)
 arrival=p['version'] in (ARRIVAL_VERSION,STEP_VERSION)
 scale=(1 if arrival else min(1,p['max_distance_px']/distance)) if distance else 0
 end=[round(own[0]+dx*scale),round(own[1]+dy*scale)]
 return {'version':STEP_VERSION if p['version']==STEP_VERSION else VERSION,'target_ref':p['target_ref'],'scene_fingerprint':s['fingerprint'],
  'start':own[:2],'end':end,'duration_ms':max(p['duration_ms'],math.ceil(distance/p['max_distance_px']*p['duration_ms'])) if arrival else p['duration_ms'],'distance_px':round(math.dist(own[:2],end),3)}


def position(planned,fraction):
 if not isinstance(fraction,(int,float)) or not math.isfinite(fraction):raise ValueError('invalid movement phase')
 t=max(0,min(1,fraction))
 if planned['version']==STEP_VERSION:
  # Short acceleration/landing, steady travel between them. Whole-route
  # smoothstep used to make the middle 50% faster than the authored speed.
  ramp=.1
  t=(t*t/(2*ramp) if t<ramp else (1-ramp-(1-t)**2/(2*ramp) if t>1-ramp else t-ramp/2))/(1-ramp)
 else:t=t*t*(3-2*t)
 return [round(a+(b-a)*t) for a,b in zip(planned['start'],planned['end'])]


def result(planned,end):
 if not isinstance(end,(tuple,list)) or len(end)!=2 or any(type(v)is not int for v in end):raise ValueError('actual movement coordinates missing')
 return {'version':RESULT,'target_ref':planned['target_ref'],'scene_fingerprint':planned['scene_fingerprint'],
  'start':list(planned['start']),'end':list(end),'distance_px':round(math.dist(planned['start'],end),3)}


def realized(content,actual,*,status,reason=None,cause_ref=None):
 if (not isinstance(actual,Mapping) or set(actual)!={'version','target_ref','scene_fingerprint','start','end','distance_px'}
     or actual['version']!=RESULT or actual['target_ref']!=content.parameters['movement']['target_ref']):raise ValueError('movement result differs from selected action')
 spatial._ref(actual['scene_fingerprint'])
 for point in ('start','end'):
  if len(actual[point])!=2 or any(type(v)is not int for v in actual[point]):raise ValueError('invalid actual movement point')
 if abs(actual['distance_px']-math.dist(actual['start'],actual['end']))>.001:raise ValueError('movement displacement differs')
 text='从桌面坐标'+str(list(actual['start']))+'移动到'+str(list(actual['end']))+'，朝向'+actual['target_ref']+'当时所在的位置。'
 if content.semantic_key in LABELS:
  text=LABELS[content.semantic_key]+'：从桌面坐标'+str(list(actual['start']))+'移动到'+str(list(actual['end']))+'。'
 if status=='started':text=('开始'+LABELS[content.semantic_key]+'，从桌面坐标'+str(list(actual['start']))+'出发。') if content.semantic_key in LABELS else '开始靠近'+actual['target_ref']+'，从桌面坐标'+str(list(actual['start']))+'出发。'
 elif status=='interrupted':text+='中途停止。'+{'user_contact':'用户接触中止了移动。','source_expired':'现场条件已变化。','technical_failure':'执行发生故障。','window_closed':'窗口已关闭。'}.get(reason,'')
 elif status!='completed':raise ValueError('invalid actual movement status')
 if actual['target_ref'].startswith('world:'):
  label=content.parameters.get('world_object',{}).get('label',actual['target_ref'])
  text=('开始走向公共物件「'+label+'」。' if status=='started' else
        '从桌面坐标'+str(list(actual['start']))+'移动到'+str(list(actual['end']))+
        '，目标为公共物件「'+label+'」。'+('中途停止。' if status=='interrupted' else ''))
 if reason=='physical_contact':text+='移动路径被其他角色占据，停止靠近。阻挡对象：'+str(cause_ref)+'。'
 return replace(content,description=text,parameters={**dict(content.parameters),'movement_result':thaw_model_data(actual)})


def catalog(base=None, *, scene=None, target_ref=None):
 """Bind a physical approach entity to the original selected relationship.

 Availability is geometric. New v2 offers let the model choose intent; token
 conditions below are retained only for old v1 sealed selection replay.
 """
 from copy import deepcopy
 from .action_candidates import CATALOG
 value=deepcopy(thaw_model_data(CATALOG if base is None else base))
 if any(c['key']==KEY for c in value['candidates']):return value
 if scene is None:return value
 current=spatial.validate(scene)
 if target_ref=='front' or target_ref not in spatial.targets(current):return value
 spec=arrival_definition()
 if plan(current,{**spec,'target_ref':target_ref})['distance_px']==0:return value
 value={**value,'version':'desktop-pet-spatial-action-catalog-v1',
        'base_catalog_version':value['version']}
 value['candidates'].append({'key':KEY,'description':'走近当前交谈对象，在对方身旁停下',
  'motion':spec,
  'all':[{'kind':'phase','var':'V_attr','level':'mid'},
         {'kind':'phase','var':'V_rep','level':'low'}],
  'any':[{'kind':'self','text':'[行为高度同向]'},
         {'kind':'self','text':'[行为中等同向]'}],
  'none':[{'kind':'pain'}]})
 return value


def basic_plan(scene,parameters):
 s=spatial.validate(scene);p=thaw_model_data(parameters)
 validate_definition({k:v for k,v in p.items() if k!='target_ref'})
 from .physical_space import position_clear
 own=next(r['bounds'] for r in s['participants'] if r['ref']==s['owner_ref'])
 others=[r for r in s['participants'] if r['ref']!=s['owner_ref']]
 target=next((r['bounds'] for r in others if r['ref']==p['target_ref']),None)
 if p['mode']=='move_away' and target is None:raise ValueError('departure target not visible')
 vx,vy,vw,vh=s['viewport'];points=[]
 if vw<own[2] or vh<own[3]:raise ValueError('viewport too small for movement')
 radius=140 if p['mode']=='stroll' else 260
 for dx,dy in ((1,0),(-1,0),(0,1),(0,-1),(1,1),(-1,1),(1,-1),(-1,-1)):
  x=round(max(vx,min(vx+vw-own[2],own[0]+dx*radius)))
  y=round(max(vy,min(vy+vh-own[3],own[1]+dy*radius)))
  if math.dist(own[:2],[x,y])<40:continue
  if not position_clear(s,[x,y,*own[2:]]):continue
  if p['mode']=='move_away' and math.dist([x,y],target[:2])<=math.dist(own[:2],target[:2])+30:continue
  points.append([x,y])
 if not points:raise ValueError('no free movement destination')
 if p['mode']=='move_away':end=max(points,key=lambda q:math.dist(q,target[:2]))
 elif p['mode']=='relocate':end=max(points,key=lambda q:min([math.dist(q,o['bounds'][:2]) for o in others] or [math.dist(q,own[:2])]))
 else:end=min(points,key=lambda q:abs(math.dist(q,own[:2])-radius))
 distance=math.dist(own[:2],end)
 return {'version':STEP_VERSION,'target_ref':p['target_ref'],'scene_fingerprint':s['fingerprint'],
  'start':own[:2],'end':end,'duration_ms':max(800,math.ceil(distance/180*1000)),'distance_px':round(distance,3)}


def basic_catalog(base,*,scene,target_ref):
 from copy import deepcopy
 value=deepcopy(thaw_model_data(base))
 if scene is None:return value
 for key,label in LABELS.items():
  if any(c['key']==key for c in value['candidates']):continue
  motion={**arrival_definition(),'version':FREE_VERSION,'mode':key}
  try:basic_plan(scene,{**motion,'target_ref':target_ref})
  except ValueError:continue
  value['candidates'].append({'key':key,'description':label+('（对象：'+target_ref+'）' if key=='move_away' else ''),'motion':motion})
 return value
