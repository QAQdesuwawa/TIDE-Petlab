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
 expected=definition(**{k:spec[k] for k in ('duration_ms','gap_px','max_distance_px')})
 if spec.get('version') in (ARRIVAL_VERSION,STEP_VERSION):expected['version']=spec['version']
 if spec!=expected:raise ValueError('movement definition differs')
 return spec


def content(target_ref,selected):
 from .events import Action
 from .behavior import VERSION as BEHAVIOR
 spec=thaw_model_data(selected.get('motion'))
 validate_definition(spec)
 if selected['key']!=KEY:raise ValueError('movement entity key differs')
 spatial._ref(target_ref)
 return Action(content_id='activity',description=selected['description'],semantic_key=KEY,recipient_refs=(target_ref,),
  parameters={'behavior_version':BEHAVIOR,'movement':{**spec,'target_ref':target_ref}})


def plan(scene,parameters):
 s=spatial.validate(scene);p=thaw_model_data(parameters)
 if not isinstance(p,dict) or set(p)!=set(definition())|{'target_ref'}:raise ValueError('movement fields differ')
 validate_definition({k:v for k,v in p.items() if k!='target_ref'})
 if p['target_ref']=='front' or p['target_ref'] not in spatial.targets(s):raise ValueError('movement target not visible')
 own=next(r['bounds'] for r in s['participants'] if r['ref']==s['owner_ref']);peer=next(r['bounds'] for r in s['participants'] if r['ref']==p['target_ref'])
 # Go to the currently nearer side, without choosing a new recipient/relationship.
 # Registered whole-pose canvases include transparent side margins. New
 # small-step approaches align the bodies/hands, not the canvas borders.
 inset=.22*(own[2]+peer[2]) if p['version']==STEP_VERSION else 0
 left=peer[0]-own[2]-p['gap_px']+inset;right=peer[0]+peer[2]+p['gap_px']-inset
 x=min((left,right),key=lambda x:abs(x-own[0]));y=peer[1]+peer[3]-own[3]
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


def realized(content,actual,*,status,reason=None):
 if (not isinstance(actual,Mapping) or set(actual)!={'version','target_ref','scene_fingerprint','start','end','distance_px'}
     or actual['version']!=RESULT or actual['target_ref']!=content.parameters['movement']['target_ref']):raise ValueError('movement result differs from selected action')
 spatial._ref(actual['scene_fingerprint'])
 for point in ('start','end'):
  if len(actual[point])!=2 or any(type(v)is not int for v in actual[point]):raise ValueError('invalid actual movement point')
 if abs(actual['distance_px']-math.dist(actual['start'],actual['end']))>.001:raise ValueError('movement displacement differs')
 text='从桌面坐标'+str(list(actual['start']))+'移动到'+str(list(actual['end']))+'，朝向'+actual['target_ref']+'当时所在的位置。'
 if status=='started':text='开始靠近'+actual['target_ref']+'，从桌面坐标'+str(list(actual['start']))+'出发。'
 elif status=='interrupted':text+='中途停止。'+{'user_contact':'用户接触中止了移动。','source_expired':'现场条件已变化。','technical_failure':'执行发生故障。','window_closed':'窗口已关闭。'}.get(reason,'')
 elif status!='completed':raise ValueError('invalid actual movement status')
 return replace(content,description=text,parameters={**dict(content.parameters),'movement_result':thaw_model_data(actual)})


def catalog(base=None, *, scene=None, target_ref=None):
 """Bind a physical approach entity to the original selected relationship.

 Only availability is geometric. Published tokens and original eligibility
 still decide whether this entity is chosen; no Writer or new score is used.
 Existing explicit catalogs and frozen selections retain their definitions.
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
