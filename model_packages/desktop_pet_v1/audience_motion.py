"""Compile selected restricted speech into physical prerequisites, without inference."""
from dataclasses import replace
from copy import deepcopy
from framework.model_api import thaw_model_data
from . import audience,spatial,movement,whisper
from .composite import CompositePlan

PREFIX='audience:approach:'
VERSION='desktop-pet-audience-motion-v1'

def compile(contents,children,*,choice,bound,scene,reuse_selected=False,single_target_only=False):
 b=audience.validate(bound);refs=audience.select(choice,b)
 contents=audience.apply(tuple(contents),choice,b)
 if set(refs)==set(b['physical_observers']) or not refs:return contents,children
 # Audience permissions do not specify a multi-stop movement itinerary.
 if single_target_only and len(refs)!=1:return contents,children
 s=spatial.validate(scene)
 if s['owner_ref']!=b['owner']:raise ValueError('private approach owner differs')
 # Humans have no desktop avatar. Every selected on-screen listener is a real
 # geometry prerequisite; exclusions remain permissions, not sound simulation.
 targets=[r for r in refs if r in spatial.targets(s)]
 if not targets:return contents,children
 if any(c.content_id.startswith(PREFIX) for c in contents):raise ValueError('duplicate audience prerequisites')
 rows={k:{**thaw_model_data(v),'after':list(v.get('after',()))} for k,v in children.items()}
 for c in contents:
  rows.setdefault(c.content_id,{'kind':c.kind,'key':c.semantic_key if c.kind=='action' else 'reply' if c.kind=='speech' else 'inner_monologue' if c.kind=='thought' else 'selected_presentation','after':[]})
 if reuse_selected and len(targets)==1:
  first=next((c for c in contents if c.kind=='action'),None)
  if (first is not None and first.semantic_key=='move_near'
      and first.parameters.get('movement',{}).get('target_ref')==targets[0]
      and not rows[first.content_id]['after']):
   ctx=whisper.context(s,targets[0],b['physical_observers'])
   result=tuple(replace(c,parameters={**dict(c.parameters),'audience_context':ctx}) if c.content_id==first.content_id else c for c in contents)
   for c in contents:
    if c.kind=='speech' and first.content_id not in rows[c.content_id]['after']:
     rows[c.content_id]['after'].append(first.content_id)
   plan=CompositePlan.create(rows);plan.validate_contents(result)
   return result,thaw_model_data(plan.children)
 preps=[];previous=None;contexts=[]
 for i,target in enumerate(targets):
  ctx=whisper.context(s,target,b['physical_observers']);contexts.append(ctx)
  c=movement.content(target,{'key':'move_near','description':'走近本次话语接收者','motion':ctx['movement']})
  c=replace(c,content_id=PREFIX+str(i),recipient_refs=tuple(b['physical_observers']))
  c=replace(c,parameters={**dict(c.parameters),'audience_context':ctx})
  preps.append(c)
  rows[c.content_id]={'kind':'action','key':'move_near','after':[] if previous is None else [previous]}
  previous=c.content_id
 result=[]
 for c in contents:
  if c.kind in {'speech','action'} and not rows[c.content_id]['after']:rows[c.content_id]['after']=[previous]
  result.append(c)
 plan=CompositePlan.create(rows);plan.validate_contents((*preps,*result))
 return (*preps,*result),thaw_model_data(plan.children)

def verify_arrival(scene,contexts):
 # Recheck all listeners at speech time. A departed/blocked listener cancels
 # the unsaid suffix; earlier successful visits do not mean current proximity.
 return tuple(whisper.check_arrival(scene,c) for c in contexts)


def wrap(payload,control,bound,scene):
 from .events import Content
 base=thaw_model_data(payload)
 rows=base.get('action_sequence',{}).get('children',control.get('composite',{}).get('children',{}))
 items,rows=compile(tuple(Content.from_record(c) for c in base['contents']),rows,
  choice=base['audience'],bound=bound,scene=scene,reuse_selected=control.get('audience_motion_policy') in ('physical-audience-policy-v3','physical-audience-policy-v4'),
  single_target_only=control.get('audience_motion_policy')=='physical-audience-policy-v4')
 return {**base,'contents':[c.to_record() for c in items],'audience_plan':rows}


def unwrap_verified(request,payload):
 if request.control.get('audience_motion_contract')!=VERSION:raise ValueError('unknown audience motion contract')
 raw=thaw_model_data(payload)
 base={k:v for k,v in raw.items() if k!='audience_plan'}
 base['contents']=deepcopy([c for c in raw['contents'] if not c['content_id'].startswith(PREFIX)])
 if request.control.get('audience_motion_policy') in ('physical-audience-policy-v3','physical-audience-policy-v4'):
  for c in base['contents']:
   c.get('parameters',{}).pop('audience_context',None)
 if not request.control.get('action_sequence_contract'):
  from collections import Counter
  if Counter(c['semantic_key'] for c in base['contents'] if c['kind']=='action')!=Counter(request.control.get('selected_action_keys',())):
   raise ValueError('audience contains an unselected body action')
 if wrap(base,request.control,request.readonly_facts['audience_envelope'],request.readonly_facts['scene_context'])!=raw:
  raise ValueError('audience movement differs from sealed selection')
 return base
