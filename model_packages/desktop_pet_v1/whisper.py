"""Physical prerequisites for an explicit private NPC destination, not a choice model."""
from dataclasses import replace
from framework.model_api import thaw_model_data,model_data_fingerprint as fp
from . import spatial,movement
from .composite import CompositePlan
VERSION='desktop-pet-whisper-v1'
APPROACH='whisper:approach'

def context(scene,target,audience):
 s=spatial.validate(scene)
 if target=='front' or target not in spatial.targets(s):raise ValueError('whisper target not present')
 if target not in audience or s['owner_ref'] in audience or len(set(audience))!=len(audience):raise ValueError('whisper audience differs')
 body={'version':VERSION,'owner_ref':s['owner_ref'],'target_ref':target,'public_audience':list(audience),
       'scene_fingerprint':s['fingerprint'],'movement':movement.arrival_definition()}
 return {**body,'fingerprint':fp(body)}

def validate(ctx):
 c=thaw_model_data(ctx)
 if c.get('version')!=VERSION or c.get('fingerprint')!=fp({k:v for k,v in c.items() if k!='fingerprint'}):raise ValueError('whisper context changed')
 if c['movement']!=movement.arrival_definition():raise ValueError('whisper movement differs')
 return c

def compose(contents,children,ctx):
 c=validate(ctx);contents=tuple(contents)
 if not any(x.kind=='speech' for x in contents):
  return contents,CompositePlan.create(children) if children else None
 if any(x.content_id==APPROACH for x in contents):raise ValueError('whisper prerequisite duplicated')
 public=tuple(c['public_audience']);target=c['target_ref']
 prep=replace(movement.content(target,{'key':'move_near','description':'走近交谈对象','motion':c['movement']}),content_id=APPROACH,recipient_refs=public)
 prep=replace(prep,parameters={**dict(prep.parameters),'whisper_context':c})
 rows={key:{**thaw_model_data(row),'after':list(row.get('after',()))} for key,row in children.items()}
 # Existing body children run after arrival too, preserving their own order.
 for item in contents:
  if item.kind=='speech' and item.recipient_refs!=(target,):raise ValueError('whisper speech must have exactly one recipient')
  row=rows.setdefault(item.content_id,{'kind':item.kind,'key':item.semantic_key if item.kind=='action' else 'selected_presentation' if item.kind=='expression' else 'reply' if item.kind=='speech' else 'inner_monologue','after':[]})
  if item.kind in {'speech','action'} and not row['after']:row['after']=[APPROACH]
 rows[APPROACH]={'kind':'action','key':'move_near','after':[]}
 return (prep,*contents),CompositePlan.create(rows)

def check_arrival(scene,ctx):
 c=validate(ctx);s=spatial.validate(scene)
 if s['owner_ref']!=c['owner_ref']:raise ValueError('whisper geometry owner differs')
 # Two-pixel raster rounding tolerance, not psychological distance or audibility.
 plan=movement.plan(s,{**c['movement'],'target_ref':c['target_ref']})
 distance=spatial.separation(s,c['target_ref'])
 if plan['distance_px']>2 or distance['window_gap_px']>c['movement']['gap_px']+2 or distance['floor_difference_px']>2:
  raise ValueError('whisper target is not alongside')
 return {'scene_fingerprint':s['fingerprint'],'remaining_px':plan['distance_px']}

def wrap(payload,control,ctx):
 from .events import Content
 base=thaw_model_data(payload)
 children=base.get('action_sequence',{}).get('children',control.get('composite',{}).get('children',{}))
 items,plan=compose(tuple(Content.from_record(r) for r in base['contents']),children,ctx)
 return {**base,'contents':[r.to_record() for r in items],
         'whisper_plan':None if plan is None else thaw_model_data(plan.children)}

def unwrap(payload):
 return {k:([r for r in v if r['content_id']!=APPROACH] if k=='contents' else v)
         for k,v in thaw_model_data(payload).items() if k!='whisper_plan'}

def verify(request,payload):
 if request.control.get('whisper_contract')!=VERSION:raise ValueError('unknown whisper execution contract')
 ctx=validate(request.readonly_facts['whisper_context'])
 if ctx['owner_ref']!=request.subject_ref or ctx['target_ref']!=request.target_ref:raise ValueError('whisper authorization target differs')
 if ctx['scene_fingerprint']!=request.readonly_facts['scene_context']['fingerprint']:raise ValueError('whisper scene differs from sealed request')
 base=unwrap(payload)
 if request.control.get('action_sequence_contract'):
  from .action_sequence import validate_candidate
  validate_candidate(request,base)
 else:
  from collections import Counter
  if Counter(x['semantic_key'] for x in base['contents'] if x['kind']=='action')!=Counter(request.control.get('selected_action_keys',())):
   raise ValueError('whisper contains an unselected body action')
 if wrap(base,request.control,ctx)!=thaw_model_data(payload):raise ValueError('whisper prerequisite differs from sealed context')
 return CompositePlan.create(payload['whisper_plan']) if payload['whisper_plan'] else None
