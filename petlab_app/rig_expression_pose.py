"""Authored local feature transforms, independent of behavioral state."""
import math
from copy import deepcopy

DEFAULT={'x':0.,'y':0.,'angle':0.,'scale_x':1.,'scale_y':1.}

def prepare(rig, declaration):
    if declaration is None:return
    if set(declaration)!={'duration_ms','states'} or type(declaration['duration_ms']) is not int or not 40<=declaration['duration_ms']<=500:
        raise ValueError('invalid expression pose')
    states=declaration['states'];parts={p['id'] for p in rig['parts']}
    if not isinstance(states,dict) or 'normal' not in states:raise ValueError('missing normal expression pose')
    keys=set()
    for code,rows in states.items():
        if not isinstance(code,str) or not isinstance(rows,dict):raise ValueError('invalid expression pose state')
        for part,values in rows.items():
            if part not in parts or not isinstance(values,dict) or set(values)-set(DEFAULT):raise ValueError('invalid expression pose part')
            for name,value in values.items():
                if type(value) not in (int,float) or not math.isfinite(value) or (name.startswith('scale') and value<=0):raise ValueError('invalid expression pose value')
                keys.add((part,name))
    rig['expression_pose']={'duration_ms':declaration['duration_ms'],'specified':{
        code:{(part,name) for part,row in rows.items() for name in row} for code,rows in states.items()},'states':{
        code:{key:rows.get(key[0],{}).get(key[1],DEFAULT[key[1]]) for key in keys} for code,rows in states.items()}}

def endpoint(rig,code):
    config=rig.get('expression_pose')
    return {} if config is None else config['states'].get(code,config['states']['normal'])

def compose(pose,values):
    result=deepcopy(pose)
    for (part,name),value in values.items():
        row=result.setdefault(part,{})
        original=row.get(name,DEFAULT[name])
        row[name]=original*value if name.startswith('scale') else original+value
    return result

class Transition:
    def __init__(self,values):self.source=dict(values);self.target=dict(values);self.start=0.;self.duration=0.
    def sample(self,now):
        t=1. if not self.duration else min(1.,max(0.,(now-self.start)/self.duration))
        t=t*t*(3-2*t)
        return {key:self.source[key]+(value-self.source[key])*t for key,value in self.target.items()}
    def retarget(self,values,now,duration):
        if self.target==values:return
        self.source=self.sample(now);self.target=dict(values);self.start=now;self.duration=duration
    def active(self,now):return self.source!=self.target and now<self.start+self.duration
