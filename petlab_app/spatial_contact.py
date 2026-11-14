"""Swept body contact against the current desktop snapshot."""
import math
from model_packages.desktop_pet_v1.physical_space import blocks,obstacle_envelopes

def blocker(scene,point,*,include_objects=False):
 return limit(scene,point,include_objects=include_objects)[1]

def limit(scene,point,*,include_objects=False):
 own=next(r['bounds'] for r in scene['participants'] if r['ref']==scene['owner_ref'])
 obstacles=list(obstacle_envelopes(scene,include_objects=include_objects))
 steps=max(1,math.ceil(math.dist(own[:2],point)/4))
 safe=list(own[:2])
 for i in range(1,steps+1):
  b=[own[0]+(point[0]-own[0])*i/steps,own[1]+(point[1]-own[1])*i/steps,*own[2:]]
  for ref,envelope in obstacles:
   if blocks(b,ref,envelope):return safe,ref
  safe=[round(b[0]),round(b[1])]
 return list(point),None
